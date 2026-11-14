# Measuring DataDistill

Two measurements back the claims in the pitch. Both compare our output with something a **person** produced from
the **original**. A reference or question written from DataDistill's own output proves nothing.

| Claim | Command | Needs |
|---|---|---|
| "The transcript is X% accurate, speakers are right Y% of the time" | `datadistill accuracy <file_id> <reference>` | a human transcript of the recording |
| "We deleted 98% of the bytes and kept N of M answers" | `datadistill questions <file_id> <questions.json>` | questions + expected facts, written while watching the original |

Find the file id in the web app's address bar (`/transcripts/<id>`). Results are saved as JSON + Markdown in
`output/benchmarks/`.

## Accuracy

The reference can be:
- a `.vtt` / `.srt` with speakers as `<v Name>text</v>` (the Teams format). This gives word errors, who-spoke-when error and names;
- a `.json` list of `{start, end, speaker, text}`;
- a `.txt` with the words only. This gives word errors only.
- a **transcript PDF** from a court reporter or transcription service, with lines like `00:18:10 Ms. Paulsen: Hi, good morning.` (FDA advisory committees publish these). Line and page numbers are skipped. A timestamp without a name continues the previous speaker. When the clock restarts after a break, the transcript is split into sessions;
- For the AMI meeting corpus (CC BY 4.0), run `datadistill ami-reference <words folder> <meeting> ref.vtt` first.

**The recording can be an excerpt.** A reference often covers a whole day while you process 60–90 minutes of it. By default (`--offset auto`) the tool finds where the recording sits in the reference. It uses runs of 4 words that occur once in both, takes the median time difference, and picks the session with the most matches (at least 8 are required). It then cuts the reference to the recording's length, including the part of a turn that crosses the start or end. The report says where the recording starts in the reference and how many matches placed it. `--offset <seconds>` sets the shift by hand. Checked on the 201-page FDA transcript (6 Nov 2025): a 20-minute stretch of the third session with 8.6% of the words removed is placed within 0.02 s, and scores exactly 8.6% word errors and 0% speaker error.

**Names in such transcripts** are often "Dr. Torous" while the meeting introduces "John Torous". A name counts as right when a first or last name matches, ignoring titles (Dr., Ms., LCDR …).

What is measured:
- **Word error rate:** (wrong + missed + extra words) / reference words. Case, punctuation and fillers ("uh", "um") are ignored, and numbers count the same written out or as digits.
- **Content words:** meaningful words of the reference we have (recall), and our meaningful words that are right (precision). This is closer to "is the meaning there?" than the word error rate.
- **Who-spoke-when error (DER):** missed speech + speech where nobody spoke + speech given to the wrong person, with the standard 0.25 s margin at turn boundaries. Our speakers are matched to the reference speakers by shared talk time.
- **Names:** our speaker names against the reference names, for speakers whose reference name is a real name.

The transcript is always measured as the machine produced it. Lines a person corrected in the app count with their original text.

## Question test

Write `questions.json` while watching the original recording, or from its script or official minutes:

```json
{"written_from": "the original video, by <who>",
 "questions": [
  {"q": "By how much did traffic grow?", "facts": [["18 percent", "eighteen percent"]], "time": "0:05"},
  {"q": "Who negotiates with the supplier?", "facts": ["Maria", "partial delivery"], "source": "speech"},
  {"q": "What does the slide give as the next step?", "facts": ["cooling"], "source": "screen"}]}
```

- `facts`: every fact must be found. A fact is a string, or a list of wordings that count the same.
- `source`: `speech` (transcript), `screen` (text of a shared slide) or `either` (default).
- `time`: optional. If given, the answer must be within 2 minutes of it.

Two results per question:
- **Kept:** the facts are in the distilled output, either in transcript lines within 60 s of each other or in one shared screen's text. This is the storage claim.
- **AI:** Ministral 3 8B (local, Ollama) answers from the distilled output only. The answer is correct when it contains the facts (facts already in the question need not be repeated). This is the "AI-ready" claim. It needs Ollama to be running; `--no-ai` skips it.

Good questions mix the kinds of things people go back to a recording for: decisions, owners, numbers, dates, what a slide showed, and who said something. Aim for 15–25 per meeting, and don't drop the ones we fail.

## Why the local AI gets answers wrong, and what was done (12 Nov)

> **13 Nov:** the project now uses **Ministral 3 8B** (the 3B model was removed). Every AI result in this file was measured with the 3B model on 12 Nov and has **not** been re-measured with 8B yet.

Question: is the small model (Ministral 3 3B, measured 12 Nov) the problem? Test: the 20 FDA questions answered from **FDA's own verbatim transcript** of the morning (11,459 words, no transcription errors), so only the AI is measured.

| Method | Correct | Notes |
|---|---|---|
| Plain: the record (long meetings: its 10 best-matching 2-minute stretches), answer in 1-2 sentences | **20 of 20** | 19 at first; the 20th ("metacognitive" for "metacognition") was the grader, now fixed |
| The model cites numbered lines itself | 15, then 18 of 20 | It often left the citations out, and some answers became "not in the record". Dropped. |
| **Plain + the program finds the source lines and checks numbers and names** (now used) | **20 of 20** | 18 of 20 source lines hold the facts. 3 answers flagged as "not verified", all fairly: the model added expansions never said ("Medicaid", "Attention Deficit Hyperactivity") |

What this means for the AMI misses (13-14 of 18):
- **2 were misheard numbers** ("fifteen million" → "50 million"). The AI answered faithfully from a wrong transcript. That is a transcription problem, and no model fixes it. What helps: the answer now shows its source line, so a reader can see where the figure comes from.
- **1 was strict grading.** Word forms now count ("metacognitive" = "metacognition").
- **2 were real AI mistakes on loose talk** ("the industrial designer will work on the working design", said once, mid-sentence, after a list of design phases). A larger model would likely help. The only other allowed family at that size is Llama; Mistral's own larger Ministral models would need a download of 6+ GB. Not done.

How answers are now given (`answer.py`, also the **Ask this meeting** box on a recording's page):
1. The model answers from the record only.
2. The program finds the 1-3 lines that support the answer (its numbers, names and words) and shows them with time and speaker. One click jumps to the line.
3. Every number and name in the answer must occur in the record. If not, the answer is marked "Check: … not found in the record". This catches invented figures. It cannot catch a figure that was misheard in the transcript; the source line makes that visible.

## Real Zoom recordings (12 Nov): what failed and what was fixed

Two public Zoom meetings, checked by hand, are the regression test for names, speakers and shared screens:
`zoom-cases/expected.json` (what must and must not come out) and `python benchmarks/zoom-cases/check_cases.py`.
- **Product Marketing (42 min):** speaker view, then a shared screen (GitLab, Google Docs) with **unlabeled** tiles.
- **CRCOG committee (85 min):** gallery with name labels, then a slide deck shared beside a strip of tiles.

| Check | Before | After |
|---|---|---|
| Wrong speaker names | 5 of 5 fake in Product Marketing ("Craig OKR Deck", "Service Desk"); CRCOG: "Jason B", "Paulin... Z", presenter named after another person | **none** in either (unsure voices stay unnamed) |
| Shared screens kept | 6 (none after 16 min) / **1** of a deck shown for an hour | **57** (25 after 16 min) / **29**, with their real slide titles |
| Speaker changes certainly mid-sentence | 29% / 11% | **11% / 7%** |
| Formal votes in the minutes | 0 of 2 (CRCOG) | **2 of 2** |
| Checks passed | 5 of 18 | **18 of 18** |

The causes, and the fixes:
1. **One screen area for the whole recording.** Meeting apps switch layout (gallery, then a shared screen beside a strip of moving video tiles). With the whole frame as "area", the moving strip made every slide look like live video. **Now** the layout is found per moment (where the moving pixels are, and what the tile strip looks like), and each period gets its own shared area, or none for gallery view (`slides.layout_timeline`). A shared document too small to read in the video is kept as a key frame, without OCR garbage.
2. **Names read anywhere in the frame.** Links in a shared document and an app's menu became "participants". **Now** a label inside the shared content is never a name. A colour-border "speaker highlight" is only trusted if one tile lights up at a time. Zoom's microphone icon ("Rob Aloise Z") and roles after the name ("Jason Hayes First Select…") are removed. Two people with one first name are no longer merged ("Jason H" ≠ "Jason B").
3. **Faces matched to voices during a screen share.** Zoom reorders the thumbnail strip around whoever speaks, so a label's position no longer tells whose face it is (a 38-minute presenter got another participant's name). **Now** tiles are matched to voices only in gallery view, and only with at least a minute of that voice speaking there. Otherwise the voice stays unnamed.
4. **Names heard in speech.** Besides "this is Jason", people being called on ("Caitlin, anything else?") and thanked ("Thank you, Jonathan", "Was that John Coleman?") are counted for known names. Clear agreement names an unnamed voice or corrects a tile match. A first name alone is never guessed between two people.
5. **Speaker changes mid-sentence.** The voice model decides per 1.5 s window. **Now** a change is only kept at a pause (≥ 0.25 s) or a sentence end, moved to the nearest one within 8 words / 3 s. A voice left with fewer than 12 words joins its neighbours.
6. **The report.** It said "MB saved" while the video was still on disk; it now says "can be saved once the video is deleted". Votes ("motion passes", "we are adjourned") are found in the transcript and added to the decisions if the model left them out. A shared screen is only called "a presentation slide" when it was matched to an uploaded deck.

Still open:
- The main CRCOG presenter (Matt Hart) is unnamed. Neither face movement nor being called by name gives enough evidence.
- The Product Marketing meeting has no name labels at all, so all its voices stay unnamed.
- It is split into more voices than people (11 for about 8).
- Any accuracy effect on AMI and the FDA meeting has not been re-measured since these changes.

## Real Ericsson hybrid Teams recording (13 Nov): what failed and what was fixed

A 2 h 35 min hybrid Teams recording: room camera, remote presenters, two shared decks (both uploaded as PDFs), a
song and two narrated videos played in the meeting, and a Swedish Q&A inside an English talk. The data is not kept in
the repo and is deleted after the event; people are described by role here.

| Check | Before | After |
|---|---|---|
| Distinct voices | 55 (the second presenter split into 5) | **17** (the second presenter is one voice, 54 min) |
| Host's name | wrong: a name said once ("this is …") beat "my name is …" said once | **right** ("my name is" counts 3×; a tie leaves a voice unnamed) |
| Second presenter's on-screen name | dropped as "document text" | **kept** (that filter now only runs when no layout is known) |
| Song and videos played in the meeting | transcribed as people talking | one row "Music / video sound" (5.5 min), not a participant |
| Swedish Q&A (2 min) | forced into English, unreadable | **real Swedish** lines (re-decoded per line) |
| Shared screens | 112, ~25 of them the Teams window itself | **82**; the same 54 matched to the decks |
| Deck pages | font-table pages ("Characters for Embedded characters") counted as slides | skipped (1 per deck) |
| Summary times in the 3rd hour | the model wrote "26:36" for 2:26:36; the brief was put at 0:26, 4 items dropped | **all at the right hour**; 1 item dropped |
| Processing time | 22.4 min | 17.3 min |

The fixes (all general, none tuned to this file):
1. **Intros:** "my name is" / "I'm" weigh 3, "this is" 1 and only at the start of a turn; a tie names no one (`speech_names`).
2. **Split voices:** after clustering, voices whose average voice prints are very close (cosine distance < 0.22) are merged (`speakers.merge_same_voice`). The closest two different voices here (the host and a video narrator) were 0.29 apart, so the limit is kept below that.
3. **Music / video sound:** speech has many short pauses; music and narrated video do not. 20 s windows with < 8% near-silent frames, over at least 30 s, are taken out of speaker detection (`soundtrack`).
4. **Other languages:** each 30 s is checked for language; lines in another language, or with low confidence, are decoded again without a fixed language and replaced only when that is confidently another language (`transcribe.other_language_pass`).
5. **App windows:** a shared screen that matches no deck page and reads as app UI (5+ menu/button words) is not a slide (`slides.is_app_window`).
6. **Summary:** a time without hours is tried at every hour of the meeting, and the transcript decides; the speakers listed for a point must speak there, not only be mentioned (`summary.check`).

Still open:
- 14 of 17 voices are unnamed (audience, a closing speaker). Nobody says their name, and the room has no name labels.
- Probably still more voices than people: one person through a room microphone and a laptop sounds different.
- A few lines are attributed to the wrong person around handovers.
- Swedish place names in English speech are misheard ("Mammö"), and a product name on the slide was heard as a US state name. The summary repeats it.

## 13 Nov (second round): who is speaking, search, diagrams, offboarding

### Who is speaking: what the picture adds

| Check on the public CRCOG recording (Zoom gallery, 85 min) | Before (12 Nov) | After |
|---|---|---|
| The 38-minute presenter (Matt Hart) | unnamed, then wrongly named "Jonathan Colman" | **named Matt Hart** (lit name label) |
| Speaker highlight used | border only: not found in this video | **name label lights up** in the speaker's colour: found and learned from the recording (30 label positions) |
| Chair's voice vs. her lit label, first 2 min | not measured | 61 of 63 s agree; presenter 56 of 57 s |
| Voices named | 3 | 4 (Lisa Thomas, Matt Hart, Donna Hamzy Carroccia by label; Jason by introduction); 8 voices stay unnamed (board members who speak while a deck is shared: the lit label is not read in the tile strip yet) |
| Zoom regression (18 checks) | 18 of 18 (after the 12 Nov fixes) | **18 of 18** with the new pipeline and 8B summaries; the check that failed in between ("ask Donna if she" spoken by the unnamed presenter) now resolves to the right person |

What each tile is (reported per recording): CRCOG: 5 live camera tiles, 1 room, 7 too far for a readable face. **Mouth movement alone was a weak signal on real video** (people look down at notes, compression): in the first 2 minutes the right tile ranked first for one voice and second for the other. The lit name label is far stronger, so it decides where it exists; mouth movement is the generic fallback and the tool for unlabelled tiles.

What did **not** work and is not in the product: detecting overlapping speech from the voice alone. Real voices of two people were mixed at known times (equal level, half level, quarter level): a mixture is dominated by one voice, its similarity to the second voice stays near 0.1, the same as a window with one speaker. No threshold separated them. Overlap is therefore marked only from visible evidence (two name labels lit at once, or two mouths moving at once).

On the Ericsson hybrid recording the three remote presenters are **camera-off photos** and the audience is a wide room shot with faces of a few pixels: mouth movement cannot help there (the program reports "photo" and "far"); names come from the tile labels, introductions and being called by name.

Cost: processing the 85-minute CRCOG video took 949 s (586 s before: the face and label analysis add about 6 minutes); the 8B summary took 648 s. Ministral 3 8B is about 2.5 times slower than 3B on this laptop (70% of the model fits the 8 GB GPU). Its decisions on CRCOG name who moved and seconded ("moved by Speaker 2, seconded by Jason"); no side-by-side with the 3B model was run on the same text, so no quality gain is claimed.

### Names and voices after the teammate's upgrade (14 Nov): not yet measured

The second Ericsson video (a thesis talk, 16 min) showed the failure this change targets: two presenters in one room camera, labelled with the room account "Stefan Hartwig", got that name and a remote participant's name ("Johan A") from weak tile movement (scores 0.38 and 0.45, minimum 0.35). Since then names come only from evidence (see README, "Who is speaking"), tile movement is a suggestion, and a label lit for two voices is reported as a shared camera. **Checked offline on the GraalVM video (frames + the saved transcript, no pipeline run):** the label "Stefan Hartwig" is lit in 97% of the speech seconds and 23% of the silent seconds; Speaker 1 speaks while it is lit 98.7% of the time, Speaker 2 96.3% -> both are reported as speaking through that one camera (a room), neither is named after it; 12 of the 22 lines the voice model could not place were also said while it was lit. **What to check when the recordings are re-run:** (1) video 2: both presenters unnamed or named only from the presenter caption / an introduction, not "Stefan Hartwig" / "Johan A"; (2) Ericsson video 1: Anders, Tobbe Bergström, Marco Ellison and Dalia still named; (3) Zoom regression (`check_cases.py`) still 18 of 18, with Matt Hart named from the lit label; (4) the share of words / lines marked uncertain ("voice uncertain"), which the teammate's own run put at about 10% on Product Marketing; (5) speaker changes mid-sentence still at or below the earlier figure. Hand-labelled "who said it" on at least 3 meetings is still missing.

### Search (22 queries on the CRCOG recording, `benchmarks/search/`)

Hit = the opened line is within 60 s of where the answer is said. Mean reciprocal rank (MRR; 1.0 = always first).

| Query kind | n | 12 Nov ranking: top1 / top3 / top10, MRR | New (fusion + multilingual + re-ranker): top1 / top3 / top10, MRR |
|---|---|---|---|
| paraphrase | 11 | 9 / 9 / 11, 0.86 | 7 / 11 / 11, 0.82 |
| exact words | 3 | 2 / 3 / 3, 0.83 | 2 / 2 / 2, 0.67 |
| "what did X say about Y" | 2 | 1 / 1 / 2, 0.56 | **2 / 2 / 2, 1.00** |
| Swedish question, English talk | 2 | 1 / 1 / 1, 0.50 | **2 / 2 / 2, 1.00** |
| French question, English talk | 2 | 0 / 0 / 1, 0.10 | **1 / 2 / 2, 0.75** |
| misspelt words | 2 | 1 / 1 / 1, 0.50 | **2 / 2 / 2, 1.00** |
| **all** | **22** | 14 / 15 / 19, **0.69** | 16 / 21 / 21, **0.84** |

Honest reading: the gain is where the old ranking had no answer (other languages, speaker questions, spelling). On plain English paraphrase it is about even, and one exact-words query ("a technical correction") is no longer found in the top 10. 22 queries from one meeting: a difference of one or two queries is not evidence.

### Diagram slides (`benchmarks/diagrams/`)

Three test diagrams with known answers (flowchart with a decision and arrow labels, architecture with a two-way arrow, org chart), as PPTX, PDF and PNG.

| Reader | Boxes | Arrows with direction | Arrow labels |
|---|---|---|---|
| PPTX connectors | 15 of 15 | 18 of 18 | 4 of 4 |
| PDF vector drawing | 15 of 15 | 18 of 18 | 4 of 4 |
| Vision model on the picture (Ministral 3 8B, 14-28 s each) | 15 of 15, none invented | **11 of 18**: flowchart 4/5, architecture 4/5, org chart 3 of its 4 lines (plus one wrong) | 4 of 4 |

These diagrams are generated by a script, so they are the easy case: a real PowerPoint export is messier (grouped shapes, curved connectors, icons). The two structural readers have no model to be wrong. The vision reader gets the boxes right but misses or mis-directs about a third of the arrows, so it is only the fallback for pictures (scans, video frames); its boxes are checked against the slide's own text so it cannot add one that is not there, but a wrong arrow between two real boxes cannot be caught that way.

### Offboarding through Microsoft 365

No tenant exists in this project: the flow is built and tested against a local mock that speaks the Graph API (paths, JSON, status codes, paging, throttling, device-code sign-in). 18 tests run the real client over HTTP against it, including: a file edited after the plan is left alone; a corrupted download (hash differs) is refused; a file edited during quarantine is not deleted; protected and externally shared files are skipped; a stale delete is refused by the server (412); throttling (429) is waited out; the audit chain detects a changed entry. What a mock cannot show: real permissions and consent, the exact recycle-bin behaviour of a real tenant, and quickXorHash against real cloud values (the algorithm follows Microsoft's published code and gives the documented value for an empty file). Those need one supervised run on a real tenant.

## Results so far

| Recording | Reference | Result |
|---|---|---|
| Demo - network review (2 min, synthetic voice, 6 slides, 3 in the PDF) | original script | WER **1.2%** (only error: "Johan" heard as "Yuan", twice); content words 98% / 98% |
| same | 18 questions from the script and slides (`demo-network-review/`) | **18 of 18 kept**; local AI **17 of 18** correct (first run, before the OCR fix: 16 / 16) |
| AMI ES2008a (17 min, 4 real people, each with a headset microphone, mixed to one track; AMI Meeting Corpus, CC BY 4.0) | AMI human transcript | WER **15.8%** (11 Nov: 15.7%); content words found **90%**, of ours right **96%**; who-spoke-when error (DER) 29.5% (11 Nov: 28.7%); **91.5%** of our words under the right person's name; names 3 of 4 right (Iain written "Ian"), Jessy's name on a split-off fifth voice |
| same | 18 questions from AMI's **human-written summary** (decisions, actions, open problems) and names from the human transcript (`ami-es2008a/`) | **16 of 18 kept**; local AI **13 of 18** by strict keyword grading (14 on a manual read: Q5 lists each person's animal instead of "favourite animal") |

**What the question test found, and what was fixed (12 Nov).**
The first run kept 16 of 18 answers. Both losses were real:
1. *"Who owns the rollout plan?"*: the transcript says "Yuan" instead of "Johan". This is not fixed. Since the OCR fix, the slide "Next steps and owners" carries "Johan: rollout plan", so the answer is kept. The local AI still answers "Yuan", because it trusts the transcript over the slide.
2. *"What is the next step of the energy programme, according to the slide?"*: OCR read "Next: cooling optimisation" correctly, but the OCR noise filter (`clean_screen_text`) dropped it, because "optimisation" is not in its ~20k-word list. It also dropped "Rollout reordered by traffic" and "Johan: rollout plan". **Fixed:** the filter now accepts other forms of a listed word (British spelling, -s/-ed/-ing endings, re-/un- prefixes) and compounds of two real words (roll+out, off+board), for words of 6+ letters only.

How the fix was checked against noise: 3,014 raw OCR lines from 358 frames of the three real meeting videos (one every 20 s) were run through the old and the new filter.
- 0 lines kept before are dropped now.
- 14 lines are newly kept: the 3 demo lines, 6 clear lines from a board meeting ("GMCB approves — nothing further required.", "July 1, 2027: UVMMC submits reports and plans."), and 5 lines of real slide text read poorly from a blurry video ("Presenter. Mchee! Berber, General Coursed").
- No pure noise got through. The first version of the fix let 20 noise lines through ("tame setter ee toe tems"), which is why the 6-letter and 4-letter limits were added.

Still dropped: words with no listed form at all, like "mitigations" (so the slide title "Risks and mitigations" is lost; the key frame image still shows it). Unit tests: `tests/` (53 tests on 13 Nov).

**What the AMI run shows (12 Nov, re-measured after the chunking change):**
- Word accuracy is unchanged by the 10-minute chunking and the silence fix (15.8% vs 15.7% word errors).
- **Numbers are the weak spot.** Both lost answers are misheard figures: "fifteen million euro" became "50 million", and "twelve fifty euro" became "1250 euro". The local AI then repeats the wrong number. For finance-heavy meetings, the slides (where the figures are written) matter.
- **Of DER's 17% "missed speech", most is not lost content.** The reference counts hesitations ("Mm 'kay um") and short pauses inside a turn as speech; our lines leave them out. Only 3.8% of the reference speech is two people at once. "Our words under the right person's name" (91.5%) is the reader's view: when the transcript says "Rose: …", it is Rose 91.5% of the time, by words.
- **The AI is weaker than the record:** 16 answers are in the output, but the 3B model found 13–14 of them. It answered "conceptual and detailed design" where the record says "working design". A larger local model or better retrieval would close part of that gap.
- **Scoring rules added for comparability with 11 Nov,** applied to the reference and our text alike: "gonna" = "going to", contractions expanded, British = US spelling, AMI's spelled letters ("T_V_") = "TV", "kick-off" = "kickoff", and words compared in time order. All are in `evaluate.py` and covered by tests.
- **Data:** the AMI files, the meeting and everything derived from it were deleted after the run. Only the questions (short facts from the summary, with attribution) and the result reports are kept, in `ami-es2008a/`.

One change to the question file after the first run: question 14 said "southern" while its fact said "south". Both are now accepted, because the AI's answer ("9 percent") was right.

The demo meeting is synthetic (one clear text-to-speech voice), so these are best-case numbers. The AMI meeting and real Ericsson recordings are the honest test.
