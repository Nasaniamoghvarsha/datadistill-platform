# Pitch video

A 60-second pitch video built from code. Everything in it comes from `metrics.json` and from screenshots of the running app.

    python pitch/capture.py        take fresh screenshots of the running app (http://localhost:8000)  -> pitch/shots (not in git)
    pitch\build.bat                build pitch/out/datadistill-pitch.mp4   (add --preview for a fast low-res check)
    pitch\build.bat --stills       write a few still frames per scene to pitch/out/stills
    pitch\build.bat --voice-only   voice + timing report only

* **Numbers and claims**: edit `metrics.json` (sentences the voice speaks are derived from it; the rail and captions too).
* **Structure**: the video follows the seven judging criteria. A rail at the bottom lights the criterion being shown, with one line of evidence.
* **Voice**: Kokoro-82M (Apache-2.0), local. Models are in `pitch/models` (not in git): kokoro-v1.0.int8.onnx + voices-v1.0.bin from
  github.com/thewh1teagle/kokoro-onnx (release model-files-v1.0). Install: `python -m venv pitch/.venv-tts` then `pip install kokoro-onnx soundfile`.
* **Music and sound effects**: synthesised with numpy in `audio.py`; no samples, no licences.
* The screenshots show the Ericsson-provided test material: they are not committed. Do not publish the video without checking them.
