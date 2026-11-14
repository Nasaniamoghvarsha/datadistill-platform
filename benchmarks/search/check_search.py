"""Measures search: the new hybrid (rank fusion + multilingual model + re-ranker) against the ranking used until 12 Nov.

    python benchmarks/search/check_search.py [--file NAME]

Runs against whatever library is configured (DD_DATA_DIR / DD_OUTPUT_DIR) and needs the recording named in queries.json
to be processed there. A hit counts if the line it opens is within window_s seconds of the time the answer is said.
Reported per query kind: found at rank 1, in the top 3, in the top 10, and mean reciprocal rank (MRR).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from datadistill import db, search  # noqa: E402

db.init()
spec = json.loads((Path(__file__).parent / "queries.json").read_text(encoding="utf-8"))
f = db.q1("SELECT id FROM files WHERE name=?", (spec["file"],))
if not f:
    sys.exit(f"{spec['file']} is not in this library")


def rank_of(results, t):
    for i, h in enumerate(results, 1):
        if h.get("file_id") == f["id"] and h.get("t") is not None and abs(h["t"] - t) <= spec["window_s"]:
            return i
    return None


def run(name, fn):
    rows = defaultdict(list)
    detail = []
    for q in spec["queries"]:
        r = rank_of(fn(q["q"]), q["t"])
        rows[q["kind"]].append(r)
        rows["ALL"].append(r)
        detail.append((q["q"], r))
    return rows, detail


def summarize(rows):
    out = {}
    for kind, rs in rows.items():
        n = len(rs)
        out[kind] = {"n": n, "top1": sum(1 for r in rs if r == 1), "top3": sum(1 for r in rs if r and r <= 3),
                     "top10": sum(1 for r in rs if r and r <= 10), "mrr": round(sum(1 / r for r in rs if r) / n, 3)}
    return out


old_rows, old_detail = run("legacy", lambda q: search.search_legacy(q, k=10))
new_rows, new_detail = run("new", lambda q: search.search(q, k=10)["results"])
no_rr_rows, _ = run("no rerank", lambda q: search.search(q, k=10, rerank=False)["results"])
so, sn, sr = summarize(old_rows), summarize(new_rows), summarize(no_rr_rows)
print(f"{'query kind':18} {'n':>3} | {'12 Nov ranking':^26} | {'rank fusion only':^26} | {'new (fusion + re-ranker)':^26}")
print(f"{'':18} {'':>3} | {'top1':>5}{'top3':>6}{'top10':>6}{'MRR':>7} | {'top1':>5}{'top3':>6}{'top10':>6}{'MRR':>7} | {'top1':>5}{'top3':>6}{'top10':>6}{'MRR':>7}")
for kind in [k for k in sn if k != "ALL"] + ["ALL"]:
    a, b, c = so[kind], sr[kind], sn[kind]
    print(f"{kind:18} {a['n']:>3} | {a['top1']:>5}{a['top3']:>6}{a['top10']:>6}{a['mrr']:>7} | {b['top1']:>5}{b['top3']:>6}{b['top10']:>6}{b['mrr']:>7} | {c['top1']:>5}{c['top3']:>6}{c['top10']:>6}{c['mrr']:>7}")
print()
for (q, ro), (_, rn) in zip(old_detail, new_detail):
    print(f"  {q[:62]:62}  12 Nov: {ro or '-':>3}   new: {rn or '-':>3}")
