"""Take the screenshots the video uses from the RUNNING app (http://localhost:8000), so the video always shows the real dashboard.

Run again after the dashboard changes:   python pitch/capture.py
Uses headless Microsoft Edge (already on Windows). Output: pitch/shots/*.png
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
PAGES = {                       # name: (path, window height)
    "overview": ("/overview", 1100),
    "search": ("/search?q=jvm&type=page", 1080),
    "transcript": ("/transcripts/5", 1080),
    "duplicates_tall": ("/duplicates", 2200),
    "storage_tall": ("/storage", 1800),
}
(HERE / "shots").mkdir(exist_ok=True)
for name, (path, h) in PAGES.items():
    out = HERE / "shots" / f"{name}.png"
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size=1920,{h}", "--virtual-time-budget=30000", f"--screenshot={out}", BASE + path],
                   capture_output=True, timeout=180)
    print(name, out.stat().st_size if out.exists() else "FAILED")
