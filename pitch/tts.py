"""Speech for the pitch video with Kokoro (82M, Apache-2.0), run with the small venv in pitch/.venv-tts.

Input  (stdin JSON): {"voice": "af_heart", "speed": 1.05, "items": [{"id": "hook_0", "text": "..."}], "out": "pitch/out/audio"}
Output: one WAV per item (24 kHz mono) and a JSON line {id: seconds}.
Everything is local: the model files are in pitch/models.
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

HERE = Path(__file__).resolve().parent


def main():
    job = json.load(sys.stdin)
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=True)
    k = Kokoro(str(HERE / "models" / "kokoro-v1.0.int8.onnx"), str(HERE / "models" / "voices-v1.0.bin"))
    durations = {}
    for it in job["items"]:
        samples, sr = k.create(it["text"], voice=it.get("voice") or job["voice"], speed=job["speed"], lang="en-us")
        samples = np.asarray(samples, dtype=np.float32)
        # trim silence at both ends so the timeline is tight (keep 60 ms)
        thr = 0.012
        idx = np.where(np.abs(samples) > thr)[0]
        if len(idx):
            a, b = max(0, idx[0] - int(0.06 * sr)), min(len(samples), idx[-1] + int(0.10 * sr))
            samples = samples[a:b]
        sf.write(out / f"{it['id']}.wav", samples, sr)
        durations[it["id"]] = len(samples) / sr
    print("DURATIONS " + json.dumps(durations))


if __name__ == "__main__":
    main()
