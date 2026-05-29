#!/usr/bin/env bash
# Optional local pre-push hook template.
# Install: cp automation/frontend_automation/pre-push-branch-guard.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push
set -euo pipefail
EXPECTED_BRANCH="frontend_automation"
CURRENT_BRANCH="$(git branch --show-current)"
if [[ "${CURRENT_BRANCH}" != "${EXPECTED_BRANCH}" ]]; then
  echo "BLOCKED: frontend_automation hook refuses pushes on branch '${CURRENT_BRANCH}'."
  echo "Switch to '${EXPECTED_BRANCH}' or remove this hook."
  exit 1
fi
