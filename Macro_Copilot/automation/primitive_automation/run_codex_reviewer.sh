#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <prompt-file> [log-file]" >&2
  exit 2
fi

PROMPT_FILE="$1"
LOG_FILE="${2:-/tmp/codex_reviewer_run.log}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

bash automation/primitive_automation/check_branch.sh

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "prompt file not found: $PROMPT_FILE" >&2
  exit 2
fi

: >"$LOG_FILE"

codex exec \
  --sandbox danger-full-access \
  -m gpt-5.5 \
  -c model_reasoning_effort="high" \
  "$(cat "$PROMPT_FILE")" \
  2>&1 | tee "$LOG_FILE"
