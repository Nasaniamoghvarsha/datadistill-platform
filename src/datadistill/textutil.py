"""Text normalisation, metadata regexes, hashing, OCR."""
from pathlib import Path
import hashlib
import re
import threading

import cv2
import imagehash
import numpy as np
import pytesseract
from PIL import Image

from .config import TESSERACT_CMD

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# Ericsson-style document numbers, e.g. "1/1551-DS 101 01 Uen", "EN/LZT 123 4567"
DOC_NO_RE = re.compile(r"\b\d{1,4}/\d{3,5}-[A-Z]{2,5}\s?\d{2,4}(?:\s?\d{2,4})?(?:/\d+)?(?:\s?Uen)?\b"
                       r"|\b[A-Z]{2,3}/LZT\s?\d{3}\s?\d{3,4}\b")
REV_RE = re.compile(r"\bRev(?:ision)?\.?\s*:?\s*([A-Z]{1,2}\d{0,2}|P[A-Z]\d{1,2})\b")
LABEL_RE = re.compile(r"\bEricsson\s+(Internal|Confidential|Open|Restricted)\b", re.I)
COPY_SUFFIX_RE = re.compile(r"(\s*\(\d+\)|\s*-\s*Copy(\s*\(\d+\))?|\s*_copy\d*)$", re.I)


def normalize_name(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    stem = stem if dot else name
    return COPY_SUFFIX_RE.sub("", stem).strip().lower() + (("." + ext.lower()) if dot else "")


def normalize_text(text: str) -> str:
    """Normalise slide text for hashing: drop footer-like lines (doc no, rev, label, page numbers)."""
    keep = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or re.fullmatch(r"[\d\s/|.-]+", s):
            continue
        if DOC_NO_RE.search(s) or LABEL_RE.search(s) or REV_RE.search(s):
            continue
        keep.append(s)
    t = " ".join(keep).lower()
    t = re.sub(r"[^\w\s?]", " ", t)
    t = re.sub(r"\b\d{1,3}\s*$", "", t)  # trailing page number
    return re.sub(r"\s+", " ", t).strip()


def text_hash(norm: str) -> str:
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16] if norm else ""


def phash_hex(img: Image.Image) -> str:
    return str(imagehash.phash(img.convert("L")))


def hamming(a: str, b: str) -> int:
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def make_uid(norm: str, ph: str) -> str:
    return hashlib.sha256(f"{norm}|{ph}".encode()).hexdigest()[:16]


def slide_title(page_text=None, ocr_text=None):
    """First meaningful line of a slide (document text layer preferred over OCR)."""
    for line in (page_text or ocr_text or "").splitlines():
        line = line.strip(" •-*\t")
        if len(line) >= 3 and sum(c.isalpha() for c in line) >= 3 and not DOC_NO_RE.search(line):
            return line[:80]
    return None


def extract_meta(text: str) -> dict:
    out = {}
    m = DOC_NO_RE.search(text or "")
    if m:
        out["doc_no"] = m.group(0).strip()
    m = REV_RE.search(text or "")
    if m:
        out["rev"] = m.group(1)
    m = LABEL_RE.search(text or "")
    if m:
        out["label"] = "Ericsson " + m.group(1).capitalize()
    return out


_ocr_lock = threading.Lock()


def _retry(fn, *args, **kwargs):
    """Retry a Tesseract call (temp-file races on Windows); give up on this one image instead of the whole job."""
    import time
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except (pytesseract.TesseractError, OSError):
            time.sleep(0.2 * (attempt + 1))
    return None
WORD = re.compile(r"^[A-Za-zÀ-ÿ][a-zà-ÿ]{2,}$|^[A-Z]{2,6}$")


def text_quality(img: Image.Image):
    """How much readable text is on a frame: (number of confident real-looking words, mean OCR confidence).
    Slides give many confident words; faces, rooms and backgrounds give few, low-confidence fragments."""
    g = np.asarray(img.convert("L"))
    if g.shape[1] < 1400:
        f = 1400 / g.shape[1]
        g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    g = dark_on_light(g)
    with _ocr_lock:
        d = _retry(pytesseract.image_to_data, g, config="--psm 3", output_type=pytesseract.Output.DICT)
    if d is None:
        return 0, 0.0
    confs = [float(c) for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= 0]
    good = [w for w, c in zip(d["text"], d["conf"]) if WORD.match(w.strip(".,:;!?()")) and float(c) >= 70]
    return len(good), (float(np.mean(confs)) if confs else 0.0)


APP_CHROME = set("""file home insert draw design layout references mailings review view help acrobat transitions animations
slide show record share comments editing dictate editor sensitivity add-ins format table tools data formulas page
recording presenting stop sharing chat people raise react more camera mic leave rooms apps notes click""".split())
_VOWEL = re.compile(r"[aeiouyåäöAEIOUY]")


def clean_screen_text(text: str) -> str:
    """Keep only lines of a shared screen that read like language. Drops OCR noise from low-resolution
    video ("Fan tae et Dre Comp") and application chrome (Word / PowerPoint ribbons, meeting toolbars)."""
    keep = []
    for line in (text or "").splitlines():
        toks = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]*|\d[\d.,:/%-]*", line)
        if len(toks) < 2:
            continue
        words = [t for t in toks if not t[0].isdigit()]
        chrome = sum(t.lower() in APP_CHROME for t in words)
        wordlike = sum(1 for t in words if len(t) >= 2 and _VOWEL.search(t) and (t.islower() or t.istitle() or t.isupper()))
        if words and chrome / len(words) >= 0.5:
            continue
        if wordlike / max(1, len(toks)) < 0.7:
            continue
        vocab = english_vocab()
        long_words = [t.lower().strip("'’") for t in words if len(t) >= 3]
        if vocab and long_words and sum(known_word(w, vocab) for w in long_words) / len(long_words) < 0.75:
            continue  # looks like words but mostly isn't: OCR noise from a too-small image
        keep.append(line.strip())
    return "\n".join(keep)


# Word forms, so a real word is not taken for OCR noise only because the ~20k-word list lacks that form:
# British spelling (optimisation), endings (reordered, stakeholders), prefixes (reorder), compounds (rollout).
_SPELLING = (("isations", "izations"), ("isation", "ization"), ("ised", "ized"), ("ises", "izes"), ("ising", "izing"),
             ("ise", "ize"), ("yse", "yze"), ("lled", "led"), ("lling", "ling"))
_INSIDE = (("our", "or"), ("tre", "ter"), ("ogue", "og"))
_SUFFIXES = (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"), ("ly", ""), ("er", ""),
             ("ers", ""), ("ment", ""), ("ments", ""), ("ness", ""), ("ation", "ate"), ("ations", "ate"), ("ion", ""),
             ("ions", ""), ("ion", "e"), ("ity", "e"), ("able", ""), ("al", ""), ("ive", "e"))
_PREFIXES = ("re", "un", "pre", "sub", "non", "co", "multi", "over", "under", "inter", "de", "mis", "out", "up", "cross", "self")


def _forms(w):
    out = {w}
    for a, b in _SPELLING:
        if w.endswith(a):
            out.add(w[: -len(a)] + b)
            break
    for a, b in _INSIDE:
        if a in w:
            out.add(w.replace(a, b))
    for x in list(out):
        for s, r in _SUFFIXES:
            if x.endswith(s) and len(x) - len(s) >= 3:
                out.add(x[: -len(s)] + r)
    for x in list(out):
        for p in _PREFIXES:
            if x.startswith(p) and len(x) - len(p) >= 4:
                out.add(x[len(p):])
    return out


_PARTICLES = {"out", "up", "off", "map", "way", "day", "set", "end"}          # roll+out, set+up, road+map
_LEADS = {"off", "out", "up", "on", "over", "under", "back", "down", "cross"}   # off+board, up+load, on+board


def known_word(w: str, vocab) -> bool:
    """Is w an English word, allowing for other forms of a listed word and two-word compounds?
    Only for words of 6+ letters: short OCR fragments ("vals", "tems") must be listed as they are,
    and a form or compound part must itself be a real word of 4-5+ letters, so noise is not "explained" by
    3-letter pieces ("set" + "ter")."""
    w = w.lower().strip("'’-")
    if w in vocab:
        return True
    if len(w) < 6:
        return False
    f = _forms(w)
    f |= {y for x in list(f) for y in _forms(x)}
    if any(len(x) >= 5 and x in vocab for x in f - {w}):
        return True
    for x in {w} | {y for y in f if len(y) >= 6}:          # offboarding -> offboard -> off + board
        for i in range(2, len(x) - 1):
            a, b = x[:i], x[i:]
            if (a in _LEADS and len(b) >= 4 and b in vocab) or \
               (len(a) >= 4 and a in vocab and (b in _PARTICLES or (len(b) >= 4 and b in vocab))):
                return True
    return False


_vocab = None


def english_vocab():
    """~20k common English words, used to tell real text from OCR noise. Shipped as english_words.txt (the whole-word
    entries of a common English tokenizer vocabulary); falls back to the search model's vocabulary on disk, if any."""
    global _vocab
    if _vocab is None:
        _vocab = set()
        own = Path(__file__).with_name("english_words.txt")
        if own.exists():
            _vocab = {w for w in own.read_text(encoding="utf-8").split() if w}
        else:
            from .config import ROOT
            files = sorted((ROOT / ".models").glob("hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/*/vocab.txt"))
            if files:
                for w in files[0].read_text(encoding="utf-8").splitlines():
                    if w.isalpha() and not w.startswith("##") and len(w) >= 3:
                        _vocab.add(w)
    return _vocab


def dark_on_light(g):
    """Dark text on a light background everywhere: invert a dark theme, and invert dark horizontal bands
    (a slide's coloured title band with white text, which OCR otherwise reads as noise)."""
    if np.median(g) < 128:
        g = 255 - g
    rows = np.median(g, axis=1)
    dark = rows < 110
    if dark.any():
        g = g.copy()
        g[dark] = 255 - g[dark]
    return g


def ocr_image(img: Image.Image) -> str:
    """OCR tuned for slides: dark themes and title bands made dark-on-light, adaptive threshold."""
    g = np.asarray(img.convert("L"))
    if g.shape[1] < 1400:
        f = 1400 / g.shape[1]
        g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    g = dark_on_light(g)
    th = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
    with _ocr_lock:
        txt = _retry(pytesseract.image_to_string, Image.fromarray(th), config="--psm 3") or ""
    # Tesseract reads bullet glyphs as "•", "*", "-", "¢", "°", "e", "@" ... strip them
    lines = [BULLET_RE.sub("", ln.strip()) for ln in txt.splitlines()]
    return "\n".join(ln for ln in lines if len(ln) > 1)


BULLET_RE = re.compile(r"^(?:[•*\-¢°·@eo]{1,2}\s+)")
