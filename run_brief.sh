#!/usr/bin/env bash
# Collect candidates and their audit for a Codex-written brief.
set -eu

if [ "${1:-}" = "--help" ]; then
  printf '%s\n' 'Usage: ./run_brief.sh [PROJECT_DIR] [fetch_candidates options]' \
    'Writes candidates, a request audit and a coverage report under PROJECT_DIR/work/.'
  exit 0
fi

repo="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
skill="$repo/.agents/skills/ecology-morning-brief"
project="$(CDPATH='' cd -- "${1:-.}" && pwd)"
if [ "$#" -gt 0 ]; then shift; fi
python="${PYTHON_BIN:-python3}"
"$python" -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ required")'

config="$skill/config/interests.yaml"
if [ -f "$project/config/interests.local.yaml" ]; then
  config="$project/config/interests.local.yaml"
fi
mkdir -p "$project/work"
run="$(mktemp -d "$project/work/ecology-run.XXXXXXXX")"
cd "$project"
printf 'Run files: %s\n' "$run"
"$python" -B "$skill/scripts/fetch_candidates.py" \
  --config "$config" --state-dir "$project/.ecology-morning-brief-state" "$@" \
  --output "$run/candidates.json" --coverage-output "$run/coverage.json"
