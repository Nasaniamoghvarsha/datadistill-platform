"""Exports: AI-ready JSON, readable dialogue (Markdown / plain text), per-folder bundle.

Always generated from the database, so speaker renames and line corrections are included.
"""
import io
import json
import time
import zipfile
from pathlib import Path

from . import db
from .config import OUTPUT_DIR
from .textutil import slide_title

SCHEMA = "datadistill.transcript/v1"


def clock(t):
    t = int(t or 0)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def speaker_label(sid, meta):
    if not sid:
        return "Unknown speaker"
    m = meta.get(sid) or {}
    return m.get("name") or f"Speaker {sid[1:]}"


def slide_prompt(s):
    """UC-F5: a text description from which the slide can be recreated (by a person or an image/slide model)."""
    import re
    # private-use characters are bullet glyphs of symbol fonts (Wingdings): not text, and no font can show them
    raw = re.sub(r"[-]", "", s.get("text") or "")
    lines = [ln.strip(" •-*\t") for ln in raw.splitlines() if ln.strip(" •-*\t") and not re.fullmatch(r"[\d\s/.]+", ln.strip())]
    title = s.get("title") or (lines[0] if lines else None)
    body = [ln for ln in lines if ln != title][:12]
    # "slide" only when it was matched to an uploaded presentation; otherwise it may be a document, web page or app
    kind = "A presentation slide" if s.get("source") else "A shared screen"
    parts = [f"{kind} titled \"{title}\"." if title else "A shared screen without readable text (too small or blurred in the video)."]
    dg = s.get("diagram")
    if dg:
        parts.append(f"It shows a {dg['kind']} with {len(dg['nodes'])} boxes and {len(dg['edges'])} arrows: "
                     + "; ".join(dg["text"].splitlines()[:25]) + ".")
    if body:
        parts.append("Content, top to bottom: " + "; ".join(f"\"{b[:120]}\"" for b in body) + ".")
    if s.get("source"):
        parts.append(f"Original: {s['source']['doc']}, slide {s['source']['page']}"
                     + (f" ({s['source']['doc_no']} rev {s['source']['rev']})" if s["source"].get("doc_no") else "") + ".")
    return " ".join(parts)


def screen_key(s):
    """What makes two moments 'the same slide': the stored slide id when there is one (a slide that was shown, left and shown
    again, or two screens detected for one slide, is one slide), otherwise the screen's own number."""
    if not s or s["kind"] == "non_slide":
        return None
    return s.get("slide_uid") or f"screen{s['idx']}"


def slide_changes(d):
    """The moments where what is on screen changes, in order: [(turn_index, screen)]. A line that is said while the same slide
    stays up is not a change, so the slide is shown once and its text is not repeated for every line."""
    by_idx = {s["idx"]: s for s in d["screens"]}
    out, cur = [], object()
    for i, t in enumerate(d["turns"]):
        s = by_idx.get(t["slide_idx"])
        key = screen_key(s)
        if key != cur:
            cur = key
            if s and key:
                out.append((i, s))
    return out


def load_recording(file_id):
    rec = db.q1("SELECT * FROM recordings WHERE file_id=?", (file_id,))
    if not rec:
        return None
    manifest = json.loads(rec["manifest"])
    meta = json.loads(rec["speakers"] or "{}")
    participants = json.loads(rec.get("participants") or "[]")
    screens = db.q("""SELECT s.idx, s.t_start, s.t_end, s.kind, s.slide_uid, s.ocr_text, s.page_id, s.image, p.page, p.text page_text,
                             p.image page_image, p.thumb page_thumb, p.diagram page_diagram, p.file_id doc_file_id, f.name doc_name, f.meta doc_meta
                      FROM rec_slides s LEFT JOIN pages p ON p.id=s.page_id LEFT JOIN files f ON f.id=p.file_id
                      WHERE s.file_id=? ORDER BY s.idx""", (file_id,))
    for s in screens:
        m = json.loads(s.pop("doc_meta") or "{}")
        s["title"] = slide_title(s["page_text"], s["ocr_text"]) if s["kind"] != "non_slide" else None
        s["text"] = (s["page_text"] or s["ocr_text"] or "").strip() if s["kind"] != "non_slide" else ""
        s["diagram"] = json.loads(s.pop("page_diagram")) if s.get("page_diagram") else None
        s["source"] = ({"doc_file_id": s["doc_file_id"], "doc": s["doc_name"], "page": s["page"],
                        "doc_no": m.get("doc_no", ""), "rev": m.get("rev", "")} if s["page_id"] else None)
        s["prompt"] = slide_prompt(s) if s["kind"] != "non_slide" else None
    turns = db.q("SELECT id, idx, start, end, speaker, text, slide_idx, confidence, speaker_confidence, edited, original_text, overlap, attribution "
                 "FROM segments WHERE file_id=? ORDER BY idx", (file_id,))
    for t in turns:
        t["speaker_name"] = speaker_label(t["speaker"], meta)
        t["overlap_with"] = json.loads(t.pop("overlap")) if t.get("overlap") else None
        t["attribution"] = json.loads(t["attribution"]) if t.get("attribution") else None
        t["camera_label"] = (t["attribution"] or {}).get("camera") or ((meta.get(t["speaker"]) or {}).get("camera") or {}).get("label")
    speakers = [{"id": sid, "label": speaker_label(sid, meta), **m} for sid, m in
                sorted(meta.items(), key=lambda kv: int(kv[0][1:]))]
    f = db.file_row(file_id)
    snaps = db.q("SELECT idx, t, seg_idx, phrase, image, bytes, width, height FROM snapshots WHERE file_id=? ORDER BY idx",
                 (file_id,))
    return {"file": f, "manifest": manifest, "speakers": speakers, "participants": participants,
            "screens": screens, "turns": turns, "snapshots": snaps}


def ai_json(file_id):
    d = load_recording(file_id)
    m = d["manifest"]
    shared = [s for s in d["screens"] if s["kind"] != "non_slide"]
    docs = {}
    for s in shared:
        if s["source"]:
            docs.setdefault(s["source"]["doc_file_id"], {"name": s["source"]["doc"], "doc_no": s["source"]["doc_no"],
                                                         "rev": s["source"]["rev"]})
    folder_docs = db.q("SELECT id, name, meta FROM files WHERE system=? AND kind='document' AND status='processed'",
                       (d["file"]["system"],))
    changes = {i: s for i, s in slide_changes(d)}
    sp = OUTPUT_DIR / "recordings" / str(file_id) / "summary.json"  # the checked AI summary, when one was made
    summ = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else None
    return {
        "schema": SCHEMA,
        "recording": {"name": m["source"]["name"], "folder": m["source"]["folder"], "duration_s": m["source"]["duration_s"],
                      "sha256": m["source"]["sha256"], "processed_at": m["processed_at"],
                      "processing_time_s": m["timings_s"].get("total")},
        "quality": {**m["quality"], "asr_model": m["models"]["asr"], "language": m["transcript"].get("language"),
                    "transcript_source": m["transcript"].get("source")},
        "summary": ({k: summ.get(k) for k in ("title", "purpose", "summary", "discussion", "decisions", "action_items",
                                              "open_questions", "model")} if summ and not summ.get("error") else None),
        "speakers": [{"id": s["id"], "name": s.get("name"), "label": s["label"], "name_source": s.get("source"),
                      "name_score": s.get("score"), "talk_time_s": s.get("talk_time_s"),
                      "camera": s.get("camera"), "person_id": s.get("person_id"), "identity_status": s.get("identity_status"),
                      "candidate_name": s.get("candidate_name"), "account_label": s.get("account_label"),
                      "voice_sample": ({k: s["voice"][k] for k in ("file", "seconds", "clips")} if s.get("voice") else None)}
                     for s in d["speakers"]],
        "snapshots": [{"t": s["t"], "transcript_index": s["seg_idx"], "trigger": s["phrase"], "file": s["image"],
                       "width": s["width"], "height": s["height"], "bytes": s["bytes"]} for s in d["snapshots"]],
        "participants_on_screen": d["participants"],
        "context": {
            "shared_screens": [{"id": s["idx"], "start": s["t_start"], "end": s["t_end"], "title": s["title"],
                                "type": "document_slide" if s["kind"] == "slide" else "screen_share",
                                "slide_uid": s["slide_uid"],
                                "source": ({k: s["source"][k] for k in ("doc", "page", "doc_no", "rev")} if s["source"] else None),
                                "image": s["image"] or s.get("page_image"),
                                "text": s["text"], "slide_prompt": s["prompt"],
                                "diagram": ({k: s["diagram"][k] for k in ("kind", "method", "nodes", "edges", "text", "mermaid")}
                                            if s.get("diagram") else None)} for s in shared],
            "documents_shown": list(docs.values()),
            "documents_in_folder": [{"name": x["name"], **{k: json.loads(x["meta"]).get(k, "") for k in ("doc_no", "rev", "title")}}
                                    for x in folder_docs],
        },
        # the slide on screen is spelled out only on the line where it changes ("slide_change"); every line still says which
        # shared screen (context.shared_screens) it was said in front of
        "slide_changes": [{"at": d["turns"][i]["start"], "transcript_index": i, "shared_screen": s["idx"], "slide_uid": s["slide_uid"],
                           "title": s["title"], "image": s["image"] or s.get("page_image")} for i, s in changes.items()],
        "transcript": [{"start": t["start"], "end": t["end"], "speaker": t["speaker"], "speaker_name": t["speaker_name"],
                        "text": t["text"], "confidence": t["confidence"],
                        "shared_screen": t["slide_idx"] if any(s["idx"] == t["slide_idx"] for s in shared) else None,
                        **({"slide_change": True, "slide_uid": changes[i]["slide_uid"],
                            # UC-L1: the frame that comes on screen here
                            "screen_image": changes[i]["image"] or changes[i].get("page_image")} if i in changes else {}),
                        "overlap_with": t.get("overlap_with"), "attribution": t.get("attribution"), "camera_label": t.get("camera_label"),
                        "edited": bool(t["edited"])} for i, t in enumerate(d["turns"])],
    }


def dialogue_text(file_id, markdown=True):
    d = load_recording(file_id)
    m = d["manifest"]
    by_idx = {s["idx"]: s for s in d["screens"]}
    out = []
    if markdown:
        out += [f"# {m['source']['name']}", "",
                f"- Folder: {m['source']['folder']}",
                f"- Duration: {clock(m['source']['duration_s'])}",
                f"- Speakers: {', '.join(s['label'] for s in d['speakers']) or 'not identified'}",
                f"- Recognition confidence: {round(100 * m['quality']['mean_confidence'])}%" if m["quality"]["mean_confidence"] else "- Recognition confidence: n/a",
                ""]
    cur = object()
    last_blank = False
    last_start = 0.0
    para = None  # consecutive lines of one speaker (pause < 3 s) form one dialogue turn
    snaps = {}
    for s in d["snapshots"]:
        snaps.setdefault(s["seg_idx"], s)

    def flush():
        if para:
            mark = f" (overlapping with {', '.join(para['overlap'])}; text may be incomplete)" if para.get("overlap") else ""
            out.append(f"[{clock(para['start'])}] {para['speaker']}{mark}: {' '.join(para['texts'])}")
            for s in para["snaps"]:
                out.append(f"![Snapshot {clock(s['t'])}: \"{s['phrase']}\"]({s['image']})" if markdown
                           else f"    [snapshot {clock(s['t'])}: {s['image']}]")

    for t in d["turns"]:
        s = by_idx.get(t["slide_idx"])
        key = screen_key(s)
        if key != cur:                                    # the slide CHANGED (not just a new detection of the same slide)
            cur = key
            if s and key:
                img = s.get("image") or s.get("page_image")
                blank = not (s["title"] or s["text"] or img or s["source"])
                if not (blank and last_blank):            # a run of unreadable screens is announced once
                    flush()
                    para = None
                    src = f" ({s['source']['doc']}, slide {s['source']['page']})" if s["source"] else ""
                    # never earlier than the line before it: the screen may have been detected before that line ended
                    at = min(max(s["t_start"] or 0, last_start), t["start"])
                    out += ["", f"[{clock(at)}] SHARED SCREEN: {s['title'] or 'Screen share'}{src}"
                            + (f" [slide id {s['slide_uid']}]" if s.get("slide_uid") else "")]
                    if img:
                        out.append(f"![{s['title'] or 'Shared screen'}]({img})" if markdown
                                   else f"    [slide picture: {img} (in the zip from Download all)]")
                    if s["text"]:
                        out += ["    " + ln for ln in s["text"].splitlines() if ln.strip()][:60]
                    out.append("")
                last_blank = blank
        last_start = t["start"]
        if para and para["speaker"] == t["speaker_name"] and t["start"] - para["end"] < 3.0:
            para["texts"].append(t["text"])
            para["end"] = t["end"]
            para["overlap"] = sorted(set(para.get("overlap") or []) | set(t.get("overlap_with") or []))
        else:
            flush()
            para = {"speaker": t["speaker_name"], "start": t["start"], "end": t["end"], "texts": [t["text"]], "snaps": [],
                    "overlap": list(t.get("overlap_with") or [])}
        if t["idx"] in snaps:
            para["snaps"].append(snaps[t["idx"]])
    flush()
    return "\n".join(out) + "\n"


_ASCII = {"å": "a", "Å": "A", "ä": "a", "Ä": "A", "ö": "o", "Ö": "O", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
          "ß": "ss", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ı": "i",
          "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2013": "-", "\u2014": "--",
          "\u2212": "-", "\u2026": "...", "\u2022": "*", "\u00b7": "*", "\u25cf": "*", "\u25aa": "*", "\u2192": "->",
          "\u2190": "<-", "\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u00d7": "x", "\u00a9": "(c)", "\u00ae": "(R)",
          "\u2122": "(TM)", "\u20ac": "EUR", "\u00b0": " deg"}


def to_ascii(text):
    """Plain 7-bit ASCII for tools that cannot read anything else. Accents are dropped (Hägglund -> Hagglund), Nordic letters
    and typographic quotes, dashes and bullets get their usual ASCII form; whatever has no ASCII form becomes '?'."""
    import unicodedata
    out = []
    for ch in text or "":
        if ord(ch) < 128:
            out.append(ch)
        elif ch in _ASCII:
            out.append(_ASCII[ch])
        else:
            base = unicodedata.normalize("NFKD", ch)
            base = "".join(c for c in base if not unicodedata.combining(c))
            out.append(base if base and all(ord(c) < 128 for c in base) else "?")
    return "".join(out)


def ascii_text(file_id):
    """The readable transcript (with the slide text) as pure ASCII."""
    return to_ascii(dialogue_text(file_id, markdown=False))


def document_json(file_id):
    """A document as AI-ready JSON. Every line carries a line_id; text that also appears elsewhere (other slides,
    other decks, other PDFs) lists those places in also_in, so it can be stored once and referenced."""
    from . import lines
    f = db.file_row(file_id)
    pages = db.q("SELECT page, text, slide_uid, diagram FROM pages WHERE file_id=? ORDER BY page", (file_id,))
    m = f["meta"]
    pl = lines.page_lines(file_id)
    refs = lambda also: [{"file_id": o["file_id"], "document": o["name"], "folder": o["folder"], "page": o["page"]} for o in also]
    out_pages = []
    for p in pages:
        ls = pl.get(p["page"], [])
        out_pages.append({"page": p["page"], "slide_id": p["slide_uid"], "title": slide_title(p["text"]),
                          "text": (p["text"] or "").strip(),
                          "diagram": json.loads(p["diagram"]) if p.get("diagram") else None,
                          "lines": [{"line_id": x["line_id"], "text": x["text"], "footer": x["footer"],
                                     "also_in": refs(x["also_in"])} for x in ls]})
    all_lines = [x for p in out_pages for x in p["lines"]]
    shared = [x for x in all_lines if x["also_in"] and not x["footer"]]
    return {"schema": "datadistill.document/v2", "name": f["name"], "folder": f["system"], "sha256": f["sha256"],
            "doc_no": m.get("doc_no", ""), "rev": m.get("rev", ""), "title": m.get("title", ""), "author": m.get("author", ""),
            "classification": m.get("label", ""),
            "repeated_text": {"lines": len(all_lines), "also_elsewhere": len(shared),
                              "footer_lines": sum(x["footer"] for x in all_lines),
                              "other_documents": sorted({r["document"] for x in shared for r in x["also_in"]
                                                         if r["file_id"] != file_id})},
            "pages": out_pages}


def write_outputs(file_id):
    base = OUTPUT_DIR / "recordings" / str(file_id)
    base.mkdir(parents=True, exist_ok=True)
    (base / "transcript.json").write_text(json.dumps(ai_json(file_id), indent=1, ensure_ascii=False), encoding="utf-8")
    (base / "transcript.md").write_text(dialogue_text(file_id), encoding="utf-8")
    return base


def extract_files(file_id):
    """Relative paths of the snapshot images and voice samples of a recording."""
    base = OUTPUT_DIR / "recordings" / str(file_id)
    return [p.relative_to(base).as_posix() for sub in ("snapshots", "voices") if (base / sub).exists()
            for p in sorted((base / sub).iterdir()) if p.is_file()]


def slides_transcript(file_id, markdown=True):
    """The transcript sorted by slide: every slide (picture, title, full text, source) followed by what was said while it was on
    screen. A section ends when the slide changes, so a slide shown again later gets its own section."""
    d = load_recording(file_id)
    m = d["manifest"]
    by_idx = {s["idx"]: s for s in d["screens"]}
    out = [f"# {m['source']['name']} - transcript by slide" if markdown else f"{m['source']['name']} - TRANSCRIPT BY SLIDE", ""]
    sections = []
    for t in d["turns"]:
        s = by_idx.get(t["slide_idx"])
        key = screen_key(s)
        if not sections or sections[-1]["key"] != key:
            sections.append({"key": key, "screen": s if key else None, "turns": []})
        sections[-1]["turns"].append(t)
    for sec in sections:
        s, ts = sec["screen"], sec["turns"]
        at = clock(ts[0]["start"])
        if s:
            src = f" ({s['source']['doc']}, slide {s['source']['page']})" if s["source"] else ""
            title = f"{s['title'] or 'Shared screen'}{src}"
            out += [f"## [{at}] {title}" if markdown else f"[{at}] SLIDE: {title}"]
            if s.get("slide_uid"):
                out.append(f"slide id: {s['slide_uid']}")
            img = s.get("image") or s.get("page_image")
            if img:
                out.append(f"![{s['title'] or 'slide'}]({img})" if markdown else f"[slide picture: {img}]")
            if s["text"]:
                out += ["", "Text on the slide:"] + [("> " if markdown else "    ") + ln for ln in s["text"].splitlines() if ln.strip()]
            if s.get("diagram"):
                out += ["", f"Diagram ({s['diagram']['kind']}):"] + ["    " + ln for ln in s["diagram"]["text"].splitlines()[:40]]
        else:
            out.append(f"## [{at}] No slide on screen" if markdown else f"[{at}] NO SLIDE ON SCREEN")
        out += ["", "Said while this slide was on screen:" if s else "Said:"]
        para = None
        for t in ts:
            if para and para["speaker"] == t["speaker_name"] and t["start"] - para["end"] < 3.0:
                para["texts"].append(t["text"])
                para["end"] = t["end"]
            else:
                if para:
                    out.append(f"[{clock(para['start'])}] {para['speaker']}: {' '.join(para['texts'])}")
                para = {"speaker": t["speaker_name"], "start": t["start"], "end": t["end"], "texts": [t["text"]]}
        if para:
            out.append(f"[{clock(para['start'])}] {para['speaker']}: {' '.join(para['texts'])}")
        out.append("")
    return "\n".join(out) + "\n"


def audio_track(file_id):
    """The whole sound of the recording as a small mono Opus file (cached next to the transcript), or None when the original
    video is gone (offboarding deleted it: only the voice samples are kept then)."""
    import subprocess

    from .config import DATA_DIR, FFMPEG
    f = db.file_row(file_id)
    dst = OUTPUT_DIR / "recordings" / str(file_id) / "audio.ogg"
    if dst.exists():
        return dst
    meta = f["meta"] if isinstance(f["meta"], dict) else json.loads(f["meta"] or "{}")
    src = DATA_DIR / f["path"]
    if meta.get("original_deleted") or not src.is_file():
        return None
    try:
        from .media import NO_WINDOW
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "24k",
                        "-application", "voip", str(dst)], capture_output=True, timeout=1800, creationflags=NO_WINDOW)
    except Exception:  # noqa: BLE001
        dst.unlink(missing_ok=True)
        return None
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    dst.unlink(missing_ok=True)
    return None


_README = """DataDistill - {name}

report.pdf                   One report: summary, people, quality, the shared slides, and the full transcript with every slide
                             shown where it comes on screen.
transcript.md / .txt         The transcript with the slides (picture, title, text) where they change. The .md shows the pictures
                             (they are in snapshots/ and pages/).
transcript.ascii.txt         The same, as plain ASCII.
slides-transcript.md / .txt  The transcript sorted by slide: each slide with its text, then what was said while it was on screen.
slides-transcript.ascii.txt  The same, as plain ASCII.
transcript.json              Everything as structured data (speakers, lines, slides, summary, quality).
audio/                       The sound of the whole recording{audio_note}
voices/                      A short sample of each speaker's voice.
snapshots/, pages/           Pictures of the shared screens and of the matched document pages.
"""


def recording_zip(file_id) -> bytes:
    """Everything of one recording in one zip: report PDF, transcript (with slides) as md / txt / ASCII, the transcript by slide,
    JSON, the pictures of the slides, the voice samples and the sound of the recording."""
    from . import report
    f = db.file_row(file_id)
    stem = Path(f["name"]).stem if f else str(file_id)
    d = load_recording(file_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        try:
            pdf, _ = report.build(file_id)
            z.writestr("report.pdf", pdf)
        except Exception as e:  # noqa: BLE001  (the rest of the package is still worth having)
            z.writestr("report-error.txt", f"The PDF report could not be made: {type(e).__name__}: {e}\n")
        z.writestr("transcript.md", dialogue_text(file_id))
        z.writestr("transcript.txt", dialogue_text(file_id, markdown=False))
        z.writestr("transcript.ascii.txt", ascii_text(file_id))
        z.writestr("slides-transcript.md", slides_transcript(file_id))
        z.writestr("slides-transcript.txt", slides_transcript(file_id, markdown=False))
        z.writestr("slides-transcript.ascii.txt", to_ascii(slides_transcript(file_id, markdown=False)))
        z.writestr("transcript.json", json.dumps(ai_json(file_id), indent=1, ensure_ascii=False))
        written = set()
        for rel in extract_files(file_id):
            z.write(OUTPUT_DIR / "recordings" / str(file_id) / rel, rel)
            written.add(rel)
        for rel in page_images(file_id):
            z.write(OUTPUT_DIR / rel, rel)
            written.add(rel)
        for s in d["screens"]:                               # a slide picture that is not among the snapshots
            rel = s.get("image")
            if rel and rel not in written and (OUTPUT_DIR / "recordings" / str(file_id) / rel).is_file():
                z.write(OUTPUT_DIR / "recordings" / str(file_id) / rel, rel)
                written.add(rel)
        audio = audio_track(file_id)
        if audio:
            z.write(audio, f"audio/{to_ascii(stem)}.ogg")
        note = " (audio/)." if audio else ".\n                             Not included: the original video was deleted after processing; the voice samples are kept."
        z.writestr("README.txt", _README.format(name=f["name"] if f else file_id, audio_note=note))
    return buf.getvalue()


def page_images(file_id):
    """Document page images that the recording's matched slides point to (paths relative to the output folder)."""
    rows = db.q("SELECT DISTINCT p.image FROM rec_slides s JOIN pages p ON p.id=s.page_id WHERE s.file_id=? AND p.image IS NOT NULL",
                (file_id,))
    return [r["image"] for r in rows if (OUTPUT_DIR / r["image"]).exists()]


def folder_zip(folder) -> bytes:
    files = db.q("SELECT id, name, kind FROM files WHERE system=? AND status='processed' AND kind IN ('video','document') "
                 "ORDER BY kind DESC, name", (folder,))
    buf = io.BytesIO()
    index = {"schema": "datadistill.folder/v1", "folder": folder, "exported_at": time.time(), "files": []}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            stem = f["name"].rsplit(".", 1)[0]
            if f["kind"] == "video":
                if not db.q1("SELECT 1 FROM recordings WHERE file_id=?", (f["id"],)):
                    continue
                z.writestr(f"recordings/{stem}.json", json.dumps(ai_json(f["id"]), indent=1, ensure_ascii=False))
                z.writestr(f"recordings/{stem}.txt", dialogue_text(f["id"], markdown=False))
                for rel in extract_files(f["id"]):  # snapshots/..., voices/... next to the transcript
                    z.write(OUTPUT_DIR / "recordings" / str(f["id"]) / rel, f"recordings/{stem}/{rel}")
                for rel in page_images(f["id"]):
                    if rel not in z.namelist():
                        z.write(OUTPUT_DIR / rel, rel)
                index["files"].append({"name": f["name"], "type": "recording", "json": f"recordings/{stem}.json",
                                       "extracts": f"recordings/{stem}/"})
            else:
                z.writestr(f"documents/{stem}.json", json.dumps(document_json(f["id"]), indent=1, ensure_ascii=False))
                index["files"].append({"name": f["name"], "type": "document", "json": f"documents/{stem}.json"})
        z.writestr("index.json", json.dumps(index, indent=1, ensure_ascii=False))
    return buf.getvalue()
