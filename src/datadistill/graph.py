"""Microsoft Graph client (OneDrive for Business, SharePoint, Teams meeting artifacts).

Works unchanged against a real tenant (https://graph.microsoft.com/v1.0) and against the local mock (graph_mock.py).
  * sign-in: OAuth 2.0 device code flow (the user signs in in a browser; no password ever passes through DataDistill)
  * paging: follows @odata.nextLink; throttling: waits Retry-After on 429 / 503 / 504, then tries again
  * downloads are streamed and hashed on the way (quickXorHash and SHA-256)
Permissions needed (least privilege; the real app registration is made by a tenant administrator):
  Files.ReadWrite.All (or Files.ReadWrite.Selected for chosen sites), User.Read.All,
  OnlineMeetingTranscript.Read.All and OnlineMeetingRecording.Read.All (Teams transcripts and attendance).
"""
import hashlib
import json
import time
from pathlib import Path

import requests

from . import db
from .config import OUTPUT_DIR
from .quickxor import QuickXorHash

REAL_BASE = "https://graph.microsoft.com/v1.0"
REAL_AUTHORITY = "https://login.microsoftonline.com/{tenant}"
MOCK_BASE = "http://127.0.0.1:8000/mock-graph/v1.0"
MOCK_AUTHORITY = "http://127.0.0.1:8000/mock-graph"
SCOPES = ["Files.ReadWrite.All", "User.Read.All", "OnlineMeetingTranscript.Read.All", "OnlineMeetingRecording.Read.All"]
TOKEN_FILE = OUTPUT_DIR / "graph-token.json"
MAX_RETRIES = 6


class GraphError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class HashMismatch(Exception):
    pass


# ------------------------------------------------------------------ settings and token
def setting(key, default=None):
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def set_setting(key, value):
    db.ex("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (key, json.dumps(value)))


def config():
    mode = setting("m365.mode", "mock")
    return {"mode": mode, "tenant": setting("m365.tenant", ""), "client_id": setting("m365.client_id", ""),
            "base": MOCK_BASE if mode == "mock" else REAL_BASE,
            "authority": MOCK_AUTHORITY if mode == "mock" else REAL_AUTHORITY.format(tenant=setting("m365.tenant", "organizations"))}


def _read_token():
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def token_status():
    cfg = config()
    t = _read_token()
    ok = bool(t) and t.get("mode") == cfg["mode"] and t.get("expires_at", 0) > time.time() + 60
    return {"mode": cfg["mode"], "signed_in": ok, "scopes": t.get("scopes") if ok else [],
            "expires_in": int(t["expires_at"] - time.time()) if ok else 0}


def sign_out():
    TOKEN_FILE.unlink(missing_ok=True)


def sign_in(on_code=None, timeout_s=300, sleep=time.sleep):
    """Device code flow. `on_code(message, url, code)` shows the user where to sign in. Returns the token record."""
    cfg = config()
    client_id = cfg["client_id"] or "datadistill-mock"
    r = requests.post(cfg["authority"] + "/oauth2/v2.0/devicecode", data={"client_id": client_id, "scope": " ".join(SCOPES) + " offline_access"}, timeout=30)
    r.raise_for_status()
    dc = r.json()
    if on_code:
        on_code(dc.get("message", ""), dc["verification_uri"], dc["user_code"])
    deadline = time.time() + min(timeout_s, dc.get("expires_in", 900))
    while time.time() < deadline:
        sleep(max(1, dc.get("interval", 5)) if cfg["mode"] != "mock" else 0.2)
        tr = requests.post(cfg["authority"] + "/oauth2/v2.0/token", timeout=30, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": client_id, "device_code": dc["device_code"]})
        body = tr.json()
        if tr.status_code == 200:
            rec = {"access_token": body["access_token"], "expires_at": time.time() + int(body.get("expires_in", 3600)),
                   "scopes": body.get("scope", "").split(), "mode": cfg["mode"]}
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(json.dumps(rec), encoding="utf-8")
            return rec
        if body.get("error") in ("authorization_pending", "slow_down"):
            continue
        raise GraphError(tr.status_code, body.get("error", "sign_in_failed"), body.get("error_description", ""))
    raise GraphError(408, "timeout", "sign-in was not completed in time")


# ------------------------------------------------------------------ client
class Graph:
    def __init__(self, base=None, token=None, sleep=time.sleep):
        cfg = config()
        self.base = (base or cfg["base"]).rstrip("/")
        self._token = token
        self.sleep = sleep
        self.requests_made = 0
        self.throttled = 0

    @property
    def token(self):
        if self._token:
            return self._token
        t = _read_token()
        if not t or t.get("expires_at", 0) < time.time() + 30:
            raise GraphError(401, "not_signed_in", "Sign in to Microsoft 365 first")
        return t["access_token"]

    def request(self, method, path, expect=(200, 201, 204, 206), stream=False, **kw):
        url = path if path.startswith("http") else self.base + path
        headers = {"Authorization": f"Bearer {self.token}", **kw.pop("headers", {})}
        for attempt in range(MAX_RETRIES):
            self.requests_made += 1
            r = requests.request(method, url, headers=headers, timeout=120, stream=stream, **kw)
            if r.status_code in (429, 503, 504) and attempt < MAX_RETRIES - 1:
                self.throttled += 1
                self.sleep(min(30.0, float(r.headers.get("Retry-After", 2 ** attempt))))
                continue
            if r.status_code not in expect:
                try:
                    e = r.json().get("error", {})
                except Exception:
                    e = {}
                raise GraphError(r.status_code, e.get("code", "error"), e.get("message", r.text[:200]))
            return r
        raise GraphError(429, "throttled", "still throttled after several tries")

    def json(self, method, path, **kw):
        return self.request(method, path, **kw).json()

    def pages(self, path, **kw):
        """All items of a collection, following @odata.nextLink."""
        url = path
        while url:
            body = self.json("GET", url, **kw)
            yield from body.get("value", [])
            url = body.get("@odata.nextLink")

    # -- users, drives, files
    def users(self):
        return list(self.pages("/users"))

    def drive_of(self, user):
        return self.json("GET", f"/users/{user}/drive")

    def walk(self, drive_id, folder_id=None, _path=""):
        """Every file below a folder (default: the drive's root), depth first: dicts with an added `path`."""
        url = f"/drives/{drive_id}/items/{folder_id}/children" if folder_id else f"/drives/{drive_id}/root/children"
        for it in self.pages(url):
            p = f"{_path}/{it['name']}"
            if "folder" in it:
                yield from self.walk(drive_id, it["id"], p)
            else:
                yield {**it, "path": p}

    def item(self, drive_id, item_id):
        return self.json("GET", f"/drives/{drive_id}/items/{item_id}")

    def retention_label(self, drive_id, item_id):
        try:
            return self.json("GET", f"/drives/{drive_id}/items/{item_id}/retentionLabel")
        except GraphError as e:
            if e.status == 404:
                return None
            raise

    def permissions(self, drive_id, item_id):
        return self.json("GET", f"/drives/{drive_id}/items/{item_id}/permissions").get("value", [])

    def batch_get(self, urls):
        """GET many urls in $batch calls of up to 20: {url: (status, body)}."""
        out = {}
        for i in range(0, len(urls), 20):
            chunk = urls[i:i + 20]
            body = self.json("POST", "/$batch", json={"requests": [{"id": str(k), "method": "GET", "url": u} for k, u in enumerate(chunk)]})
            for r in body["responses"]:
                out[chunk[int(r["id"])]] = (r["status"], r.get("body"))
        return out

    def download(self, drive_id, item_id, dest, expect_quickxor=None, chunk=1 << 20):
        """Stream a file to `dest` (a .part file that is renamed when complete) and hash it on the way.
        Raises HashMismatch if the cloud's quickXorHash differs from the bytes received: nothing is kept then."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        qx, sha, size = QuickXorHash(), hashlib.sha256(), 0
        r = self.request("GET", f"/drives/{drive_id}/items/{item_id}/content", stream=True)
        try:
            with open(part, "wb") as f:
                for b in r.iter_content(chunk):
                    f.write(b)
                    qx.update(b)
                    sha.update(b)
                    size += len(b)
        finally:
            r.close()
        got = qx.b64()
        if expect_quickxor and got != expect_quickxor:
            part.unlink(missing_ok=True)
            raise HashMismatch(f"quickXorHash differs: cloud {expect_quickxor}, received {got}")
        part.replace(dest)
        return {"quickxor": got, "sha256": sha.hexdigest(), "size": size}

    def create_folder(self, drive_id, parent_id, name):
        return self.json("POST", f"/drives/{drive_id}/items/{parent_id}/children", json={"name": name, "folder": {},
                                                                                         "@microsoft.graph.conflictBehavior": "fail"})

    def move(self, drive_id, item_id, new_parent_id, etag=None):
        return self.json("PATCH", f"/drives/{drive_id}/items/{item_id}", json={"parentReference": {"id": new_parent_id}},
                         headers={"If-Match": etag} if etag else {})

    def delete(self, drive_id, item_id, etag=None):
        """Moves the item to the recycle bin (Graph never deletes for good here). `etag`: refuse if it changed (412)."""
        self.request("DELETE", f"/drives/{drive_id}/items/{item_id}", headers={"If-Match": etag} if etag else {})

    def restore(self, drive_id, item_id):
        return self.json("POST", f"/drives/{drive_id}/items/{item_id}/restore")

    # -- Teams
    def meetings(self, user):
        return list(self.pages(f"/users/{user}/onlineMeetings"))

    def transcripts(self, user, meeting_id):
        return list(self.pages(f"/users/{user}/onlineMeetings/{meeting_id}/transcripts"))

    def transcript_vtt(self, user, meeting_id, transcript_id):
        return self.request("GET", f"/users/{user}/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content?$format=text/vtt").text

    def recordings(self, user, meeting_id):
        return list(self.pages(f"/users/{user}/onlineMeetings/{meeting_id}/recordings"))

    def attendance(self, user, meeting_id):
        return list(self.pages(f"/users/{user}/onlineMeetings/{meeting_id}/attendanceReports"))
