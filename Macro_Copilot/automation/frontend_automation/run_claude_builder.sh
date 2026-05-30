#!/usr/bin/env bash
set -euo pipefail

# Builder wrapper for the frontend factory.  Mirrors the shape of
# automation/primitive_automation/run_claude_builder.sh exactly (same
# CLI invocation, same explicit flag discipline, same log capture, same
# branch guard).  Used by the OpenClaw frontend-orchestrator cron job
# at scheduled wakes to build one frontend module per dispatch.
#
# Single-round-pipeline note (SINGLE_REVIEW_ROUND_POLICY.md):
# This wrapper is dispatched at most TWICE per tool — once for the
# initial build, optionally once more to apply reviewer findings as a
# single fix pass.  No third dispatch ever occurs for the same tool.
# The pre-commit gate enforces test/typecheck cleanliness before the
# commit is allowed.

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <prompt-file> [log-file]" >&2
  exit 2
fi

PROMPT_FILE="$1"
LOG_FILE="${2:-/tmp/claude_frontend_builder_run.log}"

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
