# sales-lead-inbox

Cheap local SMB lead ingest + cross-day inbox for Warsaw / ZIP **46580** (Kosciusko County).

Built for a Sales Grok Bot workflow. **Research-only** — no Azure, no outbound sends, **no paid APIs**.

## What it does

1. **`local_lead_ingest.py`** — pulls InkFreeNews (WP JSON), KEDCO (RSS), Times-Union homepage headlines, chamber calendar (best-effort). Scores with geo + SMB signal / noise filters.
2. **`lead_inbox.py`** — wraps ingest with durable state so morning digests only surface **new** (or score **bumps**), not the same Autocam/Wingstop story every day.
3. **`fetch_article_text.py`** — optional plain-text fetch for top links (no browser).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
# Preferred for weekday digests — only new / bumped items
.venv/bin/python lead_inbox.py --days 2 --new-only

# Full candidate list (also updates state unless --dry-run)
.venv/bin/python lead_inbox.py --days 7

# Raw ingest without inbox state
.venv/bin/python local_lead_ingest.py --days 7 --json out/digest_candidates.json
```

Outputs (created under `./out` by default when using absolute defaults from Sales box; locally set `--json` / `--md` / `--state` as needed):

- `lead_inbox_latest.json` / `.md`
- `lead_inbox_state.json` (gitignored)

## Notes

- Times-Union RSS returns 403; homepage scrape only.
- Free/local HTTP only. Close browser tabs when done; prefer scripts over browser.
