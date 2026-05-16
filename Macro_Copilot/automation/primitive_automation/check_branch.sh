#!/usr/bin/env bash

set -euo pipefail

EXPECTED_BRANCH="primitive_automation"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: not inside a git worktree."
  exit 1
fi

CURRENT_BRANCH="$(git branch --show-current)"

if [[ "${CURRENT_BRANCH}" != "${EXPECTED_BRANCH}" ]]; then
  echo "ERROR: primitive automation may only run on branch '${EXPECTED_BRANCH}'."
  echo "Current branch: '${CURRENT_BRANCH}'"
  exit 1
fi

echo "OK: on allowed branch '${EXPECTED_BRANCH}'."
