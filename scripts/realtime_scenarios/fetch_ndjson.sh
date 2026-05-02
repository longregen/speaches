#!/usr/bin/env bash
# Download per-scenario NDJSONs from a running speaches server using the
# session ids written by the run_*.js drivers.
#
# Usage:
#   SPEACHES_HTTP=http://127.0.0.1:1327 OUT_DIR=/tmp/realtime_scenarios \
#     ./fetch_ndjson.sh [sessions.json ...]
#
# With no arguments, fetches NDJSONs for every sessions_*.json (and sessions.json)
# under $OUT_DIR.

set -euo pipefail

HTTP="${SPEACHES_HTTP:-http://127.0.0.1:1327}"
OUT="${OUT_DIR:-/tmp/realtime_scenarios}"

if (( $# == 0 )); then
  shopt -s nullglob
  set -- "$OUT"/sessions.json "$OUT"/sessions_*.json
fi

for sess in "$@"; do
  if [[ ! -f "$sess" ]]; then
    echo "skip (not found): $sess" >&2
    continue
  fi
  echo "== $sess =="
  # Print "name sid" pairs; skip nulls.
  while IFS=$'\t' read -r name sid; do
    [[ -z "$sid" || "$sid" == "null" ]] && continue
    out="$OUT/$name.ndjson"
    curl -fsS "$HTTP/v1/inspect/sessions/history/$sid" -o "$out"
    printf '  %-40s %s  (%d lines)\n' "$name" "$sid" "$(wc -l < "$out")"
  done < <(python3 -c "
import json, sys
for k, v in json.load(open('$sess')).items():
    print(f'{k}\t{v}')
")
done
