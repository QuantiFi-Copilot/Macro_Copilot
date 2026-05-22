#!/usr/bin/env bash
set -euo pipefail

# Claude-as-reviewer FALLBACK runner.
#
# Used by the orchestrator when Codex limits are exhausted (detected by
# parse_reviewer_log.py from a prior run_codex_reviewer.sh log). Claude
# reviews the build using the SAME REVIEWER_PROMPT.md the Codex path
# uses; the only difference is the worker model.
#
# P5 (honest disclosure): the orchestrator records
#   reviewer_mode: claude_fallback
#   reviewer_mode_history: [{at, from: codex, to: claude_fallback, reason}]
# in primitive_runtime_state.yaml whenever this script is used, so the
# audit trail captures which engine reviewed each primitive. Lower
# model-independence in fallback mode is a known trade-off vs the
# alternative of halting the factory.
#
# The wrapper preserves the same explicit-flag discipline as
# run_codex_reviewer.sh:
#   - branch check upfront
#   - explicit model + effort + permission flags
#   - log capture to a dedicated log file

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <prompt-file> [log-file]" >&2
  exit 2
fi

PROMPT_FILE="$1"
LOG_FILE="${2:-/tmp/claude_reviewer_run.log}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

bash automation/primitive_automation/check_branch.sh

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "prompt file not found: $PROMPT_FILE" >&2
  exit 2
fi

: >"$LOG_FILE"

claude \
  --print \
  --model opus \
  --effort xhigh \
  --permission-mode bypassPermissions \
  "$(cat "$PROMPT_FILE")" \
  2>&1 | tee "$LOG_FILE"
