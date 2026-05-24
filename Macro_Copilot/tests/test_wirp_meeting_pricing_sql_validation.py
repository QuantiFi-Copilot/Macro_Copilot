#!/usr/bin/env python3
"""
test_wirp_meeting_pricing_sql_validation.py — WIRP-pricing validator
====================================================================

Independently reproduces ``calculate_wirp_meeting_pricing``'s logic
against the live TimescaleDB and compares row-for-row (no sampling
per PR16 / Codex P2 lesson from PR #186).

INGEST-primitive parity check (P12 Bloomberg Accuracy Boundary): the
four Bloomberg WIRP fields are surfaced VERBATIM — the SQL baseline
below pulls the same DISTINCT ON (instrument_id, field_name) ORDER BY
trade_date DESC shape the production fetcher uses, applies the same
rounding, and emits the same identity-field set.  No derived
hike/cut/hold computation: ``WIRP_MOVE_PROB`` is a cumulative quantity
(can exceed ±100 when multiple 25bp moves are priced — observed range
-360.1..548.0 per wirp.yml Stage-B) and any single-event probability
identity would be empirically wrong (BOE 2025-05-08 ships
-104.9% verbatim).  See Codex P0 finding on PR #190.

SQL-bind syntax: ``CAST(:x AS DATE)`` per the Codex P0 lesson from
PR #186.

Both ``next_n_meetings`` and ``specific_meeting_date`` selection
modes are exercised — added in the PR #190 Codex P2 fix.

Read-only:
  - Only ``SELECT`` queries.
  - Excluded from pytest collection via ``tests/conftest.py``.

Usage:

    DB_HOST=localhost DB_PORT=5433 DB_USER=quantuser DB_PASSWORD=...
    python tests/test_wirp_meeting_pricing_sql_validation.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as _date_cls, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.ois.tools.wirp_meeting_pricing import (  # noqa: E402
    CONFIG_PATH,
    WirpMeetingPricingInput,
    calculate_wirp_meeting_pricing,
)
from shared.config import load_tool_config  # noqa: E402
from tests.sql_validation_common import (  # noqa: E402
    add_exact_field_mismatches,
    add_numeric_field_mismatches,
    print_case_header,
)


DEFAULT_N_MEETINGS = 6

REGRESSION_CASES: List[str] = ["FOMC", "ECB", "BOE", "BOJ"]

TOLERANCE_BY_FIELD: Dict[str, float] = {
    "implied_policy_rate_pct": 1e-3,
    "cumulative_move_prob_pct": 1e-2,
    "num_25bp_moves_priced": 1e-3,
    "rate_change_native": 1e-3,
    "next_implied_policy_rate_pct": 1e-3,
    "next_cumulative_move_prob_pct": 1e-2,
    "next_num_25bp_moves_priced": 1e-3,
    "next_rate_change_native": 1e-3,
}


# ---------------------------------------------------------------------------
# SQL baseline — reproduces every step of the Python compute.
# ---------------------------------------------------------------------------

_SQL_BASELINE = text(
    """
    WITH latest_per_field AS (
        SELECT DISTINCT ON (md.instrument_id, md.field_name)
            i.instrument_id,
            i.vendor_ticker,
            i.maturity_date AS meeting_date,
            i.attributes->>'central_bank'        AS central_bank,
            i.attributes->>'wirp_meeting_token'  AS meeting_token,
            i.attributes->>'wirp_ticker_fr'      AS bloomberg_ticker_fr,
            i.attributes->>'wirp_ticker_pr'      AS bloomberg_ticker_pr,
            i.attributes->>'wirp_ticker_nm'      AS bloomberg_ticker_nm,
            i.attributes->>'wirp_ticker_ch'      AS bloomberg_ticker_ch,
            md.field_name,
            md.field_value::float AS field_value,
            md.trade_date         AS as_of_date
        FROM macro_data.instrument_master i
        JOIN macro_data.market_data_daily md
          ON md.instrument_id = i.instrument_id
        WHERE i.instrument_type = 'wirp_meeting'
          AND i.attributes->>'central_bank' = :central_bank
          AND md.field_name IN (
              'WIRP_IMPLIED_RATE', 'WIRP_MOVE_PROB',
              'WIRP_NUM_MOVES', 'WIRP_RATE_CHANGE'
          )
          AND i.maturity_date >= CAST(:earliest_meeting_date AS DATE)
          AND i.maturity_date <= CAST(:latest_meeting_date AS DATE)
        ORDER BY md.instrument_id, md.field_name, md.trade_date DESC
    )
    SELECT
        instrument_id,
        vendor_ticker,
        TO_CHAR(meeting_date, 'YYYY-MM-DD') AS meeting_date,
        central_bank,
        meeting_token,
        bloomberg_ticker_fr,
        bloomberg_ticker_pr,
        bloomberg_ticker_nm,
        bloomberg_ticker_ch,
        MAX(CASE WHEN field_name = 'WIRP_IMPLIED_RATE' THEN field_value END)
            AS implied_rate,
        MAX(CASE WHEN field_name = 'WIRP_MOVE_PROB' THEN field_value END)
            AS move_prob,
        MAX(CASE WHEN field_name = 'WIRP_NUM_MOVES' THEN field_value END)
            AS num_moves,
        MAX(CASE WHEN field_name = 'WIRP_RATE_CHANGE' THEN field_value END)
            AS rate_change,
        TO_CHAR(MAX(as_of_date), 'YYYY-MM-DD') AS as_of_date
    FROM latest_per_field
    GROUP BY instrument_id, vendor_ticker, meeting_date, central_bank,
             meeting_token, bloomberg_ticker_fr, bloomberg_ticker_pr,
             bloomberg_ticker_nm, bloomberg_ticker_ch
    ORDER BY meeting_date ASC
    """
)


def sql_baseline(
    engine,
    *,
    central_bank: str,
    earliest_meeting_date_iso: str,
    latest_meeting_date_iso: str,
    selection_mode: str,
    n_meetings: Optional[int],
    requested_meeting_date_iso: Optional[str],
    rate_round: int,
    prob_round: int,
    num_moves_round: int,
) -> Dict[str, Any]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                _SQL_BASELINE,
                {
                    "central_bank": central_bank,
                    "earliest_meeting_date": earliest_meeting_date_iso,
                    "latest_meeting_date": latest_meeting_date_iso,
                },
            )
            .mappings()
            .all()
        )

    if not rows:
        return {"current_metrics": None, "meetings": []}

    if selection_mode == "specific_meeting_date":
        assert requested_meeting_date_iso is not None
        display_rows = [
            r for r in rows if r["meeting_date"] == requested_meeting_date_iso
        ]
        n_meetings_requested = None
    else:
        assert n_meetings is not None
        display_rows = list(rows[:n_meetings])
        n_meetings_requested = n_meetings

    def _round_or_none(v: Any, decimals: int) -> Any:
        if v is None:
            return None
        return round(float(v), decimals)

    meetings = []
    for r in display_rows:
        implied_rate = _round_or_none(r["implied_rate"], rate_round)
        cumulative_prob = _round_or_none(r["move_prob"], prob_round)
        num_moves = _round_or_none(r["num_moves"], num_moves_round)
        rate_change = _round_or_none(r["rate_change"], rate_round)

        meetings.append({
            "central_bank": r["central_bank"],
            "meeting_date": r["meeting_date"],
            "meeting_token": r["meeting_token"],
            "as_of_date": r["as_of_date"],
            "implied_policy_rate_pct": implied_rate,
            "cumulative_move_prob_pct": cumulative_prob,
            "num_25bp_moves_priced": num_moves,
            "rate_change_native": rate_change,
            "vendor_ticker": r["vendor_ticker"],
            "bloomberg_ticker_implied_rate": r["bloomberg_ticker_fr"],
            "bloomberg_ticker_move_prob": r["bloomberg_ticker_pr"],
            "bloomberg_ticker_num_moves": r["bloomberg_ticker_nm"],
            "bloomberg_ticker_rate_change": r["bloomberg_ticker_ch"],
        })

    if not meetings:
        return {"current_metrics": None, "meetings": []}

    first = meetings[0]
    current_metrics = {
        "central_bank": central_bank,
        "selection_mode": selection_mode,
        "n_meetings_returned": len(meetings),
        "n_meetings_requested": n_meetings_requested,
        "requested_meeting_date": requested_meeting_date_iso,
        "next_meeting_date": first["meeting_date"],
        "next_implied_policy_rate_pct": first["implied_policy_rate_pct"],
        "next_cumulative_move_prob_pct": first["cumulative_move_prob_pct"],
        "next_num_25bp_moves_priced": first["num_25bp_moves_priced"],
        "next_rate_change_native": first["rate_change_native"],
        "next_as_of_date": first["as_of_date"],
    }
    return {"current_metrics": current_metrics, "meetings": meetings}


def compare_results(tool_result: Dict[str, Any], sql_result: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []

    if "error" in tool_result:
        mismatches.append(f"Tool returned error: {tool_result['error']}")
        return mismatches

    tool_cm = tool_result["current_metrics"]
    sql_cm = sql_result["current_metrics"]

    if sql_cm is None:
        mismatches.append("SQL baseline empty but tool returned current_metrics")
        return mismatches

    add_exact_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_cm,
        sql_payload=sql_cm,
        fields=(
            "central_bank",
            "selection_mode",
            "n_meetings_returned",
            "n_meetings_requested",
            "requested_meeting_date",
            "next_meeting_date",
            "next_as_of_date",
        ),
        prefix="current_metrics.",
    )
    add_numeric_field_mismatches(
        mismatches=mismatches,
        tool_payload=tool_cm,
        sql_payload=sql_cm,
        fields=(
            "next_implied_policy_rate_pct",
            "next_cumulative_move_prob_pct",
            "next_num_25bp_moves_priced",
            "next_rate_change_native",
        ),
        tolerances=TOLERANCE_BY_FIELD,
        prefix="current_metrics.",
    )

    # Row-for-row meetings list comparison — NO sampling (PR16).
    meetings_tool = tool_result["meetings"]
    meetings_sql = sql_result["meetings"]
    if len(meetings_tool) != len(meetings_sql):
        mismatches.append(
            f"meetings length: tool={len(meetings_tool)} sql={len(meetings_sql)}"
        )
        return mismatches

    for index, (tool_m, sql_m) in enumerate(zip(meetings_tool, meetings_sql), start=1):
        add_exact_field_mismatches(
            mismatches=mismatches,
            tool_payload=tool_m,
            sql_payload=sql_m,
            fields=(
                "central_bank",
                "meeting_date",
                "meeting_token",
                "as_of_date",
                "vendor_ticker",
                "bloomberg_ticker_implied_rate",
                "bloomberg_ticker_move_prob",
                "bloomberg_ticker_num_moves",
                "bloomberg_ticker_rate_change",
            ),
            prefix=f"meetings[{index}].",
        )
        add_numeric_field_mismatches(
            mismatches=mismatches,
            tool_payload=tool_m,
            sql_payload=sql_m,
            fields=(
                "implied_policy_rate_pct",
                "cumulative_move_prob_pct",
                "num_25bp_moves_priced",
                "rate_change_native",
            ),
            tolerances=TOLERANCE_BY_FIELD,
            prefix=f"meetings[{index}].",
        )

    return mismatches


def _earliest_latest_iso_next_n(
    cfg, *, today: _date_cls
) -> Tuple[str, str]:
    """Mirror compute.py's forward window for next_n_meetings mode —
    earliest = today, latest = today + forward_horizon_days.  The
    ``past_horizon_days`` YAML convention is not used here (compute.py
    does not consult it for next_n; it bounds the future
    'any-meeting' mode)."""
    forward_horizon_days = int(cfg.convention_value("forward_horizon_days"))
    earliest = today.isoformat()
    latest = (today + timedelta(days=forward_horizon_days)).isoformat()
    return earliest, latest


def run_case_next_n(
    engine, *, central_bank: str, n_meetings: int
) -> Tuple[str, List[str]]:
    cfg = load_tool_config(CONFIG_PATH)
    rate_round = int(cfg.convention_value("rate_round_decimals"))
    prob_round = int(cfg.convention_value("prob_round_decimals"))
    num_moves_round = int(cfg.convention_value("num_moves_round_decimals"))

    tool_result = calculate_wirp_meeting_pricing(
        engine=engine,
        params=WirpMeetingPricingInput(
            central_bank=central_bank,
            selection_mode="next_n_meetings",
            n_meetings=n_meetings,
        ),
    )

    today = _date_cls.today()
    earliest, latest = _earliest_latest_iso_next_n(cfg, today=today)

    sql_result = sql_baseline(
        engine=engine,
        central_bank=central_bank,
        earliest_meeting_date_iso=earliest,
        latest_meeting_date_iso=latest,
        selection_mode="next_n_meetings",
        n_meetings=n_meetings,
        requested_meeting_date_iso=None,
        rate_round=rate_round,
        prob_round=prob_round,
        num_moves_round=num_moves_round,
    )

    if (
        "error" in tool_result
        and sql_result["current_metrics"] is None
    ):
        return "SKIP", []

    mismatches = compare_results(tool_result, sql_result)
    return ("PASS" if not mismatches else "FAIL"), mismatches


def run_case_specific_meeting(
    engine, *, central_bank: str
) -> Tuple[str, List[str]]:
    """Exercise selection_mode='specific_meeting_date' against the first
    upcoming meeting for the central bank, sourced from the SQL baseline
    itself so the test stays deterministic across DB refreshes."""
    cfg = load_tool_config(CONFIG_PATH)
    rate_round = int(cfg.convention_value("rate_round_decimals"))
    prob_round = int(cfg.convention_value("prob_round_decimals"))
    num_moves_round = int(cfg.convention_value("num_moves_round_decimals"))

    today = _date_cls.today()
    earliest, latest = _earliest_latest_iso_next_n(cfg, today=today)

    # Discover the first upcoming meeting via the SQL baseline (read-only).
    discovery = sql_baseline(
        engine=engine,
        central_bank=central_bank,
        earliest_meeting_date_iso=earliest,
        latest_meeting_date_iso=latest,
        selection_mode="next_n_meetings",
        n_meetings=1,
        requested_meeting_date_iso=None,
        rate_round=rate_round,
        prob_round=prob_round,
        num_moves_round=num_moves_round,
    )
    if discovery["current_metrics"] is None:
        return "SKIP", []

    requested_iso = discovery["meetings"][0]["meeting_date"]

    tool_result = calculate_wirp_meeting_pricing(
        engine=engine,
        params=WirpMeetingPricingInput(
            central_bank=central_bank,
            selection_mode="specific_meeting_date",
            meeting_date=requested_iso,
        ),
    )

    # specific_meeting_date mode collapses the window to the single
    # requested date — mirror compute.py exactly.
    sql_result = sql_baseline(
        engine=engine,
        central_bank=central_bank,
        earliest_meeting_date_iso=requested_iso,
        latest_meeting_date_iso=requested_iso,
        selection_mode="specific_meeting_date",
        n_meetings=None,
        requested_meeting_date_iso=requested_iso,
        rate_round=rate_round,
        prob_round=prob_round,
        num_moves_round=num_moves_round,
    )

    if (
        "error" in tool_result
        and sql_result["current_metrics"] is None
    ):
        return "SKIP", []

    mismatches = compare_results(tool_result, sql_result)
    return ("PASS" if not mismatches else "FAIL"), mismatches


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate calculate_wirp_meeting_pricing against direct "
            "SQL on macro_data.instrument_master + market_data_daily "
            "(ADR 0009).  Row-for-row parity per PR16. Exercises BOTH "
            "next_n_meetings and specific_meeting_date selection modes."
        )
    )
    parser.add_argument("--n-meetings", type=int, default=DEFAULT_N_MEETINGS)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Allow exit 0 when no WIRP meetings are ingested (pre-"
            "D-wirp-deployment per ADR 0009)."
        ),
    )
    args = parser.parse_args()

    print("=" * 80)
    print("CALCULATE_WIRP_MEETING_PRICING TOOL — SQL VALIDATION (row-for-row)")
    print("=" * 80)
    print(f"  n_meetings : {args.n_meetings}")
    print(f"  modes      : next_n_meetings + specific_meeting_date")
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running per-central-bank tool vs SQL comparisons (row-for-row)...")
    pass_count = 0
    skip_count = 0
    failed_cases: List[Tuple[str, List[str]]] = []

    total_cases = len(REGRESSION_CASES) * 2  # next_n + specific
    case_index = 0
    for cb in REGRESSION_CASES:
        # --- next_n_meetings mode ---
        case_index += 1
        print_case_header(case_index, total_cases, f"{cb} [next_n_meetings]")
        status, mismatches = run_case_next_n(
            engine, central_bank=cb, n_meetings=args.n_meetings,
        )
        if status == "PASS":
            print("PASS")
            pass_count += 1
        elif status == "SKIP":
            print("SKIP (no WIRP meetings ingested)")
            skip_count += 1
        else:
            print("FAIL")
            for mismatch in mismatches[:10]:
                print(f"  - {mismatch}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed_cases.append((f"{cb}[next_n]", mismatches))

        # --- specific_meeting_date mode ---
        case_index += 1
        print_case_header(case_index, total_cases, f"{cb} [specific_meeting_date]")
        status, mismatches = run_case_specific_meeting(engine, central_bank=cb)
        if status == "PASS":
            print("PASS")
            pass_count += 1
        elif status == "SKIP":
            print("SKIP (no WIRP meetings ingested)")
            skip_count += 1
        else:
            print("FAIL")
            for mismatch in mismatches[:10]:
                print(f"  - {mismatch}")
            if len(mismatches) > 10:
                print(f"  - ... plus {len(mismatches) - 10} more mismatches")
            failed_cases.append((f"{cb}[specific]", mismatches))

    print("[3/3] Summary")
    print("-" * 80)
    print(f"  total_cases : {total_cases}")
    print(f"  passed      : {pass_count}")
    print(f"  skipped     : {skip_count}")
    print(f"  failed      : {len(failed_cases)}")

    if failed_cases:
        sys.exit(1)
    if pass_count == 0 and skip_count > 0:
        if args.allow_empty:
            sys.exit(0)
        sys.exit(2)


if __name__ == "__main__":
    main()
