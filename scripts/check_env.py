"""Environment check + model pre-download + optional Whisper speed benchmark.

Usage:
  .venv\\Scripts\\python.exe scripts\\check_env.py                     # tools, OCR, PDF, embeddings, models
  .venv\\Scripts\\python.exe scripts\\check_env.py --audio <file.mp4>  # + Whisper benchmark on a real recording
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / ".models"))

import torch  # noqa: E402  (load CUDA/cuDNN DLLs before ctranslate2 on Windows)

TESSERACT = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def ok(label, value=""):
    print(f"[OK]   {label} {value}")


def fail(label, err):
    print(f"[FAIL] {label}: {err}")


def check_tools():
    for exe in ("ffmpeg", "ffprobe"):
        p = shutil.which(exe)
        ok(exe, p) if p else fail(exe, "not on PATH")
    ok("tesseract", TESSERACT) if Path(TESSERACT).exists() else fail("tesseract", TESSERACT)
    ok("torch", f"{torch.__version__} cuda={torch.cuda.is_available()} "
       f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''}")


def check_ocr_and_pdf():
    import imagehash
    import pypdfium2  # noqa: F401
    import pytesseract
    from PIL import Image, ImageDraw, ImageFont

    pytesseract.pytesseract.tesseract_cmd = TESSERACT
    img = Image.new("RGB", (900, 160), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((30, 50), "Optical character check", fill="black", font=font)
    text = pytesseract.image_to_string(img).strip()
    (ok if "character" in text.lower() else fail)("tesseract OCR", repr(text))
    ok("pypdfium2 + imagehash", str(imagehash.phash(img)))


def check_embeddings():
    from sentence_transformers import SentenceTransformer

    t = time.time()
    m = SentenceTransformer(os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
                            device="cuda" if torch.cuda.is_available() else "cpu")
    v = m.encode(["slide deduplication", "duplicate slides across decks"], normalize_embeddings=True)
    ok("embeddings", f"dim={v.shape[1]} sim={float(v[0] @ v[1]):.2f} ({time.time() - t:.1f}s)")


def check_whisper(audio_file=None, minutes=10):
    from faster_whisper import WhisperModel
    import numpy as np

    name = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    t = time.time()
    model = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
    ok("whisper model", f"{name} on {device} ({time.time() - t:.1f}s load)")
    if not audio_file:
        return
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio_file), "-t", str(minutes * 60), "-vn", "-f", "f32le",
                          "-ac", "1", "-ar", "16000", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, dtype=np.float32)
    secs = len(audio) / 16000
    t = time.time()
    segs, _ = model.transcribe(audio, vad_filter=True, word_timestamps=True)
    n = len(list(segs))
    run = time.time() - t
    ok("whisper benchmark", f"{secs / 60:.1f} min audio in {run:.0f}s = {secs / run:.0f}x realtime, "
                            f"~{7200 / (secs / run) / 60:.1f} min per 2 h recording ({n} segments)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", help="a real recording to benchmark Whisper on (first --minutes are used)")
    ap.add_argument("--minutes", type=int, default=10)
    a = ap.parse_args()
    check_tools()
    check_ocr_and_pdf()
    check_embeddings()
    check_whisper(a.audio, a.minutes)


if __name__ == "__main__":
    sys.exit(main())
