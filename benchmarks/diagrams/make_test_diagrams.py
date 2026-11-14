"""Builds three test diagrams with known contents (flowchart, architecture, org chart) as PPTX (real connectors),
PDF (vector drawing, like a PowerPoint export) and PNG (picture), plus truth.json.

    python benchmarks/diagrams/make_test_diagrams.py            (writes into benchmarks/diagrams/files/)
    python benchmarks/diagrams/check_diagrams.py                 (scores the readers)
"""
import json
import math
from pathlib import Path

OUT = Path(__file__).parent / "files"
OUT.mkdir(exist_ok=True)

# name: (title, boxes {id: (label, col, row, shape)}, edges [(from, to, label, style)])
# style: "->" arrow, "<->" arrow at both ends, "--" plain line
CASES = {
    "flowchart": ("Order processing", {
        "a": ("Start", 0, 0, "oval"), "b": ("Check stock?", 1, 0, "diamond"), "c": ("Pack order", 2, 0, "box"),
        "d": ("Ship", 3, 0, "box"), "e": ("Back-order", 1, 1, "box")},
        [("a", "b", "", "->"), ("b", "c", "yes", "->"), ("c", "d", "", "->"), ("b", "e", "no", "->"), ("e", "c", "", "->")]),
    "architecture": ("Web architecture", {
        "a": ("Browser", 0, 1, "box"), "b": ("API Gateway", 1, 1, "box"), "c": ("Auth Service", 2, 0, "box"),
        "d": ("Orders Service", 2, 2, "box"), "e": ("Database", 3, 2, "box")},
        [("a", "b", "HTTPS", "<->"), ("b", "c", "", "->"), ("b", "d", "", "->"), ("d", "e", "SQL", "->")]),
    "orgchart": ("Engineering organisation", {
        "a": ("CEO", 1, 0, "box"), "b": ("CTO", 0, 1, "box"), "c": ("CFO", 2, 1, "box"),
        "d": ("Dev Lead", 0, 2, "box"), "e": ("QA Lead", 1, 2, "box")},
        [("a", "b", "", "--"), ("a", "c", "", "--"), ("b", "d", "", "--"), ("b", "e", "", "--")]),
}
CELL_W, CELL_H, BOX_W, BOX_H, X0, Y0 = 170, 100, 120, 44, 50, 90      # points; page is 720 x 405
DIAMOND = 12                                                            # a decision diamond sticks out this far


def pos(col, row):
    return X0 + col * CELL_W, Y0 + row * CELL_H


def make_pdf(name, title, boxes, edges):
    from reportlab.lib.colors import HexColor, black
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(OUT / f"{name}.pdf"), pagesize=(720, 405))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(40, 405 - 50, title)
    geo = {}
    for k, (label, col, row, shape) in boxes.items():
        x, y = pos(col, row)
        y = 405 - y - BOX_H                                             # PDF origin is bottom-left
        geo[k] = (x, y, BOX_W, BOX_H)
        c.setStrokeColor(HexColor("#1f4e79"))
        c.setFillColor(HexColor("#dbe8f5"))
        c.setLineWidth(1.5)
        if shape == "box":
            c.rect(x, y, BOX_W, BOX_H, stroke=1, fill=1)
        elif shape == "oval":
            c.roundRect(x, y, BOX_W, BOX_H, 18, stroke=1, fill=1)
        else:
            p = c.beginPath()
            p.moveTo(x + BOX_W / 2, y + BOX_H + DIAMOND)
            p.lineTo(x + BOX_W + DIAMOND, y + BOX_H / 2)
            p.lineTo(x + BOX_W / 2, y - DIAMOND)
            p.lineTo(x - DIAMOND, y + BOX_H / 2)
            p.close()
            c.drawPath(p, stroke=1, fill=1)
        c.setFillColor(black)
        c.setFont("Helvetica", 11)
        c.drawCentredString(x + BOX_W / 2, y + BOX_H / 2 - 4, label)

    def edge_pts(a, b):
        ax, ay, aw, ah = geo[a]
        bx, by, bw, bh = geo[b]
        ea = DIAMOND if boxes[a][3] == "diamond" else 0
        eb = DIAMOND if boxes[b][3] == "diamond" else 0
        acx, acy, bcx, bcy = ax + aw / 2, ay + ah / 2, bx + bw / 2, by + bh / 2
        if abs(bcx - acx) >= abs(bcy - acy):
            if bcx > acx:
                return (ax + aw + ea, acy), (bx - eb, bcy)
            return (ax - ea, acy), (bx + bw + eb, bcy)
        if bcy > acy:                                                   # target is higher up (PDF y grows upwards)
            return (acx, ay + ah + ea), (bcx, by - eb)
        return (acx, ay - ea), (bcx, by + bh + eb)

    def head(p, q):                                                     # filled triangle at q, coming from p
        ang = math.atan2(q[1] - p[1], q[0] - p[0])
        s = 9
        pts = [q, (q[0] - s * math.cos(ang - .4), q[1] - s * math.sin(ang - .4)),
               (q[0] - s * math.cos(ang + .4), q[1] - s * math.sin(ang + .4))]
        path = c.beginPath()
        path.moveTo(*pts[0])
        path.lineTo(*pts[1])
        path.lineTo(*pts[2])
        path.close()
        c.setFillColor(HexColor("#1f4e79"))
        c.drawPath(path, stroke=0, fill=1)

    for a, b, label, style in edges:
        p, q = edge_pts(a, b)
        c.setStrokeColor(HexColor("#1f4e79"))
        c.setLineWidth(1.5)
        c.line(p[0], p[1], q[0], q[1])
        if style in ("->", "<->"):
            head(p, q)
        if style == "<->":
            head(q, p)
        if label:
            c.setFillColor(black)
            c.setFont("Helvetica", 10)
            c.drawString((p[0] + q[0]) / 2 + 5, (p[1] + q[1]) / 2 + 5, label)
    c.showPage()
    c.save()


def make_pptx(name, title, boxes, edges):
    from lxml import etree
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.util import Emu, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(9144000), Emu(5143500)
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = title
    k = 12700                                                           # 1 pt in EMU
    shp = {}
    for key, (label, col, row, shape) in boxes.items():
        x, y = pos(col, row)
        kind = {"box": MSO_SHAPE.RECTANGLE, "oval": MSO_SHAPE.ROUNDED_RECTANGLE, "diamond": MSO_SHAPE.FLOWCHART_DECISION}[shape]
        pad = DIAMOND if shape == "diamond" else 0
        sp = s.shapes.add_shape(kind, Emu((x - pad) * k), Emu((y - pad) * k), Emu((BOX_W + 2 * pad) * k), Emu((BOX_H + 2 * pad) * k))
        sp.text_frame.text = label
        run = sp.text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0, 0, 0)
        sp.fill.solid()
        sp.fill.fore_color.rgb = RGBColor(0xdb, 0xe8, 0xf5)
        shp[key] = sp
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    for a, b, label, style in edges:
        A, B = shp[a], shp[b]
        c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, 0, 0, 0)
        # connection sites of a rectangle: 0 top, 1 left, 2 bottom, 3 right
        if abs(B.left - A.left) >= abs(B.top - A.top):
            ai, bi = (3, 1) if B.left > A.left else (1, 3)
        else:
            ai, bi = (2, 0) if B.top > A.top else (0, 2)
        c.begin_connect(A, ai)
        c.end_connect(B, bi)
        ln = c.line._get_or_add_ln()
        if style in ("->", "<->"):
            etree.SubElement(ln, ns + "tailEnd", type="triangle")
        if style == "<->":
            etree.SubElement(ln, ns + "headEnd", type="triangle")
        if label:
            mx, my = (c.begin_x + c.end_x) // 2, (c.begin_y + c.end_y) // 2
            tb = s.shapes.add_textbox(Emu(mx - 200000), Emu(my - 230000), Emu(400000), Emu(200000))
            tb.text_frame.text = label
            tb.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
    prs.save(str(OUT / f"{name}.pptx"))


def make_png(name):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(OUT / f"{name}.pdf"))
    pdf[0].render(scale=2.2).to_pil().convert("RGB").save(OUT / f"{name}.png")


if __name__ == "__main__":
    truth = {}
    for name, (title, boxes, edges) in CASES.items():
        make_pdf(name, title, boxes, edges)
        make_pptx(name, title, boxes, edges)
        make_png(name)
        truth[name] = {"nodes": [b[0] for b in boxes.values()],
                       "edges": [{"from": boxes[a][0], "to": boxes[b][0], "label": lab, "directed": st != "--", "both": st == "<->"}
                                 for a, b, lab, st in edges]}
    (OUT / "truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
    print("written:", sorted(p.name for p in OUT.iterdir()))
