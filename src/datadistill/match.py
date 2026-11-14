"""Frame -> source slide matching (UC-F3/F4). Spec §4.2 steps 2-4.

1. candidate retrieval: OCR text similarity (rapidfuzz) + pHash similarity -> top K pages
2. geometric verification: SIFT + Lowe ratio + RANSAC homography -> inlier count
3. accept if inliers >= MIN_INLIERS; final score combines text similarity and inliers
"""
import threading

import cv2
import numpy as np
from PIL import Image
from rapidfuzz import fuzz

from . import db
from .config import OUTPUT_DIR
from .textutil import hamming, normalize_text

TOP_K = 6
MIN_INLIERS = 20
MIN_TEXT_SIM = 0.5   # OCR text of the frame vs the page text
MIN_SPREAD = 0.25    # inliers must cover >= 25% of the page (a logo/footer covers a few %)
READABLE_WORDS = 8   # with this many OCR words the text decides: a different text is a different slide
SIFT_W = 800


def _gray(img: Image.Image, w=SIFT_W):
    g = np.asarray(img.convert("L"))
    f = w / g.shape[1]
    return cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)


class RefLibrary:
    """All non-quarantined document pages, with lazily computed SIFT features."""

    def __init__(self, folder=None):
        self.folder = folder  # documents in the same folder are the most likely source of a shared slide
        self.pages = db.q("""SELECT p.id, p.file_id, p.page, p.slide_uid, p.text, p.text_norm, p.phash, p.image, f.name,
                                    f.system folder
                             FROM pages p JOIN files f ON f.id=p.file_id
                             WHERE f.status NOT IN ('quarantined','removed') AND p.image IS NOT NULL""")
        self.sift = cv2.SIFT_create(nfeatures=1500)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self._feat = {}
        self._lock = threading.Lock()

    def __len__(self):
        return len(self.pages)

    def features(self, page):
        with self._lock:
            if page["id"] not in self._feat:
                img = Image.open(OUTPUT_DIR / page["image"])
                self._feat[page["id"]] = self.sift.detectAndCompute(_gray(img), None)
            return self._feat[page["id"]]

    def inliers(self, frame_feat, page):
        """(inlier count, spread): spread = area of the inliers' convex hull / page area. A shared logo or
        footer gives inliers in one corner (small spread); the same slide gives inliers all over it."""
        kp1, d1 = frame_feat
        kp2, d2 = self.features(page)
        if d1 is None or d2 is None or len(d1) < 8 or len(d2) < 8:
            return 0, 0.0
        good = [m for m, n in (p for p in self.matcher.knnMatch(d1, d2, k=2) if len(p) == 2)
                if m.distance < 0.75 * n.distance]
        if len(good) < 10:
            return 0, 0.0
        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        if mask is None:
            return 0, 0.0
        pts = dst[mask.ravel().astype(bool)].reshape(-1, 2)
        if len(pts) < 3:
            return int(mask.sum()), 0.0
        page_area = float(self._page_size(page))
        return int(mask.sum()), float(cv2.contourArea(cv2.convexHull(pts))) / page_area

    def _page_size(self, page):
        img = Image.open(OUTPUT_DIR / page["image"])
        g = _gray(img)
        return g.shape[0] * g.shape[1]

    def match(self, img: Image.Image, ocr_text: str, ph: str):
        """Return best {page_id, file_id, page, slide_uid, inliers, text_sim, score} or None."""
        if not self.pages:
            return None
        norm = normalize_text(ocr_text)
        scored = []
        for p in self.pages:
            ts = fuzz.token_set_ratio(norm, p["text_norm"]) / 100 if norm and p["text_norm"] else 0.0
            hs = 1 - hamming(ph, p["phash"]) / 64
            bonus = 0.05 if self.folder and p["folder"] == self.folder else 0.0
            scored.append((0.7 * ts + 0.3 * hs + bonus, ts, p))
        scored.sort(key=lambda x: -x[0])
        feat = self.sift.detectAndCompute(_gray(img), None)
        best = None
        for pre, ts, p in scored[:TOP_K]:
            n, spread = self.inliers(feat, p)
            if n < MIN_INLIERS:
                continue
            # same slide = visual matches spread over the page (not just a shared logo), or the WHOLE text
            # matches. token_set_ratio alone is not enough: an agenda slide that quotes this deck's title
            # contains all its words.
            whole = fuzz.token_sort_ratio(norm, p["text_norm"]) / 100 if norm and p["text_norm"] else 0.0
            if spread < MIN_SPREAD and not (ts >= MIN_TEXT_SIM and whole >= 0.7):
                continue
            # slides of one template (same header band, bullets, footer) give spread-out inliers too: when the
            # screen's text is clearly readable and says something else, geometry alone does not make a match
            if len(norm.split()) >= READABLE_WORDS and ts < MIN_TEXT_SIM:
                continue
            # text similarity separates near-identical revisions; inliers confirm geometry
            score = 0.6 * ts + 0.4 * min(n, 150) / 150
            if best is None or score > best["score"]:
                best = {"page_id": p["id"], "file_id": p["file_id"], "page": p["page"], "slide_uid": p["slide_uid"],
                        "inliers": n, "text_sim": round(ts, 3), "score": round(score, 3), "text": p["text"]}
        return best
