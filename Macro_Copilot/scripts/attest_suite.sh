#!/usr/bin/env bash
# =============================================================================
# scripts/attest_suite.sh — HONEST full-suite attestation (Fable Plan 2 §10/§12)
# -----------------------------------------------------------------------------
# WHY THIS EXISTS
#   The §12 "Definition of Done" requires attesting that the backend test
#   suite is green ("every test passes").  The old, ad-hoc attestation piped
#   pytest through ``| tail`` and read ``$?`` *after the pipe* — which is
#   ``tail``'s exit code, NOT pytest's.  When two async collection files
#   ERRORed ("Interrupted: 2 errors during collection"), pytest exited
#   non-zero but the pipeline reported exit 0, so the attestation cheerfully
#   reported "green" while having collected ZERO tests.  That is a dishonest
#   gate (review finding B5).
#
# WHAT THIS DOES (the honest contract)
#   1. Runs the FULL suite with ``--continue-on-collection-errors`` so a
#      single bad import cannot abort collection of the other ~8k tests.
#   2. Reads pytest's *real* exit code (captured directly, never through a
#      pipe), AND defensively parses the printed summary line.
#   3. FAILS (exit non-zero) on test failures, errors, OR collection errors,
#      and PASSES (exit 0) only when the run is genuinely green.
#   4. Tees the full transcript to a log so a human can read the summary —
#      the ``| tail`` for human eyes is fine; the *decision* never depends
#      on a piped exit code.
#
# DESIGN NOTES (the anti-foot-gun rules this script obeys)
#   * Decision is driven by pytest's captured rc, taken via a tmp rc-file
#     (NOT ``$?`` after ``| tee``).  ``set -o pipefail`` is also on as belt
#     and braces, but the rc-file is authoritative.
#   * ``--continue-on-collection-errors`` is MANDATORY: without it a single
#     ImportError aborts the whole run, and pytest's "Interrupted" path is
#     exactly what the masked-rc bug hid.
#   * A run that collects 0 tests, or whose summary line is missing, is
#     treated as FAILURE — "no tests ran" is never "green".
#
# USAGE
#   In the macro-env container (the normal path):
#     docker compose run --rm --no-deps api-server bash -lc \
#       "cd /app && micromamba run -n macro-env bash scripts/attest_suite.sh"
#   Or via the Makefile wrapper (which builds that command for you):
#     make attest
#
#   Override the suite scope or extra pytest args:
#     ATTEST_PYTEST_TARGET="tests/test_workflow_event_study.py" \
#       bash scripts/attest_suite.sh
#     bash scripts/attest_suite.sh -k known_primitives_includes_all_canonical
#
# EXIT CODES
#   0  suite genuinely green (>=1 test collected, 0 failures/errors/collec-errs)
#   1  test failures and/or collection errors  (the honest RED)
#   2  environment/usage problem (pytest not runnable, etc.)
# =============================================================================

set -u -o pipefail

# --- configuration -----------------------------------------------------------
# The suite the §12 attestation names.  Default = the whole backend tree.
ATTEST_PYTEST_TARGET="${ATTEST_PYTEST_TARGET:-tests/}"

# Where to tee the full transcript so a human can read the real summary.
ATTEST_LOG="${ATTEST_LOG:-/tmp/attest_suite.log}"

# How to invoke pytest.  Inside the macro-env container pytest is on PATH;
# we use ``python -m pytest`` for robustness (honours the active env's pytest).
PYTEST=(python -m pytest)

# Mandatory flags — see the design notes above.  ``--continue-on-collection
# -errors`` is the load-bearing one; ``-p no:cacheprovider`` matches the
# project's documented invocation; ``-rfE`` surfaces failure+error short-info.
PYTEST_FLAGS=(--continue-on-collection-errors -p no:cacheprovider -rfE)

# Any extra args ($@) are appended (e.g. ``-k <expr>``, ``-q``).
EXTRA_ARGS=("$@")

# --- preflight ---------------------------------------------------------------
if ! "${PYTEST[@]}" --version >/dev/null 2>&1; then
  echo "attest_suite: FATAL — 'python -m pytest' is not runnable in this environment." >&2
  echo "  Run inside the macro-env container, e.g.:" >&2
  echo "    docker compose run --rm --no-deps api-server bash -lc \\" >&2
  echo "      \"cd /app && micromamba run -n macro-env bash scripts/attest_suite.sh\"" >&2
  exit 2
fi

echo "=============================================================================="
echo "attest_suite: running FULL suite (honest attestation)"
echo "  target : ${ATTEST_PYTEST_TARGET}"
echo "  flags  : ${PYTEST_FLAGS[*]} ${EXTRA_ARGS[*]:-}"
echo "  log    : ${ATTEST_LOG}"
echo "=============================================================================="

# --- run ---------------------------------------------------------------------
# CRITICAL: capture pytest's REAL exit code, not the pipeline's.  We write
# pytest's rc to a tmp file from inside the pipeline so the value survives
# the ``| tee``.  ``$?`` after ``| tee`` would be tee's rc — that is exactly
# the bug B5 calls out.  ``pipefail`` is also set, but the rc-file is the
# authoritative source of truth.
RC_FILE="$(mktemp)"
trap 'rm -f "${RC_FILE}"' EXIT

{
  "${PYTEST[@]}" "${ATTEST_PYTEST_TARGET}" "${PYTEST_FLAGS[@]}" "${EXTRA_ARGS[@]}"
  echo "$?" >"${RC_FILE}"
} | tee "${ATTEST_LOG}"

PYTEST_RC="$(cat "${RC_FILE}" 2>/dev/null || echo 99)"

# --- defensive summary parsing (belt and braces) -----------------------------
# We do NOT trust the rc alone — we also read pytest's printed summary so a
# masked/odd rc can never let a dirty run through.  pytest's last summary line
# looks like e.g.  "===== 1 failed, 46 passed in 2.3s =====" (verbose) or the
# bare ``-q`` form "1 failed, 46 passed in 2.3s" / "47 passed in 2.86s".  A
# collection error prints "errors during collection" and/or "Interrupted".
# The regex matches BOTH the ``=====``-decorated and the bare ``-q`` forms:
# any line that ends in a pytest outcome + an "in <time>s" / "no tests ran".
SUMMARY_LINE="$(grep -E '(passed|failed|error|errors|skipped|xfailed|xpassed|warning|warnings|deselected|no tests ran).* in [0-9]+(\.[0-9]+)?s|no tests ran|=+ Interrupted' "${ATTEST_LOG}" | tail -1)"
COLLECTED_LINE="$(grep -E 'tests? collected|no tests ran|errors? during collection|Interrupted' "${ATTEST_LOG}" | tail -3)"

echo
echo "------------------------------------------------------------------------------"
echo "attest_suite: decision inputs"
echo "  pytest exit code (authoritative) : ${PYTEST_RC}"
echo "  summary line                     : ${SUMMARY_LINE:-<none found>}"
echo "------------------------------------------------------------------------------"

# A run is DIRTY if any of these is true:
#   * pytest rc != 0                         (failures, errors, or interrupted)
#   * the summary mentions failed / error / collection-error / Interrupted
#   * no test was collected ("no tests ran" / missing summary)
dirty=0
reason=""

if [ "${PYTEST_RC}" != "0" ]; then
  dirty=1
  reason="pytest exit code ${PYTEST_RC} (non-zero)"
fi

if printf '%s\n' "${SUMMARY_LINE}" "${COLLECTED_LINE}" \
     | grep -Eiq 'failed|[0-9]+ errors?|error during|errors during collection|Interrupted'; then
  dirty=1
  reason="${reason:+${reason}; }summary reports failures/errors/collection-errors"
fi

if [ -z "${SUMMARY_LINE}" ] \
   || printf '%s\n' "${SUMMARY_LINE}" "${COLLECTED_LINE}" | grep -Eiq 'no tests ran'; then
  dirty=1
  reason="${reason:+${reason}; }no tests collected / no summary line (a green attestation must run >=1 test)"
fi

echo
if [ "${dirty}" -eq 0 ]; then
  echo "attest_suite: PASS — suite is genuinely green."
  echo "  ${SUMMARY_LINE}"
  exit 0
else
  echo "attest_suite: FAIL — attestation is RED."
  echo "  reason: ${reason}"
  echo "  ${SUMMARY_LINE:-<no summary line>}"
  echo "  full transcript: ${ATTEST_LOG}"
  exit 1
fi
