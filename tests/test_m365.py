"""Offboarding through Microsoft Graph, tested against the local mock tenant over real HTTP (graph.py is the same client
a real tenant would use). Processing of a video is replaced by a small stand-in: what is tested here is the safety of
what happens to the CLOUD files."""
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))
import _isolated  # noqa: E402,F401

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from datadistill import db, graph, graph_mock, library, m365  # noqa: E402
from datadistill.config import DATA_DIR, OUTPUT_DIR  # noqa: E402
from datadistill.quickxor import QuickXorHash  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def fake_process(file_id, ctx=None):
    """Stands in for the transcription pipeline: marks the file processed and writes a small transcript."""
    f = db.file_row(file_id)
    base = OUTPUT_DIR / "recordings" / str(file_id)
    base.mkdir(parents=True, exist_ok=True)
    (base / "transcript.json").write_text("{}", encoding="utf-8")
    low = 0.9 if "unreliable" in f["name"] else 0.02
    man = {"transcript": {"source": "whisper"}, "quality": {"low_confidence_share": low}}
    db.ex("INSERT OR REPLACE INTO recordings(file_id, manifest, speakers, participants) VALUES (?,?,?,?)", (file_id, json.dumps(man), "{}", "[]"))
    if "silent" not in f["name"]:
        db.ex("INSERT INTO segments(file_id, idx, start, end, speaker, text) VALUES (?,?,?,?,?,?)", (file_id, 0, 0, 5, "S1", "hello"))
    db.ex("UPDATE files SET status='processed' WHERE id=?", (file_id,))


class M365(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        app = FastAPI()
        app.include_router(graph_mock.router)
        cls.port = free_port()
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        graph.MOCK_BASE = f"http://127.0.0.1:{cls.port}/mock-graph/v1.0"
        graph.MOCK_AUTHORITY = f"http://127.0.0.1:{cls.port}/mock-graph"
        m365.pipeline.process_file = fake_process
        m365.summary.available = lambda: (False, "tests")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True

    def setUp(self):
        graph_mock.reset()
        for t in ("m365_plans", "m365_quarantine", "files", "segments", "recordings", "audit", "settings"):
            db.ex(f"DELETE FROM {t}")
        self.uid = graph_mock.add_user("alex.leaver@contoso.example", "Alex Leaver")
        self.drive = graph_mock._load()["users"][self.uid]["driveId"]
        self.tmp = Path(OUTPUT_DIR) / "_src"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.g = graph.Graph(token=graph_mock.issue_token(), sleep=lambda s: None)

    def file(self, name, size=3000, seed=1, **kw):
        p = self.tmp / name
        p.write_bytes(bytes((i * seed) % 251 for i in range(size)))
        return graph_mock.add_file(self.drive, kw.pop("folder", "/Recordings"), name, p, **kw)

    def plan(self, **opts):
        return m365.plan("alex.leaver@contoso.example", opts, g=self.g)

    def approve(self, p):
        return m365.approve(p["id"], "Sam Admin", "alex.leaver@contoso.example")

    def run_plan(self, p):
        m365.run(p["id"], g=self.g)
        return m365.get_plan(p["id"])

    # ---------------------------------------------------------------- the plan (dry run)
    def test_plan_changes_nothing_and_names_every_check(self):
        good = self.file("good.mp4")
        self.file("shared.mp4", seed=2, shared="anonymous")
        self.file("held.mp4", seed=3, retention="Legal hold 2026")
        self.file("deck.pdf", seed=4)
        self.file("notes.txt", seed=5)
        before = {k: v["etag"] for k, v in graph_mock._load()["items"].items()}
        p = self.plan()
        act = {i["name"]: i["action"] for i in p["items"]}
        self.assertEqual(act["good.mp4"], "distill_then_remove")
        self.assertEqual(act["shared.mp4"], "skip")
        self.assertEqual(act["held.mp4"], "skip")
        self.assertEqual(act["deck.pdf"], "copy_and_keep")
        self.assertEqual(act["notes.txt"], "ignore")
        held = next(i for i in p["items"] if i["name"] == "held.mp4")
        self.assertTrue(any("protected" in c["check"] and not c["ok"] for c in held["checks"]))
        self.assertEqual(before, {k: v["etag"] for k, v in graph_mock._load()["items"].items()})   # nothing changed in the cloud
        self.assertEqual(db.q1("SELECT COUNT(*) n FROM files")["n"], 0)                             # nor in the library
        self.assertTrue(good)

    def test_limits_leave_the_rest_for_another_run(self):
        for i in range(4):
            self.file(f"m{i}.mp4", seed=i + 1)
        p = self.plan(max_items=2)
        self.assertEqual(sum(i["action"] == "distill_then_remove" for i in p["items"]), 2)
        self.assertEqual(sum(i["action"] == "skip" for i in p["items"]), 2)

    # ---------------------------------------------------------------- approval
    def test_approval_needs_the_typed_address_and_a_second_person_when_required(self):
        self.file("a.mp4")
        p = self.plan(require_second_approver=True)
        with self.assertRaises(ValueError):
            m365.approve(p["id"], "Sam", "wrong@contoso.example")
        with self.assertRaises(ValueError):
            m365.approve(p["id"], "Sam", "alex.leaver@contoso.example")                       # no second approver
        with self.assertRaises(ValueError):
            m365.approve(p["id"], "Sam", "alex.leaver@contoso.example", "sam")                 # the same person
        self.assertEqual(m365.approve(p["id"], "Sam", "alex.leaver@contoso.example", "Kim")["status"], "approved")
        with self.assertRaises(ValueError):                                                    # cannot run an unapproved plan
            m365.run(self.plan()["id"], g=self.g)

    # ---------------------------------------------------------------- the run
    def test_distilled_video_is_quarantined_and_can_be_restored(self):
        vid = self.file("review.mp4")
        doc = self.file("deck.pdf", seed=4)
        p = self.approve(self.plan())
        done = self.run_plan(p)
        res = {r["name"]: r for r in done["report"]["files"]}
        self.assertEqual(res["review.mp4"]["result"], "quarantined")
        self.assertEqual(res["deck.pdf"]["result"], "kept")
        it = graph_mock.item(vid)
        self.assertEqual(graph_mock._load()["items"][it["parent"]]["name"], "plan-%d" % p["id"])      # moved, not deleted
        self.assertIsNone(it["deleted"])
        self.assertIsNone(graph_mock.item(doc)["deleted"])                                             # documents stay in the cloud
        q = m365.quarantine("quarantined")[0]
        self.assertGreater(q["days_left"], 13)
        self.assertEqual(m365.finalize(g=self.g)["deleted"], [])                                       # not due yet
        m365.restore(q["id"], g=self.g)
        self.assertEqual(graph_mock._load()["items"][graph_mock.item(vid)["parent"]]["name"], "Recordings")
        # the local working copy is gone, the distilled form stays
        f = db.q1("SELECT * FROM files WHERE name='review.mp4'")
        self.assertTrue(json.loads(f["meta"]).get("original_deleted"))
        self.assertFalse((DATA_DIR / f["path"]).exists())

    def test_finalize_deletes_only_what_is_due_and_checks_again(self):
        a, b = self.file("a.mp4"), self.file("b.mp4", seed=2)
        done = self.run_plan(self.approve(self.plan(quarantine_days=1)))
        self.assertEqual(done["report"]["summary"]["quarantined"], 2)
        qa, qb = (m365.quarantine("quarantined") + [None, None])[:2]
        db.ex("UPDATE m365_quarantine SET delete_after=? WHERE id=?", (time.time() - 5, qa["id"]))
        out = m365.finalize(g=self.g)
        self.assertEqual(out["deleted"], [qa["id"]])
        self.assertIsNotNone(graph_mock.item(qa["item_id"])["deleted"])                                # in the recycle bin
        self.assertIsNone(graph_mock.item(qb["item_id"])["deleted"])
        # a quarantined file that someone changed meanwhile is NOT deleted
        db.ex("UPDATE m365_quarantine SET delete_after=? WHERE id=?", (time.time() - 5, qb["id"]))
        graph_mock.overwrite(qb["item_id"], b"edited later")
        out = m365.finalize(g=self.g)
        self.assertEqual(out["deleted"], [])
        self.assertIn("changed", out["kept"][0]["reason"])
        self.assertTrue(a and b)

    def test_immediate_mode_goes_to_the_recycle_bin(self):
        vid = self.file("x.mp4")
        done = self.run_plan(self.approve(self.plan(mode="recycle_bin")))
        self.assertEqual(done["report"]["summary"]["recycled"], 1)
        self.assertIsNotNone(graph_mock.item(vid)["deleted"])
        m365.restore(m365.quarantine("recycled")[0]["id"], g=self.g)
        self.assertIsNone(graph_mock.item(vid)["deleted"])

    def test_file_changed_after_the_plan_is_left_alone(self):
        vid = self.file("c.mp4")
        p = self.approve(self.plan())
        graph_mock.overwrite(vid, b"someone edited this recording")
        done = self.run_plan(p)
        r = done["report"]["files"][0]
        self.assertEqual(r["result"], "kept")
        self.assertIn("plan again", r["note"])
        self.assertIsNone(graph_mock.item(vid)["deleted"])
        self.assertEqual(graph_mock._load()["items"][graph_mock.item(vid)["parent"]]["name"], "Recordings")

    def test_corrupt_download_is_refused(self):
        vid = self.file("d.mp4", size=20000)
        p = self.approve(self.plan())
        graph_mock.corrupt_blob(vid)                         # the bytes no longer match the hash the cloud reports
        r = self.run_plan(p)["report"]["files"][0]
        self.assertEqual(r["result"], "kept")
        self.assertIn("differs", r["note"])
        self.assertEqual(db.q1("SELECT COUNT(*) n FROM files")["n"], 0)                    # nothing kept locally either
        self.assertEqual(graph_mock._load()["items"][graph_mock.item(vid)["parent"]]["name"], "Recordings")

    def test_video_without_speech_or_with_unreliable_transcript_is_kept(self):
        a, b = self.file("silent.mp4"), self.file("unreliable.mp4", seed=2)
        done = self.run_plan(self.approve(self.plan()))
        notes = {r["name"]: r for r in done["report"]["files"]}
        self.assertEqual(notes["silent.mp4"]["result"], "kept")
        self.assertIn("no speech", notes["silent.mp4"]["note"])
        self.assertEqual(notes["unreliable.mp4"]["result"], "kept")
        self.assertIn("low confidence", notes["unreliable.mp4"]["note"])
        self.assertIsNone(graph_mock.item(a)["deleted"])
        self.assertEqual(m365.quarantine(), [])

    def test_the_server_refuses_a_stale_delete_even_if_the_client_forgets(self):
        vid = self.file("e.mp4")
        etag = self.g.item(self.drive, vid)["eTag"]
        graph_mock.overwrite(vid, b"changed")
        with self.assertRaises(graph.GraphError) as cm:
            self.g.delete(self.drive, vid, etag=etag)
        self.assertEqual(cm.exception.status, 412)
        self.assertIsNone(graph_mock.item(vid)["deleted"])

    def test_protected_items_cannot_be_deleted(self):
        vid = self.file("f.mp4", retention="Records 7y")
        with self.assertRaises(graph.GraphError) as cm:
            self.g.delete(self.drive, vid)
        self.assertEqual(cm.exception.status, 423)

    # ---------------------------------------------------------------- client behaviour
    def test_throttling_is_waited_out(self):
        self.file("t.mp4")
        waited = []
        g = graph.Graph(token=graph_mock.issue_token(), sleep=waited.append)
        graph_mock.fault(2)
        self.assertEqual(len(list(g.walk(self.drive))), 1)
        self.assertEqual(g.throttled, 2)
        self.assertEqual(waited, [1.0, 1.0])

    def test_listing_follows_the_paging_links(self):
        for i in range(8):
            self.file(f"p{i}.mp4", seed=i + 1)
        self.assertEqual(len(list(self.g.walk(self.drive))), 8)                         # the mock pages by 3

    def test_a_token_without_the_permission_gets_403(self):
        g = graph.Graph(token=graph_mock.issue_token(["User.Read.All"]), sleep=lambda s: None)
        with self.assertRaises(graph.GraphError) as cm:
            list(g.walk(self.drive))
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(graph.GraphError) as cm:
            graph.Graph(token="nonsense").users()
        self.assertEqual(cm.exception.status, 401)

    def test_device_code_sign_in(self):
        shown = []
        rec = graph.sign_in(on_code=lambda msg, url, code: shown.append(code), sleep=lambda s: None)
        self.assertTrue(rec["access_token"] and shown)
        self.assertTrue(graph.token_status()["signed_in"])
        self.assertTrue(graph.Graph().users())
        graph.sign_out()
        self.assertFalse(graph.token_status()["signed_in"])

    def test_teams_transcript_becomes_a_sidecar_with_speaker_names(self):
        vid = self.file("meeting.mp4")
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Alex Leaver>Welcome everyone.</v>\n"
        graph_mock.add_meeting(self.uid, "Weekly", recording_item=vid, vtt_text=vtt, attendees=["alex.leaver@contoso.example"])
        p = self.approve(self.plan())
        self.assertEqual(p["items"][0]["meeting"]["subject"], "Weekly")
        self.run_plan(p)
        self.assertTrue(any(f.suffix == ".vtt" for f in (DATA_DIR / library.clean_folder_name("M365 alex.leaver@contoso.example")).iterdir()))

    # ---------------------------------------------------------------- audit
    def test_audit_chain_detects_tampering(self):
        self.file("z.mp4")
        self.run_plan(self.approve(self.plan()))
        v = m365.verify_audit()
        self.assertTrue(v["ok"])
        self.assertGreater(v["entries"], 5)
        row = db.q1("SELECT id, details FROM audit WHERE action='m365.quarantined'")
        d = json.loads(row["details"])
        d["name"] = "something else.mp4"
        db.ex("UPDATE audit SET details=? WHERE id=?", (json.dumps(d), row["id"]))
        self.assertFalse(m365.verify_audit()["ok"])


class QuickXor(unittest.TestCase):
    def test_known_and_incremental(self):
        self.assertEqual(QuickXorHash().b64(), "AAAAAAAAAAAAAAAAAAAAAAAAAAA=")                  # empty file, documented value
        data = os.urandom(100_003)
        h = QuickXorHash()
        for i in range(0, len(data), 7777):
            h.update(data[i:i + 7777])
        self.assertEqual(h.b64(), QuickXorHash().update(data).b64())
        self.assertNotEqual(QuickXorHash().update(data).b64(), QuickXorHash().update(data[:-1] + b"x").b64())


if __name__ == "__main__":
    unittest.main()
