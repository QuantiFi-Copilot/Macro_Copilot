#!/usr/bin/env bash

set -euo pipefail

EXPECTED_BRANCH="primitive_automation"
CURRENT_BRANCH="$(git branch --show-current)"

if [[ "${CURRENT_BRANCH}" != "${EXPECTED_BRANCH}" ]]; then
  echo "ERROR: pushes are only allowed from branch '${EXPECTED_BRANCH}'."
  echo "Current branch: '${CURRENT_BRANCH}'"
  exit 1
fi

exit 0
