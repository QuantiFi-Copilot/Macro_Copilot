#!/usr/bin/env python3
"""
test_wirp_meeting_pricing_sql_validation.py — WIRP-pricing validator
====================================================================

Independently reproduces ``calculate_wirp_meeting_pricing``'s logic
against the live TimescaleDB and compares row-for-row (no sampling
per PR16 / Codex P2 lesson from PR #186).

INGEST-primitive parity check: the four Bloomberg WIRP fields are
surfaced verbatim — the SQL baseline below pulls the same DISTINCT
ON (instrument_id, field_name) ORDER BY trade_date DESC shape the
production fetcher uses and reproduces the identity-derived
hike/cut/hold computation row-for-row.

SQL-bind syntax: ``CAST(:x AS DATE)`` per the Codex P0 lesson from
PR #186.

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
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
    "signed_move_prob_pct": 1e-2,
    "num_25bp_moves_priced": 1e-3,
    "rate_change_native": 1e-3,
    "hike_prob_pct": 1e-2,
    "cut_prob_pct": 1e-2,
    "hold_prob_pct": 1e-2,
    "next_implied_policy_rate_pct": 1e-3,
    "next_signed_move_prob_pct": 1e-2,
    "next_hike_prob_pct": 1e-2,
    "next_cut_prob_pct": 1e-2,
    "next_hold_prob_pct": 1e-2,
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
    n_meetings: int,
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

    # Trim to n_meetings (next_n mode).
    display_rows = list(rows[:n_meetings])

    def _round_or_none(v: Any, decimals: int) -> Any:
        if v is None:
            return None
        return round(float(v), decimals)

    meetings = []
    for r in display_rows:
        implied_rate = _round_or_none(r["implied_rate"], rate_round)
        signed_prob = _round_or_none(r["move_prob"], prob_round)
        num_moves = _round_or_none(r["num_moves"], num_moves_round)
        rate_change = _round_or_none(r["rate_change"], rate_round)

        if signed_prob is None:
            hike, cut, hold = None, None, None
        else:
            hike = round(max(signed_prob, 0.0), prob_round)
            cut = round(max(-signed_prob, 0.0), prob_round)
            hold = round(100.0 - abs(signed_prob), prob_round)

        meetings.append({
            "central_bank": r["central_bank"],
            "meeting_date": r["meeting_date"],
            "meeting_token": r["meeting_token"],
            "as_of_date": r["as_of_date"],
            "implied_policy_rate_pct": implied_rate,
            "signed_move_prob_pct": signed_prob,
            "num_25bp_moves_priced": num_moves,
            "rate_change_native": rate_change,
            "hike_prob_pct": hike,
            "cut_prob_pct": cut,
            "hold_prob_pct": hold,
            "vendor_ticker": r["vendor_ticker"],
            "bloomberg_ticker_implied_rate": r["bloomberg_ticker_fr"],
            "bloomberg_ticker_move_prob": r["bloomberg_ticker_pr"],
            "bloomberg_ticker_num_moves": r["bloomberg_ticker_nm"],
            "bloomberg_ticker_rate_change": r["bloomberg_ticker_ch"],
        })

    first = meetings[0]
    current_metrics = {
        "central_bank": central_bank,
        "selection_mode": "next_n_meetings",
        "n_meetings_returned": len(meetings),
        "n_meetings_requested": n_meetings,
        "requested_meeting_date": None,
        "next_meeting_date": first["meeting_date"],
        "next_implied_policy_rate_pct": first["implied_policy_rate_pct"],
        "next_signed_move_prob_pct": first["signed_move_prob_pct"],
        "next_hike_prob_pct": first["hike_prob_pct"],
        "next_cut_prob_pct": first["cut_prob_pct"],
        "next_hold_prob_pct": first["hold_prob_pct"],
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
            "next_signed_move_prob_pct",
            "next_hike_prob_pct",
            "next_cut_prob_pct",
            "next_hold_prob_pct",
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
                "signed_move_prob_pct",
                "num_25bp_moves_priced",
                "rate_change_native",
                "hike_prob_pct",
                "cut_prob_pct",
                "hold_prob_pct",
            ),
            tolerances=TOLERANCE_BY_FIELD,
            prefix=f"meetings[{index}].",
        )

    return mismatches


def run_case(engine, *, central_bank: str, n_meetings: int) -> Tuple[str, List[str]]:
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

    from datetime import date as _date_cls, timedelta
    today = _date_cls.today()
    earliest = today.isoformat()
    latest = (today + timedelta(days=1500)).isoformat()

    sql_result = sql_baseline(
        engine=engine,
        central_bank=central_bank,
        earliest_meeting_date_iso=earliest,
        latest_meeting_date_iso=latest,
        n_meetings=n_meetings,
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
            "(ADR 0009).  Row-for-row parity per PR16."
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
    print("-" * 80)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running per-central-bank tool vs SQL comparisons (row-for-row)...")
    pass_count = 0
    skip_count = 0
    failed_cases: List[Tuple[str, List[str]]] = []
    for index, cb in enumerate(REGRESSION_CASES, start=1):
        print_case_header(index, len(REGRESSION_CASES), cb)
        status, mismatches = run_case(
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
            failed_cases.append((cb, mismatches))

    print("[3/3] Summary")
    print("-" * 80)
    print(f"  total_cases : {len(REGRESSION_CASES)}")
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
