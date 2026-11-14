"""File library: register/scan files per folder, upload, shortcuts to duplicates, remove, quarantine/restore.

Duplicates: identical bytes (SHA-256) are stored once. A second upload of the same file becomes a
shortcut: a library entry without bytes of its own that points to the first copy (its transcript,
snapshots and voices). Only the offboarding run deletes originals, and only after verification."""
import json
import os
import shutil
import time
from pathlib import Path

from . import db
from .config import DATA_DIR, DOC_EXT, OUTPUT_DIR, QUARANTINE_DIR, TRANSCRIPT_EXT, VIDEO_EXT
from .media import sha256
from .textutil import normalize_name


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in DOC_EXT:
        return "document"
    if ext in TRANSCRIPT_EXT:
        return "transcript"
    return "other"


def register(path: Path, sha=None):
    rel = path.relative_to(DATA_DIR).as_posix()
    if db.q1("SELECT id FROM files WHERE path=?", (rel,)):
        return None
    parts = rel.split("/")
    system = parts[0] if len(parts) > 1 else "unsorted"
    kind = kind_of(path)
    fid = db.ex("INSERT INTO files(system,path,name,kind,size,sha256,status,meta,added_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (system, rel, path.name, kind, path.stat().st_size, sha or sha256(path),
                 "processed" if kind in ("transcript", "other") else "new", "{}", time.time()))
    db.audit("register", fid, path=rel)
    return fid


def scan():
    added = []
    for p in sorted(DATA_DIR.rglob("*")):
        if p.is_file() and QUARANTINE_DIR not in p.parents and not p.name.startswith("."):
            fid = register(p)
            if fid:
                added.append(fid)
    return added


def on_disk(f) -> bool:
    """Does this library entry occupy its own bytes? (not a shortcut, original not deleted)"""
    meta = f["meta"] if isinstance(f["meta"], dict) else json.loads(f["meta"] or "{}")
    return f["status"] != "shortcut" and not meta.get("original_deleted")


def _taken(target: Path) -> bool:
    rel = target.relative_to(DATA_DIR).as_posix()
    return target.exists() or bool(db.q1("SELECT 1 FROM files WHERE path IN (?, ?)", (rel, rel + ".shortcut")))


def unique_target(folder: Path, name: str) -> Path:
    """Same behaviour as a browser download: 'x.pdf' -> 'x (1).pdf' if it exists (on disk or in the library)."""
    target = folder / name
    stem, suf = Path(name).stem, Path(name).suffix
    n = 1
    while _taken(target):
        target = folder / f"{stem} ({n}){suf}"
        n += 1
    return target


INVALID = set('<>:"/\\|?*')


def clean_folder_name(name: str) -> str:
    name = "".join(ch for ch in (name or "").strip() if ch not in INVALID and ord(ch) >= 32).strip(" .")
    if not name or name.startswith("_"):
        raise ValueError("Invalid folder name")
    return name[:80]


def folders():
    """Folders = top-level directories under data/ (so they can also be managed in Explorer)."""
    names = {p.name for p in DATA_DIR.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))}
    names |= {r["system"] for r in db.q("SELECT DISTINCT system FROM files WHERE status != 'removed'")}
    out = []
    for n in sorted(names, key=str.lower):
        rows = db.q("SELECT kind, status, size, meta FROM files WHERE system=? AND status NOT IN ('removed')", (n,))
        out.append({"name": n, "files": len([r for r in rows if r["kind"] in ("video", "document")]),
                    "recordings": sum(r["kind"] == "video" and r["status"] != "shortcut" for r in rows),
                    "documents": sum(r["kind"] == "document" and r["status"] != "shortcut" for r in rows),
                    "shortcuts": sum(r["status"] == "shortcut" for r in rows),
                    "bytes": sum(r["size"] for r in rows if on_disk(r))})
    return out


def create_folder(name: str) -> str:
    name = clean_folder_name(name)
    (DATA_DIR / name).mkdir(parents=True, exist_ok=True)
    db.audit("create_folder", None, folder=name)
    return name


def rename_folder(old: str, new: str) -> str:
    new = clean_folder_name(new)
    if (DATA_DIR / new).exists():
        raise ValueError(f"A folder named {new} already exists")
    (DATA_DIR / old).rename(DATA_DIR / new)
    for f in db.q("SELECT id, path FROM files WHERE system=?", (old,)):
        db.ex("UPDATE files SET system=?, path=? WHERE id=?", (new, new + f["path"][len(old):], f["id"]))
    db.audit("rename_folder", None, old=old, new=new)
    return new


def folder_contents(name: str) -> dict:
    """What deleting a folder would remove (for the confirmation)."""
    rows = db.q("SELECT * FROM files WHERE system=?", (name,))
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    on = [r for r in rows if on_disk(r) and (DATA_DIR / r["path"]).exists()]
    ids = {r["id"] for r in rows}
    kept_by = [s for s in db.q("SELECT id, system, name, meta FROM files WHERE status='shortcut' AND system != ?", (name,))
               if json.loads(s["meta"] or "{}").get("master_id") in ids]
    running = db.q1(f"SELECT 1 FROM jobs WHERE status='running' AND file_id IN ({','.join('?' * len(ids)) or 'NULL'})", tuple(ids))
    return {"files": len([r for r in rows if r["status"] != "removed"]), "bytes": sum(r["size"] for r in on),
            "recordings": sum(r["kind"] == "video" and r["status"] == "processed" for r in rows),
            "documents": sum(r["kind"] == "document" and r["status"] == "processed" for r in rows),
            "shortcuts_elsewhere": len(kept_by), "processing": bool(running)}


def _promote(master: dict, shortcut: dict):
    """The folder of `master` is being deleted, but `shortcut` elsewhere points to it: the master takes the
    shortcut's place (its bytes and outputs move there), so that copy keeps its content. Same file id, so every
    other shortcut, transcript and page still points to it."""
    target = DATA_DIR / shortcut["path"][: -len(".shortcut")] if shortcut["path"].endswith(".shortcut") else DATA_DIR / shortcut["path"]
    src = DATA_DIR / master["path"]
    new_path = target.relative_to(DATA_DIR).as_posix()
    if on_disk(master) and src.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))
    with db.tx() as c:
        c.execute("DELETE FROM files WHERE id=?", (shortcut["id"],))
        c.execute("UPDATE files SET system=?, path=?, name=? WHERE id=?", (shortcut["system"], new_path, shortcut["name"], master["id"]))
    db.audit("promote_shortcut", master["id"], from_folder=master["system"], to_folder=shortcut["system"],
             replaced_shortcut=shortcut["id"], name=shortcut["name"])


def delete_folder(name: str, with_files: bool = False) -> dict:
    """Delete a folder. Without with_files only an empty folder. With with_files everything in it is deleted for
    good: the uploaded files, transcripts, document pages, images, voice samples and their library entries.
    Kept working: a shortcut in another folder to a file here (the file moves to it, see _promote); a file elsewhere
    held as 'same recording' as one here is reset to be processed on its own; slides in other recordings matched to
    a deleted document keep their text but lose the link to the page. The audit log is kept."""
    rows = db.q("SELECT * FROM files WHERE system=?", (name,))
    for r in rows:
        r["meta"] = json.loads(r["meta"] or "{}")
    live = [r for r in rows if r["status"] != "removed"]
    if live and not with_files:
        raise ValueError("Only empty folders can be deleted")
    ids = {r["id"] for r in rows}
    marks = ",".join("?" * len(ids)) or "NULL"
    if db.q1(f"SELECT 1 FROM jobs WHERE status='running' AND file_id IN ({marks})", tuple(ids)):
        raise ValueError("A file in this folder is being processed: cancel it or wait until it is done")
    db.ex(f"UPDATE jobs SET status='cancelled' WHERE status='queued' AND file_id IN ({marks})", tuple(ids))

    # 1. copies elsewhere that point here keep their content: the original moves to the first of them
    promoted = []
    others = db.q("SELECT * FROM files WHERE status='shortcut' AND system != ? ORDER BY added_at, id", (name,))
    for o in others:
        o["meta"] = json.loads(o["meta"] or "{}")
    for m in rows:
        if m["status"] in ("shortcut", "removed"):
            continue
        sc = next((o for o in others if o["meta"].get("master_id") == m["id"] and o["id"] not in {p["shortcut"] for p in promoted}), None)
        if sc:
            _promote(m, sc)
            promoted.append({"file_id": m["id"], "shortcut": sc["id"], "to": f"{sc['system']}/{sc['name']}"})
    gone = ids - {p["file_id"] for p in promoted}
    marks = ",".join("?" * len(gone)) or "NULL"
    gone_t = tuple(gone)

    # 2. files elsewhere waiting as "same recording as one here" are processed on their own again
    requeue = []
    for f in db.q("SELECT id, meta FROM files WHERE status='duplicate'"):
        meta = json.loads(f["meta"] or "{}")
        if (meta.get("similar_to") or {}).get("id") in gone:
            meta.pop("similar_to", None)
            db.ex("UPDATE files SET status='new', meta=? WHERE id=?", (json.dumps(meta), f["id"]))
            requeue.append(f["id"])

    # 3. document pages: slides elsewhere matched to them keep their text; the shared slide store loses the image
    pages = db.q(f"SELECT id, slide_uid, image FROM pages WHERE file_id IN ({marks})", gone_t)
    page_ids = tuple(p["id"] for p in pages)
    detached = 0
    if page_ids:
        pm = ",".join("?" * len(page_ids))
        detached = db.q1(f"SELECT COUNT(*) n FROM rec_slides WHERE page_id IN ({pm}) AND file_id NOT IN ({marks})",
                         page_ids + gone_t)["n"]
        db.ex(f"UPDATE rec_slides SET page_id=NULL, kind='unknown_slide' WHERE page_id IN ({pm}) AND file_id NOT IN ({marks})",
              page_ids + gone_t)
        for p in pages:
            if p["image"]:
                alt = db.q1(f"SELECT image FROM pages WHERE slide_uid=? AND image IS NOT NULL AND file_id NOT IN ({marks})",
                            (p["slide_uid"],) + gone_t)
                db.ex("UPDATE slide_store SET image=? WHERE uid=? AND image=?", (alt["image"] if alt else None, p["slide_uid"], p["image"]))

    # 4. everything of the deleted files: database rows (not the audit log), outputs, the folder on disk
    freed = sum(r["size"] for r in rows if r["id"] in gone and on_disk(r) and (DATA_DIR / r["path"]).exists())
    tables = [t["name"] for t in db.q("SELECT name FROM sqlite_master WHERE type='table'") if t["name"] not in ("audit", "files")]
    with db.tx() as c:
        for t in tables:
            cols = [x[1] for x in c.execute(f"PRAGMA table_info({t})").fetchall()]
            if "file_id" in cols:
                c.execute(f"DELETE FROM {t} WHERE file_id IN ({marks})", gone_t)
        c.execute(f"DELETE FROM files WHERE id IN ({marks})", gone_t)
    for fid in gone:
        shutil.rmtree(OUTPUT_DIR / "recordings" / str(fid), ignore_errors=True)
        shutil.rmtree(OUTPUT_DIR / "pages" / str(fid), ignore_errors=True)
    shutil.rmtree(DATA_DIR / name, ignore_errors=True)
    out = {"folder": name, "deleted_files": len(gone), "freed_bytes": freed, "kept_as_shortcut_target": promoted,
           "requeued": requeue, "slides_detached": detached}
    db.audit("delete_folder", None, **{k: v for k, v in out.items() if k != "kept_as_shortcut_target"},
             names=[r["name"] for r in rows if r["id"] in gone][:200], promoted=promoted)
    return out


def move_file(file_id: int, folder: str):
    f = db.file_row(file_id)
    folder = clean_folder_name(folder)
    if f["system"] == folder:
        return f
    src = DATA_DIR / f["path"]
    if not on_disk(f) or not src.exists():  # nothing on disk to move
        (DATA_DIR / folder).mkdir(parents=True, exist_ok=True)
        dst = unique_target(DATA_DIR / folder, f["name"])
        path = dst.relative_to(DATA_DIR).as_posix() + (".shortcut" if f["status"] == "shortcut" else "")
        db.ex("UPDATE files SET system=?, path=?, name=? WHERE id=?", (folder, path, dst.name, file_id))
        db.audit("move", file_id, to_folder=folder)
        return db.file_row(file_id)
    (DATA_DIR / folder).mkdir(parents=True, exist_ok=True)
    dst = unique_target(DATA_DIR / folder, f["name"])
    shutil.move(str(src), str(dst))
    moved = [(file_id, dst)]
    side = src.with_suffix(".vtt")  # keep a Teams transcript next to its video
    if f["kind"] == "video" and side.exists():
        sdst = dst.with_suffix(".vtt")
        shutil.move(str(side), str(sdst))
        srow = db.q1("SELECT id FROM files WHERE path=?", (side.relative_to(DATA_DIR).as_posix(),))
        if srow:
            moved.append((srow["id"], sdst))
    for fid, p in moved:
        db.ex("UPDATE files SET system=?, path=?, name=? WHERE id=?", (folder, p.relative_to(DATA_DIR).as_posix(), p.name, fid))
    db.audit("move", file_id, to_folder=folder)
    return db.file_row(file_id)


def master_for(sha: str, exclude_id=None, avoid_folder=None):
    """The library entry that holds the content with this SHA-256, or None. Preference: already
    transcribed (its transcript is what a shortcut shows), outside `avoid_folder` (the folder being
    offboarded), a name without a copy suffix ("x (1).mp4", "x - Copy.mp4"), then the oldest."""
    rows = [r for r in db.q("SELECT * FROM files WHERE sha256=? AND kind IN ('video','document') "
                            "AND status NOT IN ('removed','shortcut','quarantined','duplicate')", (sha,)) if r["id"] != exclude_id]
    if not rows:
        # these exact bytes were replaced by a shortcut earlier (e.g. a re-encoded copy): its master holds the content
        sc = db.q1("SELECT * FROM files WHERE sha256=? AND status='shortcut' AND id != ?", (sha, exclude_id or -1))
        if sc:
            sc["meta"] = json.loads(sc["meta"] or "{}")
            m = master_of(sc)
            if m and m["status"] not in ("removed", "quarantined"):
                return m
        return None
    r = min(rows, key=lambda r: (r["status"] != "processed", r["system"] == avoid_folder,
                                 normalize_name(r["name"]) != r["name"].lower(), r["id"]))
    r["meta"] = json.loads(r["meta"] or "{}")
    return r


def master_of(f):
    """Follow a shortcut to the file it points to (a regular file returns itself)."""
    seen = set()
    while f and f["status"] == "shortcut" and f["id"] not in seen:
        seen.add(f["id"])
        f = db.file_row(f["meta"].get("master_id"))
    return f


def check_duplicates(items):
    """Upload pre-check. items: [{name, size, sha256?}]. Without a hash: is there any file of that size
    (only then the browser hashes the file). With a hash: the existing copy, if any."""
    out = []
    for it in items:
        if not it.get("sha256"):
            hit = db.q1("SELECT 1 FROM files WHERE size=? AND kind IN ('video','document') AND status NOT IN ('removed','quarantined')",
                        (int(it.get("size") or -1),))
            out.append({"name": it.get("name"), "possible": bool(hit)})
            continue
        m = master_for(it["sha256"].lower())
        if not m:
            out.append({"name": it.get("name"), "possible": True, "duplicate": None})
            continue
        copies = db.q("SELECT system, name FROM files WHERE status='shortcut' AND sha256=?", (m["sha256"],))
        out.append({"name": it.get("name"), "possible": True, "duplicate": {
            "id": m["id"], "name": m["name"], "folder": m["system"], "kind": m["kind"], "size": m["size"],
            "added_at": m["added_at"], "status": m["status"], "original_deleted": bool(m["meta"].get("original_deleted")),
            "shortcuts": [{"folder": c["system"], "name": c["name"]} for c in copies]}})
    return out


def create_shortcut(folder: str, name: str, master_id: int) -> int:
    """A library entry in `folder` that points to `master_id` (no bytes stored, no processing)."""
    folder = clean_folder_name(folder)
    m = master_of(db.file_row(master_id))
    if not m or m["status"] in ("removed", "quarantined"):
        raise ValueError("The original file is no longer in the library")
    name = Path(name).name
    same = db.q1("SELECT id FROM files WHERE status='shortcut' AND system=? AND name=? AND sha256=?", (folder, name, m["sha256"]))
    if same:
        return same["id"]
    (DATA_DIR / folder).mkdir(parents=True, exist_ok=True)
    target = unique_target(DATA_DIR / folder, name)
    fid = db.ex("INSERT INTO files(system,path,name,kind,size,sha256,status,meta,added_at,processed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (folder, target.relative_to(DATA_DIR).as_posix() + ".shortcut", target.name, m["kind"], m["size"], m["sha256"],
                 "shortcut", json.dumps({"master_id": m["id"], "saved_bytes": m["size"]}), time.time(), time.time()))
    db.audit("shortcut", fid, master_id=m["id"], folder=folder, name=target.name, saved_bytes=m["size"])
    return fid


def drop_outputs(file_id: int):
    """Remove a recording's own distilled outputs (transcript rows and files, snapshots, voices).
    Document pages stay: slides in other recordings may be matched to them."""
    with db.tx() as c:
        for t in ("segments", "rec_slides", "snapshots", "recordings"):
            c.execute(f"DELETE FROM {t} WHERE file_id=?", (file_id,))
    shutil.rmtree(OUTPUT_DIR / "recordings" / str(file_id), ignore_errors=True)


def to_shortcut(file_id: int, master_id: int) -> int:
    """Replace an existing duplicate file by a shortcut: its bytes are deleted, the entry stays.
    Exact copy: same SHA-256. Same recording in another encoding: confirmed by samecontent (status 'duplicate')."""
    f, m = db.file_row(file_id), master_of(db.file_row(master_id))
    same_recording = f["status"] == "duplicate" and (f["meta"].get("similar_to") or {}).get("id") == (m or {}).get("id")
    if not m or m["id"] == file_id or (m["sha256"] != f["sha256"] and not same_recording):
        raise ValueError("not a duplicate of that file")
    src = DATA_DIR / f["path"]
    if src.exists():
        if sha256(src) != f["sha256"]:  # verify the bytes once more right before deleting
            raise ValueError("file changed since it was registered")
        os.remove(src)
    drop_outputs(file_id)  # the shortcut shows the master's transcript: no second copy of it either
    meta = f["meta"]
    meta.update(master_id=m["id"], saved_bytes=f["size"], prev_status=f["status"])
    db.ex("UPDATE files SET status='shortcut', path=?, meta=? WHERE id=?", (f["path"] + ".shortcut", json.dumps(meta), file_id))
    db.audit("duplicate_to_shortcut", file_id, master_id=m["id"], deleted=f["path"], saved_bytes=f["size"])
    return file_id


def save_upload(folder: str, filename: str, fileobj):
    """Store an upload. Returns (file_id, 'new' | 'shortcut' | 'updated').
    Identical content already in the library is never stored twice: the upload becomes a shortcut."""
    folder = clean_folder_name(folder)
    (DATA_DIR / folder).mkdir(parents=True, exist_ok=True)
    name = Path(filename).name
    # a Teams transcript must keep the video's name to be picked up -> overwrite instead of "(1)"
    target = (DATA_DIR / folder / name) if Path(name).suffix.lower() in TRANSCRIPT_EXT else unique_target(DATA_DIR / folder, name)
    with open(target, "wb") as out:
        shutil.copyfileobj(fileobj, out, 4 << 20)
    existing = db.q1("SELECT id FROM files WHERE path=?", (target.relative_to(DATA_DIR).as_posix(),))
    if existing:
        db.ex("UPDATE files SET size=?, sha256=? WHERE id=?", (target.stat().st_size, sha256(target), existing["id"]))
        return existing["id"], "updated"
    if kind_of(target) in ("video", "document"):
        sha = sha256(target)
        m = master_for(sha)
        if m:
            os.remove(target)
            return create_shortcut(folder, name, m["id"]), "shortcut"
        return register(target, sha), "new"
    return register(target), "new"


def quarantine(file_id: int, reason: str):
    f = db.file_row(file_id)
    if not f or f["status"] == "quarantined":
        return f
    src = DATA_DIR / f["path"]
    dst = QUARANTINE_DIR / f["path"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    side = src.with_suffix(".vtt")
    meta = f["meta"]
    meta.update(orig_path=f["path"], prev_status=f["status"], quarantine_reason=reason, quarantined_at=time.time())
    db.ex("UPDATE files SET path=?, status='quarantined', meta=? WHERE id=?",
          (dst.relative_to(DATA_DIR).as_posix(), json.dumps(meta), file_id))
    db.audit("quarantine", file_id, reason=reason, from_path=f["path"], sidecar_left=side.exists())
    return db.file_row(file_id)


def restore(file_id: int):
    f = db.file_row(file_id)
    if not f or f["status"] != "quarantined":
        return f
    meta = f["meta"]
    orig = meta.pop("orig_path")
    status = meta.pop("prev_status", "processed")
    for k in ("quarantine_reason", "quarantined_at"):
        meta.pop(k, None)
    dst = DATA_DIR / orig
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(DATA_DIR / f["path"]), str(dst))
    db.ex("UPDATE files SET path=?, status=?, meta=? WHERE id=?", (orig, status, json.dumps(meta), file_id))
    db.audit("restore", file_id, to_path=orig)
    return db.file_row(file_id)


def remove_from_library(file_id: int):
    """Hide from the library and all analyses. The original file is NOT touched."""
    f = db.file_row(file_id)
    if f:
        db.set_meta(file_id, prev_status=f["status"])
        db.ex("UPDATE files SET status='removed' WHERE id=?", (file_id,))
        db.audit("remove_from_library", file_id)


def readd(file_id: int):
    f = db.file_row(file_id)
    if f and f["status"] == "removed":
        db.ex("UPDATE files SET status=? WHERE id=?", (f["meta"].get("prev_status", "new"), file_id))
        db.audit("readd", file_id)


def write_reference_stub(file_id: int, canonical_id: int):
    """'Replace copy with a reference': a small JSON pointer instead of the duplicate bytes."""
    f, c = db.file_row(file_id), db.file_row(canonical_id)
    p = OUTPUT_DIR / "references" / (f["meta"].get("orig_path") or f["path"])
    p = p.with_name(p.name + ".ref.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"replaced_file": f["name"], "system": f["system"], "sha256": f["sha256"],
                             "canonical": {"id": c["id"], "system": c["system"], "path": c["path"], "sha256": c["sha256"]},
                             "ts": time.time()}, indent=1), encoding="utf-8")
    return p
