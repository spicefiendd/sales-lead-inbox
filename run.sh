#!/usr/bin/env bash
# Local News lead-inbox runner. Self-heals:
#   - scripts/requirements missing from /workspace -> restored from a git mirror
#     of spicefiendd/sales-lead-inbox kept under $BASE/src (re-cloned if missing)
#   - venv missing/broken -> rebuilt from requirements (newest broken copy kept)
#   - output dir missing -> recreated
# Venv, state, market config, mirror and log live under /home/box/agent-data/local-news because
# the box restore drops `.venv/` and `out/` dirs under /workspace.
# Usage: run.sh [lead_inbox.py flags], e.g. run.sh --days 2 --new-only
set -euo pipefail

BASE="${LOCAL_NEWS_HOME:-/home/box/agent-data/local-news}"
VENV="${LOCAL_NEWS_VENV:-$BASE/pyenv}"
STATE_DIR="${LOCAL_NEWS_STATE_DIR:-$BASE/state}"
MIRROR="${LOCAL_NEWS_MIRROR:-$BASE/src}"
REPO_URL="${LOCAL_NEWS_REPO_URL:-https://github.com/spicefiendd/sales-lead-inbox.git}"
SALES="${LOCAL_NEWS_SALES:-/workspace/sales}"
SCRIPTS="${LOCAL_NEWS_SCRIPTS:-$SALES/scripts}"
REQ="${LOCAL_NEWS_REQUIREMENTS:-$SALES/requirements.txt}"
OUT="${LOCAL_NEWS_OUT:-$SALES/out}"
LOG="${LOCAL_NEWS_LOG:-$BASE/run.log}"
PY="${PYTHON:-python3}"
MARKET_CFG="${LOCAL_NEWS_MARKET_CONFIG:-$BASE/market.local.json}"
export LOCAL_NEWS_STATE_DIR="$STATE_DIR" LOCAL_NEWS_MARKET_CONFIG="$MARKET_CFG"

mkdir -p "$BASE" "$STATE_DIR"
log() { local m="$(date '+%Y-%m-%d %H:%M:%S %Z') run.sh: $*"; echo "$m" >&2; echo "$m" >>"$LOG"; }

exec 9>"$BASE/.bootstrap.lock"
flock -w 600 9 || { log "could not get bootstrap lock"; exit 1; }

# --- 1. source files -------------------------------------------------------
ensure_mirror() {
  if [ -d "$MIRROR/.git" ] && git -C "$MIRROR" rev-parse --verify -q HEAD >/dev/null; then
    timeout 60 git -C "$MIRROR" pull --ff-only -q >/dev/null 2>&1 \
      || log "mirror pull failed (using existing mirror at $(git -C "$MIRROR" rev-parse --short HEAD))"
  else
    if [ -e "$MIRROR" ]; then
      local aside="$MIRROR.bad-$(date +%Y%m%d%H%M%S)"
      log "mirror at $MIRROR unusable; moving it to $aside"
      mv "$MIRROR" "$aside"
    fi
    log "cloning $REPO_URL -> $MIRROR"
    timeout 120 git clone -q "$REPO_URL" "$MIRROR"
  fi
}

needed=(lead_inbox.py local_lead_ingest.py fetch_article_text.py)
missing=0
for f in "${needed[@]}"; do [ -f "$SCRIPTS/$f" ] || missing=1; done
[ -f "$REQ" ] || missing=1

if ! ensure_mirror; then
  [ "$missing" = 1 ] && { log "mirror unavailable and sources missing; cannot continue"; exit 1; }
  log "mirror unavailable; sources present, continuing"
fi
if [ "$missing" = 1 ]; then
  mkdir -p "$SCRIPTS" "$(dirname "$REQ")"
  for f in "${needed[@]}"; do
    if [ ! -f "$SCRIPTS/$f" ]; then
      cp -p "$MIRROR/$f" "$SCRIPTS/$f"
      log "restored $SCRIPTS/$f from mirror @ $(git -C "$MIRROR" rev-parse --short HEAD)"
    fi
  done
  if [ ! -f "$REQ" ]; then
    cp -p "$MIRROR/requirements.txt" "$REQ"
    log "restored $REQ from mirror @ $(git -C "$MIRROR" rev-parse --short HEAD)"
  fi
fi

# --- 2. venv ---------------------------------------------------------------
venv_ok() {
  [ -f "$VENV/.local-news-ready" ] &&
    [ -x "$VENV/bin/python" ] &&
    "$VENV/bin/python" -c 'import requests, bs4, lxml' >/dev/null 2>&1
}

if ! venv_ok; then
  if [ -e "$VENV" ]; then
    aside="$VENV.broken-$(date +%Y%m%d%H%M%S)"
    log "venv at $VENV is broken; moving it to $aside"
    mv "$VENV" "$aside"
    # keep only the newest broken copy
    mapfile -t old < <(ls -1dt -- "$VENV".broken-* 2>/dev/null | tail -n +2)
    for d in "${old[@]}"; do
      case "$d" in "$VENV".broken-*) rm -rf -- "$d"; log "removed older broken venv $d";; esac
    done
  fi
  log "building venv at $VENV from $REQ"
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$REQ"
  "$VENV/bin/python" -c 'import requests, bs4, lxml'
  touch "$VENV/.local-news-ready"
  log "venv ready"
fi
flock -u 9

# --- 3. outputs + run ------------------------------------------------------
[ -f "$MARKET_CFG" ] || { log "market config missing: $MARKET_CFG (copy market.example.json and fill it in)"; exit 1; }
mkdir -p "$OUT"
extra=()
case " $* " in *" --state "*|*" --state="*) ;; *) extra+=(--state "$STATE_DIR/lead_inbox_state.json");; esac
case " $* " in *" --json "*|*" --json="*) ;; *) extra+=(--json "$OUT/lead_inbox_latest.json");; esac
case " $* " in *" --md "*|*" --md="*) ;; *) extra+=(--md "$OUT/lead_inbox_latest.md");; esac

exec "$VENV/bin/python" "$SCRIPTS/lead_inbox.py" "${extra[@]}" "$@"
