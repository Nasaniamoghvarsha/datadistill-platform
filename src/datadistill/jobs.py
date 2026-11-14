"""Background job worker: one thread, GPU jobs one at a time. Documents run before videos
(videos are matched against the document library)."""
import json
import threading
import time
import traceback

from . import db, pipeline

_thread = None
_wake = threading.Event()


class Ctx:
    def __init__(self, job_id):
        self.job_id = job_id
        self._last = 0.0
        self._stage = None
        self._cancel_checked = 0.0
        self._cancel = False

    def stage(self, name, pct, msg=None):
        now = time.time()
        if name != self._stage or now - self._last > 0.5 or pct >= 1.0:
            db.ex("UPDATE jobs SET stage=?, progress=? WHERE id=?", (name, round(pct, 4), self.job_id))
            if msg and name != self._stage:
                self.log(f"{name}: {msg}")
            self._stage, self._last = name, now

    def log(self, msg):
        db.ex("UPDATE jobs SET log = COALESCE(log,'') || ? WHERE id=?",
              (time.strftime("%H:%M:%S ") + str(msg) + "\n", self.job_id))

    def cancelled(self):
        now = time.time()
        if now - self._cancel_checked > 1.0:
            r = db.q1("SELECT cancel FROM jobs WHERE id=?", (self.job_id,))
            self._cancel = bool(r and r["cancel"])
            self._cancel_checked = now
        return self._cancel


def new_batch():
    """A batch id for one upload: the files of one upload are queued together, after every earlier batch."""
    return time.time_ns()


def enqueue(kind, file_id=None, params=None, batch=None):
    if file_id is not None:
        j = db.q1("SELECT id FROM jobs WHERE file_id=? AND kind=? AND status IN ('queued','running')", (file_id, kind))
        if j:
            return j["id"]
        if kind == "process":
            db.ex("UPDATE files SET status='queued' WHERE id=? AND status NOT IN ('quarantined','removed','shortcut')", (file_id,))
    p = dict(params or {})
    if batch is not None:
        p["batch"] = batch
    jid = db.ex("INSERT INTO jobs(file_id, kind, params, created_at) VALUES (?,?,?,?)",
                (file_id, kind, json.dumps(p), time.time()))
    _wake.set()
    return jid


def queue():
    """Queued jobs in the order they run. FIFO by batch: a whole upload is finished before the next upload
    starts (a job without a batch, e.g. 'process again', is a batch of its own, placed by when it was asked).
    Inside a batch documents go first: a recording's slides are matched against the decks uploaded with it."""
    rows = db.q("SELECT j.*, f.kind fkind FROM jobs j LEFT JOIN files f ON f.id=j.file_id WHERE j.status='queued'")
    first = {}  # batch -> id of its first job = its place in the line
    for j in rows:
        b = json.loads(j["params"] or "{}").get("batch")
        if b is not None:
            first[b] = min(first.get(b, j["id"]), j["id"])

    def key(j):
        b = json.loads(j["params"] or "{}").get("batch")
        return (first[b] if b is not None else j["id"], 0 if j["kind"] == "process" and j["fkind"] == "document" else 1, j["id"])
    return sorted(rows, key=key)


def _next_job():
    q = queue()
    return q[0] if q else None


def _run(job):
    ctx = Ctx(job["id"])
    params = json.loads(job["params"] or "{}")
    if job["kind"] == "process":
        f0 = db.file_row(job["file_id"])
        if f0 and f0["kind"] == "video":
            from . import summary
            summary.unload_model()                      # the language model must not sit on the GPU while speech is recognised
        try:
            pipeline.process_file(job["file_id"], ctx)
        finally:
            if f0 and f0["kind"] == "video":
                from . import speakers, transcribe
                transcribe.release()                    # ...and the speech / voice models must not sit on it while Ollama loads
                speakers.release()
        f = db.file_row(job["file_id"])
        if f["kind"] == "video" and f["status"] == "processed":
            from . import summary
            if summary.available()[0]:  # the meeting summary is written next (local model), in its own job
                enqueue("summary", job["file_id"])
        return None
    if job["kind"] == "summary":
        from . import summary
        ctx.stage("Writing the meeting summary", 0.1)
        try:
            out = summary.get(job["file_id"])
        finally:
            summary.unload_model()
        if out.get("error"):
            raise RuntimeError(out["error"])
        from . import package
        package.write_outputs(job["file_id"])
        ctx.stage("Done", 1.0)
        return {"seconds": out.get("seconds"), "removed": len(out.get("removed") or [])}
    if job["kind"] == "m365":
        from . import m365
        return {"plan_id": m365.run(params["plan_id"], ctx)}
    if job["kind"] == "extras":  # snapshots + voice samples for a recording transcribed earlier
        ctx.stage("Saving snapshots and voice samples", 0.05)
        out = pipeline.keep_extracts(job["file_id"], ctx=ctx)
        from . import package
        package.write_outputs(job["file_id"])
        return out
    raise ValueError(job["kind"])


class _SubCtx:
    def __init__(self, parent, base, span):
        self.p, self.base, self.span = parent, base, span

    def stage(self, name, pct, msg=None):
        self.p.stage(name, self.base + self.span * pct, msg)

    def log(self, msg):
        self.p.log(msg)

    def cancelled(self):
        return self.p.cancelled()


def _loop():
    db.init()
    while True:
        job = _next_job()
        if not job:
            _wake.wait(2.0)
            _wake.clear()
            continue
        db.ex("UPDATE jobs SET status='running', started_at=? WHERE id=?", (time.time(), job["id"]))
        try:
            result = _run(job)
            db.ex("UPDATE jobs SET status='done', progress=1, finished_at=?, result=? WHERE id=?",
                  (time.time(), json.dumps(result), job["id"]))
        except InterruptedError:
            db.ex("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (time.time(), job["id"]))
        except Exception as e:
            Ctx(job["id"]).log(traceback.format_exc())
            db.ex("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?",
                  (f"{type(e).__name__}: {e}", time.time(), job["id"]))


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    db.init()
    # jobs interrupted by a restart are re-queued
    db.ex("UPDATE jobs SET status='queued', cancel=0 WHERE status='running'")
    db.ex("UPDATE files SET status='queued' WHERE status='processing'")
    _thread = threading.Thread(target=_loop, name="datadistill-worker", daemon=True)
    _thread.start()
