#!/usr/bin/env python3
"""parse_reviewer_log.py — extract heading + quota state from a reviewer log.

The orchestrator runs a reviewer (Codex primary, Claude fallback) and pipes
the worker's stdout/stderr to a log file. This script reads that log and
returns a small JSON record the orchestrator consumes to decide what to do
next:

  - ``heading``                  — "APPROVED" / "CHANGES REQUIRED" /
                                   "DEFER / DO NOT BUILD" / null. Latest
                                   occurrence wins (workers sometimes
                                   restate the contract verbatim before
                                   emitting their own verdict).
  - ``environment_failure``      — true when the worker bailed out before
                                   ever evaluating the build (sandbox /
                                   filesystem / bwrap errors). Signals
                                   "retry, don't penalise the primitive."
  - ``reviewer_quota_exhausted`` — true when the log shows a quota /
                                   rate-limit signal AND no valid heading
                                   was emitted. Signals the orchestrator
                                   to switch the active reviewer engine
                                   (Codex -> Claude) and re-dispatch the
                                   SAME build. This is the trigger that
                                   makes the Codex -> Claude fallback
                                   work; see
                                   ``QUOTA_AND_RESUME_POLICY.md`` §10
                                   and ``run_claude_reviewer.sh``.

Single output line of JSON on stdout. Exit code 0 = parse succeeded
(regardless of the verdict inside the log); 1 = log file unreadable;
2 = bad CLI usage.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


VALID_HEADINGS = ["APPROVED", "CHANGES REQUIRED", "DEFER / DO NOT BUILD"]


# Substrings that, when found in a reviewer log, indicate the worker
# engine could not complete the review because its account / session
# quota was exhausted. The orchestrator treats this as a signal to
# switch engines (Codex -> Claude) rather than as a "real" review
# outcome. Match is case-insensitive; keep markers lowercase here.
#
# Curated from observed Codex CLI + Claude CLI outputs and the public
# OpenAI / Anthropic rate-limit error vocabulary. Add cautiously:
# false positives turn a real CHANGES REQUIRED into a wasteful engine
# swap.
QUOTA_EXHAUSTED_MARKERS = (
    "rate limit",
    "rate-limited",
    "rate_limit_exceeded",
    "usage limit",
    "usage-limit",
    "usage_limit_exceeded",
    "quota exhausted",
    "out of quota",
    "insufficient_quota",
    "429 ",
    "too many requests",
    "weekly limit",
    "five-hour limit",
    "5-hour limit",
    "limit reached",
    "you have reached your",
    "free credits exhausted",
    "credit balance is too low",
    "anthropic-ratelimit",
    "model_overloaded",
    "overloaded_error",
)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_reviewer_log.py <logfile>", file=sys.stderr)
        return 2

    log_path = Path(sys.argv[1])
    if not log_path.exists():
        print(json.dumps({"ok": False, "error": f"log file not found: {log_path}"}))
        return 1

    text = log_path.read_text(errors="replace")
    text_lower = text.lower()

    heading = None
    latest_pos = -1
    for candidate in VALID_HEADINGS:
        for match in re.finditer(rf"(?m)^{re.escape(candidate)}\s*$", text):
            if match.start() > latest_pos:
                latest_pos = match.start()
                heading = candidate

    environment_failure = any(
        marker in text
        for marker in (
            "Operation not permitted",
            "could not perform the requested review",
            "failed before execution",
            "cannot honestly approve",
            "bwrap:",
        )
    )

    quota_marker_present = any(
        marker in text_lower for marker in QUOTA_EXHAUSTED_MARKERS
    )
    # A quota signal only counts as "the reviewer couldn't run" when no
    # valid heading was emitted. If the worker reached a verdict despite
    # an upstream rate-limit hiccup, the verdict stands; we don't
    # second-guess a real review.
    reviewer_quota_exhausted = quota_marker_present and heading is None

    result = {
        "ok": True,
        "heading": heading,
        "environment_failure": environment_failure,
        "reviewer_quota_exhausted": reviewer_quota_exhausted,
        "log_file": str(log_path),
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
