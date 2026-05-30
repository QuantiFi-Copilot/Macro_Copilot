#!/usr/bin/env bash
# Optional local pre-commit hook template.
# Install: cp automation/frontend_automation/pre-commit-branch-guard.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
set -euo pipefail
EXPECTED_BRANCH="frontend_automation"
CURRENT_BRANCH="$(git branch --show-current)"
if [[ "${CURRENT_BRANCH}" != "${EXPECTED_BRANCH}" ]]; then
  echo "BLOCKED: frontend_automation hook refuses commits on branch '${CURRENT_BRANCH}'."
  echo "Switch to '${EXPECTED_BRANCH}' or remove this hook."
  exit 1
fi
