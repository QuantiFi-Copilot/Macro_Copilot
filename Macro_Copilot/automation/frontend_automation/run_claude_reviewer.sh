#!/usr/bin/env bash
set -euo pipefail

# Claude-as-reviewer runner for the frontend automation factory.
#
# Two-Claudes design note (DESIGN_PRINCIPLES.md §0 + §10):
# The reviewer engine is also Claude (NOT Codex). The independence
# property is preserved by:
#   - --print mode is naturally fresh-context per invocation (no
#     prior conversation state from the builder dispatch is visible)
#   - the reviewer's system prompt (CLAUDE_REVIEWER_PROMPT.md) is
#     adversarial-tuned and never contains the builder's prompt
#   - the reviewer reads only the diff + the repo docs + the mockups,
#     never the builder's reasoning or tool calls
#
# This trades some model-distribution independence (vs Codex) for
# operational simplicity and consistent quota handling on a single
# provider.  See DESIGN_PRINCIPLES.md §10 for the rationale.
#
# Single-round-pipeline note (SINGLE_REVIEW_ROUND_POLICY.md):
# This wrapper is dispatched EXACTLY ONCE per tool.  The reviewer
# emits one of three headings (APPROVED / CHANGES REQUIRED /
# DEFER / DO NOT BUILD).  There is no re-review after a fix pass.

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <prompt-file> [log-file]" >&2
  exit 2
fi

PROMPT_FILE="$1"
LOG_FILE="${2:-/tmp/claude_frontend_reviewer_run.log}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

bash automation/frontend_automation/check_branch.sh

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
