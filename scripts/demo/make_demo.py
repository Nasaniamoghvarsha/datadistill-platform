"""Record the DataDistill demo video: every use case of the brief, on real public meetings, from an empty library.

    python scripts/demo/make_demo.py [--out demo/DataDistill-demo.mp4]

Needs: the app running on http://localhost:8000 with an EMPTY library, Ollama with ministral-3:3b, and the material
in test-material/ (see MATERIAL below). Processing waits are cut out of the video; each cut is marked "… later".
Captions name the use case (IDs from the challenge brief).
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from recorder import Recorder  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
APP = "http://localhost:8000"
TM = ROOT / "test-material"
FDA = TM / "fda-dhac-2025-11-06"
DEMO = TM / "demo"
MATERIAL = {
    "fda_video": DEMO / "FDA DHAC 2025-11-06 morning.mp4",
    "fda_slides": [FDA / "slides" / n for n in ("FDA - Gioia Guerrieri.pdf", "FDA - Jay Gupta.pdf", "FDA - Pamela Scott.pdf",
                                                  "Anthony Becker.pdf", "Vaile Wright.pdf")],
    "vt_video": ROOT / "videos" / "video and ppt" / "videoplayback (1).mp4",
    "vt_pdf": ROOT / "videos" / "video and ppt" / "Presentation re Potential Modifications to FY27 UVMMC Order - 11.4.26.pdf",
    "sec_video": ROOT / "videos" / "sec-growth-datascience-staff-meeting-sep-14-2022-ytmp4.savetube.vip.mp4",
    "sec_reencoded": DEMO / "staff meeting - Teams download.mp4",
    "leaver_video": ROOT / "videos" / "videoplayback.mp4",
}
F_FDA, F_VT, F_SEC, F_SHARED, F_LEAVER = ("FDA advisory committee", "Vermont board (Teams)", "SEC staff meeting",
                                          "Shared drive", "Leaver - A. Jensen")


def api(path, method="GET", body=None):
    req = urllib.request.Request(APP + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def files(folder=None):
    fs = api("/api/files")
    return [f for f in fs if folder is None or f["folder"] == folder]


def file_id(folder, name_part):
    return next(f["id"] for f in files(folder) if name_part.lower() in f["name"].lower())


def settled(folder):
    """Every file of the folder is finished (processed / shortcut / duplicate / error) and no job is running."""
    fs = files(folder)
    return fs and all(f["status"] in ("processed", "shortcut", "duplicate", "error") for f in fs) and \
        api("/api/stats").get("active_jobs", 0) == 0


async def new_folder(r, name):
    await r.click("New", tags="button", exact=True)
    await r.type(name, css="input.field")
    await r.click("Create", tags="button", exact=True)
    await r.hold(0.8)


async def scene_upload(r):
    r.caption("R1 · Upload meetings with their slides", "One folder per meeting. Recordings and the presentations shown in them go in together.")
    await r.click("Upload", tags="a", exact=True)
    await r.hold(1.0)
    await new_folder(r, F_FDA)
    await r.upload([MATERIAL["fda_video"], *MATERIAL["fda_slides"]])
    await r.hold(3.0)
    r.caption("Batch upload, first in first out", "Documents are read first, so their slides are known when the recording is transcribed.")
    await r.hold(3.0)
    await new_folder(r, F_VT)
    await r.upload([MATERIAL["vt_video"], MATERIAL["vt_pdf"]])
    await r.hold(2.0)
    await new_folder(r, F_SEC)
    await r.upload([MATERIAL["sec_video"]])
    await r.hold(2.5)
    r.caption("Runs on this laptop", "Whisper on the GPU, voices, OCR and slide matching. Nothing leaves the machine.")
    await r.wait_cut_fn(lambda: settled(F_FDA) and settled(F_VT) and settled(F_SEC), later="Processing 2 h 15 min of meetings")
    await r.click(F_FDA, tags="div", nth=0)
    await r.hold(2.0)


async def scene_duplicates_on_upload(r):
    r.caption("R2 · SD3 · Same file uploaded again", "Fingerprint (SHA-256) checked in the browser before uploading: no second copy is stored.")
    await new_folder(r, F_SHARED)
    copy = DEMO / "Presentation re Potential Modifications to FY27 UVMMC Order - 11.4.26 (1).pdf"
    shutil.copyfile(MATERIAL["vt_pdf"], copy)
    await r.upload([copy])
    await r.wait_text("already in DataDistill", 20)
    await r.hold(2.5)
    await r.click("Add 1 shortcut", tags="button")
    await r.hold(2.0)
    r.caption("R5 · The same recording in another file", "A re-encoded 'Teams download' of a meeting already transcribed: same picture and same words.")
    await r.upload([MATERIAL["sec_reencoded"]])
    await r.wait_cut_fn(lambda: any(f["status"] == "duplicate" for f in files(F_SHARED)), timeout=900, later="Checking the new file")
    await r.wait_text("This recording is already in DataDistill", 30)
    await r.hold(3.0)
    await r.click("Replace with shortcut", tags="button")
    await r.hold(2.5)


async def scene_meeting(r):
    fid = file_id(F_FDA, ".mp4")
    r.caption("UC-T1 · UC-T2 · Who said what, and when", "Speaker-attributed transcript with timestamps. Names come from self-introductions and the screen.")
    await r.goto(f"{APP}/transcripts/{fid}")
    await r.hold(3.0)
    await r.scroll(500)
    await r.hold(2.0)
    r.caption("Meeting summary by the local AI", "Ministral 3 3B on this laptop. Every point is checked against the transcript.")
    await r.point("Decisions", tags="h3")
    await r.hold(3.5)
    r.caption("R4 · Ask the meeting", "The answer comes only from the transcript and slides, with the lines it comes from.")
    await r.point("Ask this meeting", tags="h2")
    await r.type("What two vulnerabilities of large language models did Anthony Becker describe?", css="input[aria-label='Question about this meeting']", per_char=0.025)
    await r.click("Ask", tags="button", exact=True)
    await r.wait_js("document.body.innerText.includes('Where this comes from') || document.body.innerText.includes('not in the record')", 120)
    await r.hold(4.0)
    await r.click(css="button[title='Show in the transcript']")
    await r.hold(3.0)
    r.caption("UC-F1 to F4 · Slides found in the presentations", "Each shared screen is matched to the PDF: document and page, one stored image, a slide id.")
    await r.point(css="[title='Slide id: the same slide anywhere gets the same id']")
    await r.hold(4.0)
    await r.scroll(900)
    await r.hold(2.5)
    r.caption("UC-E1 · L1 · \"As you can see here\"", "Where a speaker refers to the screen, a snapshot is kept at that moment (≤ 2 MB per recording).")
    try:
        await r.point(css="button[title='Open snapshot']", timeout=5)
        await r.hold(3.0)
    except TimeoutError:
        await r.hold(1.0)
    r.caption("Speakers and voice samples", "Talk time per person and a 10-second voice sample, kept instead of the video.")
    await r.point("Speakers", tags="h2")
    await r.hold(4.0)


async def scene_teams(r):
    fid = file_id(F_VT, ".mp4")
    r.caption("A Microsoft Teams meeting", "Vermont Green Mountain Care Board on Teams: the shared slides are matched to the board's PDF.")
    await r.goto(f"{APP}/transcripts/{fid}")
    await r.hold(3.0)
    await r.scroll(700)
    await r.hold(3.0)


async def scene_search(r):
    r.caption("R3 · UC-S1 · Search what was said and shown", "Meaning, not only exact words. Each hit opens the line, with the slide that was on screen.")
    await r.click("Search", tags="a", exact=True)
    await r.type("rules for changing an AI model after approval", css="input[placeholder='Search transcripts and documents']", per_char=0.03)
    await r.press_enter(css="input[placeholder='Search transcripts and documents']")
    await r.hold(4.0)
    await r.click(css="main a[href*='/transcripts/']")
    await r.hold(3.5)


async def scene_documents(r):
    fid = file_id(F_FDA, "Pamela Scott")
    r.caption("UC-D1 · Presentations as text", "Every slide as text with its id. A line that also appears in other decks links to them.")
    await r.goto(f"{APP}/transcripts/{fid}")
    await r.hold(2.5)
    try:
        await r.click(css="button[title='The same text is stored once and referenced from each place']", timeout=5)
        await r.hold(3.0)
    except TimeoutError:
        await r.hold(1.0)
    r.caption("R2 · UC-D3 · Duplicates across all folders", "Copies stored once as shortcuts, the same recording in another file, shared slides and repeated lines.")
    await r.click("Duplicates", tags="a", exact=True)
    await r.hold(3.5)
    await r.scroll(900)
    await r.hold(3.0)
    await r.point("Repeated text", tags="h2")
    await r.hold(3.0)


async def scene_offboarding(r):
    leaver = ROOT / "data" / F_LEAVER
    leaver.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MATERIAL["sec_video"], leaver / "Team meeting 14 Sep.mp4")          # a copy of a meeting we have
    shutil.copyfile(MATERIAL["fda_slides"][2], leaver / "FDA - Pamela Scott.pdf")         # a copy of a deck we have
    shutil.copyfile(MATERIAL["leaver_video"], leaver / "Board meeting recording.mp4")     # only here
    r.caption("R6 · A leaver's folder", "On the file share: copies of content we already hold, and one recording that exists only here.")
    await r.click("Offboarding", tags="a", exact=True)
    await r.select(F_LEAVER)
    await r.click("Check folder", tags="button")
    await r.wait_text("Run offboarding", 60)
    await r.hold(4.0)
    r.caption("R6 · Delete only what is safe elsewhere", "Copies become shortcuts. The unique recording is transcribed, verified, and only then deleted.")
    await r.click("Run offboarding", tags="button", exact=True)
    await r.wait_text("Cancel", 10)
    await r.hold(2.0)
    await r.click("Run offboarding", tags="button", exact=True, nth=-1)       # the button in the confirm dialog
    await r.wait_cut_fn(lambda: api("/api/stats").get("active_jobs", 0) == 0 and settled(F_LEAVER),
                        later="Offboarding run")
    await r.goto(f"{APP}/offboarding")
    await r.hold(4.0)
    await r.scroll(600)
    await r.hold(3.0)


async def scene_storage(r):
    r.caption("P3 · How much storage is saved", "Brought in, stored now, and where the savings come from. Measured, not estimated.")
    await r.click("Storage", tags="a", exact=True)
    await r.hold(4.0)
    await r.scroll(700)
    await r.hold(3.5)
    await r.point("Savings scenarios", tags="h2")
    await r.hold(3.0)


async def scene_export(r):
    fid = file_id(F_FDA, ".mp4")
    r.caption("R4 · AI-ready output", "One JSON per meeting: speakers, lines, slide ids, slide prompts, snapshots, summary.")
    await r.goto(f"{APP}/api/export/{fid}?format=json", settle=1.5)
    await r.hold(3.0)
    await r.scroll(800)
    await r.hold(3.0)
    r.caption("Proof per file", "A PDF report per file: summary, people, quality, storage, the full transcript and its JSON.")
    await r.goto(f"{APP}/reports")
    await r.hold(4.0)


async def scene_end(r):
    r.caption("DataDistill", "Meeting recordings to AI-ready text, on one laptop.")
    await r.goto(f"{APP}/overview")
    await r.hold(6.0)


SCENES = [scene_upload, scene_duplicates_on_upload, scene_meeting, scene_teams, scene_search, scene_documents,
          scene_offboarding, scene_storage, scene_export, scene_end]


async def main(out, only=None, keep_library=False):
    missing = [str(p) for v in MATERIAL.values() for p in (v if isinstance(v, list) else [v]) if not Path(p).exists()]
    if missing:
        raise SystemExit("missing material:\n  " + "\n  ".join(missing))
    if files() and not keep_library:
        raise SystemExit("the library is not empty: start the demo from an empty library (or --keep-library to re-run scenes)")
    ff = glob.glob(str(Path.home() / r"AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe"))[0]
    r = Recorder(out)
    await r.start(f"{APP}/overview")
    r.caption("DataDistill", "Meeting recordings to speaker-attributed text with their slides: AI-ready, on one laptop.")
    await r.hold(4.0)
    try:
        for s in SCENES:
            if only and s.__name__ not in only:
                continue
            print("scene", s.__name__, flush=True)
            await s(r)
    finally:
        await r.stop()
        print(f"recorded {r.now():.0f} s, {len(r.frames)} frames; encoding", flush=True)
        r.encode(ff)
        r.cleanup()
    print("written", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "demo" / "DataDistill-demo.mp4"))
    ap.add_argument("--only", nargs="*", help="scene names, e.g. scene_meeting scene_search")
    ap.add_argument("--keep-library", action="store_true", help="run on the data already in the library")
    a = ap.parse_args()
    asyncio.run(main(Path(a.out), a.only, a.keep_library))
