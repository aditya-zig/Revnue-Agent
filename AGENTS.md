## Dashboard shell

Dashboard uses the Razorpay design system. Tokens at `app/static/css/tokens.css`, shell styles and primitives at `app/static/css/shell.css`, local Inter fonts at `app/static/fonts/`, brand mark at `app/static/img/header.svg`, all served at `/static` via `app/main.py`. Dashboard HTML and tab contract at `app/api/dashboard.py` — keep the 7 tabs and container IDs stable for parallel workers.

## Agent skills

### Issue tracker

Issues live in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

This repository uses the five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

This repository uses single-context domain documentation. See `docs/agents/domain.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
