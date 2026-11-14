"""Check processed recordings against the hand-checked expectations in expected.json.

    python benchmarks/zoom-cases/check_cases.py            (finds the recordings by file name in the library)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from datadistill import db, package, summary  # noqa: E402

CANT_END = set("the a an to of and for with our we i is are was were some that this in on at by from my your their "
               "it's i'm we're you're if so but or as like".split())


def mid_sentence_share(turns):
    """Speaker changes right after a word that cannot end a sentence, with < 0.3 s pause: certainly wrong."""
    changes = bad = 0
    for a, b in zip(turns, turns[1:]):
        if a["speaker"] != b["speaker"] and a["text"].strip():
            changes += 1
            last = re.sub(r"[^a-z']", "", a["text"].strip().split()[-1].lower())
            if last in CANT_END and b["start"] - a["end"] < 0.3:
                bad += 1
    return bad / max(1, changes), bad, changes


def check(case, exp, fid):
    d = package.load_recording(fid)
    turns = d["turns"]
    names = {s["id"]: (s.get("name") or None) for s in d["speakers"]}
    rows = []

    def row(what, ok, detail):
        rows.append((what, ok, detail))

    for c in exp.get("line_speaker", []):
        t = next((t for t in turns if c["line"].lower() in t["text"].lower()), None)
        got = names.get(t["speaker"]) if t else "line not found"
        row(f"speaker of \"{c['line'][:40]}\"", t is not None and got in c["allowed"], f"{got}  (allowed: {c['allowed']})")
    for pat in exp.get("forbidden_name_patterns", []):
        bad = [n for n in names.values() if n and re.search(pat, n)]
        row(f"no name matching /{pat}/", not bad, ", ".join(bad) or "none")
    if "allowed_names_only" in exp:
        ok_first = {n.lower() for n in exp["allowed_names_only"]}
        bad = [n for n in names.values() if n and n.split()[0].lower() not in ok_first]
        row("only real participants named", not bad, ", ".join(bad) or f"{sum(1 for n in names.values() if n)} named, all real")
    shared = [s for s in d["screens"] if s["kind"] != "non_slide"]
    if "shared_screens_min" in exp:
        row(f"shared screens >= {exp['shared_screens_min']}", len(shared) >= exp["shared_screens_min"], str(len(shared)))
    if "shared_after_s" in exp:
        late = [s for s in shared if s["t_start"] >= exp["shared_after_s"]]
        row(f"shared screens after {package.clock(exp['shared_after_s'])} >= {exp['shared_after_min']}",
            len(late) >= exp["shared_after_min"], str(len(late)))
    if "slide_titles_any_of" in exp:
        text = " ".join((s.get("text") or "") for s in shared).lower()
        found = [t for t in exp["slide_titles_any_of"] if t.lower() in text]
        row(f"slide titles found >= {exp['slide_titles_min']}", len(found) >= exp["slide_titles_min"], "; ".join(found))
    if "decisions_must_mention" in exp:
        sm = summary.get(fid, create=False) or {}
        dec = " ".join(x.get("decision", "") for x in sm.get("decisions") or []).lower()
        for w in exp["decisions_must_mention"]:
            row(f"decisions mention '{w}'", w in dec, "no summary yet" if not sm else (dec[:120] or "no decisions"))
    if "mid_sentence_changes_max" in exp:
        share, bad, n = mid_sentence_share(turns)
        row(f"mid-sentence speaker changes <= {exp['mid_sentence_changes_max']:.0%}", share <= exp["mid_sentence_changes_max"],
            f"{bad} of {n} ({share:.0%})")
    print(f"\n{case}: {exp['file']} (file {fid})")
    for what, ok, detail in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {what:48} {detail}")
    return sum(ok for _, ok, _ in rows), len(rows)


def main():
    db.init()
    exp = json.loads((Path(__file__).parent / "expected.json").read_text(encoding="utf-8"))["cases"]
    total = passed = 0
    for case, e in exp.items():
        f = db.q1("SELECT id FROM files WHERE name=? AND kind='video' ORDER BY id DESC", (e["file"],))
        if not f or not db.q1("SELECT 1 FROM recordings WHERE file_id=?", (f["id"],)):
            print(f"\n{case}: {e['file']} not processed in this library; skipped")
            continue
        p, n = check(case, e, f["id"])
        passed, total = passed + p, total + n
    print(f"\n{passed} of {total} checks passed")


if __name__ == "__main__":
    main()
