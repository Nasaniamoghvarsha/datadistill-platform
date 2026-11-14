# Test material (not in git)

The recordings, slide decks and demo library we tested on are large, so they are **not** in this repository. They stay on
each team member's disk in folders that `.gitignore` skips. The unit tests (`python -m unittest discover -s tests`) do not
need any of them; they build what they need in memory.

| Folder (ignored by git) | What it holds | Where it came from |
|---|---|---|
| `videos/` | Public meeting recordings used for the accuracy, speaker and search checks: public Zoom meetings, a public staff meeting, and one recording with its slide deck | Public videos and documents found online. Not Ericsson material |
| `test-material/fda-dhac-2025-11-06/` | Agenda, slides, discussion questions and the official 201-page transcript of a public US FDA advisory committee meeting | Published by the US FDA. Used as a human reference for the accuracy test |
| `test-material/demo/` | A Teams download of a short staff meeting | Used for the demo and the duplicate-recording check |
| `archive/` | An older copy of the demo library (database and outputs) | Made by us. Safe to delete |
| `ericsson data/` | The material provided by Ericsson for this challenge | **Never shared through git, and deleted after the event** |

The AMI meeting corpus (CC BY 4.0) is used for one benchmark (`benchmarks/ami-es2008a/`); download it from the AMI site if
you want to repeat that run, then use `datadistill ami-reference` to build the reference file.

## Recreating the set

1. Create the folders above in the repository root (they are listed in `.gitignore`, so git ignores them).
2. Put public meeting recordings in `videos/`, and the FDA files in `test-material/fda-dhac-2025-11-06/`.
3. Upload them in the web app (**Upload**), or copy them into `data/<folder>/` and click **Scan data folder**.
4. Repeat the measurements with the commands in [benchmarks/README.md](../benchmarks/README.md).

## What needs the material

- `scripts/demo/make_demo.py` records the demo video from an empty library and expects the files named in its `MATERIAL`
  table. If a file is missing, that step cannot run.
- The mock Microsoft 365 tenant (offboarding demo) fills its fake OneDrive from `videos/` and `archive/` when they exist;
  without them it starts empty, and you can upload your own files into it.
- The benchmark scripts in `benchmarks/` print which file they are missing.

## Rules

- Do not commit these folders, and do not use `git add -f` on them.
- Do not copy real participants' names or partner slide text into code, tests or docs. Tests use made-up names.
- Anything that came from Ericsson is deleted from every laptop after the event.
