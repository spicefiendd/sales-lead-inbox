#!/usr/bin/env python3
"""lead-inbox — durable local SMB lead inbox with cross-day dedupe.

Extends local_lead_ingest.py. Free/local HTTP only. No paid APIs. No browser.
Research-only; no Azure; no outbound sends.

Usage:
  /workspace/sales/.venv/bin/python /workspace/sales/scripts/lead_inbox.py --days 2
  /workspace/sales/.venv/bin/python /workspace/sales/scripts/lead_inbox.py --days 2 --new-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# allow importing sibling module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_lead_ingest import collect_candidates  # noqa: E402

DEFAULT_ROOT = Path("/workspace/sales")
DEFAULT_STATE = DEFAULT_ROOT / "out" / "lead_inbox_state.json"
DEFAULT_JSON = DEFAULT_ROOT / "out" / "lead_inbox_latest.json"
DEFAULT_MD = DEFAULT_ROOT / "out" / "lead_inbox_latest.md"
BUMP_DELTA = 4  # resurface if score jumped by this much


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def lead_id(link: str, title: str) -> str:
    raw = (link or title or "").rstrip("/").encode()
    return hashlib.sha1(raw).hexdigest()[:12]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "market": "46580", "leads": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"version": 1, "market": "46580", "leads": {}, "corrupt_reset": now_iso()}


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        Path(tmp).replace(path)
    finally:
        t = Path(tmp)
        if t.exists():
            t.unlink(missing_ok=True)


def classify(item: dict, prev: dict | None) -> str:
    score = int(item.get("score") or 0)
    if prev is None:
        return "new"
    old = int(prev.get("last_score") or 0)
    if score >= old + BUMP_DELTA:
        return "bump"
    return "seen"


def main() -> int:
    ap = argparse.ArgumentParser(description="Sales lead-inbox with cross-day dedupe (free/local)")
    ap.add_argument("--market", default="46580")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--min-score", type=int, default=4)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--md", type=Path, default=DEFAULT_MD)
    ap.add_argument(
        "--sources",
        default="inkfreenews,kedco,timesunion,kchamber",
        help="Comma list of sources",
    )
    ap.add_argument("--new-only", action="store_true", help="Print/write only new + bump items")
    ap.add_argument("--dry-run", action="store_true", help="Do not update state file")
    args = ap.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    ranked, errors = collect_candidates(args.days, args.min_score, sources)
    state = load_state(args.state)
    state.setdefault("leads", {})
    state["market"] = args.market
    state["last_run"] = now_iso()

    enriched: list[dict] = []
    counts = {"new": 0, "seen": 0, "bump": 0}

    for it in ranked:
        lid = lead_id(it.get("link", ""), it.get("title", ""))
        prev = state["leads"].get(lid)
        status = classify(it, prev)
        counts[status] = counts.get(status, 0) + 1
        first_seen = (prev or {}).get("first_seen") or now_iso()
        row = {
            "id": lid,
            "title": it.get("title"),
            "link": it.get("link"),
            "date": it.get("date"),
            "source": it.get("source"),
            "score": it.get("score"),
            "match_reasons": it.get("match_reasons") or [],
            "summary": it.get("summary"),
            "status": status,
            "first_seen": first_seen,
            "method": it.get("method"),
        }
        enriched.append(row)

        if not args.dry_run:
            entry = {
                "title": row["title"],
                "link": row["link"],
                "first_seen": first_seen,
                "last_surfaced": now_iso(),
                "last_score": row["score"],
                "source": row["source"],
                "times_seen": int((prev or {}).get("times_seen") or 0) + 1,
            }
            state["leads"][lid] = entry

    if args.new_only:
        export_items = [r for r in enriched if r["status"] in ("new", "bump")]
    else:
        export_items = enriched

    payload = {
        "generated_at": now_iso(),
        "market": f"{args.market} / Warsaw / Kosciusko County",
        "days": args.days,
        "counts": {"ingested": len(enriched), **counts, "exported": len(export_items)},
        "errors": errors,
        "items": export_items,
        "notes": [
            "Research-only. No Azure. No outbound. No paid APIs.",
            "status=new first time; seen already briefed; bump if score jumped materially.",
            "Morning digest should prefer --new-only.",
        ],
    }

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2) + "\n")
    lines = [
        f"# Lead inbox — {payload['generated_at']}",
        f"Market: {payload['market']} · lookback {args.days}d",
        f"Counts: {payload['counts']}",
        "",
    ]
    for i, it in enumerate(export_items, 1):
        lines += [
            f"## {i}. [{it['status']}] {it['title']}",
            f"- id: `{it['id']}` · score: {it['score']} · source: {it['source']} · date: {it.get('date') or 'n/a'}",
            f"- link: {it['link']}",
            f"- reasons: {', '.join(it.get('match_reasons') or [])}",
            f"- first_seen: {it['first_seen']}",
            f"- summary: {it.get('summary') or ''}",
            "",
        ]
    if errors:
        lines += ["## Ingest errors", *[f"- {e}" for e in errors], ""]
    args.md.write_text("\n".join(lines) + "\n")

    if not args.dry_run:
        atomic_write_json(args.state, state)

    print(
        f"lead-inbox: ingested={len(enriched)} new={counts['new']} "
        f"seen={counts['seen']} bump={counts['bump']} exported={len(export_items)}",
        file=sys.stderr,
    )
    print(f"Wrote {args.json}", file=sys.stderr)
    print(f"Wrote {args.md}", file=sys.stderr)
    if not args.dry_run:
        print(f"Updated state {args.state}", file=sys.stderr)

    # human stdout: new/bump first
    show = sorted(export_items, key=lambda x: ({"new": 0, "bump": 1, "seen": 2}.get(x["status"], 9), -int(x.get("score") or 0)))
    print(f"\n# Lead inbox ({len(show)})\n")
    for it in show:
        print(f"- [{it['status']}|{it['score']}] {it['title']}")
        print(f"  {it.get('date') or 'n/a'} · {it['source']} · {it['link']}")
    return 0 if not errors or enriched else 1


if __name__ == "__main__":
    raise SystemExit(main())
