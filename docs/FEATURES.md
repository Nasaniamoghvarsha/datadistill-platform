# DataDistill: feature reference

*The detailed feature guide. For the overview and quick start, see the [main README](../README.md).*

Turns meeting recordings into compact, speaker-attributed text with timestamps, with the shared slides and
documents as context, ready to hand to an AI. Everything runs locally: no data leaves the machine.

The full requirements, design and decisions are in **[PROJECT_SPEC.md](../PROJECT_SPEC.md)**.
The whole story (how it works, the stack, what we tried, the team roles, why it is better) is in **[docs/SOLUTION.md](SOLUTION.md)**.

## Run

```bash
.venv\Scripts\datadistill.exe serve
```
Open http://127.0.0.1:8000. The backend serves the built web app from `web/dist`.

Frontend development with hot reload (proxies `/api` to the backend):
```bash
npm --prefix web run dev
```

## The app

| Tab | What it does |
|---|---|
| **Upload data** | Folders (one per meeting or project), upload recordings and documents, live progress (stage, %, time left), result (duration, speakers, recognition confidence, processing time). Files already in DataDistill are recognised **before uploading** (SHA-256 in the browser) and can be added as a **shortcut** instead of a second copy. |
| **Transcripts** | The dialogue per speaker with timestamps and confidence, shared slides inline with their image (key frame, or the matched document page) and slide id, **snapshots** where the speaker refers to the screen, a **voice sample** per speaker, speaker names (from the screen, Teams captions, self-introductions or edited by hand). Download JSON (AI-ready), text, or everything as ZIP, per file or per folder. Documents show each line of text; a line that also appears on other slides, decks or PDFs links to those places. **Ask this meeting**: a question is answered by the local AI from the transcript and slides only, with the source lines (click to jump) and a warning if a number or name in the answer is not in the record. |
| **Search** | Hybrid search over all transcripts and documents: keywords + meaning (English, Swedish, French), re-ranked; understands "what did Lisa say about the budget", ignores accents, forgives misspellings; filter by folder, type and speaker. Each hit shows the frame that was on screen and opens the exact line |
| **Reports** | **One self-contained PDF per file**, the final proof: meeting summary (purpose, summary, discussion, decisions, action items with owners, open questions), people, quality and processing, storage (original vs. kept, saved), shared screens with their images, the full transcript with snapshots, the SHA-256 of the original and the audit of what happened to it, and the **complete JSON printed** as the last section (no separate or attached files). ZIP of all reports per folder. Also on each transcript: **Report (PDF)** |

**Meeting summaries** are written on this computer by **Ministral 3 8B** (Mistral AI, Apache-2.0) through **Ollama** (`ollama pull ministral-3:8b`), right after a recording is processed, and shown at the top of each transcript. Small models can invent details, so every point is checked against the transcript: its time must be inside the meeting and the transcript there must say it (the time is moved to the line that does), names and bracketed expansions that never occur in the transcript or slides are removed, and years nobody said are taken out of dates. What was removed is counted in the report. Without Ollama the report says so and keeps everything else.
| **Duplicates** | Where every copy lives (shortcuts and the copy that holds the content), decisions pending, presentation revisions with the changed slides, decks that share slides; JSON report |
| **Offboarding** | Employee offboarding through **Microsoft 365** (OneDrive / SharePoint via Microsoft Graph): dry-run plan with named safety checks, approval tied to the exact plan, hash-verified download, distil, then the cloud video is **quarantined** (restorable) before it is deleted; tamper-evident audit log. Runs against a local mock tenant (no tenant needed) or a real one. |
| **Storage** | Brought in vs. stored now vs. saved; savings by source; per recording original vs. kept; **savings scenarios** (editable, from the measured ratios) and retention tiers; switch voice samples off / delete them |

## What is kept of a recording

| Part | Size (typical) | Rule |
|---|---|---|
| Transcript (JSON + text) | ~50-100 KB per hour | speakers, timestamps, confidence, shared-screen text |
| Key frames | shared with snapshots: at most **2 MB** per recording | one per shared screen that is not in an uploaded document (a matched slide points to its document page instead); cropped to the shared screen |
| Snapshots | (same 2 MB) | only where the speech refers to the screen ("as you can see", "this slide", "on my screen", "let me show"); in a video call only while something is shared; identical frames stored once; WebP quality and size step down until all fit |
| Voice samples | ~40 KB per speaker | ~10 s of each speaker's clearest speech (Opus) |

## Duplicates and offboarding

- Identity = **SHA-256 of the bytes**. A second copy is never stored: it becomes a shortcut (a library entry that opens the first copy's transcript).
- **Same recording, other file** (re-encoded, exported again, a Teams download of a meeting already uploaded): caught at the start of processing, before transcription. The picture fingerprint (one hash per second, kept even after the video is deleted) must match ≥ 90%, **and** two 30 s speech samples must say the same words as the existing transcript (≥ 60% of content words). Same slides with a different meeting fail the speech check and are processed normally. The user chooses: replace with a shortcut, or keep as a separate recording. In offboarding such copies become shortcuts automatically.
- **Offboarding** (`Offboarding` tab, `datadistill offboard <leaver address>` for a dry run): see **Employee offboarding through Microsoft 365** below. Duplicate detection here (SHA-256, same recording in another encoding) is what lets a leaver's copy of a known recording be handled without transcribing it again.
- The master copy of a duplicate is: already transcribed > outside the leaver's folder > name without "(1)" / "- Copy" > oldest.
- **Repeated text** (presentations and PDFs): the same title, bullet or sentence on several slides, revisions or unrelated decks is found line by line (long lines split into sentences; case, bullets and punctuation ignored, numbers must match). Each line gets an id and is stored once. Every place lists the others: in the document view ("also in 2 places"), in the document JSON (`lines[].line_id`, `lines[].also_in`) and on the Duplicates page. Lines on most pages of one document (footers) are listed separately.

## Who is speaking

Names and speaker labels come from several kinds of evidence, each used only when the video provides it (`faces.py`, `highlight.py`, `visual_speakers.py`, `screen_names.py`, `speech_names.py`):

| Evidence | Works with | Notes |
|---|---|---|
| **Lit name label** | meeting apps that light up the speaker's name chip | the colour of the lit chip is learned from the recording itself; one chip lit at a time |
| **Mouth movement** | camera on (live video tiles) | MediaPipe face landmarks (Google, Apache-2.0): jaw opening per second. No face is recognised or stored |
| **Camera tiles without names, room cameras** | camera on, no labels; several people on one camera | each face is followed; a voice is matched to the face whose mouth moves with it; two voices on one face are one person |
| **Name on the tile / camera-off initials** | camera off | OCR in English, Swedish and French (å ä ö é è ç kept); accents ignored when matching |
| **Self-introduction, being called by name** | any | a clear first-person introduction counts once; a "this is X" inside a longer turn needs repeats; a voice that keeps calling someone by name is not that person |
| **Official Teams transcript** | Teams recordings in OneDrive/SharePoint | read through Microsoft Graph; exact names |
| **Voice only** | audio only, no names | "Speaker N"; this is the honest answer |

**A name is only given with evidence (14 Nov, `identity.py`, ported from a teammate's branch).** Every source produces observations (which voice, which name, when, what kind); `identity.resolve` accepts a name from captions, a clear first-person introduction ("my name is X", "I'm X", "X here"), a name label lit while that voice speaks (several stretches), or repeated calls by name. **Tile or mouth movement never names a voice**; it is kept as "Possibly X" in the speaker panel. If one label is lit for two or more voices (a conference-room camera shows the room's account, and several people speak in it), none of them gets that name: the panel shows "Shared camera / account: X". Voice IDs, camera / account labels and person names are separate fields (`person_id`, `account_label`, `name`, `identity_status`).

**One camera, several people.** In a conference-room recording the room's camera is one tile, labelled with an account ("Stefan Hartwig"), and the people in the room all speak through it. When one name label is lit almost all the time *and* that follows speech (lit while people talk, much less in the silence), every voice that speaks mostly while it is lit is shown as speaking through that camera: "Speaker 1 · speaks through Stefan Hartwig's camera", "Shared camera / account: Stefan Hartwig · 2 people speak through it". Lines the voice model could not place still say which camera they came through. The account is never given as anyone's name, and movement in another participant's tile is dropped as a suggestion for these voices.

**Slides appear where they change.** A shared slide is shown once, at the moment it comes on screen, and again only when the slide changes (a slide shown again after another one counts as a change; a second detection of the same slide does not). The text export, the transcript page and the JSON (`slide_changes`, and `slide_change: true` with `slide_uid` / `screen_image` on the first line of each slide) follow that rule; every JSON line still names the shared screen it was said in front of. "Find in transcript" also searches the slides' text, so text copied from a slide finds the slide and the lines said while it was up. **Download ASCII** gives the same transcript as pure 7-bit text (Hägglund -> Hagglund, typographic quotes and dashes simplified; the zip contains it too).

**Downloads.** *Download all* is one zip: `report.pdf` (summary, people, quality, the slides, and the full transcript with each slide shown where it changes), the transcript with the slides as `.md` / `.txt` / ASCII, the transcript sorted by slide (each slide with its text, then what was said over it), `transcript.json`, the slide pictures (`snapshots/`, `pages/`), `voices/` and `audio/` (the whole sound as a small Opus file, made once from the original video; not included when the original was deleted). Download buttons now prepare the file on the page ("Preparing…", a larger report or audio takes some seconds) and show an error if it fails, instead of leaving a silent click; *View* on Reports opens the PDF in a window over the page.

**Pictures and page comparison.** Image files (png, jpg, webp, bmp, gif, tif) are one-page documents: read by OCR and compared by text and picture fingerprint like a PDF page, so a screenshot saved twice is one stored slide and shows up under Duplicates. *Duplicates -> Compare two documents* aligns two PDFs / presentations / pictures page by page: same, edited (with how alike), moved, only in one of them. The revision view also lists pages a later version removed. Names read from tiles are merged across OCR variants of the surname ending ("Bergström" / "Althire") and nicknames are understood when a name is said ("Tobbe" -> Torbjörn).

**Voices keep their uncertainty (`speaker_stability.py`, also from the teammate's branch).** Each word keeps its own evidence (`attribution` on the word, per line `supported` / `contextual` / `unresolved`); a voice needs a clean 2-second stretch to count as a speaker, a long pause is not a new person, a sub-second A-B-A change is a possible interruption, and a word the voice model cannot place is shown under the surrounding voice only where that is certain. Lines that are mostly inferred carry a "voice uncertain" marker in the transcript. `diarization.py` supplies the timeline helpers that module needs (written for this project; it was not part of the hand-over).

Camera-off tiles (a photo) have no lips to read; the program says what each tile is (live, photo, room, too far away). People speaking at the same time are marked when two name labels are lit at once or two mouths move at once (`overlap_with` in the transcript); the speech model writes mostly the louder voice, so such text may be incomplete. Audio alone cannot find overlaps: a mixture of two voices looks like one voice to the speaker model (tested).

## Diagram slides (flowcharts, architecture, org charts)

`diagrams.py` reads boxes and arrows, not just words: PowerPoint **connectors** (exact), the **vector drawing** of a PDF exported from PowerPoint (geometry), or a picture through the local vision model (**Ministral 3 8B**), whose answer is checked: every box label must be text that is really on the slide. The result per slide: boxes, arrows with direction and labels, a readable sentence list, and Mermaid code. It is in the AI-ready JSON (`diagram`), in the slide prompt and in search.

## Employee offboarding through Microsoft 365

`graph.py` (client: device-code sign-in, paging, throttling back-off, hashing downloads), `m365.py` (the flow), `graph_mock.py` (a local stand-in with the same API; no Microsoft 365 tenant is needed to try everything). A real tenant needs an app registration by an administrator (public client, permissions `Files.ReadWrite.All`, `User.Read.All`, `OnlineMeetingTranscript.Read.All`, `OnlineMeetingRecording.Read.All`) and a first supervised run.

Safety, in layers: dry-run plan with named checks per file (protected by a retention label / hold / checked out, shared outside, too large, no hash) -> approval by name, tied to the exact plan (optional second approver, leaver's address typed) -> download compared with the cloud's **quickXorHash** -> distilled form must exist (speech found, quality gate) -> item read again right before touching it, ETag sent as `If-Match` -> **quarantine folder** for N days, then deleted to the recycle bin (restorable at every step) -> limits per run -> hash-chained audit log. Cloud space is freed only after the recycle bin retention (93 days) or when an administrator empties it.

## Add data

- Upload in **Upload data** into a folder, or copy files into `data/<folder>/` and click **Scan data folder**.
- Put the slides/PDFs that were shown in the **same folder** as the recording: shared slides are matched to them.
- A Teams transcript next to a video (`meeting.mp4` + `meeting.vtt`) is used automatically: exact speaker names.

## Commands

| Command | What it does |
|---|---|
| `datadistill serve` | Web app + API on 127.0.0.1:8000 |
| `datadistill scan` | Register files found under `data/` |
| `datadistill process-all` | Process every new file without the web app |
| `datadistill reprocess [--kind video\|document]` | Re-run processing |
| `datadistill export` | Rewrite `transcript.json` / `transcript.md` for every recording |
| `datadistill extracts [ids]` | Create snapshots + voice samples for recordings transcribed earlier (no re-transcription) |
| `datadistill offboard <leaver address>` | Dry-run plan for a leaver's OneDrive (Microsoft 365 / mock tenant); approve and run it in the web interface |
| `datadistill accuracy <id> <reference> [--offset auto\|<s>]` | Compare a transcript with a human reference (`.vtt`/`.srt`/`.json`/`.txt`, or a timestamped transcript `.pdf`): word error rate, content words, who-spoke-when error, names. The recording may be an excerpt of a longer reference; it is located automatically |
| `datadistill questions <id> <questions.json> [--no-ai]` | Question test: are the answers still in the distilled output, and can the local AI find them? |
| `datadistill ami-reference <words_dir> <meeting> <out.vtt>` | Turn AMI corpus word files into a reference `.vtt` |

Global options: `--data-dir` and `--output-dir` point to other folders (default `./data` and `./output`).

**Demo video:** `python scripts/demo/make_demo.py` records every use case from an empty library on public meetings (headless Edge, captions burned in, processing waits cut out). It needs the test material, which is not in git: see [TEST_MATERIAL.md](TEST_MATERIAL.md).

Results go to `output/benchmarks/` (JSON + Markdown). How to write references and questions: [benchmarks/README.md](../benchmarks/README.md). Scoring tests: `python -m unittest discover tests`.

## Setup on a new laptop

```bash
winget install Gyan.FFmpeg UB-Mannheim.TesseractOCR
python -m venv .venv
.venv\Scripts\pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e . --no-deps
.venv\Scripts\python scripts\fetch_models.py   # face model + Swedish/French OCR data
ollama pull ministral-3:8b                      # summaries, answers, diagram reading (Mistral AI, Apache-2.0)
npm --prefix web install
npm --prefix web run build
.venv\Scripts\python.exe scripts\check_env.py --audio <a real recording.mp4>
```
`check_env.py` verifies the tools and models. With `--audio` it also measures Whisper speed on that machine.

## Layout

```
src/datadistill/   pipeline (transcribe, speakers, screen_names, speech_names, slides, match, docs), API, CLI
web/               React + Vite + Tailwind web app
scripts/           environment check
data/              input files, one folder per meeting/project (never commit)   output/  database, transcripts (never commit)
```
