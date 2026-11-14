"""SQLite storage: files, jobs, document pages, slide store, recordings, segments, audit log."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  system TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,          -- relative to DATA_DIR (current location)
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- video | document | transcript | other
  size INTEGER NOT NULL,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'new', -- new | queued | processing | processed | error | quarantined | removed | shortcut
                                      -- shortcut: no bytes of its own, meta.master_id = the file it points to
                                      -- meta.original_deleted: original removed after distillation (offboarding)
  meta TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  added_at REAL NOT NULL,
  processed_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  file_id INTEGER,
  kind TEXT NOT NULL,                 -- process | leaver
  params TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | error | cancelled
  stage TEXT, progress REAL DEFAULT 0, log TEXT DEFAULT '', error TEXT, result TEXT,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL, cancel INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  page INTEGER NOT NULL,
  slide_uid TEXT,
  text TEXT, text_norm TEXT, text_hash TEXT, phash TEXT,
  image TEXT, thumb TEXT,
  UNIQUE(file_id, page)
);
CREATE TABLE IF NOT EXISTS slide_store (
  uid TEXT PRIMARY KEY,
  text_hash TEXT, phash TEXT, text_norm TEXT,
  image TEXT, source TEXT,            -- 'document' | 'video'
  created_at REAL
);
CREATE TABLE IF NOT EXISTS recordings (
  file_id INTEGER PRIMARY KEY,
  manifest TEXT NOT NULL,
  speakers TEXT NOT NULL DEFAULT '{}',     -- id (S1..) -> {name, source, score}
  participants TEXT NOT NULL DEFAULT '[]'  -- names read on screen
);
CREATE TABLE IF NOT EXISTS rec_slides (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  t_start REAL, t_end REAL,
  kind TEXT,                          -- slide | unknown_slide | non_slide
  slide_uid TEXT,
  page_id INTEGER,                    -- matched document page (single source of truth)
  score REAL, inliers INTEGER,
  ocr_text TEXT,
  image TEXT
);
CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  idx INTEGER NOT NULL,
  start REAL, end REAL,
  speaker TEXT, text TEXT,
  slide_idx INTEGER,
  confidence REAL,                    -- mean word probability (speech recognition)
  speaker_confidence REAL,            -- similarity of the voice to its speaker cluster
  avg_logprob REAL, no_speech_prob REAL,
  edited INTEGER DEFAULT 0, original_text TEXT
);
CREATE TABLE IF NOT EXISTS canonical (
  group_key TEXT PRIMARY KEY, file_id INTEGER
);
CREATE TABLE IF NOT EXISTS leaver_runs (
  id INTEGER PRIMARY KEY, system TEXT, created_at REAL, report TEXT, executed_at REAL
);
CREATE TABLE IF NOT EXISTS leaver_decisions (
  run_id INTEGER, file_id INTEGER, approved INTEGER, PRIMARY KEY (run_id, file_id)
);
CREATE TABLE IF NOT EXISTS snapshots (       -- video stills where the speech refers to the screen
  id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, idx INTEGER NOT NULL,
  t REAL, seg_idx INTEGER, phrase TEXT, image TEXT, bytes INTEGER, width INTEGER, height INTEGER
);
CREATE TABLE IF NOT EXISTS offboard_runs (
  id INTEGER PRIMARY KEY, folder TEXT, created_at REAL, finished_at REAL, report TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, ts REAL, action TEXT, file_id INTEGER, details TEXT
);
CREATE TABLE IF NOT EXISTS m365_plans (          -- employee offboarding through Microsoft Graph (m365.py)
  id INTEGER PRIMARY KEY, created_at REAL, user_upn TEXT, drive_id TEXT, mode TEXT,
  options TEXT, items TEXT, plan_hash TEXT,
  status TEXT,                        -- planned | approved | running | done
  approved_by TEXT, second_approver TEXT, approved_at REAL, report TEXT
);
CREATE TABLE IF NOT EXISTS m365_quarantine (      -- cloud videos moved aside (or recycled) after distillation
  id INTEGER PRIMARY KEY, plan_id INTEGER, drive_id TEXT, item_id TEXT, name TEXT, path TEXT, size INTEGER,
  quickxor TEXT, etag TEXT, library_file_id INTEGER, orig_parent TEXT, quarantine_parent TEXT,
  state TEXT,                         -- quarantined | deleted | recycled | restored
  moved_at REAL, delete_after REAL, finalized_at REAL, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_pages_uid ON pages(slide_uid);
CREATE INDEX IF NOT EXISTS ix_pages_th ON pages(text_hash);
CREATE INDEX IF NOT EXISTS ix_seg_file ON segments(file_id, idx);
CREATE INDEX IF NOT EXISTS ix_rs_file ON rec_slides(file_id, idx);
CREATE INDEX IF NOT EXISTS ix_store_th ON slide_store(text_hash);
CREATE INDEX IF NOT EXISTS ix_files_sha ON files(sha256);
CREATE INDEX IF NOT EXISTS ix_snap_file ON snapshots(file_id, idx);
"""

_local = threading.local()
_write_lock = threading.RLock()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init():
    with _write_lock:
        conn().executescript(SCHEMA)
        cols = {r[1] for r in conn().execute("PRAGMA table_info(segments)")}
        if "diagram" not in {r[1] for r in conn().execute("PRAGMA table_info(pages)")}:   # 13 Nov: boxes and arrows of a diagram slide (JSON)
            conn().execute("ALTER TABLE pages ADD COLUMN diagram TEXT")
        if "attribution" not in cols:    # 14 Nov: how sure the voice of a line is (supported / contextual / unresolved), JSON
            conn().execute("ALTER TABLE segments ADD COLUMN attribution TEXT")
        if "overlap" not in cols:      # added 13 Nov: names of the others speaking at the same time (JSON list)
            conn().execute("ALTER TABLE segments ADD COLUMN overlap TEXT")
        # 14 Nov: pictures are one-page documents (duplicates are found between them too). Files registered earlier as
        # "other" are queued to be read; nothing else about them changes.
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"):
            conn().execute("UPDATE files SET kind='document', status='new' WHERE kind='other' AND lower(name) LIKE ?", (f"%{ext}",))
        conn().commit()


def q(sql, params=()):
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def q1(sql, params=()):
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def ex(sql, params=()):
    with _write_lock:
        cur = conn().execute(sql, params)
        conn().commit()
        return cur.lastrowid


@contextmanager
def tx():
    with _write_lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def audit(action, file_id=None, **details):
    ex("INSERT INTO audit(ts, action, file_id, details) VALUES (?,?,?,?)",
       (time.time(), action, file_id, json.dumps(details)))


DEFAULT_SETTINGS = {"keep_voice_samples": True,
                    "camera_analysis": True,      # read mouth movement of camera tiles (faces.py); no face is stored or recognised
                    "mark_overlap": True}         # mark lines where two people are visibly speaking at once


def setting(key):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(r["value"]) if r else DEFAULT_SETTINGS.get(key)


def set_setting(key, value):
    ex("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (key, json.dumps(value)))
    audit("setting", None, key=key, value=value)


def file_row(file_id):
    r = q1("SELECT * FROM files WHERE id=?", (file_id,))
    if r:
        r["meta"] = json.loads(r["meta"] or "{}")
    return r


def set_meta(file_id, **kv):
    r = file_row(file_id)
    m = r["meta"]
    m.update(kv)
    ex("UPDATE files SET meta=? WHERE id=?", (json.dumps(m), file_id))
