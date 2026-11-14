"""Downloads the small model files DataDistill needs that pip / Hugging Face do not fetch by themselves:

  .models/mediapipe/face_landmarker.task        face landmarks for mouth movement (Google MediaPipe, Apache-2.0, 3.7 MB)
  .models/tessdata/{eng,osd,swe,fra}.traineddata  OCR language data: names are read in English, Swedish and French
  (the speech, speaker, search and re-ranking models download into .models/ on first use;
   the language models come through Ollama:  ollama pull ministral-3:8b)

    python scripts/fetch_models.py            (asks nothing; run it once on a new laptop; skips files already there)
"""
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    ROOT / ".models/mediapipe/face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    ROOT / ".models/tessdata/swe.traineddata": "https://github.com/tesseract-ocr/tessdata_fast/raw/main/swe.traineddata",
    ROOT / ".models/tessdata/fra.traineddata": "https://github.com/tesseract-ocr/tessdata_fast/raw/main/fra.traineddata",
    ROOT / ".models/tessdata/eng.traineddata": "https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata",
    ROOT / ".models/tessdata/osd.traineddata": "https://github.com/tesseract-ocr/tessdata_fast/raw/main/osd.traineddata",
}


def main():
    for dest, url in FILES.items():
        if dest.exists() and dest.stat().st_size > 100_000:
            print("have   ", dest.relative_to(ROOT))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        print("fetch  ", dest.relative_to(ROOT), "<-", url)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(dest)
    print("done. Language models: ollama pull ministral-3:8b")
    return 0


if __name__ == "__main__":
    sys.exit(main())
