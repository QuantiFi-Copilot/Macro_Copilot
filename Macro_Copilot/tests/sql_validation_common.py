#!/usr/bin/env python3
"""
sql_validation_common.py — Shared helpers for SQL-vs-tool validators
====================================================================

These helpers keep the sovereign validator scripts small and consistent:

- deterministic case sampling
- tolerance-aware numeric comparison
- standardised mismatch formatting
- tenor sorting / spread-label formatting

The actual SQL baselines stay inside each per-tool script so every
validator remains easy to audit in isolation.
"""

from __future__ import annotations

import random
from typing import Any, Iterable, List, Mapping, Sequence, TypeVar


T = TypeVar("T")

TENOR_UNIT_TO_YEARS = {
    "D": 1.0 / 365.0,
    "W": 1.0 / 52.0,
    "M": 1.0 / 12.0,
    "Y": 1.0,
}


def tenor_sort_key(tenor: str) -> float:
    """Convert tenor strings like ``2Y`` / ``3M`` to sortable year fractions."""
    unit = tenor[-1]
    magnitude = int(tenor[:-1])
    return magnitude * TENOR_UNIT_TO_YEARS[unit]


def format_spread_label(short_tenor: str, long_tenor: str) -> str:
    """Independent reproduction of the tool label convention."""
    if short_tenor.endswith("Y") and long_tenor.endswith("Y"):
        return f"{short_tenor.replace('Y', '')}s{long_tenor.replace('Y', '')}s"
    return f"{short_tenor}/{long_tenor}"


def sample_cases(
    pool: Sequence[T],
    *,
    fixed_cases: Iterable[T],
    case_count: int,
    seed: int,
) -> List[T]:
    """
    Deterministically sample cases while preserving any fixed regressions
    that are present in the live metadata pool.
    """
    pool_list = list(pool)
    fixed = [case for case in fixed_cases if case in pool_list]
    remaining = [case for case in pool_list if case not in fixed]

    if len(fixed) >= case_count:
        return fixed[:case_count]

    rng = random.Random(seed)
    needed = case_count - len(fixed)
    sampled = rng.sample(remaining, k=min(needed, len(remaining)))
    return fixed + sampled


def floats_match(
    actual: Any,
    expected: Any,
    field_name: str,
    tolerances: Mapping[str, float],
) -> bool:
    """Compare nullable numerics within a field-specific tolerance."""
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    tolerance = tolerances.get(field_name, 1e-9)
    return abs(float(actual) - float(expected)) <= (tolerance + 1e-12)


def numeric_delta(actual: Any, expected: Any) -> float | None:
    """Return the absolute float delta, or None if either side is nullish."""
    if actual is None or expected is None:
        return None
    return abs(float(actual) - float(expected))


def add_exact_field_mismatches(
    *,
    mismatches: List[str],
    tool_payload: Mapping[str, Any],
    sql_payload: Mapping[str, Any],
    fields: Iterable[str],
    prefix: str,
) -> None:
    """Append exact-value mismatches into the provided list."""
    for field in fields:
        if tool_payload.get(field) != sql_payload.get(field):
            mismatches.append(
                f"{prefix}{field}: tool={tool_payload.get(field)!r} "
                f"sql={sql_payload.get(field)!r}"
            )


def add_numeric_field_mismatches(
    *,
    mismatches: List[str],
    tool_payload: Mapping[str, Any],
    sql_payload: Mapping[str, Any],
    fields: Iterable[str],
    tolerances: Mapping[str, float],
    prefix: str,
) -> None:
    """Append tolerance-aware numeric mismatches into the provided list."""
    for field in fields:
        tool_value = tool_payload.get(field)
        sql_value = sql_payload.get(field)
        if floats_match(tool_value, sql_value, field, tolerances):
            continue

        message = (
            f"{prefix}{field}: tool={tool_value!r} "
            f"sql={sql_value!r}"
        )
        delta = numeric_delta(tool_value, sql_value)
        if delta is not None:
            message += f" (delta={delta:.6f})"
        mismatches.append(message)


def compare_time_series(
    *,
    tool_rows: Sequence[Mapping[str, Any]],
    sql_rows: Sequence[Mapping[str, Any]],
    exact_fields: Iterable[str],
    numeric_fields: Iterable[str],
    tolerances: Mapping[str, float],
    row_label: str = "time_series",
    date_field: str = "date",
) -> List[str]:
    """Compare ordered time-series rows with exact + tolerant numeric checks."""
    mismatches: List[str] = []

    if len(tool_rows) != len(sql_rows):
        mismatches.append(
            f"{row_label} length: tool={len(tool_rows)} sql={len(sql_rows)}"
        )
        return mismatches

    for index, (tool_row, sql_row) in enumerate(zip(tool_rows, sql_rows), start=1):
        tool_date = tool_row.get(date_field)
        sql_date = sql_row.get(date_field)
        if tool_date != sql_date:
            mismatches.append(
                f"{row_label}[{index}].{date_field}: tool={tool_date!r} sql={sql_date!r}"
            )
            continue

        for field in exact_fields:
            if tool_row.get(field) != sql_row.get(field):
                mismatches.append(
                    f"{row_label}[{index}].{field} @ {tool_date}: "
                    f"tool={tool_row.get(field)!r} sql={sql_row.get(field)!r}"
                )
                break
        else:
            for field in numeric_fields:
                tool_value = tool_row.get(field)
                sql_value = sql_row.get(field)
                if floats_match(tool_value, sql_value, field, tolerances):
                    continue

                message = (
                    f"{row_label}[{index}].{field} @ {tool_date}: "
                    f"tool={tool_value!r} sql={sql_value!r}"
                )
                delta = numeric_delta(tool_value, sql_value)
                if delta is not None:
                    message += f" (delta={delta:.6f})"
                mismatches.append(message)
                break

    return mismatches


def print_case_header(index: int, total: int, label: str) -> None:
    """Standardised per-case header."""
    print("-" * 80)
    print(f"[{index}/{total}] {label}")


def print_selected_cases(cases: Sequence[T], formatter) -> None:
    """Print the sampled case list with one bullet per case."""
    print(f"Selected {len(cases)} cases:")
    for case in cases:
        print(f"  - {formatter(case)}")
