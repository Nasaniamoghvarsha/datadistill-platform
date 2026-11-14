"""Processing pipelines.

Document  -> text per slide + slide hashes (context for recordings in the same folder).
Recording -> speaker-attributed, time-stamped transcript with the shared-screen context:
   probe -> 1 fps frames -> shared screens (slides / documents) -> speech to text
   -> who spoke when (voice) -> names from the screen -> link text to screens -> save
   -> snapshots where the speech refers to the screen (<= 2 MB) + ~10 s voice sample per speaker.
The output is text plus these small extracts: enough to delete the original video afterwards.
"""
import json
import shutil
import time

import numpy as np

from . import (db, docs, faces, highlight, identity, media, namefold, package, samecontent, screen_names, search, slides, snapshots, speakers,
               speech_names, soundtrack, transcribe, visual_speakers, voices)
from .config import DATA_DIR, EMBED_MODEL, FACES, OUTPUT_DIR, OVERLAP, TMP_DIR, WHISPER_MODEL
from .match import RefLibrary

MIN_TILE_EVIDENCE_S = 60   # seconds of a voice speaking while the participant tiles are visible, to name it from a tile
SELF_ADDRESS_VETO = 2      # a voice that calls a name this often is not that person

# share of the progress bar per stage (roughly proportional to typical run time)
STAGES = [("probe", 0.01), ("frames", 0.07), ("names", 0.12), ("screens", 0.10), ("transcribe", 0.45),
          ("speakers", 0.08), ("match", 0.12), ("save", 0.02), ("keep", 0.03)]
STAGE_LABEL = {"probe": "Reading file", "frames": "Sampling video", "names": "Reading names on screen",
               "screens": "Reading shared screens", "transcribe": "Transcribing speech", "speakers": "Separating speakers",
               "match": "Matching voices to names", "save": "Saving transcript",
               "keep": "Saving snapshots and voice samples", "document": "Reading document"}


class NullCtx:
    def stage(self, name, pct, msg=None):
        print(f"[{pct * 100:5.1f}%] {name} {msg or ''}")

    def log(self, msg):
        print("   ", msg)

    def cancelled(self):
        return False


class Progress:
    """Maps per-stage progress (0..1) to overall progress and records stage timings."""

    def __init__(self, ctx):
        self.ctx, self.t0, self.cur, self.start = ctx, time.time(), None, {}
        self.timings = {}
        self.base = {}
        acc = 0.0
        for name, w in STAGES:
            self.base[name] = (acc, w)
            acc += w

    def enter(self, name, msg=None):
        now = time.time()
        if self.cur:
            self.timings[self.cur] = round(now - self.start[self.cur], 1)
        self.cur = name
        self.start[name] = now
        self.ctx.stage(STAGE_LABEL.get(name, name), self.base[name][0], msg)

    def sub(self, p):
        a, w = self.base[self.cur]
        self.ctx.stage(STAGE_LABEL.get(self.cur, self.cur), a + w * min(1.0, max(0.0, p)))

    def done(self):
        if self.cur:
            self.timings[self.cur] = round(time.time() - self.start[self.cur], 1)
        self.timings["total"] = round(time.time() - self.t0, 1)
        return self.timings


def process_file(file_id, ctx=None):
    ctx = ctx or NullCtx()
    f = db.file_row(file_id)
    # never touch a recording whose original is gone: re-processing starts by clearing its outputs
    if f["status"] == "shortcut" or f["meta"].get("original_deleted") or not (DATA_DIR / f["path"]).exists():
        if f["status"] == "queued":
            db.ex("UPDATE files SET status=? WHERE id=?", ("processed" if f["meta"].get("original_deleted") else "error", file_id))
        raise FileNotFoundError("the original file is no longer there: the existing transcript is kept unchanged")
    db.ex("UPDATE files SET status='processing', error=NULL WHERE id=?", (file_id,))
    try:
        t0 = time.time()
        if f["kind"] == "document":
            ctx.stage(STAGE_LABEL["document"], 0.05, f["name"])
            info = docs.process_document(file_id, progress=lambda p: ctx.stage(STAGE_LABEL["document"], 0.05 + 0.9 * p))
            db.set_meta(file_id, processing_s=round(time.time() - t0, 1))
            ctx.log(f"{info['pages']} slides, doc no. {info['doc_no'] or '-'}, rev {info['rev'] or '-'}")
        elif f["kind"] == "video":
            process_video(file_id, ctx)
        db.ex("UPDATE files SET status='processed', processed_at=? WHERE id=?", (time.time(), file_id))
        search.invalidate()
        ctx.stage("Done", 1.0)
    except samecontent.SameContent as e:
        # not an error: the user decides (shortcut or keep separately); nothing was transcribed twice
        db.set_meta(file_id, similar_to=e.match)
        db.ex("UPDATE files SET status='duplicate', error=NULL WHERE id=?", (file_id,))
        db.audit("same_recording", file_id, **e.match)
        ctx.log(f"same recording as {e.match['folder']}/{e.match['name']}: not transcribed again")
        ctx.stage("Same recording already in DataDistill", 1.0)
    except InterruptedError:
        db.ex("UPDATE files SET status='new' WHERE id=?", (file_id,))
        raise
    except Exception as e:
        db.ex("UPDATE files SET status='error', error=? WHERE id=?", (friendly_error(e), file_id))
        raise


def friendly_error(e):
    import subprocess
    if isinstance(e, subprocess.CalledProcessError) and "ffprobe" in str(e.cmd[0]).lower():
        return "Not a readable video file (it may be damaged or incomplete)"
    if isinstance(e, subprocess.CalledProcessError):
        return "The video could not be decoded (ffmpeg failed)"
    return f"{type(e).__name__}: {e}"


def process_video(file_id, ctx):
    f = db.file_row(file_id)
    path = DATA_DIR / f["path"]
    base = OUTPUT_DIR / "recordings" / str(file_id)
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    tmp = TMP_DIR / str(file_id)
    shutil.rmtree(tmp, ignore_errors=True)
    pr = Progress(ctx)

    pr.enter("probe")
    info = media.probe(path)
    dur = info["duration"]
    ctx.log(f"{dur / 60:.1f} min, {info['width']}x{info['height']}, {info['video_codec']}, audio: {info['has_audio']}")

    pr.enter("frames")
    frames = media.extract_frames(path, tmp, dur, progress=pr.sub, cancel=ctx.cancelled) if info["width"] else []

    # same meeting already in the library as a different file (re-encoded copy)? then stop before transcribing
    if frames and not f["meta"].get("keep_separate") and not db.q1("SELECT 1 FROM recordings WHERE file_id=?", (file_id,)):
        ctx.stage("Checking for the same recording", pr.base["frames"][0] + pr.base["frames"][1])
        m = samecontent.find(file_id, samecontent.fingerprint(frames), dur, path, info["has_audio"], log=ctx.log)
        if m:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(base, ignore_errors=True)
            raise samecontent.SameContent(m)

    # ---------------- names on screen (participant tiles): read first, used by every later stage
    pr.enter("names")
    samples, participants, name_dbg = [], [], {}
    # text of the documents in this folder: context, and a check that a "name" is not just document text
    folder_docs = [r["text"] for r in db.q("SELECT p.text FROM pages p JOIN files f ON f.id=p.file_id "
                                          "WHERE f.system=? AND f.kind='document' AND f.status='processed'", (f["system"],))]
    # layout over time (gallery / speaker view / shared screen beside a tile strip): needed by names and screens
    periods, rdbg = [], {"method": "none"}
    if frames:
        periods, rdbg = slides.layout_timeline(frames)
        # 1 fps frame fingerprint: finds re-encoded copies of the same recording (duplicates)
        (base / "fingerprint.json").write_text(json.dumps({"duration": dur, "fps": 1, "dhash64": rdbg.pop("fingerprint")}),
                                               encoding="utf-8")
        ctx.log("layout: " + ", ".join(f"{k} {int(a) // 60}:{int(a) % 60:02d}-{int(b) // 60}:{int(b) % 60:02d}"
                                       for a, b, k, _ in periods))
    if frames:
        samples, participants, ddbg = screen_names.discover(frames, dur, progress=pr.sub)
        if not periods:
            # without a layout, a "name" that is also text of an uploaded document is probably that document.
            # With one, text inside the shared content is dropped below; a presenter's tile name that is also on
            # the title slide of their own deck ("Maria Lopez") is a real participant and stays.
            samples, participants = screen_names.drop_document_text(samples, participants, folder_docs)
        # a name tile is never inside the shared content: text there is a document, menu or link
        before = list(participants)
        samples, participants = screen_names.drop_labels_in_shared(samples, participants, periods)
        if before != participants:
            ctx.log(f"not participants (text inside a shared screen): {', '.join(n for n in before if n not in participants)}")
        name_dbg["discover"] = ddbg
        ctx.log(f"names on screen: {', '.join(participants) or 'none found'}")

    # ---------------- shared screens (slides, documents, other screen shares)
    pr.enter("screens", "finding shared content")
    segs, region, n_states = [], (0, 0, 0, 0), 0
    if frames:
        region = slides.main_region(periods)
        states = slides.find_states_timeline(frames, periods, cancel=ctx.cancelled)
        n_states = len(states)
        lib = RefLibrary(folder=f["system"])
        states = slides.analyse_states(frames, region, states, lib, cancel=ctx.cancelled, progress=pr.sub)
        segs = slides.save_outputs(file_id, frames, region, slides.prefer_consistent_docs(slides.merge_states(states)))
        # names read inside a shared slide/document are its text, not participant tiles
        before = list(participants)
        samples, participants = screen_names.drop_shared_labels(samples, participants, segs, region)
        if before != participants:
            ctx.log(f"not participants (text of a shared screen): {', '.join(n for n in before if n not in participants)}")
    avatars = []
    if frames and samples:
        avatars, ahits = screen_names.discover_avatars(frames, samples)
        name_dbg["avatars"] = ahits
        if avatars:
            ctx.log(f"camera-off participants (initials): {', '.join(a[1:] for a in avatars)}")
    n_shared = sum(s["kind"] != "non_slide" for s in segs)
    ctx.log(f"{n_shared} shared screens ({sum(s['kind'] == 'slide' for s in segs)} matched to documents)")

    # ---------------- speech to text
    pr.enter("transcribe")
    sidecar = transcribe.find_sidecar(path)
    audio = media.load_audio(path) if info["has_audio"] else np.zeros(0, np.float32)
    if sidecar:
        tsegs, tinfo = transcribe.parse_caption_file(sidecar)
        ctx.log(f"using Teams captions {sidecar.name}")
    elif len(audio):
        texts = [s["match"]["text"] for s in segs if s.get("match") and s["match"].get("text")]
        prompt = transcribe.vocabulary_prompt(texts)
        tsegs, tinfo = transcribe.transcribe(audio, dur, initial_prompt=prompt, cancel=ctx.cancelled, progress=pr.sub)
        tinfo["prompt"] = prompt
    else:
        tsegs, tinfo = [], {"source": "none (no audio)"}
    ctx.log(f"{len(tsegs)} transcript segments ({tinfo['source']}, language {tinfo.get('language')})")

    # ---------------- who spoke when (voice), unless captions already carry names
    speakers_meta = {}
    if sidecar:
        names = list(dict.fromkeys(s["speaker"] for s in tsegs if s["speaker"]))
        ids = {n: f"S{i + 1}" for i, n in enumerate(names)}
        for s in tsegs:
            s["speaker"] = ids.get(s["speaker"])
        speakers_meta = {ids[n]: {"name": n, "source": "captions", "score": None} for n in names}
        turns = tsegs
        pr.enter("speakers")
    else:
        pr.enter("speakers")
        words = [w for s in tsegs for w in s.get("words", [])]
        # music / a video played in the meeting: its words are not a participant and must not create voices
        sound = soundtrack.regions(audio) if len(audio) else []
        in_sound = [soundtrack.is_soundtrack(w["start"], w["end"], sound, share=0.5) for w in words]
        if sound:
            ctx.log("music / video sound: " + ", ".join(f"{int(a) // 60}:{int(a) % 60:02d}-{int(b) // 60}:{int(b) % 60:02d}"
                                                         for a, b in sound))
        talk = [w for w, s in zip(words, in_sound) if not s]
        if talk:
            # upper bound on voices: people seen on screen + margin for names OCR could not read
            # only when the screen clearly shows several named participants: otherwise the people we could
            # read are an undercount (room cameras, tiny labels) and capping would merge different voices
            cap = len(participants) + len(avatars) + 2 if len(participants) >= 3 else None
            spk_t, conf_t, dstats = speakers.diarize(audio, talk, dur, progress=pr.sub, max_speakers=cap)
            stab = dstats.get("stability")
            if stab:
                ws_ = stab["word_stats"]
                ctx.log(f"voice continuity: {stab['supported_voices']} supported voices, {stab['tentative_clusters']} tentative; "
                        f"{ws_['unresolved']} of {ws_['total']} words have no strict voice evidence (shown under the surrounding voice where it is certain)")
            it = iter(zip(spk_t, conf_t))
            spk, conf = [], []
            for s in in_sound:
                a, c = (soundtrack.SOUNDTRACK_ID, None) if s else next(it)
                spk.append(a)
                conf.append(c)
            turns = speakers.to_turns(tsegs, spk, conf)
            if speakers.STABILITY:
                speakers.attribution_summary(turns)
        elif words:
            turns = speakers.to_turns(tsegs, [soundtrack.SOUNDTRACK_ID] * len(words), [None] * len(words))
        else:
            turns = tsegs
        ids = sorted({t["speaker"] for t in turns if t["speaker"]}, key=lambda s: int(s[1:]))
        speakers_meta = {i: {"name": None, "source": None, "score": None} for i in ids}
        if soundtrack.SOUNDTRACK_ID in speakers_meta:
            speakers_meta[soundtrack.SOUNDTRACK_ID] = {"name": soundtrack.SOUNDTRACK_NAME, "source": "soundtrack", "score": None}
        ctx.log(f"{len(ids) - (soundtrack.SOUNDTRACK_ID in speakers_meta)} distinct voices")

    pr.enter("match")
    # ---------------- who is speaking, seen in the picture (before names): lit name labels and camera tiles
    chip, fres, dom_series, cinfo = {}, None, None, {}
    if frames and samples and not sidecar:
        chip, cinfo = highlight.run(frames, samples)
        dom_series = cinfo.pop("dominant_series", None)            # kept out of the saved debug data (one entry per second)
        name_dbg["label_highlight"] = cinfo
        if chip:
            ctx.log(f"speaker highlight on name labels found (colour h{cinfo['colour']['h']}), {cinfo['slots']} label positions")
    if frames and FACES and db.setting("camera_analysis") and faces.available() and not sidecar and any(k == "meeting" for _, _, k, _ in periods):
        import cv2
        im = cv2.imread(str(frames[0]))
        gal = [p_ for p_ in periods if p_[2] == "meeting"]
        fres = faces.analyse(path, dur, gal, samples, (im.shape[1], im.shape[0]), cancel=ctx.cancelled,
                             progress=lambda p: pr.sub(p))
        name_dbg["faces"] = {tid: {k: v for k, v in tl.items() if k not in ("activity", "faces")}
                             for tid, tl in fres["tiles"].items()}
        kinds = fres["stats"].get("kinds", {})
        ctx.log("camera tiles: " + (", ".join(f"{n} {k}" for k, n in kinds.items() if n) or "none readable") +
                " (live = mouth movement readable, photo = camera off, room = several people, far = faces too small)")

    # ---------------- match voices to the names on screen
    shared_texts = [s["match"]["text"] for s in segs if s.get("match") and s["match"].get("text")] + \
                   [s.get("ocr", "") for s in segs if s["kind"] == "unknown_slide"]
    known = list(dict.fromkeys(participants + screen_names.names_in_documents(folder_docs + shared_texts)))
    hl, mot, weak_names = {}, {}, {}
    if not sidecar and speakers_meta:
        people_turns = [t for t in turns if t.get("speaker") != soundtrack.SOUNDTRACK_ID]   # not the music / video
        # Every source produces OBSERVATIONS (which voice, which name, when, what kind of evidence). identity.resolve
        # decides: a name is given only with strong evidence (a clear first-person introduction, captions, a name label
        # lit while that voice speaks, repeated calls by name). Tile motion and anything weaker stay a "possible name"
        # and are never shown as the name: a conference-room camera is labelled with an account, and two people
        # speaking in it must not get that account's name, or a neighbouring tile's.
        observations = speech_names.intro_observations(people_turns, known)
        known = list(dict.fromkeys(known + [o["name"] for o in observations if o["kind"] == "self_introduction_first_person"]))
        candidates = participants + avatars
        if frames and candidates and people_turns:
            mot, hl = screen_names.activity(path, samples, dur, frames, progress=lambda p: pr.sub(0.6 * p))
            # who is speaking, seen in the picture: (1) a lit name label (learned colour, one at a time),
            # (2) mouth movement of camera tiles. Photos / initials (camera off) have no mouth to read.
            if chip:
                for n, series in chip.items():
                    h = hl.setdefault(n, {})
                    for t, v in series.items():
                        h[t] = max(h.get(t, 0.0), v)
            if fres:
                for tid, tl in fres["tiles"].items():
                    if tl["name"] and tl["kind"] == "live":
                        mot[tl["name"]] = {int(sec): a for sec, a in tl["activity"].items()}      # mouth, not pixel motion
                    elif tl["name"] and tl["kind"] == "photo":
                        mot.pop(tl["name"], None)                                                  # a picture does not speak
            # tile activity only where the tiles stay put: in gallery / speaker view. During a screen share the
            # thumbnail strip reorders itself around whoever speaks, so a label's position no longer tells whose
            # face is there (that named a 38-minute presenter after another participant).
            gallery = [(a, b) for a, b, k, _ in periods if k == "meeting"]
            if gallery and any(k == "share" for _, _, k, _ in periods):
                in_gallery = lambda sec: any(a <= sec < b for a, b in gallery)  # noqa: E731
                mot = {n: {s: v for s, v in secs.items() if in_gallery(s)} for n, secs in mot.items()}
                hl = {n: {s: v for s, v in secs.items() if in_gallery(s)} for n, secs in hl.items()}
            # the lit name chip is attached to the label itself, so it stays reliable in the tile strip beside a shared
            # screen: read the chip's own text there and match it to a participant
            if chip and cinfo.get("colour") and any(k == "share" for _, _, k, _ in periods):
                col = (cinfo["colour"]["h"], cinfo["colour"]["s"], cinfo["colour"]["v"], 1)
                strip = highlight.strip_series(frames, periods, col, [c for c in participants if "(initials)" not in c],
                                               highlight.ocr_chip, progress=lambda p_: pr.sub(0.95 + 0.05 * p_))
                for n, d in strip.items():
                    hl.setdefault(n, {}).update(d)
                if strip:
                    name_dbg["strip_highlight"] = {n: len(d) for n, d in strip.items()}
                    ctx.log("lit name label read in the tile strip beside shared screens: " +
                            ", ".join(f"{n} {len(d)} s" for n, d in sorted(strip.items(), key=lambda kv: -len(kv[1]))[:6]))
            # a possible name, never applied: mouth / tile movement is weak evidence (it named the wrong person before)
            mapping, mdbg = screen_names.match(people_turns, mot, hl, candidates, min_overlap_s=MIN_TILE_EVIDENCE_S)
            mapping = screen_names.resolve_initials(mapping, known)
            name_dbg["match"] = mdbg
            weak_names = {sid: mm["name"] for sid, mm in mapping.items()}
        if hl:
            # a highlight must behave like one (switch on and off, one label at a time, nobody lit most of the time);
            # a tile with a coloured border that is "lit" all the time is not who is speaking
            hl_ok, hl_why = highlight.plausible(hl, max(1, len(frames)))
            if hl_ok:
                observations += visual_speakers.highlight_observations(people_turns, hl)
            else:
                name_dbg["highlight_rejected"] = hl_why
                ctx.log(f"lit labels / borders not used for naming: {hl_why}")
        observations += speech_names.address_observations(people_turns, known)
        voice_ids = [i for i in speakers_meta if i != soundtrack.SOUNDTRACK_ID]
        resolved = identity.resolve(voice_ids, observations)
        calls_all = speech_names.addressed_by(people_turns, known + [r["name"] for r in resolved.values() if r["name"]])
        source_of = [("self_introduction_first_person", "said their name"), ("self_introduction", "said their name"),
                     ("highlight", "screen (highlight)"), ("addressed", "called by name")]
        named, suggested = [], []
        for sid, r in resolved.items():
            meta = speakers_meta[sid]
            meta.update(person_id=r["person_id"], identity_status=r["identity_status"])
            name = r["name"]
            if name and calls_all.get(sid, {}).get(name, 0) >= SELF_ADDRESS_VETO:
                ctx.log(f"{sid}: not {name} (this voice calls them by name)")       # whoever calls on a person is not them
                name = None
            if name:
                kinds = {o["kind"] for o in r["evidence"]}
                meta.update(name=name, source=next((lab for k, lab in source_of if k in kinds), "evidence"), score=None)
                named.append(f"{sid} {name}")
            else:
                cand = r["candidate_name"] or weak_names.get(sid)
                if cand:
                    meta["candidate_name"] = cand
                    suggested.append(f"{sid} ~ {cand}")
                    if cand in participants and r["identity_status"] == "conflict":
                        meta["account_label"] = cand            # one tile / account lit for several voices: a shared camera
        # one camera, several people: a label lit almost all the time while people talk (a conference-room camera shows
        # the room's account) means everything is said through that tile. The voices are then NOT that account's person.
        if cinfo.get("dominant") and dom_series:
            cam = visual_speakers.shared_camera(people_turns, set(dom_series), max(1, len(frames)))
            if cam:
                label = cinfo["dominant"]
                room = len(cam["voices"]) >= 2
                for sid, share in cam["voices"].items():
                    m = speakers_meta[sid]
                    m["camera"] = {"label": label, "room": room, "share": round(share, 2)}
                    if not m.get("name"):
                        m["account_label"] = label
                        m.pop("candidate_name", None)          # movement in another participant's tile cannot be right
                lit = set(dom_series)
                for t in turns:
                    if t.get("speaker") != soundtrack.SOUNDTRACK_ID and visual_speakers.camera_of_turn(t, lit):
                        t.setdefault("attribution", {})["camera"] = label
                name_dbg["shared_camera"] = {"label": label, **cam}
                ctx.log(f"{len(cam['voices'])} voice(s) speak through the camera labelled {label} "
                        f"({'several people share it' if room else 'one person'}; lit in {cam['lit_in_speech']:.0%} of the speech, "
                        f"{cam['lit_in_silence']:.0%} of the silence): {', '.join(cam['voices'])}")
        name_dbg["intros"] = {sid: r["name"] for sid, r in resolved.items() if r["name"]}
        name_dbg["identity"] = {sid: {"status": r["identity_status"], "candidate": r["candidate_name"]} for sid, r in resolved.items()}
        if named:
            ctx.log("named with evidence: " + ", ".join(named))
        if suggested:
            ctx.log("possible names, not used (evidence too weak, or shared by several voices): " + ", ".join(suggested))
    # ---------------- cameras: tiles without a name and room faces -> voices; one person's split voice; speech at once
    if fres and speakers_meta and not sidecar:
        people = [t for t in turns if t.get("speaker") and t.get("speaker") != soundtrack.SOUNDTRACK_ID]
        ents = visual_speakers.entities(fres["tiles"])
        if ents and people:
            cm = visual_speakers.camera_matches(people, ents)
            for sid, m in cm.items():
                if sid in speakers_meta:
                    speakers_meta[sid]["camera"] = {"tile": m["tile"], "room": m["room"], "score": m["score"]}
            if cm:
                ctx.log("voices matched to a camera picture: " + ", ".join(
                    f"{sid} -> {'a person in the room camera' if m['room'] else 'camera tile'} ({m['score']})" for sid, m in cm.items()))
            merged = {}
            for a, b, e in visual_speakers.same_face_pairs(people, ents):
                if a in speakers_meta and b in speakers_meta and not (speakers_meta[a]["name"] and speakers_meta[b]["name"]
                                                                       and speakers_meta[a]["name"] != speakers_meta[b]["name"]):
                    keep, drop = (a, b) if int(a[1:]) < int(b[1:]) else (b, a)
                    merged[drop] = keep
            for drop, keep in merged.items():
                for t in turns:
                    if t.get("speaker") == drop:
                        t["speaker"] = keep
                if speakers_meta[drop]["name"] and not speakers_meta[keep]["name"]:
                    speakers_meta[keep].update(name=speakers_meta[drop]["name"], source=speakers_meta[drop]["source"],
                                               score=speakers_meta[drop]["score"])
                speakers_meta.pop(drop)
            if merged:
                ctx.log("one person, two voices (same face): " + ", ".join(f"{d} joined {k}" for d, k in merged.items()))
    if OVERLAP != "off" and db.setting("mark_overlap") and not sidecar and (chip or fres):
        secs = visual_speakers.overlap_seconds(chip, fres["tiles"] if fres else None)
        if secs:
            for t in turns:
                t["speaker_name"] = (speakers_meta.get(t.get("speaker")) or {}).get("name")
            n_over = visual_speakers.mark_overlap(turns, secs)
            name_dbg["overlap_seconds"] = len(secs)
            if n_over > visual_speakers.MAX_MARKED_SHARE * max(1, len(turns)):
                # far more than any meeting has: the signal is faulty, so none of it is used
                for t in turns:
                    t.pop("overlap_with", None)
                name_dbg["overlap_rejected"] = f"{n_over} of {len(turns)} lines would be marked"
                ctx.log(f"overlap marking dropped: {n_over} of {len(turns)} lines would be marked, which is not credible")
            else:
                ctx.log(f"people speaking at the same time: {len(secs)} s seen, {n_over} lines marked (the text of such lines may be incomplete)")
    # the spelling on the participant's tile is the authority: names the speech model misheard are corrected in the text
    if speakers_meta and known and not sidecar:
        from .textutil import english_vocab, known_word
        vocab = english_vocab()
        full = list(dict.fromkeys(n for n in participants + [m["name"] for m in speakers_meta.values() if m.get("name")]
                                  if n and len(n.split()) >= 2 and "(initials)" not in n))   # people in the meeting, not names in documents
        fixes = namefold.correct_spellings(turns, full, lambda w: known_word(w, vocab) if vocab else False)
        if fixes:
            name_dbg["spelling_fixes"] = fixes[:30]
            ctx.log("names spelled as on screen: " + ", ".join(f"{f['from']} -> {f['to']} (x{f['count']})" for f in fixes[:8]))
    participants = participants + [f"{a[1:]} (initials)" for a in avatars]

    # ---------------- link text to shared screens, save
    pr.enter("save")
    turns = transcribe.assign_slides(turns, [{"start": s["start"], "end": s["end"]} for s in segs]) if segs else \
        [{**t, "slide_idx": None} for t in turns]
    for sid, meta in speakers_meta.items():
        meta["talk_time_s"] = round(sum(t["end"] - t["start"] for t in turns if t.get("speaker") == sid), 1)
    speakers_meta = {k: v for k, v in speakers_meta.items() if v["talk_time_s"] > 0}  # voices left with no speech
    with db.tx() as c:
        c.execute("DELETE FROM rec_slides WHERE file_id=?", (file_id,))
        c.execute("DELETE FROM segments WHERE file_id=?", (file_id,))
        c.executemany("INSERT INTO rec_slides(file_id,idx,t_start,t_end,kind,slide_uid,page_id,score,inliers,ocr_text,image) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      [(file_id, i, s["start"], s["end"], s["kind"], s.get("slide_uid"),
                        s["match"]["page_id"] if s.get("match") else None, s["match"]["score"] if s.get("match") else None,
                        s["match"]["inliers"] if s.get("match") else None, s.get("ocr"), None) for i, s in enumerate(segs)])
        c.executemany("INSERT INTO segments(file_id,idx,start,end,speaker,text,slide_idx,confidence,speaker_confidence,"
                      "avg_logprob,no_speech_prob,overlap,attribution) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [(file_id, i, t["start"], t["end"], t.get("speaker"), t["text"], t.get("slide_idx"), t.get("confidence"),
                        t.get("speaker_confidence"), t.get("avg_logprob"), t.get("no_speech_prob"),
                        json.dumps(t["overlap_with"]) if t.get("overlap_with") else None,
                        json.dumps(t["attribution"]) if t.get("attribution") else None) for i, t in enumerate(turns)])

    timings = pr.done()
    confs = [(t["end"] - t["start"], t["confidence"]) for t in turns if t.get("confidence") is not None]
    mean_conf = round(sum(d * c for d, c in confs) / max(1e-9, sum(d for d, _ in confs)), 3) if confs else None
    low_share = round(sum(d for d, c in confs if c < 0.6) / max(1e-9, sum(d for d, _ in confs)), 3) if confs else None
    manifest = {
        "file_id": file_id,
        "source": {"name": f["name"], "folder": f["system"], "path": f["path"], "sha256": f["sha256"], "bytes": f["size"],
                   "duration_s": round(dur, 2), "width": info["width"], "height": info["height"]},
        "processed_at": time.time(),
        "models": {"asr": tinfo.get("model"), "speaker_embedding": "speechbrain/spkrec-ecapa-voxceleb",
                   "ocr": "tesseract 5", "search": EMBED_MODEL},
        "transcript": {k: v for k, v in tinfo.items() if k != "prompt"},
        "quality": {"mean_confidence": mean_conf, "low_confidence_share": low_share,
                    "speaker_source": "captions" if sidecar else "voice clusters + on-screen names"},
        "screen": {"region": list(region), "region_method": rdbg.get("method"), "states_analysed": n_states,
                   "layouts": rdbg.get("periods", [])},
        "counts": {"turns": len(turns), "speakers": len(speakers_meta),
                   "named_speakers": sum(1 for m in speakers_meta.values() if m["name"]),
                   "participants_on_screen": len(participants), "shared_screens": n_shared,
                   "matched_slides": sum(s["kind"] == "slide" for s in segs), "frames": len(frames)},
        "timings_s": timings,
        "naming_debug": name_dbg,
    }
    db.ex("INSERT OR REPLACE INTO recordings(file_id, manifest, speakers, participants) VALUES (?,?,?,?)",
          (file_id, json.dumps(manifest), json.dumps(speakers_meta), json.dumps(participants)))
    db.set_meta(file_id, duration=dur, width=info["width"], height=info["height"], processing_s=timings["total"],
                confidence=mean_conf, speakers=len(speakers_meta))
    pr.enter("keep")
    extras = keep_extracts(file_id, frames=frames, audio=audio if len(audio) else None, ctx=ctx)
    timings = pr.done()
    manifest["timings_s"] = timings
    manifest["extracts"] = extras
    db.ex("UPDATE recordings SET manifest=? WHERE file_id=?", (json.dumps(manifest), file_id))
    db.set_meta(file_id, processing_s=timings["total"])
    package.write_outputs(file_id)
    shutil.rmtree(tmp, ignore_errors=True)
    ctx.log(f"done in {timings['total']} s; mean recognition confidence {mean_conf}")


def keep_extracts(file_id, frames=None, audio=None, ctx=None):
    """Snapshots + voice samples. Needs the original video (or the pipeline's frames / audio).
    Never fails the recording: a problem here is logged and the transcript is kept."""
    ctx = ctx or NullCtx()
    out = {}
    try:
        out["snapshots"] = snapshots.build(file_id, frames=frames)
        if out["snapshots"]:
            s = out["snapshots"]
            ctx.log(f"images: {s['keyframes']} key frames of shared screens, {s['snapshots']} snapshots where the speech refers "
                    f"to the screen; {s['images']} stored, {s['bytes'] / 1e6:.2f} MB")
    except Exception as e:  # noqa: BLE001
        ctx.log(f"snapshots failed: {type(e).__name__}: {e}")
    try:
        out["voices"] = voices.build(file_id, audio=audio)
        if out["voices"]:
            ctx.log(f"voice samples: {out['voices']['speakers']} speakers, {out['voices']['bytes'] / 1e3:.0f} KB")
    except Exception as e:  # noqa: BLE001
        ctx.log(f"voice samples failed: {type(e).__name__}: {e}")
    if out.get("snapshots") or out.get("voices"):
        db.set_meta(file_id, extracts_at=time.time())
    return out
