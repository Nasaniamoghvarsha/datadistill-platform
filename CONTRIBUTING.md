# Working on DataDistill together

## Rule zero: partner data stays out of git

**Not in git, kept on each person's disk:** the public test material (`videos/`, `test-material/`, `archive/`, the pitch
videos and decks). They are large and are ignored by `.gitignore` (see [docs/TEST_MATERIAL.md](docs/TEST_MATERIAL.md) for
what they are and how to get the same set). The unit tests do not need them.

**Never in the repo:** Ericsson's material (`ericsson data/`), the live library (`/data/`, `/output/`) and model files
(`.models/`). `.gitignore` blocks them; do not force-add them (`git add -f`). They must be deleted after the event, which
is impossible once they are in git history. Ericsson's files are shared inside the team directly, not through GitHub.
Do not paste real participants' names or partner slide text into code, tests or docs either: use made-up names
("Alex", "Maria") in tests. If something slipped in, tell the team before pushing.

## Set up (Windows, NVIDIA GPU recommended)

```bash
git clone https://github.com/Nasaniamoghvarsha/datadistill-platform.git
cd datadistill-platform
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e .
npm --prefix web install
npm --prefix web run build
.venv\Scripts\python scripts\fetch_models.py
.venv\Scripts\python scripts\check_env.py
```

Also needed, installed separately: ffmpeg on PATH, Tesseract OCR (`TESSERACT_CMD` in `.env` if not in the default
place), and Ollama with `ollama pull ministral-3:8b` (8 GB GPU: about 70% runs on the GPU) for summaries and "Ask this meeting". Speech and voice models
download into `.models/` on first use. Only models with permissive licences from Mistral or Meta are allowed (EU rules
decided by the team); check with the team before adding any model or library.

Run the app with `.venv\Scripts\datadistill.exe serve` and open http://127.0.0.1:8000.

## How we change things

1. Get the latest: `git switch main` then `git pull`.
2. Make a branch per change: `git switch -c fix/speaker-merge` (prefixes: `feat/`, `fix/`, `docs/`, `test/`).
3. Commit small steps with a message that says what and why.
4. Before pushing, run the tests: `.venv\Scripts\python -m unittest discover -s tests` (all must pass), and
   `npm --prefix web run build` if you touched `web/`.
5. Push the branch (`git push -u origin fix/speaker-merge`) and open a pull request into `main`. Someone else
   reviews it before it is merged.
6. After a backend change, restart the server: it does not reload Python code.

If a change affects accuracy (names, speakers, transcription, slide matching), re-run the checks in
`benchmarks/README.md` and put the before/after numbers in the pull request. Do not tune rules to one video: a fix
must make sense for any meeting.

## Versions

`main` is always a working version. Versions we demo or hand in are tagged, for example `git tag v0.1-demo` then
`git push --tags`; anyone can go back to one with `git switch --detach v0.1-demo`.
