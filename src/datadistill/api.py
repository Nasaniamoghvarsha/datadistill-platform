"""FastAPI backend. Bound to 127.0.0.1 only: meeting data never leaves the machine.

UI tabs: Upload (folders, upload with duplicate check, progress) · Transcripts (dialogue, context, snapshots,
voice samples, exports) · Search · Offboarding (folder run) · Storage (metrics).
"""
import json
import time
from pathlib import Path
from typing import List, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response

from . import db, dedup, graph, graph_mock, jobs, library, m365, package, people, search, storage
from .config import OUTPUT_DIR, ROOT

app = FastAPI(title="DataDistill", version="0.2")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(graph_mock.router)      # the local stand-in for Microsoft Graph (used when the connection mode is "mock")


@app.on_event("startup")
def _startup():
    db.init()
    library.scan()
    jobs.start()
    search.warm_up()


def _get_file(file_id):
    f = db.file_row(file_id)
    if not f:
        raise HTTPException(404, "File not found")
    return f


def _bad(e):
    raise HTTPException(400, str(e))


_qc = {"t": 0.0, "q": []}


def _queue_cache():
    """The job queue, computed at most once per second (a file list asks for every file's place in line)."""
    if time.time() - _qc["t"] > 1.0:
        _qc.update(t=time.time(), q=jobs.queue())
    return _qc["q"]


def _file_out(f):
    f = dict(f)
    if isinstance(f.get("meta"), str):
        f["meta"] = json.loads(f["meta"] or "{}")
    f["folder"] = f["system"]
    if f["status"] == "shortcut":
        m = library.master_of(db.file_row(f["id"]))
        f["master"] = {"id": m["id"], "name": m["name"], "folder": m["system"], "status": m["status"]} if m else None
    j = db.q1("SELECT id, status, stage, progress, started_at, created_at FROM jobs WHERE file_id=? ORDER BY id DESC LIMIT 1",
              (f["id"],))
    if j and j["status"] == "queued":  # place in the line (1 = next)
        j["position"] = next((k + 1 for k, q in enumerate(_queue_cache()) if q["id"] == j["id"]), None)
    f["job"] = j
    return f


# ------------------------------------------------------------------ folders
@app.get("/api/folders")
def get_folders():
    return library.folders()


@app.post("/api/folders")
def new_folder(name: str = Body(..., embed=True)):
    try:
        return {"name": library.create_folder(name)}
    except ValueError as e:
        _bad(e)


@app.patch("/api/folders/{name}")
def rename_folder(name: str, new_name: str = Body(..., embed=True)):
    try:
        n = library.rename_folder(name, new_name)
        search.invalidate()
        return {"name": n}
    except (ValueError, OSError) as e:
        _bad(e)


@app.get("/api/folders/{name}/contents")
def folder_contents(name: str):
    """What deleting the folder would remove (shown in the confirmation)."""
    return library.folder_contents(name)


@app.delete("/api/folders/{name}")
def remove_folder(name: str, with_files: bool = False):
    """Delete a folder; with_files=true also deletes everything in it for good (files, transcripts, images, entries)."""
    try:
        out = library.delete_folder(name, with_files=with_files)
    except ValueError as e:
        raise HTTPException(409, str(e))
    for fid in out.get("requeued", []):
        jobs.enqueue("process", fid)
    search.invalidate()
    return {"ok": True, **out}


@app.get("/api/folders/{name}/export")
def export_folder(name: str):
    return Response(package.folder_zip(name), media_type="application/zip",
                    headers=_disp(f"{name} - transcripts.zip"))


# ------------------------------------------------------------------ files
@app.get("/api/files")
def list_files(folder: Optional[str] = None):
    sql, p = "SELECT * FROM files WHERE status != 'removed'", []
    if folder:
        sql += " AND system=?"
        p.append(folder)
    return [_file_out(f) for f in db.q(sql + " ORDER BY system, name", p)]


@app.post("/api/files")
def upload(folder: str = Form(...), files: List[UploadFile] = File(...)):
    """Identical content already in the library is not stored again: it becomes a shortcut."""
    ids, shortcuts, notes = [], [], []
    batch = jobs.new_batch()  # this upload is processed as one batch, after every earlier upload (FIFO)
    todo = []
    try:
        for uf in files:
            fid, how = library.save_upload(folder, uf.filename, uf.file)
            ids.append(fid)
            if how == "shortcut":
                shortcuts.append(fid)
                continue
            f = db.file_row(fid)
            if f["kind"] in ("video", "document"):
                todo.append(f)
            elif f["kind"] == "transcript":  # Teams captions added later: re-process the matching video
                stem = Path(f["path"]).with_suffix("").as_posix()
                for v in db.q("SELECT id, path, name, status, meta FROM files WHERE kind='video' AND system=?", (f["system"],)):
                    if Path(v["path"]).with_suffix("").as_posix() == stem:
                        if v["status"] == "shortcut" or json.loads(v["meta"] or "{}").get("original_deleted"):
                            notes.append(f"{uf.filename} was saved but not applied: the original of {v['name']} was deleted, "
                                         "so it cannot be transcribed again. Its existing transcript is unchanged.")
                        else:
                            todo.append(db.file_row(v["id"]))
    except ValueError as e:
        _bad(e)
    # queued only after the whole upload is saved: documents of this upload are then ready before its recordings
    for f in sorted(todo, key=lambda f: (f["kind"] != "document", f["id"])):
        jobs.enqueue("process", f["id"], batch=batch)
    return {"added": ids, "shortcuts": shortcuts, "notes": notes, "batch": batch}


@app.post("/api/files/check-duplicates")
def check_duplicates(items: list = Body(..., embed=True)):
    """Before uploading: which files already exist (by size, then by SHA-256 computed in the browser)."""
    return library.check_duplicates(items)


@app.post("/api/files/shortcut")
def add_shortcut(folder: str = Body(...), name: str = Body(...), master_id: int = Body(...)):
    """'I still need it': a shortcut to the existing copy instead of a second copy (no upload, no processing)."""
    try:
        fid = library.create_shortcut(folder, name, master_id)
    except ValueError as e:
        _bad(e)
    return _file_out(db.file_row(fid))


@app.post("/api/files/{file_id}/same-recording")
def resolve_same_recording(file_id: int, action: str = Body(..., embed=True)):
    """A file found to be the same recording as an existing one (re-encoded copy):
    'shortcut' -> replace it by a shortcut (its bytes are deleted); 'keep' -> transcribe it as a separate recording."""
    f = _get_file(file_id)
    m = f["meta"].get("similar_to")
    if f["status"] != "duplicate" or not m:
        raise HTTPException(400, "This file is not marked as the same recording as another one")
    if action == "shortcut":
        try:
            library.to_shortcut(file_id, m["id"])
        except ValueError as e:
            _bad(e)
        search.invalidate()
        return _file_out(db.file_row(file_id))
    if action == "keep":
        db.set_meta(file_id, keep_separate=True)
        db.ex("UPDATE files SET status='new' WHERE id=?", (file_id,))
        db.audit("keep_separate", file_id, similar_to=m["id"])
        return {"job_id": jobs.enqueue("process", file_id)}
    raise HTTPException(400, "action must be 'shortcut' or 'keep'")


@app.post("/api/scan")
def scan():
    added = library.scan()
    for fid in added:
        if db.file_row(fid)["kind"] in ("video", "document"):
            jobs.enqueue("process", fid)
    return {"added": added}


@app.post("/api/files/{file_id}/process")
def process(file_id: int):
    f = _get_file(file_id)
    if f["kind"] not in ("video", "document"):
        raise HTTPException(400, "Only recordings and documents can be processed")
    if f["status"] == "shortcut":
        raise HTTPException(400, "A shortcut has no file of its own: the original is processed")
    if f["meta"].get("original_deleted"):
        raise HTTPException(400, "The original was deleted after distillation; the transcript is kept")
    return {"job_id": jobs.enqueue("process", file_id)}


@app.post("/api/files/{file_id}/move")
def move(file_id: int, folder: str = Body(..., embed=True)):
    _get_file(file_id)
    try:
        library.move_file(file_id, folder)
    except ValueError as e:
        _bad(e)
    search.invalidate()
    return _file_out(db.file_row(file_id))


@app.delete("/api/files/{file_id}")
def remove(file_id: int):
    _get_file(file_id)
    library.remove_from_library(file_id)
    search.invalidate()
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    j = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not j:
        raise HTTPException(404)
    return j


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    j = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not j or j["status"] not in ("queued", "running"):
        # e.g. a file processed by the command line: there is no job here to stop, so do not report "Cancelled"
        raise HTTPException(409, "Nothing to cancel: this job has already finished")
    if j["status"] == "queued":
        db.ex("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
        if j["file_id"]:
            db.ex("UPDATE files SET status='new' WHERE id=? AND status='queued'", (j["file_id"],))
    else:
        db.ex("UPDATE jobs SET cancel=1 WHERE id=?", (job_id,))
    return {"ok": True}


# ------------------------------------------------------------------ transcripts
def _content_id(file_id):
    """A shortcut shows the content of the file it points to."""
    f = _get_file(file_id)
    m = library.master_of(f) if f["status"] == "shortcut" else f
    if not m:
        raise HTTPException(404, "The original of this shortcut is no longer in the library")
    return m["id"], (f if f["status"] == "shortcut" else None)


@app.get("/api/recordings/{file_id}")
def recording(file_id: int):
    cid, sc = _content_id(file_id)
    d = package.load_recording(cid)
    if not d:
        raise HTTPException(404, "Not transcribed yet")
    d["file"] = _file_out(d["file"])
    d["shortcut"] = _file_out(sc) if sc else None
    d["storage"] = {**storage.distilled_parts(cid), "original": d["file"]["size"],
                    "original_on_disk": (library.DATA_DIR / d["file"]["path"]).exists()}
    return d


@app.get("/api/recordings/{file_id}/files/{rel:path}")
def recording_file(file_id: int, rel: str):
    """Snapshot images and voice samples."""
    cid, _ = _content_id(file_id)
    base = (OUTPUT_DIR / "recordings" / str(cid)).resolve()
    p = (base / rel).resolve()
    if base not in p.parents or not p.is_file() or p.parent.name not in ("snapshots", "voices"):
        raise HTTPException(404)
    mt = {".webp": "image/webp", ".ogg": "audio/ogg"}.get(p.suffix.lower(), "application/octet-stream")
    return FileResponse(p, media_type=mt, headers={"Cache-Control": "no-cache"})


@app.get("/api/recordings/{file_id}/summary")
def get_summary(file_id: int):
    """The meeting summary (local model, checked against the transcript), if made; and whether one can be made."""
    from . import summary
    cid, _ = _content_id(file_id)
    ok, msg = summary.available()
    job = db.q1("SELECT id, status, error FROM jobs WHERE file_id=? AND kind='summary' ORDER BY id DESC LIMIT 1", (cid,))
    return {"summary": summary.get(cid, create=False), "available": ok, "message": msg, "job": job}


@app.post("/api/recordings/{file_id}/ask")
def ask_meeting(file_id: int, question: str = Body(..., embed=True)):
    """Answer a question from this meeting's distilled output (local model), with source lines and a check that
    every number and name in the answer occurs in the record."""
    from . import answer, summary
    cid, _ = _content_id(file_id)
    d = package.load_recording(cid)
    if not d:
        raise HTTPException(404, "Not transcribed yet")
    ok, msg = summary.available()
    if not ok:
        raise HTTPException(400, msg)
    q = (question or "").strip()
    if not q:
        raise HTTPException(400, "Ask a question")
    r = answer.ask(d, d["turns"], q[:500])
    db.audit("ask", cid, question=q[:200], verified=r["verified"])
    return {**r, "model": summary.MODEL_NAME}


@app.post("/api/recordings/{file_id}/summary")
def make_summary(file_id: int):
    from . import summary
    cid, _ = _content_id(file_id)
    ok, msg = summary.available()
    if not ok:
        raise HTTPException(400, msg)
    p = summary._cache_path(cid)
    if p.exists():
        p.unlink()  # write it again
    return {"job_id": jobs.enqueue("summary", cid)}


@app.get("/api/pages/{page_id}/image")
def page_image(page_id: int, thumb: bool = False):
    """Image of a document page (the source of truth a matched slide points to)."""
    p = db.q1("SELECT image, thumb FROM pages WHERE id=?", (page_id,))
    rel = p and (p["thumb"] if thumb else p["image"])
    if not rel or not (OUTPUT_DIR / rel).is_file():
        raise HTTPException(404)
    return FileResponse(OUTPUT_DIR / rel, media_type="image/webp", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/recordings/{file_id}/extracts")
def rebuild_extracts(file_id: int):
    """Snapshots + voice samples for a recording transcribed before they existed (no re-transcription)."""
    f = _get_file(file_id)
    if f["status"] == "shortcut" or f["meta"].get("original_deleted"):
        raise HTTPException(400, "The original video is needed")
    return {"job_id": jobs.enqueue("extras", file_id)}


@app.patch("/api/recordings/{file_id}/speakers")
def rename_speakers(file_id: int, mapping: dict = Body(...)):
    rec = db.q1("SELECT speakers FROM recordings WHERE file_id=?", (file_id,))
    if not rec:
        raise HTTPException(404)
    sp = json.loads(rec["speakers"] or "{}")
    for sid, name in mapping.items():
        if sid in sp:
            name = (name or "").strip()
            sp[sid].update(name=name or None, source="manual" if name else None, score=None)
    db.ex("UPDATE recordings SET speakers=? WHERE file_id=?", (json.dumps(sp), file_id))
    db.audit("rename_speakers", file_id, mapping=mapping)
    package.write_outputs(file_id)
    search.invalidate()
    return package.load_recording(file_id)["speakers"]


@app.patch("/api/segments/{seg_id}")
def edit_segment(seg_id: int, text: str = Body(..., embed=True)):
    g = db.q1("SELECT * FROM segments WHERE id=?", (seg_id,))
    if not g:
        raise HTTPException(404)
    db.ex("UPDATE segments SET text=?, edited=1, original_text=COALESCE(original_text, ?) WHERE id=?",
          (text.strip(), g["text"], seg_id))
    db.audit("edit_segment", g["file_id"], before=g["text"], after=text)
    package.write_outputs(g["file_id"])
    search.invalidate()
    return {"ok": True}


@app.get("/api/documents/{file_id}")
def document(file_id: int):
    cid, sc = _content_id(file_id)
    f = _get_file(cid)
    if f["status"] != "processed":
        raise HTTPException(404, "Not processed yet")
    d = package.document_json(cid)
    d["file"] = _file_out(f)
    d["shortcut"] = _file_out(sc) if sc else None
    return d


def _disp(name, inline=False):
    """Content-Disposition that survives any file name (accents, Nordic letters, anything outside latin-1)."""
    from urllib.parse import quote
    plain = package.to_ascii(name).replace('"', "'").replace("\\", "_")
    return {"Content-Disposition": f"{'inline' if inline else 'attachment'}; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"}


@app.get("/api/export/{file_id}")
def export(file_id: int, format: str = "json"):
    stem = Path(_get_file(file_id)["name"]).stem
    file_id, _ = _content_id(file_id)
    f = _get_file(file_id)
    if f["kind"] == "document":
        body = json.dumps(package.document_json(file_id), indent=1, ensure_ascii=False)
        return Response(body, media_type="application/json", headers=_disp(f"{stem}.json"))
    if not db.q1("SELECT 1 FROM recordings WHERE file_id=?", (file_id,)):
        raise HTTPException(404, "Not transcribed yet")
    if format == "txt":
        return PlainTextResponse(package.dialogue_text(file_id, markdown=False),
                                 headers=_disp(f"{stem}.txt"))
    if format == "ascii":
        return PlainTextResponse(package.ascii_text(file_id), media_type="text/plain; charset=us-ascii",
                                 headers=_disp(f"{stem}.ascii.txt"))
    if format == "zip":
        return Response(package.recording_zip(file_id), media_type="application/zip",
                        headers=_disp(f"{stem}.zip"))
    if format == "md":
        return PlainTextResponse(package.dialogue_text(file_id), media_type="text/markdown",
                                 headers=_disp(f"{stem}.md"))
    body = json.dumps(package.ai_json(file_id), indent=1, ensure_ascii=False)
    return Response(body, media_type="application/json", headers=_disp(f"{stem}.json"))


# ------------------------------------------------------------------ search
@app.get("/api/search")
def do_search(q: str = Query(""), type: Optional[str] = None, folder: Optional[str] = None, speaker: Optional[str] = None, k: int = 25):
    t0 = time.time()
    out = search.search(q, k=k, types={type} if type else None, folder=folder, speaker=speaker)
    idx = search.get_index()
    return {"query": q, "results": out["results"], "interpreted": out["interpreted"], "speakers": idx["speakers"],
            "people": people.find(q), "ms": round(1000 * (time.time() - t0))}


@app.get("/api/people")
def find_people(q: str = Query("")):
    """People whose name the text names, with their lines per recording."""
    return people.find(q)


@app.get("/api/person")
def person(name: str, file_id: int):
    """Everything this person said in one recording, the slide on screen at each line, and where others mention the name."""
    d = people.detail(name, file_id)
    if d is None:
        raise HTTPException(404, "No such recording")
    return d


@app.get("/api/person/export")
def person_export(name: str, file_id: int):
    stem = package.to_ascii(f"{name} - {Path(_get_file(file_id)['name']).stem}")
    return PlainTextResponse(people.as_text(name, file_id), headers=_disp(f"{stem}.txt"))


# ------------------------------------------------------------------ employee offboarding through Microsoft 365 (m365.py)
@app.get("/api/m365/status")
def m365_status():
    cfg = graph.config()
    return {"mode": cfg["mode"], "tenant": cfg["tenant"], "client_id": cfg["client_id"], "base": cfg["base"],
            "token": graph.token_status(), "audit": m365.verify_audit(), "scopes_needed": graph.SCOPES,
            "mock": {"users": len(graph_mock._load()["users"])} if cfg["mode"] == "mock" else None}


@app.post("/api/m365/connect")
def m365_connect(mode: str = Body("mock"), tenant: str = Body(""), client_id: str = Body("")):
    if mode not in ("mock", "real"):
        raise HTTPException(400, "mode must be mock or real")
    graph.set_setting("m365.mode", mode)
    graph.set_setting("m365.tenant", tenant.strip())
    graph.set_setting("m365.client_id", client_id.strip())
    graph.sign_out()
    return m365_status()


@app.post("/api/m365/signin")
def m365_signin():
    """Device code flow. The mock approves it by itself; a real tenant shows a code to enter at microsoft.com/devicelogin."""
    shown = {}
    try:
        graph.sign_in(on_code=lambda msg, url, code: shown.update(message=msg, url=url, code=code), timeout_s=180)
    except graph.GraphError as e:
        raise HTTPException(401, str(e))
    return {**shown, "status": m365_status()}


@app.post("/api/m365/signout")
def m365_signout():
    graph.sign_out()
    return m365_status()


@app.post("/api/m365/mock/seed")
def m365_mock_seed():
    """Fill the mock tenant with a leaver whose OneDrive holds the public test recordings and decks, so the whole flow
    can be tried: a normal recording, a protected one, one shared outside, and documents."""
    if graph.config()["mode"] != "mock":
        raise HTTPException(400, "only for the mock tenant")
    return graph_mock.seed_demo()


@app.get("/api/m365/users")
def m365_users():
    try:
        return {"users": graph.Graph().users()}
    except graph.GraphError as e:
        raise HTTPException(401 if e.status == 401 else 502, str(e))


@app.post("/api/m365/plan")
def m365_plan(upn: str = Body(..., embed=False), options: dict = Body(default={})):
    try:
        return m365.plan(upn, options)
    except graph.GraphError as e:
        raise HTTPException(401 if e.status == 401 else 502, str(e))


@app.get("/api/m365/plans")
def m365_plans():
    active = db.q("SELECT id, params, status, stage, progress, log FROM jobs WHERE kind='m365' AND status IN ('queued','running') ORDER BY id")
    for a in active:
        a["plan_id"] = json.loads(a.pop("params") or "{}").get("plan_id")
        a["log"] = (a["log"] or "").splitlines()[-8:]
    return {"plans": m365.plans(), "active": active}


@app.get("/api/m365/plans/{plan_id}")
def m365_plan_get(plan_id: int):
    p = m365.get_plan(plan_id)
    if not p:
        raise HTTPException(404)
    return p


@app.post("/api/m365/plans/{plan_id}/approve")
def m365_approve(plan_id: int, approver: str = Body(""), typed_upn: str = Body(""), second_approver: str = Body("")):
    try:
        return m365.approve(plan_id, approver, typed_upn, second_approver)
    except ValueError as e:
        _bad(e)


@app.post("/api/m365/plans/{plan_id}/run")
def m365_run(plan_id: int):
    p = m365.get_plan(plan_id)
    if not p or p["status"] != "approved":
        raise HTTPException(409, "The plan must be approved first")
    return {"job_id": jobs.enqueue("m365", None, {"plan_id": plan_id})}


@app.get("/api/m365/quarantine")
def m365_quarantine():
    return {"items": m365.quarantine()}


@app.post("/api/m365/quarantine/{qid}/restore")
def m365_restore(qid: int):
    try:
        return m365.restore(qid)
    except (ValueError, graph.GraphError) as e:
        raise HTTPException(409, str(e))


@app.post("/api/m365/quarantine/{qid}/delete")
def m365_finalize_one(qid: int, typed: str = Body("", embed=True)):
    q = db.q1("SELECT name FROM m365_quarantine WHERE id=?", (qid,))
    if not q or typed.strip() != q["name"]:
        raise HTTPException(409, "Type the file name to delete it before its time is up")
    return m365.finalize(qid, force=True)


@app.post("/api/m365/quarantine/finalize-due")
def m365_finalize_due():
    return m365.finalize()


@app.get("/api/m365/audit")
def m365_audit(limit: int = 100):
    rows = db.q("SELECT id, ts, action, details FROM audit WHERE action LIKE 'm365.%' ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["details"] = json.loads(r["details"])
    return {"verify": m365.verify_audit(), "entries": rows}


# ------------------------------------------------------------------ reports (PDF: the final proof per file)
@app.get("/api/reports")
def reports_list():
    """Every transcribed recording and processed document, with the figures shown in the Reports tab."""
    rows = []
    rec = {r["id"]: r for r in storage.summary()["recordings"]}
    for f in db.q("SELECT * FROM files WHERE kind IN ('video','document') AND status IN ('processed','shortcut') ORDER BY system, name"):
        f["meta"] = json.loads(f["meta"] or "{}")
        m = library.master_of(f) if f["status"] == "shortcut" else f
        if not m or m["status"] != "processed" or (m["kind"] == "video" and not db.q1("SELECT 1 FROM recordings WHERE file_id=?", (m["id"],))):
            continue
        r = rec.get(m["id"], {})
        rows.append({"id": f["id"], "name": f["name"], "folder": f["system"], "kind": f["kind"], "shortcut": f["status"] == "shortcut",
                     "duration_s": m["meta"].get("duration"), "speakers": m["meta"].get("speakers"), "confidence": m["meta"].get("confidence"),
                     "pages": m["meta"].get("pages"), "original": m["size"], "kept": r.get("distilled"), "reduction": r.get("reduction"),
                     "original_deleted": bool(m["meta"].get("original_deleted")), "processed_at": m["processed_at"]})
    return rows


@app.get("/api/report/{file_id}")
def report_pdf(file_id: int, inline: bool = False):
    from . import report
    f = _get_file(file_id)
    try:
        data, _ = report.build(file_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Report could not be made: {type(e).__name__}: {e}")
    name = f"{Path(f['name']).stem} - DataDistill report.pdf"
    return Response(data, media_type="application/pdf",
                    headers=_disp(name, inline))


@app.get("/api/folders/{name}/reports")
def folder_reports(name: str):
    """All reports of a folder in one ZIP."""
    import io
    import zipfile
    from . import report
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in reports_list():
            if r["folder"] == name and not r["shortcut"]:
                data, _ = report.build(r["id"])
                z.writestr(f"{Path(r['name']).stem} - DataDistill report.pdf", data)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers=_disp(f"{name} - reports.zip"))


# ------------------------------------------------------------------ settings
@app.get("/api/settings")
def get_settings():
    return {k: db.setting(k) for k in db.DEFAULT_SETTINGS}


@app.patch("/api/settings")
def patch_settings(values: dict = Body(...)):
    for k, v in values.items():
        if k not in db.DEFAULT_SETTINGS:
            raise HTTPException(400, f"Unknown setting {k}")
        db.set_setting(k, bool(v))
    return get_settings()


@app.post("/api/voices/delete-all")
def delete_voices():
    from . import voices
    r = voices.delete_all()
    for row in db.q("SELECT file_id FROM recordings"):
        package.write_outputs(row["file_id"])
    return r


# ------------------------------------------------------------------ storage metrics
@app.get("/api/storage")
def storage_metrics():
    return storage.summary()


# ------------------------------------------------------------------ earlier analysis endpoints (no UI)
@app.get("/api/duplicates")
def duplicates():
    return dedup.report()


@app.get("/api/duplicates/compare")
def compare(a: int, b: int):
    """Page by page comparison of two documents (PDF, PPTX or picture)."""
    try:
        return dedup.compare_documents(a, b)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/duplicates/resolve")
def resolve_duplicate(file_id: int = Body(...), master_id: int = Body(...)):
    """Replace a duplicate by a shortcut to master_id. Exact copies: same SHA-256. Same recording in another
    file: picture fingerprint and transcript (or the sampled speech at upload) must confirm it."""
    f, m = _get_file(file_id), _get_file(master_id)
    if f["sha256"] != m["sha256"] and f["status"] != "duplicate":
        fa, fb = dedup.load_fingerprint(master_id), dedup.load_fingerprint(file_id)
        pic = dedup.fingerprint_similarity(fb, fa) if fa and fb else 0
        words = dedup.same_speech(master_id, file_id)
        if pic < dedup.LV_MIN_SIM or not words or words < 0.5:
            raise HTTPException(400, "Not confirmed as the same content")
        db.set_meta(file_id, similar_to={"id": m["id"], "name": m["name"], "folder": m["system"], "frames": round(pic, 3),
                                         "transcript_overlap": words})
        db.ex("UPDATE files SET status='duplicate' WHERE id=?", (file_id,))
    try:
        library.to_shortcut(file_id, master_id)
    except ValueError as e:
        _bad(e)
    search.invalidate()
    return _file_out(db.file_row(file_id))


@app.get("/api/stats")
def stats():
    return {"active_jobs": db.q1("SELECT COUNT(*) n FROM jobs WHERE status IN ('queued','running')")["n"]}


# ------------------------------------------------------------------ built frontend (single server)
_dist = ROOT / "web" / "dist"
if _dist.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        p = _dist / path
        return FileResponse(p if path and p.is_file() else _dist / "index.html")
