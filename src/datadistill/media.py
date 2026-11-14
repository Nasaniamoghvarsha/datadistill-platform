"""ffmpeg/ffprobe helpers. Always use the ffmpeg CLI (subprocess): GPL build, never linked."""
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from .config import FFMPEG, FFPROBE, FRAME_FPS, FRAME_WIDTH

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, creationflags=NO_WINDOW, **kw)


def sha256(path: Path, chunk=4 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def probe(path: Path) -> dict:
    out = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
              capture_output=True, text=True).stdout
    info = json.loads(out)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {
        "duration": float(info.get("format", {}).get("duration", 0) or 0),
        "width": int(v.get("width", 0) or 0),
        "height": int(v.get("height", 0) or 0),
        "video_codec": v.get("codec_name"),
        "has_audio": a is not None,
        "bit_rate": int(info.get("format", {}).get("bit_rate", 0) or 0),
    }


# Measured on a human-transcribed meeting (AMI ES2008a): loudness normalisation lowered word errors
# 29.8% -> 26.9% and raised content words found 83.9% -> 87.4% (quiet and distant speakers become audible).
SPEECH_FILTER = "highpass=f=80,loudnorm=I=-18:TP=-2"


def load_audio(path: Path, sr=16000, enhance=True, start=None, duration=None) -> np.ndarray:
    """Decode to float32 mono, loudness-normalised for speech recognition (optionally only a piece).
    (faster-whisper's own PyAV decoder breaks with PyAV >= 19, so the ffmpeg CLI is used.)"""
    cmd = [FFMPEG, "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.2f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.2f}"]
    cmd += ["-i", str(path), "-vn"]
    if enhance:
        # mono at the target rate FIRST, then the speech filter (it was run on full stereo 48 kHz before), plus an
        # inaudible -100 dB noise floor so digital silence never becomes "denormal" numbers that stall the filter.
        # Measured on 10 min: 64.6 s -> 8.5 s, identical output (correlation 1.000, same loudness).
        cmd += ["-filter_complex", f"[0:a]aformat=channel_layouts=mono,aresample={sr}[a];"
                                   f"anoisesrc=r={sr}:a=0.00001:c=white[n];"
                                   f"[a][n]amix=inputs=2:duration=first:normalize=0,{SPEECH_FILTER}[out]",
                "-map", "[out]"]
    raw = run(cmd + ["-f", "f32le", "-ac", "1", "-ar", str(sr), "-"], capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.float32).copy()
    # Digital silence (muted parts, generated or screen-recorded audio) comes out of the loudness filter as
    # extremely small "denormal" numbers, which made the speech detector ~15x slower (20 min: 35.6 s -> 2.4 s
    # once zeroed, same speech found). Anything below -120 dB is inaudible: make it exactly 0.
    a[np.abs(a) < 1e-6] = 0.0
    return a


def extract_frames(path: Path, out_dir: Path, duration: float, progress=None, cancel=None):
    """Write 1 fps JPEG frames (width FRAME_WIDTH) as out_dir/000001.jpg ... Frame k is at t = k-1 seconds."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # out_range=full + yuvj420p: limited-range sources (common in web/re-encoded video) otherwise make
    # ffmpeg's JPEG encoder refuse to start. min(..., iw): never upscale low-resolution recordings.
    vf = f"fps={FRAME_FPS},scale='min({FRAME_WIDTH},iw)':-2:out_range=full,format=yuvj420p"
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(path), "-vf", vf,
           "-q:v", "3", "-progress", "pipe:1", "-nostats", str(out_dir / "%06d.jpg")]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=NO_WINDOW)
    for line in p.stdout:
        if line.startswith("out_time_us=") and progress and duration:
            try:
                progress(min(1.0, int(line.split("=")[1]) / 1e6 / duration))
            except ValueError:
                pass
        if cancel and cancel():
            p.kill()
            raise InterruptedError("cancelled")
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("ffmpeg frame extraction failed: " + p.stderr.read()[-500:])
    return sorted(out_dir.glob("*.jpg"))
