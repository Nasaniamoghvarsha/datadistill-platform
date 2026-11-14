"""Command line: datadistill [--data-dir D] [--output-dir O] serve | scan | process-all | reprocess | export"""
import argparse
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="datadistill")
    ap.add_argument("--data-dir", help="input files, one folder per system (default: ./data)")
    ap.add_argument("--output-dir", help="database, packages, reports (default: ./output)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="start the web app on 127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("scan", help="register files found under the data folder")
    sub.add_parser("process-all", help="process every new file (documents first)")
    r = sub.add_parser("reprocess", help="re-run processing for files already in the library")
    r.add_argument("--kind", choices=["video", "document"], default=None)
    sub.add_parser("export", help="rewrite transcript.json / transcript.md of every recording")
    e = sub.add_parser("extracts", help="(re)build snapshots + voice samples of transcribed recordings (no re-transcription)")
    e.add_argument("ids", nargs="*", type=int, help="file ids (default: all whose original video is still there)")
    o = sub.add_parser("offboard", help="plan an offboarding of a leaver's Microsoft 365 OneDrive (dry run; nothing is changed)")
    o.add_argument("upn", help="the leaver's address, e.g. alex.leaver@contoso.example")
    ac = sub.add_parser("accuracy", help="compare a transcript with a human reference (.vtt/.srt/.json/.txt, or a timestamped transcript .pdf): WER, speakers, names")
    ac.add_argument("file_id", type=int)
    ac.add_argument("reference")
    ac.add_argument("--collar", type=float, default=0.25, help="seconds ignored around reference turn boundaries (DER)")
    ac.add_argument("--offset", default="auto",
                    help="seconds to add to reference times, or 'auto' (default): find where the recording sits in the reference")
    qu = sub.add_parser("questions", help="question test: are the answers still in the distilled output?")
    qu.add_argument("file_id", type=int)
    qu.add_argument("questions", help="questions.json written from the original recording")
    qu.add_argument("--no-ai", action="store_true", help="skip the local AI answers")
    am = sub.add_parser("ami-reference", help="AMI corpus words/*.words.xml -> reference .vtt")
    am.add_argument("words_dir")
    am.add_argument("meeting")
    am.add_argument("out")
    a = ap.parse_args(argv)

    # must be set before datadistill.config is imported
    if a.data_dir:
        os.environ["DD_DATA_DIR"] = os.path.abspath(a.data_dir)
    if a.output_dir:
        os.environ["DD_OUTPUT_DIR"] = os.path.abspath(a.output_dir)

    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("datadistill.api:app", host="127.0.0.1", port=a.port)
        return 0

    if a.cmd == "ami-reference":
        from pathlib import Path
        from . import evaluate
        print(evaluate.ami_reference(Path(a.words_dir), a.meeting, Path(a.out)), "reference turns written to", a.out)
        return 0

    from . import db, library, pipeline
    db.init()
    if a.cmd in ("accuracy", "questions"):
        from pathlib import Path
        from . import evaluate, questions
        res = (evaluate.run(a.file_id, Path(a.reference), a.collar, a.offset) if a.cmd == "accuracy"
               else questions.run(a.file_id, Path(a.questions), use_ai=not a.no_ai))
        sys.stdout.reconfigure(encoding="utf-8")
        print(evaluate.markdown(res))
        print("saved:", *res["files"], sep="\n  ")
        return 0
    if a.cmd == "scan":
        print("added", len(library.scan()), "files")
    elif a.cmd in ("process-all", "reprocess"):
        library.scan()
        if a.cmd == "process-all":
            sql = "SELECT id, name FROM files WHERE status IN ('new','queued','error') AND kind IN ('document','video')"
            params = ()
        else:
            sql = "SELECT id, name FROM files WHERE status NOT IN ('removed','quarantined') AND kind IN ('document','video')"
            params = ()
            if a.kind:
                sql += " AND kind=?"
                params = (a.kind,)
        for row in db.q(sql + " ORDER BY CASE kind WHEN 'document' THEN 0 ELSE 1 END, id", params):
            print(f"== {row['name']}")
            pipeline.process_file(row["id"])
    elif a.cmd == "export":
        from . import package
        for row in db.q("SELECT file_id FROM recordings"):
            print(package.write_outputs(row["file_id"]))
    elif a.cmd == "extracts":
        import time
        from . import package
        ids = a.ids or [r["file_id"] for r in db.q("SELECT file_id FROM recordings ORDER BY file_id")]
        for fid in ids:
            t0 = time.time()
            print(f"== {fid} {db.file_row(fid)['name']}")
            print("  ", pipeline.keep_extracts(fid), f"{time.time() - t0:.1f} s")
            package.write_outputs(fid)
    elif a.cmd == "offboard":
        import json
        from . import m365
        p = m365.plan(a.upn)
        print(json.dumps({"plan_id": p["id"], "summary": p["summary"], "files": [
            {k: i[k] for k in ("name", "action", "reason")} for i in p["items"]]}, indent=1))
        print("Dry run only. Approve and run the plan in the web interface (Offboarding).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
