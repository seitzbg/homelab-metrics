#!/usr/bin/env bash
set -uo pipefail
PATTERNS='fiber\.house|bsd-unix|\b(192\.168|10)\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+|\b(seitz|bseitz|munro)\b|\.vault|prometheus-sleep|[Pp]rometheus[-_ ]?[Ss]leep|\b49361\b'
targets=("${@:-.}")
hits=$(grep -rInHE "$PATTERNS" "${targets[@]}" \
  --exclude-dir=.git --exclude-dir=__pycache__ \
  --exclude='scrub-check.sh' --exclude-dir=scrub_fixtures 2>/dev/null \
  | grep -vE '^\./\.gitignore:[0-9]+:.*\*\.vault\*')
if [ -n "$hits" ]; then echo "SCRUB FAIL:"; echo "$hits"; exit 1; fi
echo "scrub-check: clean"; exit 0
