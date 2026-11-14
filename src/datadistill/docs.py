"""Documents: split PDF/PPTX into slides, hash every slide, extract metadata (UC-D2, SD5).

PDF via pypdfium2 (Apache/BSD). NOT PyMuPDF (AGPL). PPTX via python-pptx (text) and,
if LibreOffice is installed, rendered through a PDF conversion for images/pHash.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from . import db, diagrams, store
from .config import DATA_DIR, FRAME_WIDTH, IMAGE_EXT, OUTPUT_DIR
from .textutil import extract_meta, normalize_text, ocr_image, phash_hex, text_hash

RENDER_W = FRAME_WIDTH
THUMB_W = 320


def _render_image(path: Path):
    """A picture file as a one-page document: (pages, meta). No text layer, so the page is read by OCR later."""
    from PIL import ImageOps
    with Image.open(path) as im:
        im.seek(0)                                    # animated GIF / multi-frame TIFF: the first picture
        img = ImageOps.exif_transpose(im).convert("RGB")
    if img.width != RENDER_W:
        img = img.resize((RENDER_W, max(1, round(img.height * RENDER_W / img.width))), Image.LANCZOS)
    return [(img, "")], {}


def _render_pdf(path: Path):
    doc = pdfium.PdfDocument(path)
    meta = {k: v for k, v in (doc.get_metadata_dict() or {}).items() if v}
    pages = []
    for i in range(len(doc)):
        pg = doc[i]
        w = pg.get_width()
        img = pg.render(scale=RENDER_W / w).to_pil().convert("RGB")
        tp = pg.get_textpage()
        text = tp.get_text_range() or ""
        pages.append((img, text.replace("\r\n", "\n").replace("\r", "\n")))
    return pages, meta


def _pptx_texts(path: Path):
    from pptx import Presentation

    prs = Presentation(str(path))
    cp = prs.core_properties
    meta = {"Title": cp.title, "Author": cp.author, "Subject": cp.subject,
            "Revision": str(cp.revision or ""), "Modified": str(cp.modified or "")}
    texts = []
    for slide in prs.slides:
        parts = []
        for shp in slide.shapes:
            if shp.has_text_frame:
                parts += [p.text for p in shp.text_frame.paragraphs if p.text.strip()]
        texts.append("\n".join(parts))
    return texts, {k: v for k, v in meta.items() if v}


def _soffice():
    return shutil.which("soffice") or next(
        (str(p) for p in Path(r"C:\Program Files\LibreOffice\program").glob("soffice.exe")), None)


def _is_font_table(text: str) -> bool:
    """'Characters for Embedded characters: !"#$%&'()*+,./0123…ABC…' : the font page some PDF exports append."""
    t = (text or "").strip()
    if t.lower().startswith("characters for embedded"):
        return True
    run = "".join(chr(c) for c in range(0x21, 0x7f))
    return len(t) < 1500 and any(run[i:i + 30] in t.replace(" ", "") for i in range(0, len(run) - 30, 10))


MAX_VISION_PAGES = 12          # vision reads of picture pages per document (each takes a while on a laptop GPU)


def _structure_diagrams(path: Path, ext: str):
    """{page number: diagram} read exactly from the file's own structure (PPTX connectors, PDF vector drawing), and the
    set of PDF pages that look like diagrams but could not be read that way (candidates for the vision model)."""
    found, weak = {}, set()
    try:
        if ext == ".pptx":
            from pptx import Presentation
            for i, slide in enumerate(Presentation(str(path)).slides, 1):
                d = diagrams.from_pptx_slide(slide)
                if d:
                    found[i] = d
        elif ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                for i, pg in enumerate(pdf.pages, 1):
                    if diagrams.looks_like_diagram(pg):
                        d = diagrams.from_pdf_page(pg)
                        if d:
                            found[i] = d
                        else:
                            weak.add(i)
    except Exception:        # a diagram that cannot be read is simply plain text, never an error
        pass
    return found, weak


def _picture_diagram_candidate(text: str) -> bool:
    """A page of short labels (no running text) read by OCR: the shape of a diagram that only exists as a picture."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 5:
        return False
    words = [len(ln.split()) for ln in lines]
    return sum(words) / len(words) <= 3.5 and max(words) <= 8


def process_document(file_id: int, progress=None):
    f = db.file_row(file_id)
    path = DATA_DIR / f["path"]
    out = OUTPUT_DIR / "pages" / str(file_id)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    ext = path.suffix.lower()
    if ext == ".pdf":
        rendered, meta = _render_pdf(path)
    elif ext in IMAGE_EXT:
        rendered, meta = _render_image(path)
    elif ext == ".pptx":
        texts, meta = _pptx_texts(path)
        rendered = [(None, t) for t in texts]
        so = _soffice()
        if so:  # render via PDF for images + pHash
            with tempfile.TemporaryDirectory() as td:
                subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", td, str(path)],
                               check=True, capture_output=True, timeout=300)
                pdf = next(Path(td).glob("*.pdf"))
                imgs, _ = _render_pdf(pdf)
                rendered = [(imgs[i][0] if i < len(imgs) else None, t) for i, t in enumerate(texts)]
    else:
        raise ValueError(f"unsupported document type {ext}")

    all_text = []
    rows = []
    skipped = []
    diag, weak = _structure_diagrams(path, ext)
    vision_left = MAX_VISION_PAGES
    for i, (img, text) in enumerate(rendered, 1):
        if _is_font_table(text):            # a PowerPoint export artefact, not a slide anyone shows
            skipped.append(i)
            continue
        image = thumb = None
        ph = ""
        if img is not None:
            if len(normalize_text(text)) < 5:  # image-only page -> OCR
                text = ocr_image(img)
            ph = phash_hex(img)
            ip, tpth = out / f"p{i:04d}.webp", out / f"t{i:04d}.webp"
            img.save(ip, quality=80)
            t = img.copy(); t.thumbnail((THUMB_W, THUMB_W)); t.save(tpth, quality=70)
            image, thumb = ip.relative_to(OUTPUT_DIR).as_posix(), tpth.relative_to(OUTPUT_DIR).as_posix()
        norm = normalize_text(text)
        uid = store.assign_uid(norm, ph, image, "document")
        d = diag.get(i)
        if not d and img is not None and vision_left > 0 and (i in weak or _picture_diagram_candidate(text)):
            vision_left -= 1
            d = diagrams.from_image(img, text)
        rows.append((file_id, i, uid, text, norm, text_hash(norm), ph, image, thumb, json.dumps(d) if d else None))
        all_text.append(text)
        if progress:
            progress(i / max(1, len(rendered)))

    with db.tx() as c:
        c.execute("DELETE FROM pages WHERE file_id=?", (file_id,))
        c.executemany("INSERT INTO pages(file_id,page,slide_uid,text,text_norm,text_hash,phash,image,thumb,diagram) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    found = extract_meta("\n".join(all_text[:3]) + "\n" + json.dumps(meta))
    info = {"pages": len(rows), "skipped_pages": skipped, "diagram_pages": [r[1] for r in rows if r[9]],
            "diagram_methods": sorted({json.loads(r[9])["method"] for r in rows if r[9]}), "title": meta.get("Title") or path.stem, "author": meta.get("Author", ""),
            "created": meta.get("CreationDate", meta.get("Modified", "")),
            "doc_no": found.get("doc_no", ""), "rev": found.get("rev", meta.get("Revision", "")),
            "label": found.get("label", ""), "rendered": rows[0][7] is not None if rows else False}
    db.set_meta(file_id, **info)
    return info
