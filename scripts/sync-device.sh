#!/usr/bin/env bash
set -euo pipefail

# Push one payload, or every .json file in a directory, to a FixIndex instance.
#
#   FIXINDEX_URL=https://fixindex.example \
#   FIXINDEX_TOKEN=fx_live_... \
#   ./agent-kit/scripts/sync-device.sh agent-kit/data/
#
# DRY_RUN=1 prints the files without sending anything.

BASE_URL="${FIXINDEX_URL:-http://127.0.0.1:8000}"
TOKEN="${FIXINDEX_TOKEN:-}"
DRY_RUN="${DRY_RUN:-0}"
TARGET="${1:-}"

if [[ -z "$TARGET" ]]; then
  echo "usage: FIXINDEX_TOKEN=... $0 <payload.json|directory>" >&2
  exit 2
fi

if [[ "$DRY_RUN" != "1" && -z "$TOKEN" ]]; then
  echo "FIXINDEX_TOKEN is required (agent JWT or API key)." >&2
  exit 2
fi

if [[ ! -e "$TARGET" ]]; then
  echo "no such file or directory: $TARGET" >&2
  exit 2
fi

payloads=()
if [[ -d "$TARGET" ]]; then
  while IFS= read -r file; do
    payloads+=("$file")
  done < <(find "$TARGET" -maxdepth 1 -type f -name '*.json' | sort)
else
  payloads=("$TARGET")
fi

if [[ ${#payloads[@]} -eq 0 ]]; then
  echo "no .json payloads in $TARGET" >&2
  exit 2
fi

status=0
endpoint="${BASE_URL%/}/api/v1/agent/devices"

for file in "${payloads[@]}"; do
  if ! python -c 'import json,sys; json.load(open(sys.argv[1]))' "$file" >/dev/null 2>&1; then
    echo "invalid JSON: $file" >&2
    status=1
    continue
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "would POST $file"
    continue
  fi

  response_file="$(mktemp)"
  http_code="$(curl -sS -o "$response_file" -w '%{http_code}' \
    -X POST "$endpoint" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    --data @"$file")"

  echo "$file -> HTTP $http_code"

  if command -v python >/dev/null 2>&1; then
    python -m json.tool "$response_file" 2>/dev/null || cat "$response_file"
  else
    cat "$response_file"
  fi
  echo

  rm -f "$response_file"

  case "$http_code" in
    200|201) ;;
    *) status=1 ;;
  esac
done

exit "$status"
