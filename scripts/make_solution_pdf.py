"""Build docs/DataDistill-Solution.pdf from docs/SOLUTION.md (reportlab, local, no network).

    python scripts/make_solution_pdf.py

Handles the small Markdown subset SOLUTION.md uses: headings, paragraphs, bullets, numbered lists, tables,
code blocks, rules, **bold**, *italic*, `code` and [links](url). Uses Calibri and Consolas from Windows when present,
otherwise the built-in Helvetica / Courier.
"""
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "SOLUTION.md"
OUT = ROOT / "docs" / "DataDistill-Solution.pdf"
FONTS = Path("C:/Windows/Fonts")

ACCENT = colors.HexColor("#0a66e6")
INK = colors.HexColor("#14181f")
MUTED = colors.HexColor("#5b6572")
LINE = colors.HexColor("#d9dee5")
SOFT = colors.HexColor("#f2f5f9")


def fonts():
    try:
        pdfmetrics.registerFont(TTFont("Body", str(FONTS / "calibri.ttf")))
        pdfmetrics.registerFont(TTFont("Body-B", str(FONTS / "calibrib.ttf")))
        pdfmetrics.registerFont(TTFont("Body-I", str(FONTS / "calibrii.ttf")))
        pdfmetrics.registerFont(TTFont("Body-BI", str(FONTS / "calibriz.ttf")))
        pdfmetrics.registerFont(TTFont("Mono", str(FONTS / "consola.ttf")))
        pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-B", italic="Body-I", boldItalic="Body-BI")
        return "Body", "Body-B", "Mono"
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Courier"


BODY, BOLD, MONO = fonts()

ST = {
    "p": ParagraphStyle("p", fontName=BODY, fontSize=10.2, leading=14.2, textColor=INK, spaceAfter=5, alignment=TA_LEFT),
    "h1": ParagraphStyle("h1", fontName=BOLD, fontSize=24, leading=28, textColor=INK, spaceAfter=6),
    "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=15.5, leading=19, textColor=ACCENT, spaceBefore=14, spaceAfter=6),
    "h3": ParagraphStyle("h3", fontName=BOLD, fontSize=12, leading=15, textColor=INK, spaceBefore=9, spaceAfter=4),
    "li": ParagraphStyle("li", fontName=BODY, fontSize=10.2, leading=14, textColor=INK, spaceAfter=2),
    "cell": ParagraphStyle("cell", fontName=BODY, fontSize=8.8, leading=11.2, textColor=INK),
    "cellh": ParagraphStyle("cellh", fontName=BOLD, fontSize=8.8, leading=11.2, textColor=colors.white),
    "code": ParagraphStyle("code", fontName=MONO, fontSize=7.4, leading=9.2, textColor=INK),
}


def inline(t):
    t = escape(t)
    t = re.sub(r"`([^`]+)`", lambda m: f'<font name="{MONO}" size="9" color="#0b3d91">{m.group(1)}</font>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<font color="#0a66e6">{m.group(1)}</font>', t)
    return t


def table(rows):
    head, body = rows[0], rows[1:]
    n = len(head)
    data = [[Paragraph(inline(c), ST["cellh"]) for c in head]]
    for r in body:
        r = (r + [""] * n)[:n]
        data.append([Paragraph(inline(c), ST["cell"]) for c in r])
    width = A4[0] - 36 * mm
    # width by content: columns get a share of the page proportional to their longest text (min 10%)
    lens = [max(len(re.sub(r"[`*]", "", str(r[i]))) for r in rows if i < len(r)) for i in range(n)]
    w = [max(0.10, min(0.55, (l + 6) / (sum(lens) + 6 * n))) for l in lens]
    s = sum(w)
    t = Table(data, colWidths=[width * x / s for x in w], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return [t, Spacer(1, 7)]


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def build():
    lines = SRC.read_text(encoding="utf-8").splitlines()
    flow, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            box = Table([[Preformatted("\n".join(code), ST["code"])]], colWidths=[A4[0] - 36 * mm])
            box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.4, LINE),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
            flow += [box, Spacer(1, 8)]
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.fullmatch(r"\|[\s:|-]+\|?", lines[i]):
                    rows.append(split_row(lines[i]))
                i += 1
            flow += table(rows)
        elif ln.startswith("# "):
            flow += [Paragraph(inline(ln[2:]), ST["h1"]), HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=8)]
            i += 1
        elif ln.startswith("## "):
            flow.append(Paragraph(inline(ln[3:]), ST["h2"]))
            i += 1
        elif ln.startswith("### "):
            flow.append(Paragraph(inline(ln[4:]), ST["h3"]))
            i += 1
        elif ln.strip() == "---":
            flow += [Spacer(1, 3), HRFlowable(width="100%", thickness=0.5, color=LINE, spaceAfter=3)]
            i += 1
        elif re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            ordered = bool(re.match(r"^\s*\d+\.", ln))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(ListItem(Paragraph(inline(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i])), ST["li"]), leftIndent=14))
                i += 1
            flow += [ListFlowable(items, bulletType="1" if ordered else "bullet", start=1 if ordered else "•",
                                  bulletFontName=BODY, bulletFontSize=9.5, leftIndent=14), Spacer(1, 4)]
        else:
            para = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||```|---|\s*([-*]|\d+\.)\s)", lines[i]):
                para.append(lines[i])
                i += 1
            flow.append(Paragraph(inline(" ".join(para)), ST["p"]))
    return flow


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(BODY, 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "DataDistill - the whole story")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def main():
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="DataDistill: the whole story", author="DataDistill team",
                            subject="Ericsson Data Storage Optimization hackathon, 13-15 Nov 2026")
    doc.build(build(), onFirstPage=footer, onLaterPages=footer)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
