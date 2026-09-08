#!/usr/bin/env python3
"""Cheap local-lead ingest for ZIP 46580 / Kosciusko County.

Replaces expensive browser / multi-search loops with:
  - WordPress JSON + RSS (InkFreeNews, KEDCO)
  - Light HTML scrape (Times-Union homepage headlines)

Research-only. No Azure. No outbound sends.

Usage:
  /workspace/sales/.venv/bin/python scripts/local_lead_ingest.py --days 7
  /workspace/sales/.venv/bin/python scripts/local_lead_ingest.py --days 7 --json out/digest_candidates.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; GrokBotSales/1.0; research-digest)",
    "Accept": "application/json, application/rss+xml, text/html, */*",
}

# Must look like local market
GEO = re.compile(
    r"\b(warsaw|kosciusko|pierceton|winona\s*lake|syracuse|mentone|leesburg|"
    r"north\s*webster|claypool|milford|wawasee|etna\s*green|silver\s*lake|"
    r"orthoworx|kedco|grace\s*college|zimmer|biomet|slate\s*auto|hirekosciusko)\b",
    re.I,
)

# SMB / banking-relevant signals
SIGNAL = re.compile(
    r"\b(open(?:s|ing|ed)?|ribbon[- ]?cut|grand opening|expand(?:s|ing|ed|sion)?|"
    r"hiring|hire|workforce|job fair|relocated?|relocation|new (?:store|shop|location|facility|plant)|"
    r"groundbreak(?:ing)?|construction|manufactur(?:e|ing|er)|facility|plant|industrial|"
    r"rezon(?:e|ing)|zoning|tif|economic development|chamber|business|"
    r"franchise|merchant|retail|restaurant|cafe|store|shop|"
    r"loan|lender|bank(?:ing)?|capital|investment|acquisition|merger|"
    r"supplier|medtech|orthop(?:edic)?|medical device|cre|commercial|"
    r"donation|headquarters|hq|campus)\b",
    re.I,
)

# Always drop — crime blotter, sports, civic filler, etc.
HARD_NOISE = re.compile(
    r"\b(obituar|funeral|in loving memory|church news|court news|public occurrences|"
    r"jail booking|most wanted|arrest(?:ed)?|allegedly|sentenced|firearm offense|"
    r"fraudulent|lawsuit against|files lawsuit|police|sheriff|"
    r"football|basketball|baseball|softball|volleyball|soccer|\bsports\b|varsity|"
    r"junior varsity|takes on|video:|"
    r"library card|local authors|red panda|problem solved|comcast|"
    r"weather|school lunch|high school|"
    r"firefighters? take oath|oaths as .* firefighter|"
    r"meeting cancellation|most wanted)\b",
    re.I,
)

# Weak alone — need a real growth/CRE/employment signal in the title
WEAK_SIGNAL = re.compile(
    r"\b(business|chamber|commercial|facility|plant|store|shop|cafe|restaurant|retail|"
    r"campus|donation|capital|investment|bank(?:ing)?)\b",
    re.I,
)

STRONG_TITLE = re.compile(
    r"\b(opens?(?:\s+for\s+business|\s+in\b)|ribbon[- ]?cut(?:ting)?|grand opening|"
    r"expand(?:s|ing|ed|sion)|hiring|hire|job fair|workforce|"
    r"relocated?|relocation|new (?:store|shop|location|facility|plant|home)|"
    r"groundbreak(?:ing)?|rezon(?:e|ing)|zoning modernization|tif |"
    r"manufactur|headquarters|\bhq\b|franchise|acquisition|merger|"
    r"finds? (?:their |its )?new home)\b",
    re.I,
)

# Soft civic/nonprofit events that aren't banking leads unless STRONG_TITLE hits
SOFT_NOISE = re.compile(
    r"\b(open house|fundraiser|volunteers?, nonprofits|we lead|"
    r"strategic planning committee|glow \& roll|cancer services)\b",
    re.I,
)


def strip_html(s: str) -> str:
    s = unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def score_item(title: str, summary: str) -> tuple[int, str | None]:
    blob = f"{title} {summary}"

    if HARD_NOISE.search(title) or HARD_NOISE.search(blob):
        return 0, "hard_noise"

    # nonprofit open houses / fundraisers are soft noise even if title starts with "Open"
    if re.search(r"\bopen house\b|\bfundraiser\b", blob, re.I):
        return 0, "soft_noise"

    if SOFT_NOISE.search(blob) and not STRONG_TITLE.search(title):
        return 0, "soft_noise"

    geo_hits = GEO.findall(blob)
    if not geo_hits and not re.search(r"kosciusko|warsaw", blob, re.I):
        return 0, "no_geo"

    sig_hits = SIGNAL.findall(blob)
    if not sig_hits:
        return 0, "no_signal"

    # Bare weak words ("business", "facility") aren't enough without a strong title cue
    strong = bool(STRONG_TITLE.search(title))
    only_weak = all(WEAK_SIGNAL.fullmatch(h) or WEAK_SIGNAL.search(h) for h in sig_hits)
    # simpler: if no strong title and every signal token is in the weak set, drop
    weak_only = True
    for h in sig_hits:
        if not WEAK_SIGNAL.search(h):
            weak_only = False
            break
    if weak_only and not strong:
        return 0, "weak_only"

    score = len(sig_hits) * 3 + min(len(geo_hits), 3)
    if strong:
        score += 6
    # Extra boost for classic SMB verbs
    if re.search(r"\b(ribbon|expand|hiring|job fair|rezon|groundbreak|reloc|new home|opens?)\b", title, re.I):
        score += 3
    return score, None


def fetch_wp_posts(base: str, source: str, days: int, per_page: int = 50) -> list[dict]:
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    url = f"{base.rstrip('/')}/wp-json/wp/v2/posts"
    out: list[dict] = []
    page = 1
    while page <= 5:
        r = requests.get(
            url,
            headers=UA,
            params={
                "per_page": per_page,
                "page": page,
                "after": after,
                "_fields": "id,date,link,title,excerpt",
            },
            timeout=30,
        )
        if r.status_code == 400:
            break
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for p in batch:
            title = strip_html(p.get("title", {}).get("rendered", ""))
            summary = strip_html(p.get("excerpt", {}).get("rendered", ""))
            score, skip = score_item(title, summary)
            if skip or score <= 0:
                continue
            out.append(
                {
                    "source": source,
                    "title": title,
                    "link": p.get("link", ""),
                    "date": (p.get("date") or "")[:10] or None,
                    "summary": summary[:400],
                    "score": score,
                    "method": "wp-json",
                }
            )
        if len(batch) < per_page:
            break
        page += 1
    return out


def fetch_rss(url: str, source: str, days: int) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    for item in root.findall("./channel/item"):
        title = strip_html(item.findtext("title") or "")
        link = item.findtext("link") or ""
        summary = strip_html(item.findtext("description") or "")
        pub = item.findtext("pubDate")
        dt = None
        if pub:
            try:
                dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except Exception:
                dt = None
        if dt and dt < cutoff:
            continue
        score, skip = score_item(title, summary)
        if skip or score <= 0:
            continue
        out.append(
            {
                "source": source,
                "title": title,
                "link": link,
                "date": dt.date().isoformat() if dt else None,
                "summary": summary[:400],
                "score": score,
                "method": "rss",
            }
        )
    return out


def fetch_times_union_headlines() -> list[dict]:
    """Homepage only — Times-Union RSS returned 403."""
    r = requests.get("https://timesuniononline.com/", headers=UA, timeout=30)
    r.raise_for_status()
    paths = re.findall(r'href="(/stories/[^"#?]+)"', r.text)
    out: list[dict] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        slug = path.split("/")[-1].rsplit(",", 1)[0]
        title = slug.replace("-", " ").strip().title()
        score, skip = score_item(title, "")
        if skip or score <= 0:
            continue
        out.append(
            {
                "source": "timesunion",
                "title": title,
                "link": "https://timesuniononline.com" + path,
                "date": None,
                "summary": "(homepage headline only — open article if ranking)",
                "score": score,
                "method": "html-home",
            }
        )
    return out


def fetch_chamber_events() -> list[dict]:
    """Best-effort chamber calendar page pull (no login)."""
    url = "https://my.kchamber.com/chambereventcalendar"
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return [
            {
                "source": "kchamber",
                "title": f"[chamber calendar unreachable] {e}",
                "link": url,
                "date": None,
                "summary": "",
                "score": 0,
                "method": "html",
            }
        ]
    # Grab event-ish anchors
    links = re.findall(
        r'href="(https://my\.kchamber\.com/chambereventcalendar/Details/[^"]+)"[^>]*>([^<]{3,120})',
        r.text,
    )
    if not links:
        # fallback: detail URLs only
        urls = list(dict.fromkeys(re.findall(r'https://my\.kchamber\.com/chambereventcalendar/Details/[^"\s]+', r.text)))
        links = [(u, u.split("/")[-1].split("?")[0].replace("-", " ")) for u in urls[:30]]
    out: list[dict] = []
    for link, title in links[:40]:
        title = strip_html(title)
        score, skip = score_item(title, "chamber event business networking")
        # chamber events are almost always worth a peek for SMB bankers
        if skip:
            continue
        score = max(score, 4)
        out.append(
            {
                "source": "kchamber",
                "title": title,
                "link": link,
                "date": None,
                "summary": "Chamber calendar event — possible networking / women-owned / ribbon-cut surface",
                "score": score,
                "method": "html-calendar",
            }
        )
    return out


def dedupe(items: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for it in items:
        key = (it.get("link") or it.get("title") or "").rstrip("/")
        if not key:
            continue
        prev = best.get(key)
        if prev is None or it.get("score", 0) > prev.get("score", 0):
            best[key] = it
    return sorted(best.values(), key=lambda x: (-x.get("score", 0), x.get("date") or ""))



SOURCE_FUNCS = {
    "inkfreenews": lambda days: ("inkfreenews-wp", fetch_wp_posts("https://www.inkfreenews.com", "inkfreenews", days)),
    "kedco": lambda days: ("kedco-rss", fetch_rss("https://www.kosciuskoedc.com/feed/", "kedco", max(days, 180))),
    "timesunion": lambda days: ("timesunion-home", fetch_times_union_headlines()),
    "kchamber": lambda days: ("kchamber-cal", fetch_chamber_events()),
}


def match_reasons(title: str, summary: str) -> list[str]:
    blob = f"{title} {summary}"
    reasons: list[str] = []
    for g in GEO.findall(blob):
        reasons.append(f"geo:{g.lower()}")
    for s in SIGNAL.findall(blob):
        reasons.append(f"signal:{s.lower()}")
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out[:12]


def collect_candidates(
    days: int = 7,
    min_score: int = 4,
    sources: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Return (ranked items with match_reasons, errors). Free/local HTTP only."""
    wanted = sources or list(SOURCE_FUNCS.keys())
    errors: list[str] = []
    items: list[dict] = []
    for key in wanted:
        if key not in SOURCE_FUNCS:
            errors.append(f"unknown source: {key}")
            continue
        label, fn_or_pair = None, None
        try:
            label, batch = SOURCE_FUNCS[key](days)
            items.extend(batch)
            print(f"[ok] {label}: {len(batch)} candidates", file=sys.stderr)
        except Exception as e:
            errors.append(f"{key}: {e}")
            print(f"[err] {key}: {e}", file=sys.stderr)
    ranked = []
    for it in dedupe(items):
        if it.get("score", 0) < min_score:
            continue
        it = dict(it)
        it["match_reasons"] = match_reasons(it.get("title", ""), it.get("summary", ""))
        ranked.append(it)
    return ranked, errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Cheap 46580 local-lead ingest (no browser)")
    ap.add_argument("--days", type=int, default=7, help="Lookback window for news feeds")
    ap.add_argument("--json", type=Path, help="Write full JSON to this path")
    ap.add_argument("--md", type=Path, help="Write markdown candidate list")
    ap.add_argument("--min-score", type=int, default=4, help="Minimum score to include")
    ap.add_argument(
        "--sources",
        default="inkfreenews,kedco,timesunion,kchamber",
        help="Comma list: inkfreenews,kedco,timesunion,kchamber",
    )
    args = ap.parse_args()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    ranked, errors = collect_candidates(args.days, args.min_score, sources)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "market": "46580 / Warsaw / Kosciusko County",
        "days": args.days,
        "count": len(ranked),
        "errors": errors,
        "items": ranked,
        "notes": [
            "Research-only ingest. No Azure. No outbound.",
            "Use this list as the cheap first pass; rank banking angles manually / with the Sales agent.",
            "Times-Union RSS is blocked (403); homepage headlines only.",
        ],
    }
    print(f"\n# Local lead candidates ({payload['count']}) — last {args.days}d\n")
    for i, it in enumerate(ranked, 1):
        print(f"{i}. [{it['score']}] {it['title']}")
        print(f"   {it.get('date') or 'n/a'} · {it['source']} · {it['link']}")
        if it.get("summary"):
            print(f"   {it['summary'][:180]}")
        print()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2))
        print(f"Wrote {args.json}", file=sys.stderr)
    if args.md:
        lines = [
            f"# Local lead candidates — {payload['generated_at']}",
            f"Market: {payload['market']} · lookback {args.days}d · count {payload['count']}",
            "",
        ]
        for i, it in enumerate(ranked, 1):
            lines += [
                f"## {i}. {it['title']}",
                f"- Score: {it['score']} · Source: {it['source']} · Date: {it.get('date') or 'n/a'}",
                f"- Link: {it['link']}",
                f"- Reasons: {', '.join(it.get('match_reasons') or [])}",
                f"- Summary: {it.get('summary') or ''}",
                "",
            ]
        if errors:
            lines += ["## Ingest errors", *[f"- {e}" for e in errors], ""]
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text("\n".join(lines))
        print(f"Wrote {args.md}", file=sys.stderr)
    return 0 if not errors or ranked else 1



if __name__ == "__main__":
    raise SystemExit(main())
