"""Speech -> time-stamped text (UC-T1/T2). Spec §4.3.

- Teams .vtt/.srt sidecar (same folder, same name) is used when present: real speaker names, no ASR.
- Otherwise faster-whisper (local, MIT) on audio decoded by the ffmpeg CLI.
- Hallucination guard + per-segment confidence.
"""
import re
import threading
from pathlib import Path

import numpy as np

from .config import WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_MODEL

_model = None
_lock = threading.Lock()
HALLUCINATIONS = re.compile(r"^(thanks? (you )?for watching|subtitles? by|please subscribe|\.+|you)\.?$", re.I)


def release():
    """Let go of the speech model and its GPU memory. The summary model (Ollama) is loaded right after a recording is
    processed and needs that memory: a 6 GB model fits next to nothing else on an 8 GB card (the summaries of two videos
    failed with an Ollama error because this process was still holding the GPU)."""
    global _model
    with _lock:
        _model = None
    free_cuda()


def free_cuda():
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def get_model():
    global _model
    with _lock:
        if _model is None:
            import torch  # noqa: F401  load CUDA/cuDNN DLLs before ctranslate2 (Windows)
            from faster_whisper import WhisperModel
            try:
                _model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
            except Exception:
                _model = WhisperModel("small", device="cpu", compute_type="int8")
        return _model


# Speech detection: the default cut off quiet words; switching it off makes the model invent text in silence
# ("Thank you." x12 in 3 min of room noise). This gentler setting measured best on a human-transcribed meeting:
# word errors 26.0% (default 26.9%), and 0 invented words in silence.
VAD = {"threshold": 0.3, "min_silence_duration_ms": 1000, "speech_pad_ms": 800}


SR = 16000
CHUNK_S = 600      # long recordings are transcribed in ~10 min pieces (see transcribe())
SEARCH_S = 30      # each cut is placed at the quietest moment within +-30 s of the 10 min mark


def cut_points(audio, sr=SR, chunk=CHUNK_S, search=SEARCH_S):
    """Sample positions where a long recording is cut: the quietest 0.5 s near every chunk mark,
    so a sentence is not split in two."""
    cuts, n = [0], len(audio)
    hop = sr // 10  # 0.1 s frames
    while n - cuts[-1] > (chunk + search) * sr:
        a = cuts[-1] + (chunk - search) * sr
        seg = audio[a:a + 2 * search * sr]
        frames = seg[: len(seg) // hop * hop].reshape(-1, hop)
        energy = np.convolve((frames ** 2).mean(1), np.ones(5) / 5, mode="same")  # 0.5 s smoothing
        cuts.append(a + int(np.argmin(energy)) * hop + hop // 2)
    return cuts + [n]


def transcribe(audio, duration, initial_prompt=None, progress=None, cancel=None):
    """Speech -> segments with word times. Long audio is processed in ~10 min pieces: the model prepares
    (speech detection, audio features) the whole input before it returns its first sentence, which took
    minutes with no progress for a 2 h recording and grew faster than the length. Pieces keep that
    step short (~20 s each), memory bounded and progress moving. No context is lost at a cut:
    each sentence is decoded on its own (condition_on_previous_text=False) and cuts sit in pauses."""
    model = get_model()
    cuts = cut_points(audio)
    out, info0 = [], None
    for a, b in zip(cuts, cuts[1:]):
        offset = a / SR
        if progress and duration:
            progress(min(1.0, offset / duration))
        segs, info = model.transcribe(audio[a:b], vad_filter=True, vad_parameters=VAD, word_timestamps=True, beam_size=5,
                                      condition_on_previous_text=False, initial_prompt=initial_prompt or None)
        info0 = info0 or info
        out += _collect(segs, duration, progress, cancel, offset)
    main = info0.language if info0 else None
    switched = other_language_pass(model, audio, out, main, cancel=cancel) if main and out else []
    return out, {"language": main,
                 "language_probability": round(info0.language_probability, 3) if info0 else None,
                 "other_language_lines": len(switched),
                 "other_languages": sorted({s["language"] for s in switched}),
                 "source": "whisper", "model": WHISPER_MODEL, "pieces": len(cuts) - 1}


LANG_WIN_S = 30          # language check per 30 s window (one encoder pass each: ~1 min for 2.5 h)
LANG_MIN_P = 0.6         # a window counts as another language from this probability
LINE_MIN_P = 0.7         # a line is replaced by its other-language decoding from this probability


def other_language_pass(model, audio, segs, main, cancel=None):
    """Meetings switch language (Swedish questions in an English talk). Each 10 min piece is decoded in ONE
    language, which forces other-language speech into it ("Ericsson-anstalled", "not utesluted"). Windows whose
    language differs, and low-confidence lines, are decoded again without a fixed language; a line is replaced only
    when that decoding is confidently another language. Returns the replaced segments."""
    other = []
    for s in range(0, max(0, len(audio) // SR - 5), LANG_WIN_S):
        if cancel and cancel():
            raise InterruptedError("cancelled")
        a = audio[s * SR:(s + LANG_WIN_S) * SR]
        if len(a) < SR or float(np.abs(a).max()) < 1e-3:
            continue
        lang, _, probs = model.detect_language(a)
        best = max(((code, p) for code, p in probs if code != main), key=lambda x: x[1], default=(None, 0))
        if best[0] and best[1] >= LANG_MIN_P:
            other.append((s, s + LANG_WIN_S))
    replaced = []
    for seg in segs:
        overlap = sum(max(0.0, min(seg["end"], b) - max(seg["start"], a)) for a, b in other)
        low = seg["confidence"] < 0.6 and len(seg["text"].split()) >= 3
        if not (overlap >= 0.5 * max(0.1, seg["end"] - seg["start"]) or low):
            continue
        a0 = max(0, int((seg["start"] - 0.1) * SR))
        piece = audio[a0:int((seg["end"] + 0.3) * SR)]
        if len(piece) < SR // 2:
            continue
        res, info = model.transcribe(piece, word_timestamps=True, beam_size=5, condition_on_previous_text=False)
        res = list(res)
        if info.language == main or info.language_probability < LINE_MIN_P or not res:
            continue
        off = a0 / SR
        words = [{"start": round(off + w.start, 2), "end": round(off + w.end, 2), "word": w.word,
                  "probability": round(w.probability, 3)} for r in res for w in (r.words or [])]
        text = " ".join(r.text.strip() for r in res).strip()
        if not text or not words:
            continue
        seg.update(text=text, words=words, language=info.language,
                   confidence=round(float(np.mean([w["probability"] for w in words])), 3), original_text=seg["text"])
        replaced.append(seg)
    return replaced


def _collect(segs, duration, progress, cancel, offset=0.0):
    out = []
    for s in segs:
        if cancel and cancel():
            raise InterruptedError("cancelled")
        text = s.text.strip()
        if progress and duration:
            progress(min(1.0, (offset + s.end) / duration))
        if not text or HALLUCINATIONS.match(text):
            continue
        if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
            continue
        words = [{"start": round(offset + w.start, 2), "end": round(offset + w.end, 2), "word": w.word,
                  "probability": round(w.probability, 3)} for w in (s.words or [])]
        conf = float(np.mean([w["probability"] for w in words])) if words else float(np.exp(s.avg_logprob))
        out.append({"start": round(offset + s.start, 2), "end": round(offset + s.end, 2), "text": text, "speaker": None,
                    "confidence": round(conf, 3),
                    "avg_logprob": round(s.avg_logprob, 3), "no_speech_prob": round(s.no_speech_prob, 3),
                    "words": words})
    return out


_TS = r"(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}"


def _sec(ts):
    ts = ts.replace(",", ".")
    parts = [float(p) for p in ts.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def parse_caption_file(path: Path):
    """Parse WebVTT / SRT. Teams uses <v Speaker Name>text</v>."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    out = []
    for b in blocks:
        m = re.search(rf"({_TS})\s*-->\s*({_TS})", b)
        if not m:
            continue
        body = b[m.end():].strip()
        spk = None
        v = re.search(r"<v(?:\.[^ >]*)?\s+([^>]+)>", body)
        if v:
            spk = v.group(1).strip()
        body = re.sub(r"<[^>]+>", "", body).strip()
        if body:
            out.append({"start": round(_sec(m.group(1)), 2), "end": round(_sec(m.group(3)), 2), "text": body,
                        "speaker": spk, "avg_logprob": None, "no_speech_prob": None, "words": []})
    return out, {"language": None, "source": "captions:" + path.name, "model": None}


def find_sidecar(video: Path):
    for ext in (".vtt", ".srt"):
        p = video.with_suffix(ext)
        if p.exists():
            return p
    return None


def assign_slides(segments, slide_segs, lead=1.0):
    """Link every transcript line to the slide on screen (UC-L1); split lines at slide boundaries.

    Slide changes are detected on 1 fps samples, so a detected start can be up to `lead` seconds late.
    Words are therefore matched at (t + lead): "As you see here" spoken as the new slide appears
    belongs to the NEW slide, not the previous one.
    """
    bounds = [(s["start"] - (lead if i else 0), s["end"] - lead, i) for i, s in enumerate(slide_segs)]
    if bounds:
        bounds[-1] = (bounds[-1][0], float("inf"), bounds[-1][2])

    def slide_at(t):
        for a, b, i in bounds:
            if a <= t < b:
                return i
        return bounds[-1][2] if bounds and t >= bounds[-1][0] else None

    out = []
    for seg in segments:
        words = seg.get("words") or []
        if not words or not bounds:
            out.append({**seg, "slide_idx": slide_at((seg["start"] + seg["end"]) / 2)})
            continue
        groups = []
        for w in words:
            idx = slide_at((w["start"] + w["end"]) / 2)
            if groups and groups[-1][0] == idx:
                groups[-1][1].append(w)
            else:
                groups.append((idx, [w]))
        # avoid tiny fragments: only split when every piece has >= 3 words
        if len(groups) > 1 and all(len(g[1]) >= 3 for g in groups):
            for idx, ws in groups:
                out.append({**seg, "start": round(ws[0]["start"], 2), "end": round(ws[-1]["end"], 2),
                            "text": "".join(w["word"] for w in ws).strip(), "slide_idx": idx})
        else:
            out.append({**seg, "slide_idx": slide_at((seg["start"] + seg["end"]) / 2)})
    return out


def vocabulary_prompt(page_texts, limit=220):
    """Domain words from the matched slides -> Whisper initial_prompt (jargon, acronyms, names)."""
    seen, words = set(), []
    for t in page_texts:
        for w in re.findall(r"\b[A-Z][A-Za-z0-9-]{2,}\b|\b[A-Z0-9]{2,}\b", t or ""):
            if w.lower() not in seen:
                seen.add(w.lower())
                words.append(w)
    return ", ".join(words)[:limit] or None
