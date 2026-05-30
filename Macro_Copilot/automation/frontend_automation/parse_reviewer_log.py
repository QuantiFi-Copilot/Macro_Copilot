#!/usr/bin/env python3
"""parse_reviewer_log.py — extract heading + quota state from the frontend
reviewer log.

The orchestrator runs the Claude reviewer (CLAUDE_REVIEWER_PROMPT.md) and
pipes the worker's stdout/stderr to a log file. This script reads that log
and returns a small JSON record the orchestrator consumes to decide what
to do next:

  - ``heading``                  — "APPROVED" / "CHANGES REQUIRED" /
                                   "DEFER / DO NOT BUILD" / null. Latest
                                   occurrence wins (workers sometimes
                                   restate the contract verbatim before
                                   emitting their own verdict).
  - ``environment_failure``      — true when the worker bailed out before
                                   ever evaluating the build (sandbox /
                                   filesystem / bwrap errors). Signals
                                   "retry, don't penalise the tool."
  - ``reviewer_quota_exhausted`` — true when the log shows a quota /
                                   rate-limit signal AND no valid
                                   heading was emitted. In the single-
                                   round Claude-only frontend factory
                                   this signals a clean
                                   ``waiting_quota`` stop (NO engine
                                   swap; there is no fallback engine —
                                   see DESIGN_PRINCIPLES.md §10 and
                                   QUOTA_AND_RESUME_POLICY.md §3).

Contrast with the primitive automation factory: that one uses a Codex
primary + Claude fallback, so ``reviewer_quota_exhausted`` there triggers
an engine swap.  Here it triggers a clean wake stop instead.

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


# Substrings that indicate the worker engine could not complete the
# review because its account / session quota was exhausted.  In the
# single-Claude factory this signals a clean waiting_quota stop (no
# engine swap exists).
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
    "you're out of",
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
