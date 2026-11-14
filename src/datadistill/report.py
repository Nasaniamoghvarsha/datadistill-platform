"""Report PDF of one file: the final proof of what DataDistill kept and why it can replace the original.

ONE self-contained PDF, no separate or attached files. Recording: cover with key figures and proof (SHA-256,
sizes, deletion audit), the meeting summary written by a local model (Ministral 3 8B) and checked against the
transcript, people, quality and processing, storage, shared screens with their images, the full transcript with
snapshots, and the complete JSON printed as the last section. Document: cover and proof, copies and revisions,
every page with its image and text, the complete JSON. Built only from what DataDistill stores, so it works after
the original was deleted. reportlab (BSD).
"""
import hashlib
import io
import json
import re
import time
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, Image, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Preformatted, Spacer, Table, TableStyle)

from . import db, dedup, library, package, storage
from .config import OUTPUT_DIR

INK, MUTED, ACCENT, GREEN, LINE, LIGHT, AMBER = (colors.HexColor(c) for c in
                                                 ("#14213D", "#4A5568", "#2453E6", "#067647", "#DDE2EA", "#F3F5F8", "#B54708"))
W, H = A4
M = 18 * mm

# ------------------------------------------------------------------ fonts (Arial / Courier New on Windows, DejaVu elsewhere)
_FONTS = None


def fonts():
    global _FONTS
    if _FONTS:
        return _FONTS
    cands = {"DD": ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
             "DD-Bold": ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
             "DD-Italic": ["C:/Windows/Fonts/ariali.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"],
             "DD-Mono": ["C:/Windows/Fonts/cour.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]}
    fallback = {"DD": "Helvetica", "DD-Bold": "Helvetica-Bold", "DD-Italic": "Helvetica-Oblique", "DD-Mono": "Courier"}
    out = {}
    for name, paths in cands.items():
        p = next((p for p in paths if Path(p).exists()), None)
        if p:
            pdfmetrics.registerFont(TTFont(name, p))
            out[name] = name
        else:
            out[name] = fallback[name]
    if all(out[k] == k for k in ("DD", "DD-Bold", "DD-Italic")):
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        registerFontFamily("DD", normal="DD", bold="DD-Bold", italic="DD-Italic", boldItalic="DD-Bold")
    _FONTS = out
    return out


def styles():
    f = fonts()
    base = dict(fontName=f["DD"], textColor=INK)
    return {
        "eyebrow": ParagraphStyle("eyebrow", **{**base, "textColor": ACCENT}, fontSize=8.5, leading=11, spaceAfter=2),
        "title": ParagraphStyle("title", **{**base, "fontName": f["DD-Bold"]}, fontSize=20, leading=24, spaceAfter=4),
        "sub": ParagraphStyle("sub", **{**base, "textColor": MUTED}, fontSize=9.5, leading=13, spaceAfter=10),
        "h1": ParagraphStyle("h1", **{**base, "fontName": f["DD-Bold"]}, fontSize=14, leading=18, spaceBefore=12, spaceAfter=6),
        "h2": ParagraphStyle("h2", **{**base, "fontName": f["DD-Bold"]}, fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", **base, fontSize=9, leading=12.5),
        "small": ParagraphStyle("small", **{**base, "textColor": MUTED}, fontSize=7.5, leading=10),
        "cell": ParagraphStyle("cell", **base, fontSize=8.5, leading=11),
        "cellb": ParagraphStyle("cellb", **{**base, "fontName": f["DD-Bold"]}, fontSize=8.5, leading=11),
        "num": ParagraphStyle("num", **{**base, "fontName": f["DD-Bold"]}, fontSize=15, leading=18),
        "mono": ParagraphStyle("mono", **{**base, "fontName": f["DD-Mono"], "textColor": MUTED}, fontSize=7, leading=9, wordWrap="CJK"),
        "pre": ParagraphStyle("pre", **{**base, "fontName": f["DD-Mono"]}, fontSize=6.3, leading=7.6),
        "line": ParagraphStyle("line", **base, fontSize=8.8, leading=12, spaceAfter=3),
        "screen": ParagraphStyle("screen", **{**base, "textColor": MUTED}, fontSize=8, leading=10.5, spaceBefore=4, spaceAfter=3,
                                 backColor=LIGHT, borderPadding=(3, 4, 3, 4)),
    }


def _t(x):
    """Plain text -> safe for a reportlab Paragraph."""
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f-]", "", str(x if x is not None else "")))


def _date(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def _bytes(b):
    if b is None:
        return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1000 or u == "TB":
            return f"{b:.0f} {u}" if u == "B" else f"{b:.1f} {u}"
        b /= 1000


def _img(path, max_w, max_h):
    try:
        im = PILImage.open(path).convert("RGB")
    except (OSError, ValueError):
        return None
    s = min(max_w / im.width, max_h / im.height, 1.0 * mm * 10)  # never upscale beyond a sensible size
    bio = io.BytesIO()
    im.save(bio, "JPEG", quality=82)
    bio.seek(0)
    return Image(bio, width=im.width * s, height=im.height * s, hAlign="LEFT")


class Bar(Flowable):
    """A small horizontal bar (talk-time share)."""

    def __init__(self, share, width=35 * mm, height=3.2 * mm):
        super().__init__()
        self.share, self.width, self.height = share, width, height

    def draw(self):
        c = self.canv
        c.setFillColor(LIGHT)
        c.roundRect(0, 0, self.width, self.height, 1.2, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.roundRect(0, 0, max(1.5, self.width * self.share), self.height, 1.2, stroke=0, fill=1)


def _table(rows, widths, header=True, zebra=False):
    t = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE)]
    t.setStyle(TableStyle(st))
    return t


def _figures(items, S):
    """Row of key figures: [(value, label, color)]."""
    cells = [[Paragraph(f'<font color="{c.hexval()}">{_t(v)}</font>' if c else _t(v), S["num"]) for v, _, c in items],
             [Paragraph(_t(lbl), S["small"]) for _, lbl, _ in items]]
    t = Table(cells, colWidths=[(W - 2 * M) / len(items)] * len(items), hAlign="LEFT")
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0, colors.white),
                           ("LINEAFTER", (0, 0), (-2, -1), 0.6, LINE), ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, 0), 7),
                           ("BOTTOMPADDING", (0, 1), (-1, 1), 7)]))
    return t


def _doc(buf, title, sha=""):
    f = fonts()

    def frame_deco(c, doc):
        c.saveState()
        c.setFont(f["DD"], 7.5)
        c.setFillColor(MUTED)
        c.drawString(M, H - 11 * mm, f"DataDistill report · {title[:90]}")
        c.drawRightString(W - M, 10 * mm, f"Page {doc.page}")
        c.drawString(M, 10 * mm, f"Generated {_date(time.time())} · local processing, nothing left this computer")
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(M, H - 12.5 * mm, W - M, H - 12.5 * mm)
        c.restoreState()

    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=17 * mm, bottomMargin=16 * mm,
                          title=f"DataDistill report: {title}", author="DataDistill",
                          subject=f"DataDistill report; SHA-256 of the original: {sha}",
                          creator="DataDistill (reportlab)")
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(M, 16 * mm, W - 2 * M, H - 33 * mm, id="f")], onPage=frame_deco)])
    return doc


def _proof(f, extra_rows, S):
    """The proof table: identity of the original, where it is, and what happened to it (audit)."""
    meta = f["meta"]
    deleted = meta.get("original_deleted")
    audit = db.q("SELECT ts, action, details FROM audit WHERE file_id=? AND action IN "
                 "('delete_original','duplicate_to_shortcut','shortcut','register','same_recording') ORDER BY ts", (f["id"],))
    status = (f"Deleted after distillation on {_date(meta.get('deleted_at'))} (audit log entry below)" if deleted
              else "Shortcut to another copy (no bytes of its own)" if f["status"] == "shortcut" else "Original on disk")
    rows = [[Paragraph("Original file", S["cellb"]), Paragraph(_t(f"{f['system']} / {f['name']}"), S["cell"])],
            [Paragraph("SHA-256 of the original", S["cellb"]), Paragraph(_t(f["sha256"]), S["mono"])],
            [Paragraph("Size of the original", S["cellb"]), Paragraph(_t(f"{_bytes(f['size'])} ({f['size']:,} bytes)"), S["cell"])],
            [Paragraph("Status", S["cellb"]), Paragraph(_t(status), S["cell"])],
            [Paragraph("Added to DataDistill", S["cellb"]), Paragraph(_t(_date(f["added_at"])), S["cell"])]] + extra_rows
    for a in audit:
        dt = json.loads(a["details"] or "{}")
        txt = {"delete_original": f"original deleted ({_bytes(dt.get('bytes'))}, hash {str(dt.get('sha256', ''))[:16]}…, "
                                  f"{_bytes(dt.get('distilled_bytes'))} kept)",
               "duplicate_to_shortcut": f"duplicate replaced by a shortcut ({_bytes(dt.get('saved_bytes'))} freed)",
               "shortcut": f"added as a shortcut ({_bytes(dt.get('saved_bytes'))} not stored)",
               "register": "registered", "same_recording": "found to be the same recording as another file"}.get(a["action"], a["action"])
        rows.append([Paragraph(f"Audit {_t(_date(a['ts']))}", S["cellb"]), Paragraph(_t(txt), S["cell"])])
    return _table(rows, [48 * mm, W - 2 * M - 48 * mm], header=False)


def _sha(data: bytes):
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ recording
def recording_story(file_id, S, json_bytes):
    from . import summary
    d = package.load_recording(file_id)
    f = d["file"]
    m = d["manifest"]
    q = m.get("quality", {})
    st = storage.distilled_parts(file_id)
    dur = m["source"]["duration_s"] or 0
    total_talk = sum(s.get("talk_time_s") or 0 for s in d["speakers"]) or 1
    shared = [s for s in d["screens"] if s["kind"] != "non_slide"]
    reduction = 1 - st["total"] / f["size"] if f["size"] else 0
    sm = summary.get(file_id)
    story = []

    # ---- cover
    story += [Paragraph("MEETING REPORT", S["eyebrow"]), Paragraph(_t(f["name"]), S["title"]),
              Paragraph(_t(f"Folder {f['system']} · {package.clock(dur)} · processed {_date(m.get('processed_at'))}"), S["sub"])]
    named = sum(1 for s in d["speakers"] if s.get("name"))
    story.append(_figures([(package.clock(dur), "length", None),
                           (f"{len(d['speakers'])} ({named} named)", "speakers", None),
                           (f"{round(100 * q['mean_confidence'])}%" if q.get("mean_confidence") else "n/a", "recognition confidence", ACCENT),
                           (f"{100 * reduction:.1f}%", f"smaller: {_bytes(f['size'])} → {_bytes(st['total'])}", GREEN)], S))
    story += [Spacer(1, 8), Paragraph("Proof", S["h2"]),
              _proof(f, [[Paragraph("Distilled package", S["cellb"]),
                          Paragraph(_t(f"transcript JSON, printed complete in the appendix; SHA-256 {_sha(json_bytes)}"), S["mono"])]], S)]
    sc = db.q("SELECT system, name FROM files WHERE status='shortcut' AND json_extract(meta,'$.master_id')=?", (file_id,))
    if sc:
        story.append(Paragraph(_t("Copies replaced by shortcuts to this file: " + "; ".join(f"{x['system']} / {x['name']}" for x in sc)), S["small"]))

    # ---- summary (written by the local model, every item checked against the transcript)
    story += [Paragraph("1. Summary", S["h1"])]
    if not sm or sm.get("error"):
        story.append(Paragraph(_t(f"No AI summary: {sm.get('error') if sm else 'not made yet'}. The full transcript is below."), S["body"]))
    else:
        if sm.get("title"):
            story.append(Paragraph(_t(sm["title"]), S["h2"]))
        if sm.get("purpose"):
            story.append(Paragraph("<b>Purpose:</b> " + _t(sm["purpose"]), S["body"]))
        story += [Spacer(1, 3), Paragraph(_t(sm.get("summary", "")), S["body"])]

        def at(it):
            return f'<font color="{MUTED.hexval()}">{_t(it.get("time", ""))}</font>'
        if sm.get("discussion"):
            story.append(Paragraph("Discussion", S["h2"]))
            for it in sm["discussion"]:
                who = f' <font color="{MUTED.hexval()}">({_t(", ".join(it.get("speakers") or []))})</font>' if it.get("speakers") else ""
                story.append(Paragraph(f'{at(it)}  {_t(it["point"])}{who}', S["line"]))
        story.append(Paragraph("Decisions", S["h2"]))
        if sm.get("decisions"):
            for it in sm["decisions"]:
                story.append(Paragraph(f'{at(it)}  {_t(it["decision"])}', S["line"]))
        else:
            story.append(Paragraph("No decision was taken in this meeting.", S["body"]))
        story.append(Paragraph("Action items", S["h2"]))
        if sm.get("action_items"):
            rows = [[Paragraph(h, S["cellb"]) for h in ("Owner", "Task", "Due", "Said at")]]
            for it in sm["action_items"]:
                rows.append([Paragraph(_t(it.get("owner") or "unclear"), S["cellb"]), Paragraph(_t(it["task"]), S["cell"]),
                             Paragraph(_t(it.get("due") or "—"), S["cell"]), Paragraph(_t(it.get("time", "")), S["cell"])])
            story.append(_table(rows, [32 * mm, W - 2 * M - 82 * mm, 30 * mm, 20 * mm]))
        else:
            story.append(Paragraph("No action items were agreed.", S["body"]))
        if sm.get("open_questions"):
            story.append(Paragraph("Open questions", S["h2"]))
            for it in sm["open_questions"]:
                story.append(Paragraph(f'{at(it)}  {_t(it["question"])}', S["line"]))
        removed = sm.get("removed") or []
        story.append(Paragraph(_t(
            f"Written by {sm.get('model')} from the transcript on this computer"
            + (f", in {sm['parts']} parts" if sm.get("parts", 1) > 1 else "")
            + ". Every point names the time it was said and was checked against the transcript there"
            + (f"; {len(removed)} point{'s' if len(removed) != 1 else ''} without support in the transcript "
               f"{'were' if len(removed) != 1 else 'was'} removed" if removed else "; none had to be removed")
            + (f"; {len(sm['corrections'])} year{'s' if len(sm['corrections']) != 1 else ''} the speakers did not say "
               f"{'were' if len(sm['corrections']) != 1 else 'was'} taken out of dates" if sm.get("corrections") else "")
            + (f"; {sm['votes_added']} formal vote{'s' if sm['votes_added'] != 1 else ''} the model left out "
               f"{'were' if sm['votes_added'] != 1 else 'was'} added from the transcript (motion and result as said)"
               if sm.get("votes_added") else "") + "."), S["small"]))

    # ---- people
    story += [Paragraph("2. People", S["h1"])]
    src = lambda s: ("Teams captions" if s.get("source") == "captions" else "named by hand" if s.get("source") == "manual"  # noqa: E731
                     else "said their own name" if s.get("source") == "said their name"
                     else "called by name by others" if s.get("source") == "called by name"
                     else "music or video played in the meeting (not a person)" if s.get("source") == "soundtrack"
                     else "initials on screen" if "initials" in (s.get("source") or "")
                     else "name label on screen" if s.get("source") else "voice only, not named")
    rows = [[Paragraph(h, S["cellb"]) for h in ("Speaker", "How identified", "Talk time", "Share", "Lines", "Confidence", "Voice sample")]]
    for s in sorted(d["speakers"], key=lambda s: -(s.get("talk_time_s") or 0)):
        lines = [t for t in d["turns"] if t["speaker"] == s["id"]]
        confs = [t["confidence"] for t in lines if t["confidence"] is not None]
        share = (s.get("talk_time_s") or 0) / total_talk
        v = s.get("voice")
        rows.append([Paragraph(_t(s["label"]), S["cellb"]), Paragraph(_t(src(s)), S["cell"]),
                     Paragraph(package.clock(s.get("talk_time_s")), S["cell"]),
                     Table([[Bar(share, width=24 * mm), Paragraph(f"{100 * share:.0f}%", S["cell"])]], colWidths=[26 * mm, 10 * mm],
                           style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1)]),
                     Paragraph(str(len(lines)), S["cell"]),
                     Paragraph(f"{100 * sum(confs) / len(confs):.0f}%" if confs else "—", S["cell"]),
                     Paragraph(_t(f"{v['seconds']} s, {_bytes(v['bytes'])}") if v else "—", S["cell"])])
    story.append(_table(rows, [30 * mm, 32 * mm, 16 * mm, 37 * mm, 13 * mm, 20 * mm, W - 2 * M - 148 * mm]))
    if d["participants"]:
        story.append(Paragraph("<b>Names seen on screen:</b> " + _t(", ".join(d["participants"])), S["small"]))

    # ---- quality and processing
    story += [Paragraph("3. Quality and processing", S["h1"])]
    tr = m.get("transcript", {})
    rows = [["Recognition confidence (mean word probability)", f"{100 * q['mean_confidence']:.1f}%" if q.get("mean_confidence") else "n/a"],
            ["Speech below 60% confidence", f"{100 * q['low_confidence_share']:.1f}%" if q.get("low_confidence_share") is not None else "n/a"],
            ["Language", f"{tr.get('language') or '—'} ({100 * (tr.get('language_probability') or 0):.0f}% sure)"],
            ["Transcript source", "Teams captions" if tr.get("source") == "captions" else f"speech recognition ({tr.get('model') or m['models'].get('asr')})"],
            ["Who spoke when", q.get("speaker_source", "—")],
            ["Speaker model / OCR / search", f"{m['models'].get('speaker_embedding')} / {m['models'].get('ocr')} / {m['models'].get('search')}"],
            ["Shared screens", f"{len(shared)} ({sum(1 for s in shared if s['kind'] == 'slide')} matched to a document page)"],
            ["Processing time", package.clock(m.get("timings_s", {}).get("total"))]]
    story.append(_table([[Paragraph(_t(a), S["cell"]), Paragraph(_t(b), S["cellb"])] for a, b in rows], [95 * mm, W - 2 * M - 95 * mm], header=False))
    stages = {"probe": "Reading file", "frames": "Sampling video", "names": "Names on screen", "screens": "Shared screens",
              "transcribe": "Speech to text", "speakers": "Separating speakers", "match": "Voices to names", "save": "Saving",
              "keep": "Images and voice samples"}
    tm = [(stages.get(k, k), v) for k, v in m.get("timings_s", {}).items() if k != "total"]
    if tm:
        story.append(Paragraph(_t("Time per step: " + " · ".join(f"{a} {v:.0f} s" for a, v in tm)), S["small"]))
    story.append(Paragraph("Confidence is the speech model's own certainty per word, not a measured error rate; lines below "
                           "60% are marked ° in the transcript.", S["small"]))

    # ---- storage
    story += [Paragraph("4. Storage", S["h1"])]
    rows = [[Paragraph(h, S["cellb"]) for h in ("Part", "Size", "Note")],
            [Paragraph("Original video", S["cell"]), Paragraph(_bytes(f["size"]), S["cell"]),
             Paragraph("deleted" if f["meta"].get("original_deleted") else "still on disk", S["cell"])],
            [Paragraph("Transcript (JSON + text)", S["cell"]), Paragraph(_bytes(st["transcript"]), S["cell"]), Paragraph("", S["cell"])],
            [Paragraph(f"Images ({len({s['image'] for s in d['snapshots']} | {s['image'] for s in shared if s.get('image')})} stored)", S["cell"]),
             Paragraph(_bytes(st["snapshots"]), S["cell"]),
             Paragraph("key frames of shared screens and snapshots; ≤ 2 MB. Slides matched to a document point to its page.", S["cell"])],
            [Paragraph("Voice samples", S["cell"]), Paragraph(_bytes(st["voices"]), S["cell"]), Paragraph("~10 s per speaker", S["cell"])],
            [Paragraph("<b>Kept in total</b>", S["cell"]), Paragraph(f"<b>{_bytes(st['total'])}</b>", S["cell"]),
             Paragraph(f'<font color="{GREEN.hexval()}"><b>{100 * reduction:.2f}% smaller, {_bytes(f["size"] - st["total"])} saved</b></font>'
                       if f["meta"].get("original_deleted") else
                       f'<b>{100 * reduction:.2f}% smaller:</b> {_bytes(f["size"] - st["total"])} can be saved once the video is '
                       f'deleted (offboarding does this after checking the transcript)', S["cell"])]]
    story.append(_table(rows, [55 * mm, 25 * mm, W - 2 * M - 80 * mm]))
    if sc:
        story.append(Paragraph(_t(f"Shortcuts pointing here save another {_bytes(f['size'] * len(sc))} "
                                  f"({len(sc)} cop{'y' if len(sc) == 1 else 'ies'} not stored)."), S["small"]))

    # ---- shared screens
    if shared:
        story += [PageBreak(), Paragraph("5. Shared screens", S["h1"]),
                  Paragraph("Every transcript line spoken over a screen points to this one image (stored once).", S["small"]), Spacer(1, 4)]
        for s in shared:
            p = (OUTPUT_DIR / "recordings" / str(file_id) / s["image"]) if s.get("image") else (OUTPUT_DIR / s["page_image"]) if s.get("page_image") else None
            im = _img(p, 62 * mm, 40 * mm) if p and p.exists() else None
            src_txt = (f"{s['source']['doc']}, slide {s['source']['page']}" + (f" · {s['source']['doc_no']} rev {s['source']['rev']}" if s["source"].get("doc_no") else "")
                       if s.get("source") else "not in any uploaded document: key frame from the video")
            txt = [Paragraph(_t(s.get("title") or "Shared screen"), S["cellb"]),
                   Paragraph(_t(f"{package.clock(s['t_start'])}–{package.clock(s['t_end'])} · {src_txt}"), S["cell"]),
                   Paragraph(_t(f"slide id {s['slide_uid']}") if s.get("slide_uid") else "", S["mono"]),
                   Paragraph(_t((lambda p: p if len(p) <= 420 else p[:420].rsplit(" ", 1)[0] + " …")(s.get("prompt") or "")), S["small"])]
            t = Table([[im or Paragraph("no image", S["small"]), txt]], colWidths=[66 * mm, W - 2 * M - 66 * mm], hAlign="LEFT")
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
            story.append(KeepTogether(t))

    # ---- transcript
    story += [PageBreak(), Paragraph(f"{6 if shared else 5}. Transcript", S["h1"]),
              Paragraph(_t(f"{len(d['turns'])} lines. Times are hh:mm:ss from the start of the recording. ° = recognition below 60%."), S["small"]),
              Spacer(1, 4)]
    by_idx = {s["idx"]: s for s in d["screens"]}
    snaps = {}
    for s in d["snapshots"]:
        snaps.setdefault(s["seg_idx"], []).append(s)
    cur, block = object(), None

    def flush():
        if block:
            story.append(Paragraph(f'<font color="{MUTED.hexval()}">{package.clock(block["start"])}</font>  <b>{_t(block["speaker"])}</b>  '
                                   + " ".join(block["parts"]), S["line"]))
            for sn in block["snaps"]:
                p = OUTPUT_DIR / "recordings" / str(file_id) / sn["image"]
                im = _img(p, 80 * mm, 45 * mm) if p.exists() else None
                if im:
                    story.append(KeepTogether([im, Paragraph(_t(f"Snapshot {package.clock(sn['t'])} · “{sn['phrase']}”"), S["small"]), Spacer(1, 4)]))
    for t in d["turns"]:
        s = by_idx.get(t["slide_idx"])
        key = package.screen_key(s)
        if key != cur:                              # the slide changed: show it here, once, with its picture
            cur = key
            if s and key:
                flush()
                block = None
                at = min(max(s["t_start"] or 0, 0), t["start"])
                head = Paragraph(_t(f"» Screen {package.clock(at)}: {s.get('title') or 'shared screen'}"
                                    + (f" — {s['source']['doc']}, slide {s['source']['page']}" if s.get("source") else "")
                                    + (f" · slide id {s['slide_uid']}" if s.get("slide_uid") else "")), S["screen"])
                p = ((OUTPUT_DIR / "recordings" / str(file_id) / s["image"]) if s.get("image")
                     else (OUTPUT_DIR / s["page_image"]) if s.get("page_image") else None)
                im = _img(p, 80 * mm, 48 * mm) if p and p.exists() else None
                story.append(KeepTogether([head, im, Spacer(1, 4)]) if im else head)
        low = t["confidence"] is not None and t["confidence"] < 0.6
        part = (f'<font color="{AMBER.hexval()}">{_t(t["text"])}°</font>' if low else _t(t["text"]))
        if block and block["speaker"] == t["speaker_name"] and t["start"] - block["end"] < 3:
            block["parts"].append(part)
            block["end"] = t["end"]
        else:
            flush()
            block = {"speaker": t["speaker_name"], "start": t["start"], "end": t["end"], "parts": [part], "snaps": []}
        block["snaps"] += snaps.get(t["idx"], [])
    flush()
    return story


def document_story(file_id, S, json_bytes):
    f = db.file_row(file_id)
    d = package.document_json(file_id)
    pages = db.q("SELECT id, page, text, slide_uid, image, thumb FROM pages WHERE file_id=? ORDER BY page", (file_id,))
    kept = sum(len((p["text"] or "").encode()) for p in pages)
    story = [Paragraph("DOCUMENT REPORT", S["eyebrow"]), Paragraph(_t(d.get("title") or f["name"]), S["title"]),
             Paragraph(_t(f"{f['name']} · folder {f['system']} · {len(pages)} slides/pages"
                          + (f" · {d['doc_no']}" if d.get("doc_no") else "") + (f" rev {d['rev']}" if d.get("rev") else "")), S["sub"]),
             _figures([(str(len(pages)), "slides / pages", None), (d.get("doc_no") or "—", "document number", None),
                       (d.get("rev") or "—", "revision", None), (_bytes(f["size"]), "file size", None)], S),
             Spacer(1, 8), Paragraph("Proof", S["h2"]),
             _proof(f, [[Paragraph("Distilled package", S["cellb"]),
                         Paragraph(_t(f"document JSON, printed complete in the appendix; SHA-256 {_sha(json_bytes)}; text {_bytes(kept)}"), S["mono"])]], S)]
    rep = dedup.report()
    fam = [x for x in rep["families"] if any(v["id"] == file_id for v in x["versions"])]
    shr = [x for x in rep["shared"] if file_id in (x["a"]["id"], x["b"]["id"])]
    sc = [c for g in rep["stored_once"] if g["master"]["id"] == file_id for c in g["copies"]]
    story.append(Paragraph("1. Copies, revisions and shared slides", S["h1"]))
    if not (fam or shr or sc):
        story.append(Paragraph("No copies, revisions or shared slides found in the library.", S["body"]))
    for c in sc:
        story.append(Paragraph(_t(f"Copy stored as a shortcut: {c['folder']} / {c['name']} ({c['how']}, {_bytes(c['size'])} not stored)"), S["body"]))
    for x in fam:
        for v in x["versions"]:
            ch = [s["page"] for s in v["slides"] if s["state"] == "changed"]
            story.append(Paragraph(_t(f"Revision family {x['doc_no'] or ''}: {v['name']} rev {v['rev'] or '—'} — "
                                      + (f"changed slides {', '.join(map(str, ch))}" if ch else "unchanged / first version")), S["body"]))
    for x in shr:
        mine, other = (x["a"], x["b"]) if x["a"]["id"] == file_id else (x["b"], x["a"])
        pages_mine = x["a_pages"] if x["a"]["id"] == file_id else x["b_pages"]
        story.append(Paragraph(_t(f"Shares {x['shared']} slides with {other['name']} ({other['system']}): slides {', '.join(map(str, pages_mine))}"), S["body"]))
    story += [PageBreak(), Paragraph("2. Slides", S["h1"])]
    for p in pages:
        ip = OUTPUT_DIR / (p["thumb"] or p["image"]) if (p["thumb"] or p["image"]) else None
        im = _img(ip, 55 * mm, 36 * mm) if ip and ip.exists() else None
        txt = [Paragraph(_t(f"Slide {p['page']}"), S["cellb"]), Paragraph(_t(f"slide id {p['slide_uid']}"), S["mono"]),
               Paragraph(_t((p["text"] or "").strip()[:1200]).replace("\n", "<br/>"), S["small"])]
        t = Table([[im or Paragraph("no image (text only)", S["small"]), txt]], colWidths=[59 * mm, W - 2 * M - 59 * mm], hAlign="LEFT")
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
        story.append(KeepTogether(t))
    return story


def _json_text(data, list_key):
    """The complete JSON: readable top level, one compact line per transcript line / page (fewer pages)."""
    rest = {k: v for k, v in data.items() if k != list_key}
    head = json.dumps(rest, indent=1, ensure_ascii=False)
    items = data.get(list_key) or []
    body = ",\n".join("  " + json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in items)
    return head[:-2] + f',\n "{list_key}": [\n{body}\n ]\n}}' if items else head


def _appendix(data, list_key, S):
    text = _json_text(data, list_key)
    out = [PageBreak(), Paragraph("Appendix: the complete JSON", S["h1"]),
           Paragraph(_t("Everything above in the AI-ready format, complete and unabridged: copy it from this PDF into any "
                        "search index, RAG pipeline or language model."), S["small"]), Spacer(1, 4)]
    lines = []
    width = int((W - 2 * M) / (0.6 * 6.3)) - 2  # characters that fit the page in the 6.3 pt monospace font (~128)
    for ln in text.splitlines():  # wrap long lines (a transcript line is one JSON object)
        while len(ln) > width:
            lines.append(ln[:width])
            ln = "    " + ln[width:]
        lines.append(ln)
    for k in range(0, len(lines), 80):  # chunks, so the JSON flows over pages
        out.append(Preformatted("\n".join(lines[k:k + 80]), S["pre"]))
    return out


def build(file_id):
    """PDF bytes of the report. A shortcut gets the report of the file it points to."""
    f = library.master_of(db.file_row(file_id))
    S = styles()
    if f["kind"] == "video":
        data = package.ai_json(f["id"])
        name = "transcript.json"
    else:
        data = package.document_json(f["id"])
        name = "document.json"
    json_bytes = json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8")
    story = recording_story(f["id"], S, json_bytes) if f["kind"] == "video" else document_story(f["id"], S, json_bytes)
    story += _appendix(data, "transcript" if f["kind"] == "video" else "pages", S)
    buf = io.BytesIO()
    _doc(buf, f["name"], f["sha256"]).build(story)
    db.audit("report", f["id"], bytes=len(buf.getvalue()))
    return buf.getvalue(), f
