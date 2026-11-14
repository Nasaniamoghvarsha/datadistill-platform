"""Paths and settings. Loads .env from the project root before any model library is imported."""
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
os.environ.setdefault("HF_HOME", str(ROOT / ".models"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

DATA_DIR = Path(os.getenv("DD_DATA_DIR", ROOT / "data"))
OUTPUT_DIR = Path(os.getenv("DD_OUTPUT_DIR", ROOT / "output"))
QUARANTINE_DIR = DATA_DIR / "_quarantine"
TMP_DIR = OUTPUT_DIR / "_tmp"
DB_PATH = OUTPUT_DIR / "datadistill.db"

# Names on screen come in English, Swedish and French spelling (å ä ö é è ç): the extra language packs live in .models/tessdata
_TESSDATA = ROOT / ".models" / "tessdata"
if all((_TESSDATA / f"{l}.traineddata").exists() for l in ("eng", "swe", "fra")):
    os.environ.setdefault("TESSDATA_PREFIX", str(_TESSDATA))
    NAME_OCR_LANGS = "eng+swe+fra"
else:
    NAME_OCR_LANGS = "eng"
TESSERACT_CMD = os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "float16")
FACES = os.getenv("DD_FACES", "1") != "0"          # mouth movement of camera tiles (MediaPipe), see faces.py
OVERLAP = os.getenv("DD_OVERLAP", "mark")           # simultaneous speech: "mark" (flag it) or "off"
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")   # English, Swedish, French, ...
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")                  # multilingual re-ranker (Apache-2.0)

VIDEO_EXT = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
# a picture (a screenshot of a slide, a scanned page, a diagram) is a one-page document: read by OCR, compared by its
# text and by what it looks like, exactly like a page of a PDF
DOC_EXT = {".pdf", ".pptx"} | IMAGE_EXT
TRANSCRIPT_EXT = {".vtt", ".srt"}

FRAME_WIDTH = 1280      # analysis frames are scaled to this width
FRAME_FPS = 1           # analysis sampling rate
OPUS_BITRATE = "16k"    # retention tier T1 audio

# Shared/retained systems rank before personal ones when choosing a canonical copy.
SYSTEM_PRIORITY = {"sharepoint": 0, "ericsson_play": 1}


def system_rank(system: str) -> int:
    return SYSTEM_PRIORITY.get(system, 9 if is_personal(system) else 5)


def is_personal(system: str) -> bool:
    return system.lower().startswith(("onedrive", "personal", "home"))


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    # winget installs ffmpeg under LOCALAPPDATA; PATH may not be refreshed in this process yet
    base = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for p in base.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
        os.environ["PATH"] = str(p.parent) + os.pathsep + os.environ.get("PATH", "")
        return str(p)
    raise RuntimeError("ffmpeg not found on PATH")


FFMPEG = find_ffmpeg()
FFPROBE = str(Path(FFMPEG).with_name("ffprobe" + Path(FFMPEG).suffix))

for d in (DATA_DIR, OUTPUT_DIR, TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)
