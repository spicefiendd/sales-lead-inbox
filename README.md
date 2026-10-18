# sales-lead-inbox

Cheap local SMB lead ingest + cross-day inbox for one configurable local market.

Built for a Sales Grok Bot workflow. **Research-only** — no Azure, no outbound sends, **no paid APIs**.

## What it does

1. **`local_lead_ingest.py`** — pulls the market's sources (WordPress JSON, RSS, homepage headlines, chamber-style calendar; best-effort). Scores with geo + SMB signal / noise filters.
2. **`lead_inbox.py`** — wraps ingest with durable state so morning digests only surface **new** (or score **bumps**), not the same story every day.
3. **`fetch_article_text.py`** — optional plain-text fetch for top links (no browser).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp market.example.json market.local.json   # gitignored; fill in your market
```

## Market config

Place names, local organisations and feed URLs are not in the code. They load from a JSON file:
`$LOCAL_NEWS_MARKET_CONFIG`, else `market.local.json` next to the scripts. `market.example.json`
documents the format: `market_id`, `label`, `geo_terms` / `extra_signal_terms` (regex fragments),
`geo_fallback`, and `sources` (types `wp-json`, `rss`, `html-links`, `html-calendar`).

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

- Use `html-links` for sites whose RSS is blocked (homepage headlines only).
- Free/local HTTP only. Close browser tabs when done; prefer scripts over browser.

## Self-healing runner (Grok Bot box)

```bash
/home/box/agent-data/local-news/run.sh --days 2 --new-only
```

`run.sh` keeps the venv (`/home/box/agent-data/local-news/pyenv`), market config
(`/home/box/agent-data/local-news/market.local.json`), seen-state
(`/home/box/agent-data/local-news/state/lead_inbox_state.json`) and a git mirror of this repo
(`/home/box/agent-data/local-news/src`) outside `/workspace`, because a box restore drops
`.venv/` and `out/` directories there. On each run it restores missing scripts/requirements from
the mirror, rebuilds a missing or broken venv (keeping only the newest `pyenv.broken-*`), recreates
the output dir, and logs to `/home/box/agent-data/local-news/run.log`. `lead_inbox.py` copies a
legacy `out/lead_inbox_state.json` into the persistent path once if found.
