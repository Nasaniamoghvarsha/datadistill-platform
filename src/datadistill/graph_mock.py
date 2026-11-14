"""A local stand-in for Microsoft Graph (OneDrive for Business / SharePoint / Teams meetings).

There is no Microsoft 365 tenant for this project, so the offboarding flow is built against this mock, which speaks the
same API: same URL paths, same JSON field names (driveItem, file.hashes.quickXorHash, eTag, parentReference,
@odata.nextLink, $batch, onlineMeetings/transcripts, attendanceReports), same status codes (401 / 403 / 404 / 412 / 423 /
429 with Retry-After) and the OAuth device-code sign-in. `graph.py` (the client) does not know it is talking to a mock:
a real tenant needs only a different base URL and a real app registration.

What is mirrored from the documented API and what is not:
  mirrored    drive / children / item / content / delta-less listing, DELETE with If-Match (-> recycle bin), PATCH move,
              permissions, retentionLabel, $batch, users, onlineMeetings, transcripts (VTT with speaker names),
              attendanceReports, 429 throttling, OAuth device code + token
  simplified  recycle-bin restore (real tenants differ between OneDrive personal / business / SharePoint: verify on a
              real tenant), retention labels and legal holds (a field on the item), pre-authenticated download URLs
              (the /content endpoint answers directly)

Data lives in output/mock-graph/ (never in git): a JSON index and the file contents.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from .config import OUTPUT_DIR
from .quickxor import quickxor_file

ROOT_DIR = OUTPUT_DIR / "mock-graph"
INDEX = ROOT_DIR / "tenant.json"
router = APIRouter(prefix="/mock-graph")
_lock = threading.RLock()
_state = {"throttle": 0, "requests": 0}

ADMIN_SCOPES = ["Files.ReadWrite.All", "User.Read.All", "OnlineMeetingTranscript.Read.All", "OnlineMeetingRecording.Read.All"]
MIME = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
        ".webm": "video/webm", ".pdf": "application/pdf", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".vtt": "text/vtt", ".txt": "text/plain"}


# ------------------------------------------------------------------ store
def _load():
    if INDEX.exists():
        return json.loads(INDEX.read_text(encoding="utf-8"))
    return {"users": {}, "drives": {}, "items": {}, "meetings": {}, "tokens": {}, "devicecodes": {}, "recycle": {}}


def _save(t):
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(t, indent=1), encoding="utf-8")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def reset(keep_tokens=False):
    """Empty tenant (tests, a fresh demo). `keep_tokens`: stay signed in."""
    with _lock:
        import shutil
        tokens = _load()["tokens"] if keep_tokens and INDEX.exists() else {}
        shutil.rmtree(ROOT_DIR, ignore_errors=True)
        _state.update(throttle=0, requests=0)
        if tokens:
            t = _load()
            t["tokens"] = tokens
            _save(t)


def add_user(upn, name, user_id=None):
    with _lock:
        t = _load()
        uid = user_id or "u-" + hashlib.sha1(upn.encode()).hexdigest()[:10]
        did = "b!" + hashlib.sha1(("drive" + upn).encode()).hexdigest()[:16]
        t["users"][uid] = {"id": uid, "userPrincipalName": upn, "displayName": name, "mail": upn, "driveId": did}
        t["drives"][did] = {"id": did, "driveType": "business", "name": "OneDrive", "owner": uid, "root": f"{did}-root"}
        t["items"][f"{did}-root"] = _item_record(f"{did}-root", "root", None, did, True, 0, uid)
        _save(t)
        return uid


def _item_record(iid, name, parent, drive, folder, size, owner, mime=None, qx=None, sha1=None):
    return {"id": iid, "name": name, "parent": parent, "drive": drive, "folder": folder, "size": size, "mime": mime,
            "etag": 1, "created": _now(), "modified": _now(), "owner": owner, "quickxor": qx, "sha1": sha1,
            "shared": None, "retention": None, "hold": False, "checked_out": False, "deleted": None}


def ensure_folder(drive, path, owner=None):
    """Create /a/b/c in a drive; returns the id of the last folder."""
    with _lock:
        t = _load()
        cur = t["drives"][drive]["root"]
        for part in [p for p in path.strip("/").split("/") if p]:
            hit = next((i for i in t["items"].values() if i["parent"] == cur and i["name"] == part and i["folder"] and not i["deleted"]), None)
            if not hit:
                iid = "i-" + secrets.token_hex(6)
                t["items"][iid] = _item_record(iid, part, cur, drive, True, 0, owner or t["drives"][drive]["owner"])
                hit = t["items"][iid]
            cur = hit["id"]
        _save(t)
        return cur


def add_file(drive, folder_path, name, src, owner=None, shared=None, retention=None, hold=False, modified=None):
    """Put a real file into a user's drive (seeding the mock tenant)."""
    src = Path(src)
    with _lock:
        folder = ensure_folder(drive, folder_path, owner)
        t = _load()
        iid = "i-" + secrets.token_hex(6)
        sha1 = hashlib.sha1()
        with open(src, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha1.update(chunk)
        rec = _item_record(iid, name, folder, drive, False, src.stat().st_size, owner or t["drives"][drive]["owner"],
                           MIME.get(src.suffix.lower(), "application/octet-stream"), quickxor_file(src), sha1.hexdigest().upper())
        rec.update(shared=shared, retention=retention, hold=hold)
        if modified:
            rec["modified"] = modified
        (ROOT_DIR / "blobs").mkdir(parents=True, exist_ok=True)
        dst = ROOT_DIR / "blobs" / iid
        if not dst.exists():
            import shutil
            shutil.copyfile(src, dst)
        t["items"][iid] = rec
        _save(t)
        return iid


def add_meeting(user_id, subject, recording_item=None, vtt_text=None, attendees=(), start=None):
    with _lock:
        t = _load()
        mid = "m-" + secrets.token_hex(6)
        t["meetings"][mid] = {"id": mid, "subject": subject, "organizer": user_id, "recording_item": recording_item,
                              "vtt": vtt_text, "attendees": list(attendees), "start": start or _now()}
        _save(t)
        return mid


def issue_token(scopes=None, hours=1):
    """A token for tests and seeding (the real sign-in is the device code flow below)."""
    with _lock:
        t = _load()
        tok = secrets.token_urlsafe(24)
        t["tokens"][tok] = {"scopes": scopes or ADMIN_SCOPES, "expires": time.time() + hours * 3600, "user": "admin"}
        _save(t)
        return tok


def set_flags(item_id, **flags):
    """Protect / share / change an item in the mock (retention label, hold, shared scope, checked_out)."""
    with _lock:
        t = _load()
        t["items"][item_id].update(flags)
        t["items"][item_id]["etag"] += 1
        _save(t)


def overwrite(item_id, data: bytes):
    """Someone edits the file in the cloud: new content, new ETag, new hash."""
    with _lock:
        t = _load()
        it = t["items"][item_id]
        (ROOT_DIR / "blobs" / item_id).write_bytes(data)
        it["etag"] += 1
        it["size"] = len(data)
        from .quickxor import QuickXorHash
        it["quickxor"] = QuickXorHash().update(data).b64()
        _save(t)


def corrupt_blob(item_id):
    """The stored bytes differ from the hash the API reports (to test that a bad download is refused)."""
    blob = ROOT_DIR / "blobs" / item_id
    d = bytearray(blob.read_bytes())
    d[len(d) // 2] ^= 0xFF
    blob.write_bytes(bytes(d))


def item(item_id):
    return _load()["items"][item_id]


def fault(throttle=0):
    """The next `throttle` requests answer 429 Retry-After: 1 (to test the client's back-off)."""
    _state["throttle"] = throttle


# ------------------------------------------------------------------ helpers: output shapes
def _err(status, code, msg, headers=None):
    return JSONResponse({"error": {"code": code, "message": msg}}, status_code=status, headers=headers or {})


def _path(t, folder):
    """/drive/root:/Recordings/Meetings, as Graph writes parentReference.path."""
    parts, cur = [], folder
    while cur and cur["parent"]:                 # the root has no parent and is not part of the path
        parts.append(cur["name"])
        cur = t["items"].get(cur["parent"])
    return "/drive/root:" + ("/" + "/".join(reversed(parts)) if parts else "")


def _dto(t, it):
    parent = t["items"].get(it["parent"]) if it["parent"] else None
    out = {"id": it["id"], "name": it["name"], "size": it["size"], "eTag": f'"{{{it["id"]}}},{it["etag"]}"',
           "cTag": f'"c:{{{it["id"]}}},{it["etag"]}"', "createdDateTime": it["created"], "lastModifiedDateTime": it["modified"],
           "webUrl": f"https://mock.sharepoint.example/{it['drive']}/{it['id']}",
           "createdBy": {"user": {"id": it["owner"], "displayName": (t["users"].get(it["owner"]) or {}).get("displayName", "")}},
           "parentReference": ({"driveId": it["drive"], "id": parent["id"], "path": _path(t, parent)} if parent else {"driveId": it["drive"]})}
    if it["folder"]:
        out["folder"] = {"childCount": sum(1 for i in t["items"].values() if i["parent"] == it["id"] and not i["deleted"])}
    else:
        out["file"] = {"mimeType": it["mime"], "hashes": {"quickXorHash": it["quickxor"], "sha1Hash": it["sha1"]}}
    if it["shared"]:
        out["shared"] = {"scope": it["shared"]}
    if it["checked_out"]:
        out["publication"] = {"level": "checkout", "versionId": "0.1"}
    return out


def _auth(t, authorization, scope):
    """-> (user record, None) or (None, error response). Mirrors Graph: 401 without / with an unknown token, 403 without
    the permission (scope) the call needs."""
    if _state["throttle"] > 0:
        _state["throttle"] -= 1
        return None, _err(429, "activityLimitReached", "Too many requests", {"Retry-After": "1"})
    if not authorization or not authorization.lower().startswith("bearer "):
        return None, _err(401, "InvalidAuthenticationToken", "Access token is empty.")
    tok = t["tokens"].get(authorization.split(" ", 1)[1])
    if not tok or tok["expires"] < time.time():
        return None, _err(401, "InvalidAuthenticationToken", "Access token has expired or is not yet valid.")
    scopes = tok["scopes"]
    ok = scope in scopes or (scope.startswith("Files.Read") and "Files.ReadWrite.All" in scopes)
    if not ok:
        return None, _err(403, "accessDenied", f"Access denied: the token does not have {scope}.")
    return tok, None


# ------------------------------------------------------------------ OAuth device code flow
@router.post("/oauth2/v2.0/devicecode")
async def devicecode(request: Request):
    form = dict((await request.form()).items())
    with _lock:
        t = _load()
        code = secrets.token_hex(16)
        user_code = secrets.token_hex(4).upper()
        t["devicecodes"][code] = {"user_code": user_code, "scope": form.get("scope", ""), "approved": False, "created": time.time(),
                                  "polls": 0}
        _save(t)
    return {"device_code": code, "user_code": user_code, "verification_uri": "http://127.0.0.1:8000/mock-graph/device",
            "expires_in": 900, "interval": 1, "message": f"Open http://127.0.0.1:8000/mock-graph/device and enter {user_code}"}


@router.get("/device")
def device_page():
    return Response("<h3>Mock Microsoft sign-in</h3><p>In the mock tenant a device code is approved automatically on its "
                    "second poll, as the tenant administrator.</p>", media_type="text/html")


@router.post("/oauth2/v2.0/token")
async def token(request: Request):
    form = dict((await request.form()).items())
    with _lock:
        t = _load()
        dc = t["devicecodes"].get(form.get("device_code", ""))
        if not dc:
            return _err(400, "invalid_grant", "unknown device code")
        dc["polls"] += 1
        if dc["polls"] < 2:
            _save(t)
            return JSONResponse({"error": "authorization_pending", "error_description": "waiting for the user"}, status_code=400)
        scopes = [s for s in dc["scope"].split() if s != "offline_access"] or ADMIN_SCOPES
        tok = secrets.token_urlsafe(24)
        t["tokens"][tok] = {"scopes": scopes, "expires": time.time() + 3600, "user": "admin"}
        del t["devicecodes"][form["device_code"]]
        _save(t)
    return {"token_type": "Bearer", "access_token": tok, "expires_in": 3600, "scope": " ".join(scopes)}


# ------------------------------------------------------------------ users and drives
@router.get("/v1.0/users")
def users(authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "User.Read.All")
    if e:
        return e
    return {"value": [{k: u[k] for k in ("id", "userPrincipalName", "displayName", "mail")} for u in t["users"].values()]}


@router.get("/v1.0/users/{uid}/drive")
def user_drive(uid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "Files.Read.All")
    if e:
        return e
    u = next((x for x in t["users"].values() if uid in (x["id"], x["userPrincipalName"])), None)
    if not u:
        return _err(404, "itemNotFound", "User not found")
    d = t["drives"][u["driveId"]]
    return {"id": d["id"], "driveType": d["driveType"], "name": d["name"], "owner": {"user": {"id": u["id"], "displayName": u["displayName"]}}}


def _children(t, drive, parent_id):
    return sorted((i for i in t["items"].values() if i["parent"] == parent_id and i["drive"] == drive and not i["deleted"]),
                  key=lambda i: (not i["folder"], i["name"].lower()))


@router.get("/v1.0/drives/{drive}/root")
def drive_root(drive: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "Files.Read.All")
    if e:
        return e
    if drive not in t["drives"]:
        return _err(404, "itemNotFound", "Drive not found")
    return _dto(t, t["items"][t["drives"][drive]["root"]])


@router.get("/v1.0/drives/{drive}/root/children")
def root_children(drive: str, request: Request, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "Files.Read.All")
    if e:
        return e
    if drive not in t["drives"]:
        return _err(404, "itemNotFound", "Drive not found")
    return _page(t, request, _children(t, drive, t["drives"][drive]["root"]))


@router.get("/v1.0/drives/{drive}/items/{iid}/children")
def item_children(drive: str, iid: str, request: Request, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "Files.Read.All")
    if e:
        return e
    it = t["items"].get(iid)
    if not it or it["drive"] != drive or it["deleted"] or not it["folder"]:
        return _err(404, "itemNotFound", "The resource could not be found.")
    return _page(t, request, _children(t, drive, iid))


def _page(t, request, items, size=3):
    """Pages of `size` with @odata.nextLink, as Graph pages long listings: the client must follow the links."""
    skip = int(request.query_params.get("$skiptoken", "0"))
    chunk = items[skip:skip + size]
    out = {"value": [_dto(t, i) for i in chunk]}
    if skip + size < len(items):
        base = str(request.url).split("?")[0]
        out["@odata.nextLink"] = f"{base}?$skiptoken={skip + size}"
    return out


@router.get("/v1.0/drives/{drive}/items/{iid}")
def get_item(drive: str, iid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "Files.Read.All")
    if e:
        return e
    it = t["items"].get(iid)
    if not it or it["drive"] != drive or it["deleted"]:
        return _err(404, "itemNotFound", "The resource could not be found.")
    return _dto(t, it)


@router.get("/v1.0/drives/{drive}/items/{iid}/content")
def content(drive: str, iid: str, request: Request, authorization: str = Header(None), range: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "Files.Read.All")
    if e:
        return e
    it = t["items"].get(iid)
    if not it or it["drive"] != drive or it["deleted"] or it["folder"]:
        return _err(404, "itemNotFound", "The resource could not be found.")
    data = (ROOT_DIR / "blobs" / iid).read_bytes()
    if range and range.startswith("bytes="):
        a, _, b = range[6:].partition("-")
        a, b = int(a or 0), int(b) if b else len(data) - 1
        return Response(data[a:b + 1], status_code=206, headers={"Content-Range": f"bytes {a}-{b}/{len(data)}"},
                        media_type="application/octet-stream")
    return Response(data, media_type="application/octet-stream")


@router.get("/v1.0/drives/{drive}/items/{iid}/permissions")
def permissions(drive: str, iid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "Files.Read.All")
    if e:
        return e
    it = t["items"].get(iid)
    if not it or it["deleted"]:
        return _err(404, "itemNotFound", "The resource could not be found.")
    perms = [{"id": "owner", "roles": ["owner"], "grantedToV2": {"user": {"id": it["owner"]}}}]
    if it["shared"]:
        perms.append({"id": "link", "roles": ["read"], "link": {"scope": it["shared"], "type": "view"}})
    return {"value": perms}


@router.get("/v1.0/drives/{drive}/items/{iid}/retentionLabel")
def retention(drive: str, iid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "Files.Read.All")
    if e:
        return e
    it = t["items"].get(iid)
    if not it or it["deleted"]:
        return _err(404, "itemNotFound", "The resource could not be found.")
    if not it["retention"]:
        return _err(404, "itemNotFound", "No retention label is applied to this item.")
    return {"name": it["retention"], "retentionSettings": {"behaviorDuringRetentionPeriod": "retain"}, "isLabelAppliedExplicitly": True}


@router.patch("/v1.0/drives/{drive}/items/{iid}")
async def patch_item(drive: str, iid: str, request: Request, authorization: str = Header(None), if_match: str = Header(None)):
    """Move (parentReference.id) and/or rename."""
    with _lock:
        t = _load()
        _, e = _auth(t, authorization, "Files.ReadWrite.All")
        if e:
            return e
        it = t["items"].get(iid)
        if not it or it["drive"] != drive or it["deleted"]:
            return _err(404, "itemNotFound", "The resource could not be found.")
        if if_match and if_match != _dto(t, it)["eTag"]:
            return _err(412, "preconditionFailed", "The request ETag does not match the item.")
        if it["hold"] or it["checked_out"]:
            return _err(423, "resourceLocked", "The item is locked (hold / checked out).")
        body = await request.json()
        if body.get("parentReference", {}).get("id"):
            if body["parentReference"]["id"] not in t["items"]:
                return _err(404, "itemNotFound", "Target folder not found.")
            it["parent"] = body["parentReference"]["id"]
        if body.get("name"):
            it["name"] = body["name"]
        it["etag"] += 1
        it["modified"] = _now()
        _save(t)
        return _dto(t, it)


@router.post("/v1.0/drives/{drive}/items/{iid}/children")
async def create_folder(drive: str, iid: str, request: Request, authorization: str = Header(None)):
    with _lock:
        t = _load()
        _, e = _auth(t, authorization, "Files.ReadWrite.All")
        if e:
            return e
        body = await request.json()
        parent = t["items"].get(iid if iid != "root" else t["drives"][drive]["root"])
        if not parent:
            return _err(404, "itemNotFound", "Parent not found.")
        hit = next((i for i in t["items"].values() if i["parent"] == parent["id"] and i["name"] == body["name"] and not i["deleted"]), None)
        if hit:
            return _err(409, "nameAlreadyExists", "The specified item name already exists.")
        new = "i-" + secrets.token_hex(6)
        t["items"][new] = _item_record(new, body["name"], parent["id"], drive, True, 0, parent["owner"])
        _save(t)
        return JSONResponse(_dto(t, t["items"][new]), status_code=201)


@router.delete("/v1.0/drives/{drive}/items/{iid}")
def delete_item(drive: str, iid: str, authorization: str = Header(None), if_match: str = Header(None)):
    """As in Graph: the item goes to the recycle bin (recoverable), not gone for good. 412 if the ETag no longer matches."""
    with _lock:
        t = _load()
        _, e = _auth(t, authorization, "Files.ReadWrite.All")
        if e:
            return e
        it = t["items"].get(iid)
        if not it or it["drive"] != drive or it["deleted"]:
            return _err(404, "itemNotFound", "The resource could not be found.")
        if if_match and if_match != _dto(t, it)["eTag"]:
            return _err(412, "preconditionFailed", "The request ETag does not match the item.")
        if it["hold"] or it["retention"] or it["checked_out"]:
            return _err(423, "resourceLocked", "The item is protected by a hold / retention label or is checked out.")
        it["deleted"] = {"at": _now(), "parent": it["parent"]}
        _save(t)
        return Response(status_code=204)


@router.post("/v1.0/drives/{drive}/items/{iid}/restore")
async def restore(drive: str, iid: str, authorization: str = Header(None)):
    with _lock:
        t = _load()
        _, e = _auth(t, authorization, "Files.ReadWrite.All")
        if e:
            return e
        it = t["items"].get(iid)
        if not it or not it["deleted"]:
            return _err(404, "itemNotFound", "The item is not in the recycle bin.")
        it["parent"] = it["deleted"]["parent"]
        it["deleted"] = None
        it["etag"] += 1
        _save(t)
        return _dto(t, it)


@router.post("/v1.0/$batch")
async def batch(request: Request, authorization: str = Header(None)):
    """A few requests in one call (the client uses it to read many items). Each sub-request answers on its own."""
    body = await request.json()
    out = []
    for r in body.get("requests", []):
        url = r["url"].lstrip("/")
        m = None
        import re
        m = re.match(r"drives/([^/]+)/items/([^/?]+)(/permissions|/retentionLabel)?$", url)
        if not m or r.get("method", "GET") != "GET":
            out.append({"id": r["id"], "status": 400, "body": {"error": {"code": "invalidRequest", "message": "unsupported batch request"}}})
            continue
        fn = {None: get_item, "/permissions": permissions, "/retentionLabel": retention}[m.group(3)]
        res = fn(m.group(1), m.group(2), authorization=authorization)
        payload = json.loads(res.body) if isinstance(res, JSONResponse) else res
        out.append({"id": r["id"], "status": res.status_code if isinstance(res, JSONResponse) else 200, "body": payload})
    return {"responses": out}


# ------------------------------------------------------------------ Teams meeting artifacts
@router.get("/v1.0/users/{uid}/onlineMeetings")
def meetings(uid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "OnlineMeetingTranscript.Read.All")
    if e:
        return e
    u = next((x for x in t["users"].values() if uid in (x["id"], x["userPrincipalName"])), None)
    return {"value": [{"id": m["id"], "subject": m["subject"], "startDateTime": m["start"], "participants": {"attendees": [
        {"upn": a} for a in m["attendees"]]}} for m in t["meetings"].values() if u and m["organizer"] == u["id"]]}


@router.get("/v1.0/users/{uid}/onlineMeetings/{mid}/transcripts")
def transcripts(uid: str, mid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "OnlineMeetingTranscript.Read.All")
    if e:
        return e
    m = t["meetings"].get(mid)
    if not m or not m["vtt"]:
        return {"value": []}
    return {"value": [{"id": f"{mid}-t1", "meetingId": mid, "createdDateTime": m["start"]}]}


@router.get("/v1.0/users/{uid}/onlineMeetings/{mid}/transcripts/{tid}/content")
def transcript_content(uid: str, mid: str, tid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "OnlineMeetingTranscript.Read.All")
    if e:
        return e
    m = t["meetings"].get(mid)
    if not m or not m["vtt"]:
        return _err(404, "itemNotFound", "No transcript.")
    return Response(m["vtt"], media_type="text/vtt")


@router.get("/v1.0/users/{uid}/onlineMeetings/{mid}/recordings")
def meeting_recordings(uid: str, mid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "OnlineMeetingRecording.Read.All")
    if e:
        return e
    m = t["meetings"].get(mid)
    return {"value": [{"id": f"{mid}-r1", "meetingId": mid, "driveItemId": m["recording_item"]}] if m and m["recording_item"] else []}


@router.get("/v1.0/users/{uid}/onlineMeetings/{mid}/attendanceReports")
def attendance(uid: str, mid: str, authorization: str = Header(None)):
    t = _load()
    _, e = _auth(t, authorization, "OnlineMeetingTranscript.Read.All")
    if e:
        return e
    m = t["meetings"].get(mid)
    if not m:
        return {"value": []}
    return {"value": [{"id": f"{mid}-a1", "totalParticipantCount": len(m["attendees"]),
                       "attendanceRecords": [{"emailAddress": a, "identity": {"displayName": a.split("@")[0].replace(".", " ").title()},
                                              "totalAttendanceInSeconds": 3000} for a in m["attendees"]]}]}


@router.post("/_admin/fault")
async def admin_fault(request: Request):
    body = await request.json()
    fault(int(body.get("throttle", 0)))
    return {"throttle": _state["throttle"]}


# ------------------------------------------------------------------ demo tenant
def seed_demo():
    """A leaver (Alex Leaver) whose OneDrive holds the public test recordings and decks: a normal meeting recording, a
    synthetic network review with its deck, one recording shared with an anonymous link, one under a retention label,
    and documents. Ericsson material is never used here."""
    from .config import ROOT
    reset(keep_tokens=True)
    uid = add_user("alex.leaver@contoso.example", "Alex Leaver")
    add_user("jamie.manager@contoso.example", "Jamie Manager")
    drive = _load()["users"][uid]["driveId"]
    plan = [("/Recordings/2026", "Network review meeting.mp4", "archive/library-2026-11-12/data/Demo - network review/Network review meeting.mp4", {}),
            ("/Recordings/2026", "Staff meeting (Teams).mp4", "test-material/demo/staff meeting - Teams download.mp4", {}),
            ("/Recordings/2026", "Customer call - shared link.mp4", "videos/videoplayback.mp4", {"shared": "anonymous"}),
            ("/Recordings/Legal", "Growth data science staff meeting.mp4", "videos/sec-growth-datascience-staff-meeting-sep-14-2022-ytmp4.savetube.vip.mp4",
             {"retention": "Legal hold 2026"}),
            ("/Documents", "Network review deck.pdf", "archive/library-2026-11-12/data/Demo - network review/Network review deck.pdf", {}),
            ("/Documents", "FY27 order modifications.pdf", "videos/video and ppt/Presentation re Potential Modifications to FY27 UVMMC Order - 11.4.26.pdf", {})]
    added = []
    for folder, name, rel, kw in plan:
        src = ROOT / rel
        if src.exists():
            add_file(drive, folder, name, src, **kw)
            added.append(name)
    return {"user": "alex.leaver@contoso.example", "files": added}
