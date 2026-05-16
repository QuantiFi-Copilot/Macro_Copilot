#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


VALID_HEADINGS = ["APPROVED", "CHANGES REQUIRED", "DEFER / DO NOT BUILD"]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_codex_reviewer_log.py <logfile>", file=sys.stderr)
        return 2

    log_path = Path(sys.argv[1])
    if not log_path.exists():
        print(json.dumps({"ok": False, "error": f"log file not found: {log_path}"}))
        return 1

    text = log_path.read_text(errors="replace")

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

    result = {
        "ok": True,
        "heading": heading,
        "environment_failure": environment_failure,
        "log_file": str(log_path),
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
