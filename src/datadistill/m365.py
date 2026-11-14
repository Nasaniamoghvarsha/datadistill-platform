"""Employee offboarding through Microsoft Graph (OneDrive for Business / SharePoint, Teams meetings).

A leaver's OneDrive holds meeting recordings (large) next to documents. DataDistill reads them through Graph, keeps the
distilled form (speaker-attributed transcript, key frames, voice samples) and only then removes the cloud video.
Deleting in someone's cloud storage is the most dangerous thing this project does, so it is layered:

  1. dry run         `plan()` changes nothing. Every file gets named checks (protected? shared outside? too big? hash
                     known? already distilled?) and an action with the reason.
  2. approval        `approve()` binds a person (and optionally a second, different person) to the EXACT plan by its hash.
                     The leaver's address must be typed. If anything in the cloud changed, the plan is stale: plan again.
  3. identical copy  the video is downloaded and its quickXorHash compared with the one the cloud reports. No match: the
                     file is left alone (a bug in the hash can only block, never allow).
  4. distilled       the transcript must exist, speech must have been found, and recognition quality must pass a gate;
                     videos without speech and unreliable transcripts are kept.
  5. unchanged       right before the cloud is touched the item is read again; the ETag is sent as If-Match, so a file edited
                     in between is refused by the server (412).
  6. reversible      default: the video is MOVED into a quarantine folder in the same drive and only deleted after N days
                     (`finalize()`), `restore()` puts it back. Even then Graph moves it to the recycle bin, never erased.
  7. protected       files with a retention label, a hold, checked out, or shared outside are skipped.
  8. limits          maximum number and size per run; anything beyond is left for another run.
  9. audit           every step is written to a hash-chained audit log; `verify_audit()` detects edits to it.

Honest limits: the cloud space is only freed after the recycle bin retention (93 days in Microsoft 365) or when an
administrator empties it; this is stated in the report. Built and tested against the mock tenant (graph_mock.py);
a real tenant needs the app registration and a first supervised run.
"""
import hashlib
import json
import os
import time
from pathlib import Path

from . import db, graph, jobs, library, pipeline, search, storage, summary
from .config import DATA_DIR, VIDEO_EXT

QUARANTINE_FOLDER = "DataDistill-Quarantine"
from .config import IMAGE_EXT
DOC_EXT = {".pdf", ".pptx"} | IMAGE_EXT
DEFAULTS = {"mode": "quarantine", "quarantine_days": 14, "max_items": 100, "max_item_gb": 8.0, "max_total_gb": 100.0,
            "include_shared": False, "max_low_confidence_share": 0.4, "require_second_approver": False, "path_prefix": ""}
SHARED_OUT = {"anonymous", "users"}              # sharing links that reach people outside the organisation


# ------------------------------------------------------------------ audit chain
def audit(action, **details):
    prev = db.q1("SELECT details FROM audit WHERE action LIKE 'm365.%' ORDER BY id DESC LIMIT 1")
    prev_hash = json.loads(prev["details"]).get("hash", "") if prev else ""
    body = json.dumps(details, sort_keys=True, default=str)
    h = hashlib.sha256((prev_hash + action + body).encode()).hexdigest()
    db.audit("m365." + action, details.get("file_id"), **{**details, "prev": prev_hash, "hash": h})


def verify_audit():
    """Re-computes the chain of m365 audit entries. -> {"ok", "entries", "broken_at"}."""
    rows = db.q("SELECT id, action, details FROM audit WHERE action LIKE 'm365.%' ORDER BY id")
    prev = ""
    for r in rows:
        d = json.loads(r["details"])
        body = json.dumps({k: v for k, v in d.items() if k not in ("prev", "hash")}, sort_keys=True, default=str)
        want = hashlib.sha256((prev + r["action"][5:] + body).encode()).hexdigest()
        if d.get("prev") != prev or d.get("hash") != want:
            return {"ok": False, "entries": len(rows), "broken_at": r["id"]}
        prev = d["hash"]
    return {"ok": True, "entries": len(rows), "broken_at": None}


# ------------------------------------------------------------------ plan (dry run)
def _ext(name):
    return Path(name).suffix.lower()


def plan(upn, options=None, g=None):
    """Read the leaver's drive and decide per file. Changes nothing in the cloud or the library."""
    opts = {**DEFAULTS, **(options or {})}
    g = g or graph.Graph()
    drive = g.drive_of(upn)
    files = [f for f in g.walk(drive["id"]) if not f["path"].startswith("/" + QUARANTINE_FOLDER)
             and f["path"].startswith(opts["path_prefix"] or "/")]
    media = [f for f in files if _ext(f["name"]) in VIDEO_EXT]
    # retention label and sharing, many at once ($batch)
    urls = [f"/drives/{drive['id']}/items/{f['id']}/retentionLabel" for f in media] + \
           [f"/drives/{drive['id']}/items/{f['id']}/permissions" for f in media]
    extra = g.batch_get(urls) if urls else {}
    meetings = {}
    try:
        for m in g.meetings(upn):
            for r in g.recordings(upn, m["id"]):
                meetings[r.get("driveItemId")] = m
    except graph.GraphError:
        pass                                                          # no Teams permission: names come from the video
    known = {(json.loads(r["meta"] or "{}").get("quickxor")): r["id"] for r in
             db.q("SELECT id, meta FROM files WHERE kind='video' AND status='processed'") if "quickxor" in (r["meta"] or "")}
    items, total = [], 0
    for f in files:
        ext = _ext(f["name"])
        qx = f.get("file", {}).get("hashes", {}).get("quickXorHash")
        it = {"item_id": f["id"], "name": f["name"], "path": f["path"], "size": f["size"], "etag": f["eTag"], "quickxor": qx,
              "modified": f["lastModifiedDateTime"], "kind": "video" if ext in VIDEO_EXT else "document" if ext in DOC_EXT else "other",
              "checks": [], "meeting": None}
        if it["kind"] == "other":
            it.update(action="ignore", reason="not a recording or a document")
            items.append(it)
            continue
        if it["kind"] == "document":
            it.update(action="copy_and_keep", reason="read into DataDistill (slide text for the recordings); the cloud copy stays")
            items.append(it)
            continue
        checks = it["checks"]

        def chk(name, ok, detail=""):
            checks.append({"check": name, "ok": bool(ok), "detail": detail})
            return ok
        label = extra.get(f"/drives/{drive['id']}/items/{f['id']}/retentionLabel", (404, None))
        perms = extra.get(f"/drives/{drive['id']}/items/{f['id']}/permissions", (200, {"value": []}))
        scopes = {p["link"]["scope"] for p in (perms[1] or {}).get("value", []) if p.get("link")}
        protected = label[0] == 200 or "publication" in f
        sharing_out = bool(scopes & SHARED_OUT)
        mt = meetings.get(f["id"])
        if mt:
            it["meeting"] = {"id": mt["id"], "subject": mt.get("subject")}
        ok = chk("has a content hash to verify against", bool(qx), qx or "the cloud reports no quickXorHash")
        ok &= chk("not protected (retention label / hold / checked out)", not protected,
                  (label[1] or {}).get("name", "checked out") if protected else "")
        ok &= chk("not shared outside the organisation", not sharing_out or opts["include_shared"],
                  ", ".join(sorted(scopes & SHARED_OUT)) if sharing_out else "")
        ok &= chk(f"not larger than {opts['max_item_gb']:g} GB", f["size"] <= opts["max_item_gb"] * 1e9, f"{f['size'] / 1e9:.2f} GB")
        ok &= chk(f"within the run limit of {opts['max_items']} files / {opts['max_total_gb']:g} GB",
                  sum(1 for i in items if i["kind"] == "video" and i["action"] != "skip") < opts["max_items"]
                  and total + f["size"] <= opts["max_total_gb"] * 1e9, "")
        if not ok:
            it.update(action="skip", reason="left alone, check failed: " + "; ".join(c["check"] + (f" ({c['detail']})" if c.get("detail") else "") for c in checks if not c["ok"]))
        elif qx in known and known[qx]:
            it.update(action="distilled_already", library_file=known[qx],
                      reason="the same content was distilled before: only the cloud copy is moved")
            total += f["size"]
        else:
            it.update(action="distill_then_remove", reason="download, verify, distil, then " +
                      ("quarantine" if opts["mode"] == "quarantine" else "move to the recycle bin"))
            total += f["size"]
        items.append(it)
    summary = {"files": len(items), "videos": sum(i["kind"] == "video" for i in items),
               "to_distil": sum(i["action"] == "distill_then_remove" for i in items),
               "already_distilled": sum(i["action"] == "distilled_already" for i in items),
               "skipped": sum(i["action"] == "skip" for i in items), "documents": sum(i["kind"] == "document" for i in items),
               "bytes_to_remove": sum(i["size"] for i in items if i["action"] in ("distill_then_remove", "distilled_already"))}
    body = {"upn": upn, "drive_id": drive["id"], "options": opts,
            "items": [{k: i[k] for k in ("item_id", "etag", "quickxor", "action")} for i in items]}
    ph = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    pid = db.ex("INSERT INTO m365_plans(created_at, user_upn, drive_id, mode, options, items, plan_hash, status) VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), upn, drive["id"], graph.config()["mode"], json.dumps(opts), json.dumps(items), ph, "planned"))
    audit("plan", plan_id=pid, upn=upn, summary=summary, plan_hash=ph)
    return get_plan(pid)


def get_plan(pid):
    p = db.q1("SELECT * FROM m365_plans WHERE id=?", (pid,))
    if not p:
        return None
    for k in ("options", "items", "report"):
        p[k] = json.loads(p[k]) if p.get(k) else None
    items = p["items"]
    p["summary"] = {"files": len(items), "videos": sum(i["kind"] == "video" for i in items),
                    "to_distil": sum(i["action"] == "distill_then_remove" for i in items),
                    "already_distilled": sum(i["action"] == "distilled_already" for i in items),
                    "skipped": sum(i["action"] == "skip" for i in items), "documents": sum(i["kind"] == "document" for i in items),
                    "bytes_to_remove": sum(i["size"] for i in items if i["action"] in ("distill_then_remove", "distilled_already"))}
    return p


def plans(limit=20):
    return [{k: p[k] for k in ("id", "created_at", "user_upn", "mode", "status", "approved_by", "summary")}
            for p in (get_plan(r["id"]) for r in db.q("SELECT id FROM m365_plans ORDER BY id DESC LIMIT ?", (limit,)))]


# ------------------------------------------------------------------ approval
def approve(pid, approver, typed_upn, second_approver=None):
    p = get_plan(pid)
    if not p or p["status"] != "planned":
        raise ValueError("this plan is not waiting for approval")
    if (typed_upn or "").strip().lower() != p["user_upn"].lower():
        raise ValueError(f"type the leaver's address ({p['user_upn']}) to confirm")
    if not (approver or "").strip():
        raise ValueError("who approves? enter a name")
    if p["options"]["require_second_approver"]:
        if not (second_approver or "").strip() or second_approver.strip().lower() == approver.strip().lower():
            raise ValueError("this plan needs a second approver, a different person")
    db.ex("UPDATE m365_plans SET status='approved', approved_by=?, second_approver=?, approved_at=? WHERE id=?",
          (approver.strip(), (second_approver or "").strip() or None, time.time(), pid))
    audit("approve", plan_id=pid, approver=approver.strip(), second=(second_approver or "").strip() or None, plan_hash=p["plan_hash"])
    return get_plan(pid)


# ------------------------------------------------------------------ run
def _out(file_id):
    from .config import OUTPUT_DIR
    return OUTPUT_DIR / "recordings" / str(file_id) / "transcript.json"


def _transcribed(file_id):
    """Is the distilled form complete? (transcript saved in the database and as a file)"""
    rec = db.q1("SELECT manifest FROM recordings WHERE file_id=?", (file_id,))
    if not rec or not _out(file_id).exists():
        return False
    man = json.loads(rec["manifest"])
    n = db.q1("SELECT COUNT(*) n FROM segments WHERE file_id=?", (file_id,))["n"]
    return n > 0 or man.get("transcript", {}).get("source", "").startswith("none")


def _quality_ok(file_id, max_low):
    """-> (ok, detail): speech found, transcript saved, recognition quality good enough to replace the video."""
    f = db.file_row(file_id)
    rec = db.q1("SELECT manifest FROM recordings WHERE file_id=?", (file_id,))
    if f["status"] != "processed" or not rec:
        return False, "transcript incomplete"
    if not db.q1("SELECT COUNT(*) n FROM segments WHERE file_id=?", (file_id,))["n"]:
        return False, "no speech found: the content is visual, a transcript cannot replace the video"
    if not _out(file_id).exists():
        return False, "transcript incomplete (file missing)"
    low = json.loads(rec["manifest"]).get("quality", {}).get("low_confidence_share")
    if low is not None and low > max_low:
        return False, f"{low:.0%} of the speech was recognised with low confidence (limit {max_low:.0%})"
    return True, "transcript saved, speech found, quality passed"


def run(pid, ctx=None, g=None):
    ctx = ctx or pipeline.NullCtx()
    g = g or graph.Graph()
    p = get_plan(pid)
    if not p or p["status"] not in ("approved", "running"):
        raise ValueError("the plan is not approved")
    opts, upn, drive = p["options"], p["user_upn"], p["drive_id"]
    db.ex("UPDATE m365_plans SET status='running' WHERE id=?", (pid,))
    audit("run_start", plan_id=pid, upn=upn)
    system = library.clean_folder_name(f"M365 {upn}")
    folder = DATA_DIR / system
    folder.mkdir(parents=True, exist_ok=True)
    qfolder = None
    todo = [i for i in p["items"] if i["action"] in ("distill_then_remove", "distilled_already", "copy_and_keep")]
    todo.sort(key=lambda i: (0 if i["kind"] == "document" else 1, i["size"]))
    results = []
    for n, it in enumerate(todo):
        if ctx.cancelled():
            raise InterruptedError("cancelled")
        ctx.stage(it["name"], n / max(1, len(todo)), it["action"])
        res = {"item_id": it["item_id"], "name": it["name"], "size": it["size"], "action": it["action"], "steps": []}
        try:
            st = res["steps"]
            cur = g.item(drive, it["item_id"])                                         # (5a) still what the plan saw?
            if cur["eTag"] != it["etag"]:
                raise _Skip("changed in the cloud since the plan was made: plan again")
            if it["action"] == "copy_and_keep":
                dest = library.unique_target(folder, it["name"])
                h = g.download(drive, it["item_id"], dest, it["quickxor"])
                st.append("downloaded and verified")
                fid = library.register(dest, sha=h["sha256"])
                db.set_meta(fid, quickxor=h["quickxor"], m365_item=it["item_id"], m365_user=upn)
                pipeline.process_file(fid, pipeline.NullCtx())
                res["result"] = "kept"
                audit("document_copied", plan_id=pid, item=it["item_id"], name=it["name"], quickxor=h["quickxor"])
                results.append(res)
                continue
            if it["action"] == "distilled_already":
                fid = it["library_file"]
                st.append("same content already distilled")
            else:
                if it.get("meeting"):                                                  # Teams: speaker names from the cloud
                    _teams_sidecar(g, upn, it, folder)
                dest = library.unique_target(folder, it["name"])
                h = g.download(drive, it["item_id"], dest, it["quickxor"])             # (3) identical copy or nothing
                st.append(f"downloaded, quickXorHash matches ({h['size'] / 1e6:.0f} MB)")
                fid = library.register(dest, sha=h["sha256"])
                db.set_meta(fid, quickxor=h["quickxor"], m365_item=it["item_id"], m365_user=upn)
                audit("downloaded", plan_id=pid, item=it["item_id"], name=it["name"], quickxor=h["quickxor"], sha256=h["sha256"])
                pipeline.process_file(fid, _Sub(ctx, n / max(1, len(todo)), 1 / max(1, len(todo))))
                if db.file_row(fid)["status"] == "duplicate":
                    raise _Skip("recognised as a re-encoded copy of a recording already in DataDistill: decide in Duplicates")
                if summary.available()[0]:
                    jobs.enqueue("summary", fid)
            ok, detail = _quality_ok(fid, opts["max_low_confidence_share"])             # (4) distilled?
            st.append(detail)
            if not ok:
                raise _Skip(detail)
            cur = g.item(drive, it["item_id"])                                         # (5b) right before touching the cloud
            if cur["eTag"] != it["etag"] or cur.get("file", {}).get("hashes", {}).get("quickXorHash") != it["quickxor"]:
                raise _Skip("changed in the cloud while it was being distilled: nothing was touched")
            if opts["mode"] == "quarantine":                                           # (6) reversible
                qfolder = qfolder or _quarantine_folder(g, drive, pid)
                moved = g.move(drive, it["item_id"], qfolder, etag=it["etag"])
                delete_after = time.time() + opts["quarantine_days"] * 86400
                db.ex("INSERT INTO m365_quarantine(plan_id, drive_id, item_id, name, path, size, quickxor, etag, library_file_id, "
                      "orig_parent, quarantine_parent, state, moved_at, delete_after) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, drive, it["item_id"], it["name"], it["path"], it["size"], it["quickxor"], moved["eTag"], fid,
                       cur["parentReference"]["id"], qfolder, "quarantined", time.time(), delete_after))
                st.append(f"moved to quarantine, deleted after {opts['quarantine_days']} days unless restored")
                res["result"] = "quarantined"
                audit("quarantined", plan_id=pid, item=it["item_id"], name=it["name"], delete_after=delete_after)
            else:
                g.delete(drive, it["item_id"], etag=it["etag"])
                db.ex("INSERT INTO m365_quarantine(plan_id, drive_id, item_id, name, path, size, quickxor, etag, library_file_id, "
                      "orig_parent, state, moved_at, finalized_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, drive, it["item_id"], it["name"], it["path"], it["size"], it["quickxor"], it["etag"], fid,
                       cur["parentReference"]["id"], "recycled", time.time(), time.time()))
                st.append("moved to the cloud recycle bin (recoverable by an administrator)")
                res["result"] = "recycled"
                audit("recycled", plan_id=pid, item=it["item_id"], name=it["name"])
            _drop_local_copy(fid)
        except _Skip as e:
            res.update(result="kept", note=str(e))
            audit("kept", plan_id=pid, item=it["item_id"], name=it["name"], reason=str(e))
        except graph.HashMismatch as e:
            res.update(result="kept", note=f"downloaded copy differs from the cloud file: {e}")
            audit("kept", plan_id=pid, item=it["item_id"], name=it["name"], reason="hash mismatch")
        except graph.GraphError as e:
            res.update(result="kept", note=("changed in the cloud (refused by the server): " if e.status == 412 else "cloud error: ") + e.message)
            audit("kept", plan_id=pid, item=it["item_id"], name=it["name"], reason=f"{e.status} {e.code}")
        except Exception as e:  # noqa: BLE001  one file failing never stops the run, and never deletes anything
            res.update(result="kept", note=pipeline.friendly_error(e))
            audit("kept", plan_id=pid, item=it["item_id"], name=it["name"], reason=type(e).__name__)
        results.append(res)
    skipped = [{"item_id": i["item_id"], "name": i["name"], "size": i["size"], "action": i["action"], "result": "kept", "note": i["reason"]}
               for i in p["items"] if i["action"] == "skip"]
    report = {"finished_at": time.time(), "files": results + skipped,
              "summary": {"quarantined": sum(r.get("result") == "quarantined" for r in results),
                          "recycled": sum(r.get("result") == "recycled" for r in results),
                          "kept": sum(r.get("result") == "kept" for r in results + skipped),
                          "bytes_moved": sum(r["size"] for r in results if r.get("result") in ("quarantined", "recycled")),
                          "requests": g.requests_made, "throttled": g.throttled},
              "note": "Cloud space is freed only after the quarantine ends and the recycle bin retention passes "
                      "(93 days in Microsoft 365) or an administrator empties the recycle bin."}
    db.ex("UPDATE m365_plans SET status='done', report=? WHERE id=?", (json.dumps(report), pid))
    audit("run_end", plan_id=pid, summary=report["summary"])
    search.invalidate()
    return pid


class _Skip(Exception):
    pass


class _Sub:
    def __init__(self, parent, base, span):
        self.p, self.base, self.span = parent, base, span

    def stage(self, name, pct, msg=None):
        self.p.stage(name, self.base + self.span * pct, msg)

    def log(self, msg):
        self.p.log(msg)

    def cancelled(self):
        return self.p.cancelled()


def _quarantine_folder(g, drive, pid):
    root = g.json("GET", f"/drives/{drive}/root")["id"]
    try:
        top = g.create_folder(drive, root, QUARANTINE_FOLDER)["id"]
    except graph.GraphError:                                         # exists: find it
        top = next(i["id"] for i in g.pages(f"/drives/{drive}/root/children") if i["name"] == QUARANTINE_FOLDER)
    try:
        return g.create_folder(drive, top, f"plan-{pid}")["id"]
    except graph.GraphError:
        return next(i["id"] for i in g.pages(f"/drives/{drive}/items/{top}/children") if i["name"] == f"plan-{pid}")


def _teams_sidecar(g, upn, it, folder):
    """The official Teams transcript (speaker names from the meeting) saved next to the recording: the pipeline then
    uses it instead of guessing names from voices and tiles."""
    try:
        mid = it["meeting"]["id"]
        ts = g.transcripts(upn, mid)
        if ts:
            vtt = g.transcript_vtt(upn, mid, ts[0]["id"])
            target = folder / (Path(it["name"]).stem + ".vtt")
            if not target.exists():
                target.write_text(vtt, encoding="utf-8")
                it.setdefault("teams", {})["transcript"] = True
    except graph.GraphError:
        pass


def _drop_local_copy(file_id):
    """The local download is only a working copy: once distilled and the cloud copy secured, the video goes
    (transcript, snapshots and voice samples stay)."""
    f = db.file_row(file_id)
    src = DATA_DIR / f["path"]
    if src.exists() and f["status"] == "processed":
        distilled = storage.distilled_bytes(file_id)
        os.remove(src)
        db.set_meta(file_id, original_deleted=True, deleted_at=time.time(), original_bytes=f["size"], distilled_bytes=distilled)
        db.audit("delete_original", file_id, path=f["path"], bytes=f["size"])


# ------------------------------------------------------------------ quarantine: finalize / restore
def quarantine(state=None):
    q = db.q("SELECT * FROM m365_quarantine" + (" WHERE state=?" if state else "") + " ORDER BY id DESC", (state,) if state else ())
    now = time.time()
    for r in q:
        r["days_left"] = max(0, round((r["delete_after"] - now) / 86400, 1)) if r["delete_after"] and r["state"] == "quarantined" else None
    return q


def finalize(qid=None, force=False, g=None):
    """Delete quarantined videos whose time is up (or one chosen: `force`). Each is checked again: still in the
    quarantine folder, unchanged (If-Match), and its distilled form still there."""
    g = g or graph.Graph()
    now = time.time()
    done, kept = [], []
    rows = db.q("SELECT * FROM m365_quarantine WHERE state='quarantined' AND (id=? OR (? IS NULL AND delete_after<=?))", (qid, qid, now))
    for r in rows:
        try:
            ok, detail = _quality_ok(r["library_file_id"], 1.0)
            if not ok:
                raise _Skip("the distilled form is no longer complete: " + detail)
            cur = g.item(r["drive_id"], r["item_id"])
            if cur["eTag"] != r["etag"]:
                raise _Skip("the quarantined file was changed")
            g.delete(r["drive_id"], r["item_id"], etag=r["etag"])
            db.ex("UPDATE m365_quarantine SET state='deleted', finalized_at=? WHERE id=?", (now, r["id"]))
            audit("finalized", item=r["item_id"], name=r["name"], forced=bool(force))
            done.append(r["id"])
        except (_Skip, graph.GraphError) as e:
            db.ex("UPDATE m365_quarantine SET note=? WHERE id=?", (str(e), r["id"]))
            audit("finalize_refused", item=r["item_id"], name=r["name"], reason=str(e))
            kept.append({"id": r["id"], "reason": str(e)})
    return {"deleted": done, "kept": kept}


def restore(qid, g=None):
    g = g or graph.Graph()
    r = db.q1("SELECT * FROM m365_quarantine WHERE id=?", (qid,))
    if not r or r["state"] not in ("quarantined", "recycled", "deleted"):
        raise ValueError("nothing to restore")
    if r["state"] == "quarantined":
        g.move(r["drive_id"], r["item_id"], r["orig_parent"], etag=r["etag"])
    else:
        g.restore(r["drive_id"], r["item_id"])                       # recycle bin (the mock; real tenants: see graph_mock.py)
    db.ex("UPDATE m365_quarantine SET state='restored', finalized_at=? WHERE id=?", (time.time(), qid))
    audit("restored", item=r["item_id"], name=r["name"])
    return {"restored": r["name"], "to": r["path"]}
