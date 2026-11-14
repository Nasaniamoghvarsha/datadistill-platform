"""Diagram slides: flowcharts, architecture drawings, org charts, anything with boxes and arrows.

OCR gives the words of such a slide but loses what matters: which box points to which. Three readers, best first:
  pptx    PowerPoint stores real connectors between shapes (begin / end shape and the arrowhead): exact.
  pdf     A PDF exported from PowerPoint keeps the drawing as vector lines, boxes and arrowheads with the text
          positions: the graph is rebuilt geometrically (pdfplumber).
  vision  A picture (a scanned page, a frame of a shared screen): the local vision model (Ministral 3 8B, Mistral
          AI, Apache-2.0) reads boxes and arrows. Its answer is checked: every box label must be text that is really
          on the slide, every arrow must join two known boxes; anything else is dropped.

Result (JSON-able): {"kind", "method", "nodes": [{id, label}], "edges": [{from, to, label, directed}],
                     "text": "A -> B (label)" lines, "mermaid": "flowchart LR ...", "dropped": n}
Boxes are numbered n1..nN. "directed": False means a line without an arrowhead.
"""
import base64
import io
import json
import re

from rapidfuzz import fuzz

MIN_NODES = 2

# ------------------------------------------------------------------ shared: graph -> text
def finish(nodes, edges, method, kind="diagram", dropped=0):
    """Number the boxes, drop repeated arrows, and write the readable text and the Mermaid code."""
    ids = {}
    out_nodes = []
    for n in nodes:
        lab = " ".join((n.get("label") or "").split())
        if lab and lab not in ids:
            ids[lab] = f"n{len(ids) + 1}"
            out_nodes.append({"id": ids[lab], "label": lab})
    seen, out_edges = set(), []
    for e in edges:
        a, b = " ".join((e.get("from") or "").split()), " ".join((e.get("to") or "").split())
        if a not in ids or b not in ids or a == b:
            dropped += 1
            continue
        key = (ids[a], ids[b], e.get("label", ""), bool(e.get("directed", True)))
        rev = (ids[b], ids[a], e.get("label", ""), bool(e.get("directed", True)))
        if key in seen or (not e.get("directed", True) and rev in seen):
            continue
        seen.add(key)
        out_edges.append({"from": ids[a], "to": ids[b], "label": " ".join((e.get("label") or "").split()),
                          "directed": bool(e.get("directed", True))})
    label = {n["id"]: n["label"] for n in out_nodes}
    lines = [f"{label[e['from']]} {'->' if e['directed'] else '--'} {label[e['to']]}" + (f" ({e['label']})" if e["label"] else "")
             for e in out_edges]
    mm = ["flowchart LR"] + [f'  {n["id"]}["{n["label"].replace(chr(34), chr(39))}"]' for n in out_nodes]
    mm += [f'  {e["from"]} {"-->" if e["directed"] else "---"}{("|" + e["label"].replace("|", "/") + "|") if e["label"] else ""} {e["to"]}'
           for e in out_edges]
    return {"kind": kind, "method": method, "nodes": out_nodes, "edges": out_edges, "text": "\n".join(lines),
            "mermaid": "\n".join(mm), "dropped": dropped}


def usable(d):
    return bool(d) and len(d["nodes"]) >= MIN_NODES and len(d["edges"]) >= 1


# ------------------------------------------------------------------ pptx: exact connectors
def from_pptx_slide(slide):
    """Graph of one python-pptx slide from its connectors, or None."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
    from pptx.shapes.connector import Connector

    shapes = []

    def walk(shs):
        for s in shs:
            if s.shape_type == MSO_SHAPE_TYPE.GROUP:
                walk(s.shapes)
            else:
                shapes.append(s)
    walk(slide.shapes)
    conns = [s for s in shapes if isinstance(s, Connector)]
    if not conns:
        return None
    nodes = {}
    for s in shapes:
        if isinstance(s, Connector) or not getattr(s, "has_text_frame", False):
            continue
        if s.is_placeholder and s.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.SUBTITLE):
            continue
        text = " ".join(p.text.strip() for p in s.text_frame.paragraphs if p.text.strip())
        if text:
            nodes[s.shape_id] = {"label": text, "box": (s.left, s.top, s.left + s.width, s.top + s.height),
                                 "textbox": s.shape_type == MSO_SHAPE_TYPE.TEXT_BOX}

    def near(x, y, tol=int(0.35 * 914400)):
        best, bd = None, tol
        for sid, n in nodes.items():
            l, t, r, b = n["box"]
            d = max(l - x, 0, x - r) ** 2 + max(t - y, 0, y - b) ** 2
            d = d ** 0.5
            if d <= bd and not n["textbox"]:
                best, bd = sid, d
        return best

    edges, used = [], set()
    for c in conns:
        el = c._element
        st = el.xpath(".//a:stCxn/@id")
        en = el.xpath(".//a:endCxn/@id")
        a = int(st[0]) if st else near(c.begin_x, c.begin_y)
        b = int(en[0]) if en else near(c.end_x, c.end_y)
        if a not in nodes or b not in nodes:
            continue
        head = bool(el.xpath(".//a:headEnd[@type and @type!='none']"))
        tail = bool(el.xpath(".//a:tailEnd[@type and @type!='none']"))
        mx, my = (c.begin_x + c.end_x) / 2, (c.begin_y + c.end_y) / 2
        label = ""
        for sid, n in nodes.items():
            l, t, r, bt = n["box"]
            if n["textbox"] and sid not in (a, b) and l - 0.3 * 914400 <= mx <= r + 0.3 * 914400 and t - 0.3 * 914400 <= my <= bt + 0.3 * 914400 \
                    and len(n["label"].split()) <= 6:
                label = n["label"]
                used.add(sid)
                break
        if head and tail:
            edges += [{"from": nodes[a]["label"], "to": nodes[b]["label"], "label": label, "directed": True},
                      {"from": nodes[b]["label"], "to": nodes[a]["label"], "label": label, "directed": True}]
        elif head:
            edges.append({"from": nodes[b]["label"], "to": nodes[a]["label"], "label": label, "directed": True})
        else:
            edges.append({"from": nodes[a]["label"], "to": nodes[b]["label"], "label": label, "directed": bool(tail)})
    boxes = [{"label": n["label"]} for sid, n in nodes.items() if sid not in used]
    d = finish(boxes, edges, "pptx connectors", kind=_kind(boxes, edges))
    return d if usable(d) else None


# ------------------------------------------------------------------ pdf: vector geometry
def _rect_dist(px, py, b):
    return (max(b[0] - px, 0, px - b[2]) ** 2 + max(b[1] - py, 0, py - b[3]) ** 2) ** 0.5


def from_pdf_page(pl_page, tol=9.0):
    """Graph of one pdfplumber page from its vector boxes, lines, arrowheads and words, or None."""
    W, H = float(pl_page.width), float(pl_page.height)
    words = pl_page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=3, y_tolerance=3)
    if len(words) < 2:
        return None

    def bbox(o):
        return (float(o["x0"]), float(o["top"]), float(o["x1"]), float(o["bottom"]))

    boxes, lines, heads = [], [], []
    for r in pl_page.rects:
        b = bbox(r)
        w, h = b[2] - b[0], b[3] - b[1]
        if w * h > 0.6 * W * H:
            continue                                   # the page background
        if min(w, h) < 2.5 and max(w, h) > 12:
            lines.append([((b[0] + b[2]) / 2, b[1]), ((b[0] + b[2]) / 2, b[3])] if h > w else [(b[0], (b[1] + b[3]) / 2), (b[2], (b[1] + b[3]) / 2)])
        elif w >= 24 and h >= 12:
            boxes.append(b)
    for c in pl_page.curves:
        b = bbox(c)
        w, h = b[2] - b[0], b[3] - b[1]
        pts = [(float(x), float(y)) for x, y in c.get("pts", [])]
        if w * h > 0.6 * W * H:
            continue
        if c.get("fill") and w <= 16 and h <= 16 and 3 <= len(pts) <= 6:
            heads.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
        elif w >= 24 and h >= 12 and len(pts) >= 4 and (c.get("fill") or c.get("stroke")) and \
                abs(pts[0][0] - pts[-1][0]) + abs(pts[0][1] - pts[-1][1]) < 4:
            boxes.append(b)                            # a closed shape: rounded box, diamond, ellipse
        elif len(pts) >= 2 and (w >= 8 or h >= 8):
            lines.append(pts)
    for l in pl_page.lines:
        pts = [(float(x), float(y)) for x, y in l.get("pts", [])]
        lines.append(pts if len(pts) >= 2 else [(float(l["x0"]), float(l["top"])), (float(l["x1"]), float(l["bottom"]))])

    # a box and its text: the innermost box around the words; boxes without text are dropped
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    nodes = []
    taken = set()
    for b in boxes:
        inside = [i for i, w_ in enumerate(words) if i not in taken and b[0] - 1 <= (w_["x0"] + w_["x1"]) / 2 <= b[2] + 1
                  and b[1] - 1 <= (w_["top"] + w_["bottom"]) / 2 <= b[3] + 1]
        if not inside:
            continue
        taken.update(inside)
        ws = sorted((words[i] for i in inside), key=lambda w_: (round(w_["top"] / 6), w_["x0"]))
        nodes.append({"label": " ".join(w_["text"] for w_ in ws), "box": b})
    if len(nodes) < MIN_NODES:
        return None
    # a table is a grid of touching cells with ruled lines between them, not boxes joined by connectors: real diagram
    # boxes have gaps. More than a third of the boxes touching another one -> not a diagram.
    def touches(a, b, g=2.5):
        return a[0] - g <= b[2] and b[0] - g <= a[2] and a[1] - g <= b[3] and b[1] - g <= a[3]
    touching = sum(1 for i, n in enumerate(nodes) if any(touches(n["box"], m["box"]) for j, m in enumerate(nodes) if j != i))
    if touching / len(nodes) > 0.34:
        return None

    def node_at(p):
        best, bd = None, tol
        for n in nodes:
            d = _rect_dist(p[0], p[1], n["box"])
            if d <= bd:
                best, bd = n, d
        return best

    # join line pieces that touch into polylines (elbow connectors). Never at a box: two arrows leaving the same
    # side of a box are two arrows, not one bent line
    poly = [list(p) for p in lines if len(p) >= 2]
    merged = True
    while merged:
        merged = False
        for i in range(len(poly)):
            for j in range(i + 1, len(poly)):
                a, b = poly[i], poly[j]
                for ea, eb, mk in ((a[-1], b[0], lambda: a + b), (a[-1], b[-1], lambda: a + b[::-1]),
                                   (a[0], b[-1], lambda: b + a), (a[0], b[0], lambda: b[::-1] + a)):
                    if abs(ea[0] - eb[0]) + abs(ea[1] - eb[1]) <= 3.0 and not node_at(ea):
                        poly[i] = mk()
                        poly.pop(j)
                        merged = True
                        break
                if merged:
                    break
            if merged:
                break

    def has_head(p):
        return any(abs(h[0] - p[0]) + abs(h[1] - p[1]) <= 14 for h in heads)

    edges = []
    label_words = [w_ for i, w_ in enumerate(words) if i not in taken]
    for pl in poly:
        a, b = node_at(pl[0]), node_at(pl[-1])
        if not a or not b or a is b:
            continue
        mid = pl[len(pl) // 2] if len(pl) % 2 else ((pl[len(pl) // 2 - 1][0] + pl[len(pl) // 2][0]) / 2, (pl[len(pl) // 2 - 1][1] + pl[len(pl) // 2][1]) / 2)
        near = [w_ for w_ in label_words if abs((w_["x0"] + w_["x1"]) / 2 - mid[0]) <= 38 and abs((w_["top"] + w_["bottom"]) / 2 - mid[1]) <= 14]
        label = " ".join(w_["text"] for w_ in sorted(near, key=lambda w_: w_["x0"]))
        h0, h1 = has_head(pl[0]), has_head(pl[-1])
        if h0 and h1:
            edges += [{"from": a["label"], "to": b["label"], "label": label, "directed": True},
                      {"from": b["label"], "to": a["label"], "label": label, "directed": True}]
        elif h0:
            edges.append({"from": b["label"], "to": a["label"], "label": label, "directed": True})
        else:
            edges.append({"from": a["label"], "to": b["label"], "label": label, "directed": h1})
    d = finish([{"label": n["label"]} for n in nodes], edges, "pdf vector geometry", kind=_kind(nodes, edges))
    return d if usable(d) else None


def _kind(nodes, edges):
    """Flowchart if some box has a question or two outgoing arrows; org chart if it is a tree; else a general diagram."""
    labels = [n["label"] for n in nodes]
    if any(l.rstrip().endswith("?") for l in labels):
        return "flowchart"
    pairs = {(e["from"], e["to"]) for e in edges}
    if any((b, a) in pairs for a, b in pairs):
        return "architecture"                       # two-way arrows: components that talk to each other
    out = {}
    for e in edges:
        out.setdefault(e["from"], []).append(e["to"])
    indeg = {}
    for e in edges:
        indeg[e["to"]] = indeg.get(e["to"], 0) + 1
    if edges and all(v <= 1 for v in indeg.values()) and any(len(v) >= 2 for v in out.values()):
        return "tree / org chart"
    return "diagram"


# ------------------------------------------------------------------ vision: pictures
SCHEMA = {"type": "object", "properties": {
    "kind": {"type": "string"},
    "nodes": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}},
    "edges": {"type": "array", "items": {"type": "object", "properties": {
        "from": {"type": "string"}, "to": {"type": "string"}, "label": {"type": "string"}, "directed": {"type": "boolean"}},
        "required": ["from", "to"]}}},
    "required": ["nodes", "edges"]}

PROMPT = ("This is a slide that contains a diagram (flowchart, architecture, org chart, process or similar). "
          "List every box / node with its exact text as written on the slide. Then list every arrow or connecting line "
          "as an edge from one box to another, copying the box texts exactly; for an arrow use the arrow's direction "
          "(from the tail to the arrowhead), set directed=false for a line without an arrowhead, and put the text written "
          "next to an arrow in label. Use only text that is visible. Do not invent boxes or arrows. "
          "kind is one of: flowchart, architecture, org chart, sequence, process, diagram.")


def _png(img, max_side=1344):
    img = img.convert("RGB")
    s = max_side / max(img.size)
    if s < 1:
        img = img.resize((int(img.width * s), int(img.height * s)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def from_image(img, slide_text="", model=None, ollama="http://127.0.0.1:11434", timeout=300):
    """Graph of a picture from the vision model, checked against the slide's real text. None if the model is not
    available or finds nothing consistent. `slide_text` = OCR or PDF text of the same picture."""
    import requests
    from . import summary
    ok, _ = summary.available()
    if not ok:
        return None
    try:
        r = requests.post(f"{ollama}/api/chat", timeout=timeout, json={
            "model": model or summary.MODEL, "stream": False, "format": SCHEMA, "keep_alive": "2m",
            "messages": [{"role": "user", "content": PROMPT, "images": [_png(img)]}],
            "options": {"temperature": 0.0, "seed": 7, "num_ctx": 16384}})
        r.raise_for_status()
        raw = json.loads(r.json()["message"]["content"])
    except Exception:
        return None
    return check(raw, slide_text, "vision (Ministral 3 8B)")


def _words(t):
    return set(re.findall(r"[a-zà-ÿ0-9]{3,}", (t or "").lower()))


def check(raw, slide_text, method):
    """Keep only boxes whose words really occur on the slide (>= 60% of the box's words, fuzzy), and arrows between kept
    boxes. A model that invents a box is thus caught; `dropped` counts what was removed."""
    text_words = _words(slide_text)
    if not text_words:
        return None
    keep, dropped = {}, 0

    def known(label):
        ws = _words(label)
        if not ws:
            return False
        hit = sum(1 for w in ws if w in text_words or any(fuzz.ratio(w, t) >= 85 for t in text_words))
        return hit / len(ws) >= 0.6

    for n in raw.get("nodes", []):
        lab = " ".join((n.get("label") or "").split())
        if lab and known(lab):
            keep[lab.lower()] = lab
        else:
            dropped += 1
    nodes = [{"label": v} for v in keep.values()]
    edges = []
    for e in raw.get("edges", []):
        a, b = keep.get(" ".join((e.get("from") or "").split()).lower()), keep.get(" ".join((e.get("to") or "").split()).lower())
        if a and b:
            edges.append({"from": a, "to": b, "label": e.get("label") or "", "directed": e.get("directed", True) is not False})
        else:
            dropped += 1
    d = finish(nodes, edges, method, kind=raw.get("kind") or _kind(nodes, edges), dropped=dropped)
    if not usable(d):
        return None
    if method.startswith("vision"):
        # a model asked for a diagram will find one on any slide with boxes: demand the shape of a real one (at least
        # 3 boxes and 2 arrows, most boxes joined to something), otherwise a text slide would get invented arrows
        joined = {e["from"] for e in d["edges"]} | {e["to"] for e in d["edges"]}
        if len(d["nodes"]) < 3 or len(d["edges"]) < 2 or len(joined) < 0.6 * len(d["nodes"]):
            return None
    return d


# ------------------------------------------------------------------ which pages are worth reading as diagrams
def looks_like_diagram(pl_page):
    """Cheap test on a pdfplumber page: several drawn boxes or arrowheads, and not mostly running text."""
    try:
        shapes = len(pl_page.rects) + len(pl_page.curves)
        return shapes >= 4 and len(pl_page.lines) + len(pl_page.curves) + len(pl_page.rects) >= 6
    except Exception:
        return False
