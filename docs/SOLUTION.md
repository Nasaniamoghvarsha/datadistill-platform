# DataDistill: the whole story in one file

*Ericsson "Data Storage Optimization" challenge, Campor x BUILD Hackathon 2026. Written 14 Nov 2026.*
*Where a number appears, it says where it was measured. Where something is not proven, it says so.*
*Related files: [PROJECT_SPEC.md](../PROJECT_SPEC.md) (requirements and decisions), [README.md](../README.md) (overview and how to run), [FEATURES.md](FEATURES.md) (every page and command), [benchmarks/README.md](../benchmarks/README.md) (every measurement), [CONTRIBUTING.md](../CONTRIBUTING.md) (team rules).*

---

## 1. In one minute

**The problem.** Meeting recordings are huge (about 1 GB for 2 hours), nobody can search inside them, copies pile up across
SharePoint and OneDrive, and when someone leaves their files stay behind. Storage fills, so valuable knowledge gets deleted.

**Our answer.** DataDistill turns a recording into a small, speaker-attributed, AI-ready package: the words with
timestamps and who said them, a picture of each shared slide (stored once, shown where it changes), a short voice sample per
speaker, and structured JSON. It also finds duplicates (files, decks, even single slides and lines of text), makes everything
searchable, and offboards a leaver's cloud videos safely (verify, then quarantine, then delete). Everything runs on one laptop,
with free, permissively licensed software and no cloud service.

**What it achieves (measured on our test recordings, 14 Nov):**

| Claim | Number | Where it comes from |
|---|---|---|
| A recording shrinks to text + slides + voice samples | **99.7%** smaller per recording (1.22 GB to 3.0 MB; 220 MB to 0.65 MB) | Storage page, 4 recordings, 3.4 h |
| All four test recordings together | 1.47 GB to 4.4 MB | same |
| Estimated saving across a whole site, with editable assumptions | 94 to 97% | Storage page, "Savings scenarios" (estimates, not results) |
| Speed on a 2 h recording (RTX 4060 laptop) | 31.1 min to 11.1 min after our fixes | PROJECT_SPEC section 13.2 |
| Search quality | mean reciprocal rank 0.69 to 0.84 (22 queries) | benchmarks/README.md |
| Automated tests | 170 unit tests, all passing | `python -m unittest discover -s tests` |
| Cost and data flow | 0 dollars, nothing leaves the machine | by design, checked in the licence audit |

---

## 2. The problem and how we read the brief

The brief gives two goals. We tracked every requirement from it by ID in [PROJECT_SPEC.md](../PROJECT_SPEC.md).

| Goal | Meaning | Our answer |
|---|---|---|
| **G1 Storage reduction** | Replace video with text and key frames; identify duplicates; handle people who leave | The distilled package (R1), exact and fuzzy duplicate detection (R2, R5), the leaver flow (R6) |
| **G2 Data activation** | Make the content searchable and usable by AI | Hybrid search (R3), AI-ready JSON (R4), "ask this meeting" with checked answers |

**Separating the symptom from the causes** (this is how we scoped it):

- Symptom: storage is full, so people delete recordings.
- Causes: (1) the knowledge is locked inside video, so it cannot be searched or reused; (2) the same file, deck or slide exists in many places; (3) a leaver's files stay behind, unseen.
- So we do not just compress video. We extract the knowledge, store each piece once, and make removing the heavy original safe.

**Who benefits:** IT and storage owners (space), people who need to find something said or shown in a meeting (time),
and managers handling a leaver (risk), plus anyone building AI on company knowledge (clean, structured input).

---

## 3. How it works

### 3.1 The pipeline for one recording

```
 upload ──► hash (SHA-256) ──► already known? ──► shortcut, nothing stored twice
                │
                ▼
 ┌────────────────────────── per recording ──────────────────────────┐
 │ 1 frames        1 frame per second (ffmpeg)                       │
 │ 2 names on screen   OCR of tile labels (English, Swedish, French) │
 │ 3 shared screens    which area is the shared slide, when it changes│
 │                     match to uploaded decks (OCR + SIFT/RANSAC)   │
 │ 4 speech            faster-whisper (large-v3-turbo), 10 min pieces │
 │ 5 who speaks        voice embeddings (SpeechBrain ECAPA) + clustering
 │                     + visual evidence (lit name label, mouth       │
 │                       movement, camera tiles) + what people say    │
 │ 6 identity          a name only from strong evidence; shared camera│
 │                     kept apart from persons                        │
 │ 7 save              transcript JSON, key frames, voice samples,    │
 │                     fingerprint; summary by a local model          │
 └────────────────────────────────────────────────────────────────────┘
                │
                ▼
 search index (keywords + meaning) · duplicates · exports (text, ASCII,
 JSON, zip with report + slides + audio) · PDF report · offboarding
```

### 3.2 The ideas that make it work

1. **Content-addressed slide store.** A slide gets an id from its text and picture. The same slide anywhere gets the same id, so a deck becomes a list of ids, a revision only adds the changed slides, and a copy is a pointer. This gives exact copies, near copies, revisions, shared slides and repeated lines of text with one mechanism.
2. **Evidence, not guesses, for names.** Every source of a name (lit name label, introduction "my name is...", being called by name, captions, camera-off initials) is recorded as an observation with a kind and a time. A single resolver accepts a name only if the evidence is strong, and says "unknown" otherwise. Several voices speaking through one labelled camera (a conference room) are reported as one camera, never named after the account.
3. **Show slides only where they change.** A slide is shown once when it comes on screen and again only when it changes. The exports, the transcript page and the JSON follow the same rule.
4. **Everything local and checkable.** Summaries and answers come from a local model and are checked against the transcript: numbers or names that are not in the record are flagged. Every deletion in offboarding is checked, reversible for a while, and written to a tamper-evident log.
5. **One JSON contract.** `datadistill.transcript/v1` is the agreed shape of the output. The people building the pipeline, the interface and the exports all work against it.

### 3.3 The app (eight pages)

| Page | What it is for |
|---|---|
| Overview | What was distilled, saved, transcribed; recent activity |
| Upload | Folders, upload, live progress per file, retry and remove |
| Meetings | The dialogue with speakers, slides where they change, summary, "ask this meeting", downloads (text, ASCII, JSON, one zip, PDF report) |
| Search | Words and meaning, in English, Swedish and French; filter by folder, type, speaker; hits include slides shown in recordings |
| Duplicates | Exact copies, same recording in another file, revisions with changed and removed pages, decks that share slides, repeated text, compare two documents page by page |
| Offboarding | Microsoft 365 flow: dry run, approval tied to the plan, hash-verified download, distil, quarantine, restore or delete |
| Reports | One PDF per file as final proof |
| Storage | Brought in, stored, saved, savings by source, editable scenarios |

---

## 4. Frameworks and tools (all free, permissive licences, all local)

| Area | What we use | Why |
|---|---|---|
| Language, API | Python 3.13, **FastAPI** + Uvicorn, **SQLite** | One process, no server to run, easy to demo on a laptop |
| Web interface | **React + Vite + TypeScript + Tailwind** (bundled fonts and icons, no CDN) | A transcript linked to slides and search needs a real UI; a notebook-style tool cannot do it (Streamlit was dropped) |
| Speech to text | **faster-whisper** (`large-v3-turbo`) | Accurate, fast on a laptop GPU, MIT |
| Who speaks | **SpeechBrain** ECAPA voice embeddings + scikit-learn clustering | Open, runs locally, no gated model |
| Faces (mouth movement only) | **MediaPipe** face landmarks | Apache-2.0; no face is recognised or stored |
| Reading screens | **Tesseract** (+ OpenCV, imagehash, rapidfuzz) | OCR of slides and name labels; matching with SIFT and RANSAC |
| Documents | **pypdfium2**, pdfplumber, python-pptx | PDF and PowerPoint text, pages and diagrams. **PyMuPDF was removed**: AGPL licence |
| Search | **rank_bm25** + **sentence-transformers** (multilingual MiniLM) + a cross-encoder re-ranker | Words and meaning together |
| Local language model | **Ollama** with **Ministral 3 8B** (Mistral AI, Apache-2.0) | Summaries, answers, diagram reading. Team rule: Llama or Mistral only |
| Reports | **reportlab** | PDF report per file |
| Cloud files | **Microsoft Graph** client written by us, with a local mock tenant | The offboarding flow, built and tested without a real tenant |
| Media | **ffmpeg** (called as a separate program, because of its GPL licence) | Frames, audio |

The licence audit is in PROJECT_SPEC section 1.1. Anything paid, hosted or copyleft-linked was ruled out.

---

## 5. How we built it

### 5.1 What we did

| Stage | What we did |
|---|---|
| Real data | First run on the real Ericsson recordings (a 2 h 35 min hybrid meeting): voices told apart 55 to 17, music and videos handled, Swedish Q&A kept in Swedish. Who-is-speaking from the picture, diagram slides, Microsoft 365 offboarding, search v2, Ministral 8B |
| Speakers and slides | Evidence-based names with per-word voice uncertainty, shared-camera detection, slides only where they change, ASCII export and one complete zip, pictures and page-by-page comparison in Duplicates, "slide text is searchable" |
| Safety and polish | GPU hand-over fix, offboarding tested end to end in the browser, search by person, the dashboard, README and solution document, the pitch and walkthrough videos |
| Final | Regression runs, feature freeze, demo and pitch |

### 5.2 How we tested

- **Unit tests:** 170, all run in an isolated temporary library so no test can touch real data.
- **Real recordings as a regression test:** two public Zoom meetings with a hand-written list of what must and must not come out (18 of 18 checks pass), plus the Ericsson recordings.
- **Human references:** an AMI meeting against the human transcript (word error 15.8%, 91.5% of words under the right person's name), a synthetic demo (word error 1.2%), the FDA meeting for questions (20 of 20 answered from its verbatim transcript).
- **Question test:** questions written from the original video, answered from the distilled output only: "kept" (is the fact in what we store?) and "AI" (can the local model answer from it?).
- **Offboarding:** 18 tests over HTTP against the mock tenant (a file edited after the plan is left alone, a corrupted download is refused, protected and shared files are skipped), plus a full run in the browser.

### 5.3 Rules that kept us out of trouble

- Partner data never goes into git. `ericsson data/`, `data/`, `output/` and the pitch screenshots are ignored; the data is deleted after the event.
- Nothing leaves the laptop. No cloud AI, no hosted tool, no telemetry.
- Every measured number is in a file with its method. If a number moved after a change, we say so.

---

## 6. What we tried, and what we concluded

| # | We tried or weighed | What went wrong | What we do now |
|---|---|---|---|
| 1 | Just re-encode the video (considered) | Keeps the pixels and still cannot be searched | Extract the knowledge (words, slides, speakers) instead |
| 2 | Streamlit for the interface | Reruns the script on every click; cannot link a transcript to slides | FastAPI + React |
| 3 | PyMuPDF for PDFs | AGPL licence | pypdfium2 + pdfplumber |
| 4 | pyannote for speakers (the original plan) | Gated model, account needed, heavy dependency risk | SpeechBrain ECAPA embeddings + our own clustering and merging |
| 5 | Qwen and BGE models | Team rule: Llama or Mistral only | Ministral 3 8B; the 3B model was removed; the Qwen models are not used |
| 6 | Search v1: keywords + one English model | Nothing found for Swedish, French, speaker questions or typos | Rank fusion + multilingual model + re-ranker: MRR 0.69 to 0.84 |
| 7 | One screen area for the whole recording | A moving strip of tiles made every slide look like live video; slides were lost | Layout found per moment; each period gets its own shared area |
| 8 | Always give a voice the name nearest to it | 5 of 5 fake names in one meeting; the same wrong names kept returning | Evidence-based identity: a name only from strong evidence, otherwise unknown |
| 9 | Name a voice from mouth movement alone | Weak on real video (people look down, compression) | Lit name label decides where it exists; mouth movement is a suggestion |
| 10 | Detect two people talking at once from the voice | Mixed voices look like one voice; no threshold separated them | Overlap marked only from visible evidence |
| 11 | Treat a room camera's label as a person | Two people speaking through one conference-room account got the account's name | Shared camera detected and shown as "speaks through X's camera", never named after the account |
| 12 | Built a video recreation (UC-V1) | A slideshow with a computer voice is not faithful | Dropped as a feature; the data to rebuild is kept in the JSON |
| 13 | Offboarding on local folders | Not what the brief describes (cloud storage of leavers) | Microsoft Graph flow with nine layers of safety, tested on a mock tenant |
| 14 | Delete the cloud video right after distilling | Too dangerous | Verify the hash, check the transcript, move to quarantine, delete later; restore at any time |
| 15 | Extractive summary | Weak minutes | Local 8B model, every point checked against the transcript; votes added from the text |
| 16 | Long recordings in one go | The speech model showed no progress for minutes; silence stalled filters | 10-minute pieces cut at pauses; mono 16 kHz audio: 31.1 to 11.1 min |
| 17 | The speech models and the summary model on one 8 GB GPU | The summary model failed to load next to them | Release the speech models, unload the language model: explicit hand-over |
| 18 | Automatic scores for "how close is the replay video" (picture hashes, template matching, OCR overlap) | Each gave a different answer and none agreed with the eye | A random-moment audit by eye (19 of 20 the same slide), with the method written down |
| 19 | A pitch video with scorecard labels | Read as a form, not a story | A storytelling video with characters, then a replay video built from the real audio and slides |

Conclusion: **distil rather than compress, store each piece once, name people only on evidence, delete only after proof, and
keep everything local.**

---

## 7. Results, with their limits

| Area | Result | Limit |
|---|---|---|
| Storage per recording | 99.7% smaller (kept: text, slide pictures, voice samples) | Compares video bytes with the package; the full audio track is not included (with audio, 98.5%) |
| Realised saving so far | 25 MB net on 1.4 GB brought in | Originals are kept until verified; 1.3 GB more is ready to free |
| Site-wide estimate | 94 to 97% | Scenario with editable assumptions, not a measurement |
| Speed | 2 h recording in 11.1 min; the real 2 h 35 min Ericsson recording 22.4 to 17.3 min | One laptop GPU |
| Speech accuracy | Word error 1.2% (clean synthetic), 15.8% (4 people, one mixed track) | Real noisy meetings vary; numbers can be misheard |
| Who spoke | 91.5% of words under the right person's name on the AMI meeting; Ericsson voices 55 to 17 | Many audience voices stay unnamed because nothing names them |
| Search | MRR 0.69 to 0.84 on 22 queries | One meeting; a difference of one or two queries is not evidence |
| Answers from the record | 20 of 20 on the FDA verbatim transcript; every number and name checked | AI answers on loose talk can still be wrong |
| Diagram slides | PPTX and PDF readers 18 of 18 arrows | Test diagrams are generated; the picture reader got 11 of 18 |
| Offboarding | 18 HTTP tests + a browser run: 2 videos quarantined, protected and shared files skipped, restore and delete work | Mock tenant only |
| Replay video | Same slide as the original at 19 of 20 random moments | Counted by eye; one slide's stored picture was taken a few seconds early |

---

## 8. A team of four: equal contribution

All four team members contributed equally to DataDistill. We did not split the project into owned roles: everyone worked on the
design, the code, the testing and the documents, and every decision was made together. The work areas below are the parts of the
system, not a list of who did what.

| Work area | What it covers | Where it lives |
|---|---|---|
| **Platform** | Pipeline order, API, database and job queue, Microsoft 365 offboarding and its mock tenant, the test harness | `pipeline.py`, `api.py`, `db.py`, `jobs.py`, `m365.py`, `graph.py`, `graph_mock.py`, `evaluate.py`, `tests/` |
| **Speech and speakers** | Transcription, voices, clustering, speaker continuity and uncertainty, names from speech, the resolver that decides who is who, the speech benchmarks | `transcribe.py`, `speakers.py`, `speaker_stability.py`, `identity.py`, `diarization.py`, `speech_names.py`, `namefold.py`, `voices.py` |
| **Screens and documents** | Shared-screen detection, slide matching, OCR of names and slides, diagram reading, visual speaker evidence, documents, duplicates and repeated text | `slides.py`, `screen_names.py`, `faces.py`, `highlight.py`, `visual_speakers.py`, `docs.py`, `diagrams.py`, `snapshots.py`, `dedup.py`, `lines.py`, `library.py`, `store.py` |
| **Interface and knowledge access** | The React interface, search, summaries and "ask this meeting", exports, the PDF report, the Storage page | `web/`, `search.py`, `answer.py`, `summary.py`, `package.py`, `report.py`, `storage.py` |
| **Pitch and documents** | The pitch and walkthrough videos, the README and this document | `pitch/`, `docs/`, `README.md` |

**How we worked together**

- **Contract first.** The JSON shape (`datadistill.transcript/v1`) and the API routes were fixed early, so the interface was built against real output from the start.
- **Visual evidence to names.** The screen code produces observations (who, which name, what kind of evidence, when); one resolver decides. Neither side edits the other's rules.
- **One processing machine.** Heavy runs happened on the GPU laptop; the others worked on interface, tests and documents with cached outputs.
- **End-to-end every evening.** One recording through everything, results written into `benchmarks/README.md`.
- **Feature freeze** before the final run; after that only fixes found by real data.
- **Review rule.** Anything that changes a name, a deletion or a number in the pitch is read by a second person.

---

## 9. Why this is a better solution

| Compared with | Their limit | What we do better |
|---|---|---|
| Delete old recordings | The knowledge is gone for good | Keep the knowledge (words, slides, who said it) at a fraction of the size |
| Re-encode or compress the video | Pixels are still there; still cannot be searched | The package is searchable and AI-ready; reduction is measured per recording |
| Rely on built-in meeting transcripts | Older recordings have none; no slide pictures; speaker names depend on the app; stored apart from the video | We transcribe ourselves, attach slides and speakers, and use captions too when they exist |
| Cloud transcription and AI services | Cost, and the recording leaves the company | Everything local: zero cost, no data leaves the machine |
| A one-off script | Cannot show proof, cannot undo | Hash proof, quarantine with restore, audit log, a PDF report per file |

**Advantages, in short**

1. **Big, measured storage saving** that does not lose the knowledge: 99.7% per recording on our test set.
2. **Searchable, in three languages**, down to the slide and the second; text copied from a slide is found too.
3. **Speakers handled honestly:** names only from evidence; a conference room with several people is shown as one camera, not as a wrong name.
4. **Duplicates at every level:** files, re-encoded copies, shared slides, revisions with changed and removed pages, repeated lines, pictures.
5. **Safe deletion:** nine checks, approval tied to the exact plan, hash match, quarantine, restore, tamper-evident log.
6. **AI-ready:** one documented JSON shape with slide ids, line ids and screen images, ready for a language model or an index.
7. **Private and free:** no cloud, no licence cost, permissive software only, a voice sample per speaker that can be switched off and deleted.
8. **Honest about what it is:** every claim has a measurement behind it, and the limits are written down next to it.
9. **Runs on one laptop** and was built to be demonstrated live or from a recorded backup.

---

## 10. What already existed, what we built, and how we got there

We separate three things, so the credit goes to the right people.

### 10.1 Already made by others (open source, chosen by us, not claimed as ours)

| Piece | Made by | Licence | Why we picked it | What we weighed instead |
|---|---|---|---|---|
| faster-whisper, Whisper `large-v3-turbo` | Systran, OpenAI | MIT | Accurate, fast on a laptop GPU, runs offline | Cloud speech services (cost, data leaves the machine) |
| SpeechBrain ECAPA voice embeddings | SpeechBrain team | Apache-2.0 | Open, no account or gated model | pyannote (gated model, account needed) |
| MediaPipe face landmarks | Google | Apache-2.0 | Mouth movement only; no face is recognised or stored | Face recognition (privacy, not needed) |
| Tesseract, OpenCV | Google / community | Apache-2.0 | OCR of slides and name labels, SIFT matching | Cloud OCR |
| pypdfium2, pdfplumber, python-pptx | community | Apache-2.0 / BSD, MIT | Read PDF and PowerPoint text, pages, shapes | PyMuPDF (AGPL licence) |
| sentence-transformers, rank_bm25, FAISS | community | Apache-2.0, MIT | Meaning search plus keyword search | A single keyword index |
| Ministral 3 8B through Ollama | Mistral AI, Ollama | Apache-2.0, MIT | Local language model; team rule allows Llama or Mistral only | Qwen and BGE (rule), cloud models (data leaves) |
| FastAPI, React, Vite, Tailwind, SQLite, reportlab | community | MIT, BSD, public domain | One process, no server to run, a real interface | Streamlit (cannot link a transcript to slides) |
| ffmpeg | FFmpeg project | LGPL/GPL, used as a separate program | Frames and audio | |

We did not train or change any of these models. We use them as they are.

### 10.2 What we built, how we solved it, and how we reached the conclusion

| Problem | How we solved it | How we reached it (details in section 6) | Evidence |
|---|---|---|---|
| Video cannot be searched and fills the disk | Distil: keep words, slides, speakers and a voice sample; drop the pixels | Re-encoding keeps the pixels and is still not searchable (#1) | 99.7% smaller per recording |
| The same file, deck or slide exists in many places | A content-addressed slide store: a slide's id comes from its text and picture, so copies, revisions and repeated lines share one mechanism | Hashing files alone missed re-encoded copies and shared slides | Duplicates page, tests |
| Who said it | A name only from evidence (lit label, introduction, captions, being called by name); a shared camera is never named as a person | Nearest-name matching gave 5 of 5 wrong names in one meeting (#8); mouth movement alone was weak (#9, #11) | 91.5% of words under the right name on AMI |
| Slides in a meeting | Find the shared area per moment, match to the uploaded deck, show a slide only where it changes | One area for the whole recording lost slides (#7) | Matching tests, replay audit 19 of 20 |
| Finding things | Keyword and meaning search fused, re-ranked, with speaker-aware queries and person search | Keywords plus one English model found nothing in Swedish or French (#6) | MRR 0.69 to 0.84 |
| Trusting AI summaries and answers | A local model, and every point checked against the transcript; unsupported numbers and names are removed or flagged | Plain extractive summaries were weak (#15) | 20 of 20 answers on the FDA transcript |
| Leavers' cloud videos | A Microsoft 365 flow: dry run, approval tied to the plan, hash-verified download, distil, quarantine, delete later, audit log | Local-folder offboarding was not what the brief describes (#13); deleting straight away was too risky (#14) | 18 HTTP tests and a browser run on the mock tenant |
| Speed on one 8 GB GPU | 10-minute pieces cut at pauses, mono 16 kHz audio, explicit hand-over of the GPU between models | Long recordings in one go stalled (#16, #17) | 31.1 min to 11.1 min per 2 h recording |

The conclusion we reached: **distil rather than compress, store each piece once, name people only on evidence, delete only after
proof, and keep everything local.**

### 10.3 What we do not claim

- We did not create the speech, voice, OCR, language or search models, or the frameworks in 10.1. Our contribution is how they are combined, checked and made safe.
- Offboarding has run only against a local mock Microsoft 365 tenant, never a real one.
- The accuracy numbers come from our test recordings and public references; real noisy meetings vary (section 7 lists each limit).
- Before the hackathon we did the research, the analysis of the requirements and a mock-up of how we were thinking of it; we changed things as we got the details during the hackathon.

---

## 11. What is next, and what is not yet proven

**OneDrive offboarding: where it stands.** The whole flow is built and works end to end against a local mock Microsoft 365 tenant that has the same API shape as Microsoft Graph (dry run, approval, hash-verified download, distil, quarantine, restore, delete, audit log). We did not have access to a real tenant, so it has not run against one.

**Next step (one):** a supervised pilot on one real leaver's OneDrive, with a tenant administrator, using the dry-run plan and the quarantine.

**Not proven yet (so we do not claim it):**
- A real Microsoft 365 tenant has not been tried (no access): consent, real recycle-bin behaviour and real hash values are unverified.
- Transcript accuracy has no human reference for the Ericsson recordings; many audience voices stay unnamed because nothing names them.
- The speaker-attribution upgrade of 14 Nov is covered by tests but not yet re-measured on the benchmarks.
- Savings across a whole site are estimates built from the measured ratios and editable assumptions.
- Video that is not slides (a live demo, a whiteboard) is kept only as audio and a few snapshots.

---

## 12. Where to look

| I want to... | Open |
|---|---|
| Run it | README.md, "Run" |
| See every requirement and decision | PROJECT_SPEC.md |
| See every measurement | benchmarks/README.md |
| Know the team rules | CONTRIBUTING.md |
| See the code map | `src/datadistill/` (about 11,800 lines of Python, 30+ modules) and `web/src/pages/` (about 2,900 lines of React) |
| Rebuild the pitch videos | `pitch/README.md` |
