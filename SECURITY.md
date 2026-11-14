# Security and data handling

DataDistill is a hackathon project. It is built to run on one machine and to keep data there.

## What it does with data

- All processing is local. No recording, transcript, slide or voice sample is sent to a cloud service.
- The language model runs locally through Ollama. Its answers are checked against the transcript.
- The web server listens on `127.0.0.1` only. Do not expose it to a network without adding authentication: it has none.
- Voice samples identify a person (personal data under GDPR). They can be switched off or deleted on the **Storage** page.
- Microsoft 365 sign-in uses the device-code flow; tokens stay in the local `data/` folder, which git ignores.

## What must never be committed

Partner (Ericsson) material, the live library (`data/`, `output/`), model files, recordings and `.env` files. `.gitignore`
blocks them; do not use `git add -f`. See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/TEST_MATERIAL.md](docs/TEST_MATERIAL.md).

## Reporting a problem

Open a private security advisory on the GitHub repository (Security tab, "Report a vulnerability"), or contact the maintainers
directly. Please do not post details of a vulnerability, or any real recording data, in a public issue.

The offboarding flow has only been tested against a local mock tenant. Run it against a real tenant only under supervision,
with an administrator's app registration, and start with a dry run.
