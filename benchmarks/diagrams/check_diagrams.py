"""Scores the diagram readers on the test diagrams (see make_test_diagrams.py).

    python benchmarks/diagrams/check_diagrams.py [--no-vision]

Per diagram and reader: box recall / precision, arrow recall / precision (an arrow is right only with the right
direction; a plain line counts in either direction), arrow labels found. Vision needs Ollama with ministral-3:8b.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from datadistill import diagrams  # noqa: E402

FILES = Path(__file__).parent / "files"
truth = json.loads((FILES / "truth.json").read_text(encoding="utf-8"))


def norm(s):
    return " ".join((s or "").lower().split())


def edge_set(edges, labels=False):
    out = set()
    for e in edges:
        a, b = norm(e["from"]), norm(e["to"])
        out.add((a, b, norm(e.get("label")) if labels else ""))
        if not e.get("directed", True):
            out.add((b, a, norm(e.get("label")) if labels else ""))
        if e.get("both"):
            out.add((b, a, norm(e.get("label")) if labels else ""))
    return out


def score(name, d):
    t = truth[name]
    if not d:
        return {"boxes": "none read", "arrows": "none read"}
    got_nodes = {norm(n["label"]) for n in d["nodes"]}
    want_nodes = {norm(n) for n in t["nodes"]}
    label = {n["id"]: n["label"] for n in d["nodes"]}
    got = [{"from": label[e["from"]], "to": label[e["to"]], "label": e["label"], "directed": e["directed"]} for e in d["edges"]]
    want_e, got_e = edge_set(t["edges"]), edge_set(got)
    want_l, got_l = edge_set(t["edges"], True), edge_set(got, True)
    labelled = [e for e in t["edges"] if e["label"]]
    return {"boxes": f"{len(got_nodes & want_nodes)}/{len(want_nodes)} found, {len(got_nodes - want_nodes)} extra",
            "arrows": f"{len(want_e & got_e)}/{len(want_e)} right, {len(got_e - want_e)} extra",
            "labels": f"{sum(1 for e in labelled if (norm(e['from']), norm(e['to']), norm(e['label'])) in got_l)}/{len(labelled)}"}


def main(vision=True):
    import pdfplumber
    from pptx import Presentation
    from PIL import Image
    rows = []
    for name in truth:
        prs = Presentation(str(FILES / f"{name}.pptx"))
        rows.append((name, "pptx connectors", score(name, diagrams.from_pptx_slide(prs.slides[0]))))
        with pdfplumber.open(FILES / f"{name}.pdf") as pdf:
            page = pdf.pages[0]
            text = page.extract_text() or ""
            rows.append((name, "pdf vector", score(name, diagrams.from_pdf_page(page))))
        if vision:
            import time
            t = time.time()
            d = diagrams.from_image(Image.open(FILES / f"{name}.png"), text)
            rows.append((name, f"vision picture ({time.time() - t:.0f} s)", score(name, d)))
    print(f"{'diagram':14} {'reader':26} {'boxes':26} {'arrows (with direction)':28} arrow labels")
    for name, reader, s in rows:
        print(f"{name:14} {reader:26} {s['boxes']:26} {s['arrows']:28} {s.get('labels', '')}")


if __name__ == "__main__":
    main(vision="--no-vision" not in sys.argv)
