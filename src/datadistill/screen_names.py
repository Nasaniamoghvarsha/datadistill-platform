"""Name speakers from what is visible on screen (Teams / Zoom style meeting layouts).

Generic assumptions (no tuning to a specific recording):
  * participants are shown as tiles with a name label (camera on: video; camera off: avatar/initials)
  * labels are short name-like text, white on a dark background, at the bottom of a tile
  * the active speaker is marked by a highlight border, or (no border) shows movement in the tile:
    lips / head with camera on, the animated speaking ring with camera off

Pipeline:
  1. OCR sampled frames -> name labels + positions -> tile boxes (grid inferred from label positions)
  2. names merged across frames (fuzzy) -> participant list
  3. per second and per participant: border highlight and in-tile motion
  4. voice clusters (diarization) are matched to participants by how well their speaking time lines up
     with the participant's activity; weak or ambiguous matches stay "Speaker N".
"""
import re
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import pytesseract
from rapidfuzz import fuzz

from . import namefold
from .config import FFMPEG, NAME_OCR_LANGS, TESSERACT_CMD
from .media import NO_WINDOW

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def safe_ocr(fn, *args, **kwargs):
    """Tesseract occasionally loses its temp file when several OCR calls run in parallel (Windows).
    Retry, and if a single frame still fails, skip it: one unreadable frame must never abort a recording."""
    import time
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except (pytesseract.TesseractError, OSError):
            time.sleep(0.2 * (attempt + 1))
    return None

UI_WORDS = {"recording", "meeting", "presenting", "presentation", "share", "sharing", "screen", "mute", "unmute",
            "chat", "participants", "people", "leave", "raise", "hand", "camera", "speaker", "view", "gallery",
            "together", "mode", "join", "joined", "waiting", "lobby", "microsoft", "teams", "zoom", "transcript",
            "live", "captions", "more", "react", "apps", "slide", "next", "previous", "page", "agenda", "you"}
NAME_TOKEN = re.compile(r"^[A-ZÀ-Þ][A-Za-zÀ-ÿ'’.\-]*$")
LAST_FIRST = re.compile(r"^([A-ZÀ-Þ][a-zà-ÿ'’\-]+),\s*([A-ZÀ-Þ][a-zà-ÿ'’\-]+)\b")
SEPARATORS = re.compile(r"\s+[|\-–—,(]\s*|\s*\|\s*")
# job titles / roles that follow the name on Teams and Zoom labels ("Phil Calder Senior EM")
TITLE_WORDS = {"senior", "sr", "junior", "jr", "staff", "principal", "lead", "head", "chief", "director", "manager",
               "engineer", "engineering", "developer", "designer", "analyst", "architect", "consultant", "intern",
               "product", "program", "project", "marketing", "sales", "data", "science", "scientist", "fullstack",
               "backend", "frontend", "team", "group", "department", "vp", "president", "officer", "em", "pm", "tpm",
               "ceo", "cto", "cfo", "coo", "ops", "operations", "support", "security", "research", "researcher",
               "specialist", "coordinator", "owner", "admin", "administrator", "guest", "external", "host", "cohost"}


ICON_LETTERS = {"Z", "&", "%", "@"}    # what OCR makes of the microphone / pin icons next to a name
# public-sector roles shown after a name on municipal / board calls
ROLES = {"selectman", "selectwoman", "selectperson", "mayor", "councilman", "councilwoman", "councillor", "councilor",
         "alderman", "commissioner", "chairman", "chairwoman", "chairperson", "deputy", "executive", "secretary",
         "treasurer", "clerk", "superintendent", "representative", "senator"}
ROLE_LEAD = {"first", "town", "city", "vice", "deputy"}   # "First Selectman", "Town Manager", "Vice Chair"


def _is_role_start(tok):
    t = tok.lower().rstrip(".…")
    return len(t) >= 4 and any(r.startswith(t) for r in ROLES | TITLE_WORDS)


def _is_title(tok):
    t = tok.lower().strip(".,:;")
    return t in TITLE_WORDS or (len(t) >= 5 and any(fuzz.ratio(t, w) >= 85 for w in TITLE_WORDS if len(w) >= 5))


def parse_name(text):
    """'Jay Swain - EM, Data Science' -> 'Jay Swain'; 'Phil Calder Senior EM' -> 'Phil Calder';
    'Teachout, Sara (GMCB)' -> 'Sara Teachout' (the 'Last, First' display-name format of many companies).
    Single words are returned too (they may be a truncated full name); the participant list keeps only
    full names (2+ words), see canonical_names()."""
    m = LAST_FIRST.match(text.strip())
    if m and not _is_title(m.group(2)) and not _is_title(m.group(1)):
        text = f"{m.group(2)} {m.group(1)}"
    head = SEPARATORS.split(text.strip(), maxsplit=1)[0].strip(" .:;")
    toks = []
    for t in head.split():
        if _is_title(t):
            break  # everything from the first title word on is the role, not the name
        toks.append(t)
    # Zoom's (muted) microphone icon after the name is read as a letter: "Rob Aloise Z", "Paulin... Z"
    while len(toks) >= 2 and len(toks[-1]) == 1 and (toks[-1] in ICON_LETTERS or sum(len(x) > 1 for x in toks[:-1]) >= 2):
        toks.pop()
    # a role after the name, possibly cut off by the tile: "Jason Hayes First Select..." (First Selectman)
    while len(toks) >= 3 and (_is_role_start(toks[-1]) or toks[-1].lower() in ROLE_LEAD):
        toks.pop()
    if not 1 <= len(toks) <= 4:
        return None
    if not all(NAME_TOKEN.match(t) for t in toks):
        return None
    if any(t.lower().strip(".") in UI_WORDS for t in toks):
        return None
    if any(t.isupper() and len(t) > 3 for t in toks):  # "GREEN MOUNTAIN CARE BOARD": headings, logos
        return None
    name = " ".join(toks)
    if sum(c.isalpha() for c in name) < 4:
        return None
    return name


def _with_accents(name, img, x, y, w, h):
    """The English reading of a label loses accents (Åsa -> Asa, Hélène -> Helene, Björn -> Bjorn). Read the same
    label again with the Swedish and the French language data; if one reading differs from the English one only by
    its accents, that is the spelling on the tile. Swedish wins when it has å ä ö, else French when it has accents."""
    if NAME_OCR_LANGS == "eng":
        return name
    H_, W_ = img.shape[:2]
    pad = int(0.25 * h) + 4
    crop = img[max(0, int(y) - pad):min(H_, int(y + h) + pad), max(0, int(x) - pad):min(W_, int(x + w) + pad)]
    if crop.size == 0:
        return name
    got = {}
    for lang in ("swe", "fra"):
        txt = safe_ocr(pytesseract.image_to_string, crop, config="--psm 7", lang=lang)
        toks = (txt or "").split()
        if toks:
            got[lang] = toks
    words = name.split()
    out = []
    for k, w in enumerate(words):
        pick = w
        cands = [(lang, got[lang][k]) for lang in ("swe", "fra") if lang in got and len(got[lang]) == len(words)]
        cands = [(lang, c) for lang, c in cands if any(ord(ch) > 127 for ch in c) and fuzz.ratio(namefold.fold(c), namefold.fold(w)) >= 80]
        sw = [c for lang, c in cands if lang == "swe" and re.search(r"[åäöÅÄÖ]", c)]
        if sw:
            pick = sw[0]
        elif cands:
            pick = next((c for lang, c in cands if lang == "fra"), cands[0][1])
        out.append(pick)
    res = parse_name(" ".join(out))
    return res if res and namefold.fold(res) == namefold.fold(name) or (res and fuzz.ratio(namefold.fold(res), namefold.fold(name)) >= 90) else name


def _ocr_labels(path, target_h=1440):
    img = cv2.imread(str(path))
    if img is None:
        return []
    H, W = img.shape[:2]
    f = min(4.0, max(1.0, target_h / H))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    big = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    out = []
    for inv in (True, False):  # labels are usually light-on-dark; also try dark-on-light
        d = safe_ocr(pytesseract.image_to_data, 255 - big if inv else big, config="--psm 11",
                     output_type=pytesseract.Output.DICT)
        if d is None:
            continue
        lines = defaultdict(list)
        for i, w in enumerate(d["text"]):
            if w.strip() and float(d["conf"][i]) >= 50:
                lines[(d["block_num"][i], d["par_num"][i], d["line_num"][i])].append(i)
        for idx in lines.values():
            text = " ".join(d["text"][i] for i in idx)
            name = parse_name(text)
            if not name:
                continue
            x = min(d["left"][i] for i in idx) / f
            y = min(d["top"][i] for i in idx) / f
            h = max(d["top"][i] + d["height"][i] for i in idx) / f - y
            w = max(d["left"][i] + d["width"][i] for i in idx) / f - x
            conf = float(np.mean([float(d["conf"][i]) for i in idx]))
            name = _with_accents(name, (255 - big if inv else big), x * f, y * f, w * f, h * f)
            out.append({"name": name, "x": x, "y": y, "w": w, "h": h, "conf": conf, "W": W, "H": H})
        if out:
            break
    return out


def _cluster_1d(vals, tol):
    vals = sorted(vals)
    groups = []
    for v in vals:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [float(np.mean(g)) for g in groups]


def _tiles(labels):
    """Infer one tile box per label from the grid formed by all labels in the frame."""
    if not labels:
        return []
    W, H = labels[0]["W"], labels[0]["H"]
    lh = float(np.median([l["h"] for l in labels]))
    cols = _cluster_1d([l["x"] for l in labels], 0.03 * W)
    rows = _cluster_1d([l["y"] for l in labels], 0.03 * H)
    tw = float(np.median(np.diff(cols))) if len(cols) > 1 else min(W - cols[0], 0.45 * W)
    th = float(np.median(np.diff(rows))) if len(rows) > 1 else tw * 9 / 16
    # Participant tiles are many times taller than their name label. Rows of text only a line or two
    # apart are a table or list in a shared document (e.g. an agenda with presenter names), not tiles.
    if len(rows) > 1 and th < 4 * lh:
        return []
    out = []
    for l in labels:
        bottom = l["y"] + l["h"] + 0.06 * th
        left = l["x"] - 0.04 * tw
        box = [max(0, left), max(0, bottom - th), min(W, left + tw), min(H, bottom)]
        if box[2] - box[0] > 0.05 * W and box[3] - box[1] > 0.05 * H:
            out.append({**l, "box": [int(v) for v in box]})
    return out


def _is_prefix_form(short, full):
    """'Wayne' / 'Ned McC' / 'Thomas Woo' are truncated forms of 'Wayne Haber' / 'Ned McCorrison' / 'Thomas Woodham'."""
    s, f = short.lower().split(), full.lower().split()
    if len(s) > len(f) or short == full:
        return False
    return all(f[i].startswith(s[i].rstrip(".")) for i in range(len(s)))


def _ocr_variant(a, b):
    """One label read two ways: the same first name, and a surname that differs in the last letters only
    ("Torbjörn Bergström" / "Torbjörn Bergstrem" / "Torbjörn Bergstrim": the end of a label is the part that blurs). The first names
    must be equal and long enough to mean something, so "Jason Bowsza" and "Jason Hayes" stay two people."""
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb) or len(ta) < 2 or len(ta[0]) < 4:
        return False
    if namefold.fold(ta[0]) != namefold.fold(tb[0]) or any(x != y for x, y in zip(ta[1:-1], tb[1:-1])):
        return False
    x, y = namefold.fold(ta[-1]), namefold.fold(tb[-1])
    if len(x) < 6 or len(y) < 6 or x == y or abs(len(x) - len(y)) > 1:
        return False
    k = 3                                                     # the first letters of the surname agree, the ends may not
    return x[:-k] == y[:-k] or (x[:4] == y[:4] and fuzz.ratio(x, y) >= 75)


def canonical_names(samples):
    """Merge OCR spellings and truncated forms of the same person across frames -> {raw: canonical}."""
    counts = Counter(l["name"] for s in samples for l in s["labels"])
    groups = []
    initials = lambda n: [t[0].lower() for t in n.split()]  # noqa: E731
    for name, _ in counts.most_common():
        for g in groups:
            # OCR spellings of one name; "Jason H" and "Jason B" are close as strings but different people
            if initials(name) == initials(g[0]) and (fuzz.token_sort_ratio(name, g[0]) >= 85 or _ocr_variant(name, g[0])):
                g.append(name)
                break
        else:
            groups.append([name])
    rep = {}
    for g in groups:
        best = max(g, key=lambda n: (counts[n], len(n)))
        for n in g:
            rep[n] = best
    # truncated forms -> the most frequent full form they are a prefix of
    group_count = Counter()
    for raw, c in counts.items():
        group_count[rep[raw]] += c
    reps = list(group_count)
    final = {}
    for r in reps:
        # only towards a full form that is itself seen regularly (not a one-off misread with extra text)
        fulls = [f for f in reps if _is_prefix_form(r, f) and group_count[f] >= 0.2 * group_count[r]]
        final[r] = max(fulls, key=lambda f: group_count[f]) if fulls else r

    def resolve(r, depth=0):                    # Jason Bow -> Jason B... -> Jason Bowsza: follow to the end
        return r if final[r] == r or depth > 5 else resolve(final[r], depth + 1)

    for r in reps:
        # a cut-off form that fits two different people ("Jason" -> Bowsza or Hayes) is not guessed
        targets = {resolve(f) for f in reps if f != r and _is_prefix_form(r, f) and group_count[f] >= 0.2 * group_count[r]}
        final[r] = r if len(targets) > 1 else final[r]
    mapping = {raw: resolve(rep[raw]) for raw in counts}
    seen = Counter()
    for raw, c in counts.items():
        seen[mapping[raw]] += c
    return mapping, seen


def _cell_labels(path, labels):
    """Second pass: OCR the label strip of grid cells where the full-frame pass found no name."""
    if len(labels) < 2:
        return []
    img = cv2.imread(str(path))
    if img is None:
        return []
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = g.shape
    tiles = _tiles(labels)
    if not tiles:
        return []
    tw = float(np.median([t["box"][2] - t["box"][0] for t in tiles]))
    th = float(np.median([t["box"][3] - t["box"][1] for t in tiles]))
    lh = float(np.median([l["h"] for l in labels]))
    dx = float(np.median([l["x"] - t["box"][0] for l, t in zip(labels, tiles)]))
    dy = float(np.median([t["box"][3] - (l["y"] + l["h"]) for l, t in zip(labels, tiles)]))
    cols = _cluster_1d([t["box"][0] for t in tiles], 0.05 * W)
    rows = _cluster_1d([t["box"][3] for t in tiles], 0.05 * H)
    found = []
    for cx in cols:
        for by in rows:
            if any(abs(t["box"][0] - cx) < 0.3 * tw and abs(t["box"][3] - by) < 0.3 * th for t in tiles):
                continue  # this cell already has a label
            y1 = int(by - dy + 0.4 * lh)
            y0 = int(y1 - 2.0 * lh)
            x0 = int(cx + dx - 0.3 * lh)
            x1 = int(min(W, cx + 0.8 * tw))
            if y0 < 0 or x1 - x0 < 20 or y1 > H:
                continue
            crop = g[y0:y1, max(0, x0):x1]
            f = max(2.0, 64 / max(1, crop.shape[0]))
            big = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
            text = (safe_ocr(pytesseract.image_to_string, 255 - big, config="--psm 7") or "").strip()
            name = parse_name(text)
            if name:  # position expressed like a first-pass label so the tile box comes out the same
                found.append({"name": name, "x": float(cx + dx), "y": float(by - dy - lh), "w": float(x1 - x0),
                              "h": lh, "conf": 0.0, "W": W, "H": H, "second_pass": True})
    return found


def discover(frames, duration, progress=None, workers=4):
    """Sample frames, OCR labels, infer tiles. frames: list of 1-fps JPG paths (frame k at t=k)."""
    step = int(np.clip(duration / 120, 10, 45))
    idx = list(range(0, len(frames), step)) or [0]
    samples = []

    def both_passes(i):
        first = _ocr_labels(frames[i])
        return first + _cell_labels(frames[i], first)

    with ThreadPoolExecutor(workers) as ex:
        for n, (i, labels) in enumerate(zip(idx, ex.map(both_passes, idx))):
            samples.append({"t": float(i), "labels": _tiles(labels)})
            if progress:
                progress((n + 1) / len(idx))
    mapping, seen = canonical_names(samples)
    min_seen = max(2, int(0.05 * len(samples)))  # a real participant label shows up repeatedly
    # participants are full names (2+ words): single words are truncations, logos or shirt text
    keep = {n for n, c in seen.items() if c >= min_seen and len(n.split()) >= 2}
    for s in samples:
        s["labels"] = [{**l, "name": mapping[l["name"]]} for l in s["labels"] if mapping[l["name"]] in keep]
    participants = sorted(keep, key=lambda n: -seen[n])
    return samples, participants, {"samples": len(samples), "step_s": step, "label_hits": dict(seen)}


def _drop(samples, participants, bad):
    keep = [p for p in participants if p not in bad]
    for s in samples:
        s["labels"] = [l for l in s["labels"] if l["name"] in keep]
    return samples, keep


def drop_document_text(samples, participants, doc_texts):
    """Names that are really text of the documents in this folder (slide titles, agenda items) are not people.
    A real person can also appear in a document ('Presenter: X'), so this only drops names that are never
    seen often outside of what looks like that document."""
    texts = [t.lower() for t in doc_texts if t]
    if not texts:
        return samples, participants
    seen = Counter(l["name"] for s in samples for l in s["labels"])
    bad = set()
    for p in participants:
        in_docs = any(fuzz.partial_ratio(p.lower(), t) >= 92 for t in texts)
        roleish = any(re.search(rf"(presenter|chair|facilitator|lead|speaker|host)\W+{re.escape(p.lower())}", t) for t in texts)
        if in_docs and not roleish:
            bad.add(p)
    return _drop(samples, participants, bad)


def drop_labels_in_shared(samples, participants, periods):
    """A name tile is never inside the shared content. Text read there (a document's links and headings, an app's
    menu: "Craig OKR Deck", "Service Desk") is not a participant. periods: slides.layout_timeline output.
    Participants are then counted again: a name must still be seen in enough frames outside the shared content."""
    if not periods:
        return samples, participants
    for s in samples:
        reg = next((r for a, b, k, r in periods if a <= s["t"] < b), None)
        if not reg:
            continue                           # gallery / speaker view: labels anywhere are tiles
        x, y, w, h = reg
        s["labels"] = [l for l in s["labels"]
                       if not (x <= l["x"] + l["w"] / 2 <= x + w and y <= l["y"] + l["h"] / 2 <= y + h)]
    seen = Counter(l["name"] for s in samples for l in s["labels"])
    min_seen = max(2, int(0.05 * len(samples)))
    keep = [p for p in participants if seen[p] >= min_seen]
    for s in samples:
        s["labels"] = [l for l in s["labels"] if l["name"] in keep]
    return samples, keep


def drop_shared_labels(samples, participants, segs, region):
    """Names that are text of the slide/document being shared at that moment (slide titles, agenda items)
    are not participant tiles. Decided by the shared screen's own text, not by position: the shared area
    may cover the whole frame, including the participant strip next to the slide."""
    total, in_text = Counter(), Counter()
    for s in samples:
        seg = next((g for g in segs if g["start"] <= s["t"] < g["end"]), None)
        text = ""
        if seg is not None and seg["kind"] in ("slide", "unknown_slide"):
            text = ((seg.get("ocr_raw") or seg.get("ocr") or "") + "\n" +
                    ((seg.get("match") or {}).get("text") or "")).lower()
        for l in s["labels"]:
            total[l["name"]] += 1
            if text and fuzz.partial_ratio(l["name"].lower(), text) >= 85:
                in_text[l["name"]] += 1
    bad = {p for p in participants if total[p] and in_text[p] / total[p] >= 0.5}
    return _drop(samples, participants, bad)


ROLE_NAME = re.compile(r"(?:Presenter|Chair|Co-?Chair|Facilitator|Recorder|Time-?Keeper|Moderator|Host|Speaker|Lead)s?\s*[:\-–]\s*"
                       r"((?:Dr\.\s+)?[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z'’\-]+)")


def names_in_documents(doc_texts):
    """People named with a role in agendas / slides ('Presenter: Michael Barber', 'Facilitator: Dia Erpenbeck')."""
    out = []
    for t in doc_texts:
        for m in ROLE_NAME.finditer(t or ""):
            n = re.sub(r"^Dr\.\s+", "", m.group(1)).strip()
            if n not in out:
                out.append(n)
    return out


def initials_of(name):
    toks = [t for t in re.split(r"[\s\-]+", name) if t and t[0].isalpha()]
    return (toks[0][0] + toks[-1][0]).upper() if len(toks) >= 2 else name[:1].upper()


# ------------------------------------------------------------------ camera-off avatars (initials in a circle)
def _ocr_initials(gray, cx, cy, r):
    h, w = gray.shape
    a = max(1, int(0.62 * r))
    crop = gray[max(0, cy - a):min(h, cy + a), max(0, cx - a):min(w, cx + a)]
    if crop.size == 0 or min(crop.shape) < 6:
        return None
    f = 80 / crop.shape[0]
    big = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    _, th = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if th.mean() < 128:  # make the letters dark on light
        th = 255 - th
    txt = (safe_ocr(pytesseract.image_to_string, th,
                    config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ") or "").strip()
    return txt if re.fullmatch(r"[A-Z]{2,3}", txt) else None  # one letter cannot be read reliably


def discover_avatars(frames, samples):
    """Teams/Zoom show camera-off participants as a circle with initials. Detect them in the sampled
    frames; each becomes a pseudo-participant '@KH' whose activity (speaking ring) can be matched to a voice."""
    found = Counter()
    per_sample = {}
    for s in samples:
        img = cv2.imread(str(frames[int(s["t"])]))
        if img is None:
            continue
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        H, W = g.shape
        circles = cv2.HoughCircles(cv2.medianBlur(g, 3), cv2.HOUGH_GRADIENT, dp=1.2, minDist=int(0.05 * H),
                                   param1=100, param2=22, minRadius=int(0.022 * H), maxRadius=int(0.09 * H))
        dets = []
        if circles is not None:
            for cx, cy, r in np.round(circles[0][:40]).astype(int):
                # an avatar is a flat-coloured disc: the ring between the letters and the edge is uniform.
                # Faces, lamps and round objects are not -> skip them before the (slow) OCR.
                x0, y0, x1, y1 = max(0, cx - r), max(0, cy - r), min(W, cx + r + 1), min(H, cy + r + 1)
                sub = img[y0:y1, x0:x1]
                yy, xx = np.ogrid[y0:y1, x0:x1]
                d2 = (xx - cx) ** 2 + (yy - cy) ** 2
                ring = sub[(d2 >= (0.72 * r) ** 2) & (d2 <= (0.92 * r) ** 2)]
                if ring.size < 20 or ring.std(axis=0).mean() > 18:
                    continue
                ini = _ocr_initials(g, cx, cy, r)
                if ini:
                    k = 1.5 * r  # box around the circle: the speaking ring lies inside it
                    dets.append({"name": "@" + ini, "box": [int(max(0, cx - k)), int(max(0, cy - k)), int(min(W, cx + k)),
                                                            int(min(H, cy + k))], "W": W, "H": H, "avatar": True})
                    found["@" + ini] += 1
        per_sample[s["t"]] = dets
    min_seen = max(2, int(0.05 * len(samples)))
    keep = {n for n, c in found.items() if c >= min_seen}
    for s in samples:
        s["labels"] += [d for d in per_sample.get(s["t"], []) if d["name"] in keep]
    return sorted(keep, key=lambda n: -found[n]), dict(found)


def resolve_initials(mapping, known_names):
    """'@KH' -> the one known name with those initials, else 'KH (initials)'."""
    for sid, m in mapping.items():
        if m["name"].startswith("@"):
            ini = m["name"][1:]
            cands = [n for n in known_names if initials_of(n) == ini]
            m["initials"] = ini
            m["name"] = cands[0] if len(cands) == 1 else f"{ini} (initials)"
    return mapping


def meeting_view_checker(samples, region, min_cover=0.5):
    """True at time t if the participant tiles form the main view (gallery or full-screen speaker), i.e. not a
    shared screen. OCR reads only some names per frame, so the test uses the extent of the tile grid the labels
    form, not the area of the recognised tiles. A one-column/one-row strip of tiles beside a shared slide does
    not span the view and does not count."""
    rx, ry, rw, rh = region
    area = max(1, rw * rh)

    def check(t):
        boxes = [l["box"] for l in _layout_at(samples, t)]
        if not boxes:
            return False
        W = max(1, max(b[2] for b in boxes))
        cols = _cluster_1d([b[0] for b in boxes], 0.05 * W)
        rows = _cluster_1d([b[3] for b in boxes], 0.05 * W)
        is_grid = (len(cols) >= 2 and len(rows) >= 2) or len(boxes) == 1
        if not is_grid:
            return False
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        ix = max(0, min(x1, rx + rw) - max(x0, rx))
        iy = max(0, min(y1, ry + rh) - max(y0, ry))
        return ix * iy / area >= min_cover
    return check


def _layout_at(samples, t):
    best = None
    for s in samples:
        if s["t"] <= t:
            best = s
        else:
            break
    return (best or samples[0])["labels"] if samples else []


def activity(video, samples, duration, frames, fps=4, progress=None):
    """Per participant and second: in-tile motion (4 fps) and border highlight (1 fps colour frames)."""
    if not samples or not any(s["labels"] for s in samples):
        return {}, {}
    W0 = samples[0]["labels"][0]["W"] if samples[0]["labels"] else next(l["W"] for s in samples for l in s["labels"])
    H0 = next(l["H"] for s in samples for l in s["labels"])
    sc = min(1.0, 640 / W0)
    w, h = int(W0 * sc) // 2 * 2, int(H0 * sc) // 2 * 2
    cmd = [FFMPEG, "-v", "error", "-i", str(video), "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, creationflags=NO_WINDOW)
    n = w * h
    motion = defaultdict(lambda: defaultdict(list))
    prev = None
    k = 0
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        cur = np.frombuffer(buf, np.uint8).reshape(h, w).astype(np.int16)
        t = k / fps
        if prev is not None:
            diff = np.abs(cur - prev)
            for l in _layout_at(samples, t):
                x0, y0, x1, y1 = l["box"]
                # inner part of the tile: face / avatar, not the label strip or the border
                bx0, bx1 = int((x0 + 0.15 * (x1 - x0)) * sc), int((x0 + 0.85 * (x1 - x0)) * sc)
                by0, by1 = int((y0 + 0.10 * (y1 - y0)) * sc), int((y0 + 0.78 * (y1 - y0)) * sc)
                if bx1 > bx0 and by1 > by0:
                    motion[l["name"]][int(t)].append(float(diff[by0:by1, bx0:bx1].mean()))
        prev = cur
        k += 1
        if progress and k % (fps * 30) == 0 and duration:
            progress(min(1.0, t / duration))
    p.wait()
    mot = {name: {s: float(np.mean(v)) for s, v in secs.items()} for name, secs in motion.items()}

    hl = defaultdict(dict)
    for sec, path in enumerate(frames):
        lay = _layout_at(samples, sec)
        if not lay:
            continue
        img = cv2.imread(str(path))
        if img is None:
            continue
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        sat = (hsv[..., 1] > 110) & (hsv[..., 2] > 110)
        for l in lay:
            x0, y0, x1, y1 = l["box"]
            b = max(2, int(0.025 * min(x1 - x0, y1 - y0)))
            ring = np.concatenate([sat[y0:y0 + b, x0:x1].ravel(), sat[y1 - b:y1, x0:x1].ravel(),
                                   sat[y0:y1, x0:x0 + b].ravel(), sat[y0:y1, x1 - b:x1].ravel()])
            if ring.size:
                hl[l["name"]][sec] = float(ring.mean())
    return mot, dict(hl)


def match(turns, mot, hl, participants, min_overlap_s=10):
    """Map voice clusters (S1..) to participant names. Returns {speaker_id: {name, method, score}} + score table."""
    speaking = defaultdict(set)
    for t in turns:
        if t["speaker"]:
            for s in range(int(t["start"]), int(np.ceil(t["end"]))):
                speaking[t["speaker"]].add(s)
    spk_ids = sorted(speaking, key=lambda s: int(s[1:]))
    if not spk_ids or not participants:
        return {}, {}

    def zscore(series):
        if not series:
            return {}
        v = np.array(list(series.values()))
        sd = v.std() or 1.0
        return {k: (x - v.mean()) / sd for k, x in series.items()}

    # A speaking highlight switches on and off. A colourful background at a tile edge is saturated all the time.
    # Usable only if some tile's border goes from clearly off to clearly on.
    def toggles(v):
        x = np.array(list(v.values())) if v else np.zeros(1)
        return np.percentile(x, 5) < 0.1 and np.percentile(x, 95) > 0.5
    # ...and a speaking highlight is on for ONE tile at a time: borders lighting up together are coloured content
    # (links in a shared document, a coloured background), not who is speaking
    on_at = Counter(sec for n in participants for sec, v in hl.get(n, {}).items() if v > 0.5)
    exclusive = not on_at or float(np.median(list(on_at.values()))) <= 1.5
    hl_usable = exclusive and any(toggles(hl.get(n, {})) for n in participants)
    sources = {"highlight": {n: zscore(hl.get(n, {})) for n in participants}} if hl_usable else {}
    sources["motion"] = {n: zscore(mot.get(n, {})) for n in participants}

    table = {}
    for method, series in sources.items():
        S = np.full((len(spk_ids), len(participants)), np.nan)
        for i, s in enumerate(spk_ids):
            others = set().union(*(speaking[o] for o in spk_ids if o != s)) - speaking[s]
            for j, n in enumerate(participants):
                z = series[n]
                a = [z[x] for x in speaking[s] if x in z]
                b = [z[x] for x in others if x in z]
                if len(a) >= min_overlap_s and len(b) >= min_overlap_s:
                    S[i, j] = float(np.mean(a) - np.mean(b))
        table[method] = S
    from scipy.optimize import linear_sum_assignment

    def assign(method):
        S = table[method]
        min_score = 0.5 if method == "highlight" else 0.35   # in standard deviations
        rows, cols = linear_sum_assignment(-np.nan_to_num(S, nan=-9))
        result = {}
        for i, j in zip(rows, cols):
            sc = S[i, j]
            if np.isnan(sc) or sc < min_score:
                continue
            row = np.sort(np.nan_to_num(S[i], nan=-9))[::-1]
            margin = sc - (row[1] if len(row) > 1 else -9)
            if margin < 0.1:  # ambiguous: two participants fit this voice almost equally well
                continue
            result[spk_ids[i]] = {"name": participants[j], "method": method, "score": round(float(sc), 2),
                                  "margin": round(float(margin), 2)}
        scores = {spk_ids[i]: {participants[j]: (None if np.isnan(S[i, j]) else round(float(S[i, j]), 2))
                               for j in range(len(participants))} for i in range(len(spk_ids))}
        return result, {"method": method, "scores": scores, "highlight_usable": hl_usable}

    result, dbg = assign("highlight") if hl_usable else ({}, None)
    if not result:  # no highlight in this recording (or it named nobody): fall back to in-tile movement
        result, dbg = assign("motion")
    return result, dbg
