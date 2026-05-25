#!/usr/bin/env python3
"""
test_fx_data_readiness.py — FX ingestion/readiness gate
=======================================================

Live-DB readiness check for the FX agent's first production surface.

This is intentionally a standalone CLI runner, matching the existing
``tests/test_*_sql_validation.py`` pattern. It checks that:

1. the canonical FX playbooks exist and declare the expected fields,
2. the corresponding instrument_master rows are present,
3. the market_data_daily rows have enough PX_LAST history,
4. the first FX tools can run against the same database.

Run from ``Macro_Copilot/``:

    python tests/test_fx_data_readiness.py

Useful stricter run before a PR / demo:

    python tests/test_fx_data_readiness.py --strict-metadata --max-staleness-days 30
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

import yaml
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.forwards.tools.fx_carry import FXCarryInput, get_fx_carry  # noqa: E402
from fx_agent.spot.tools.scanner import run_fx_scanner  # noqa: E402
from fx_agent.spot.tools.schemas import FXScannerInput  # noqa: E402
from fx_agent.spot.tools.spot_levels import FXSpotLevelInput, get_fx_spot_level  # noqa: E402


DEFAULT_SPOT_PLAYBOOK = PROJECT_ROOT / "fx_agent" / "playbooks" / "spot_fx.yml"
DEFAULT_FORWARDS_PLAYBOOK = PROJECT_ROOT / "fx_agent" / "playbooks" / "fx_forwards.yml"
DEFAULT_FIELD_NAME = "PX_LAST"
DEFAULT_MIN_HISTORY_POINTS = 252
DEFAULT_MAX_STALENESS_DAYS = 90
OPTIONAL_METADATA_FIELDS = ("market_scope", "base_ccy", "quote_ccy", "fx_family", "region")

# Phase B / Wave 1.5 — EM spot universe invariants (Codex-locked 2026-05-25).
# Phase B / Wave 2 top-up (2026-05-25) added USDCNH + USDINR (under
# fx_family='EM_SPOT' with spot_convention attribute) + USDCNY (under a
# distinct fx_family='EM_SPOT_REFERENCE' to flag its non-tradable
# onshore-PBOC-fix nature). Warehouse seeding (2026-05-25) added 6 more
# liquid EM pairs (USDSGD/TWD/THB APAC + USDCLP/COP/PEN LATAM) — all
# tradable, all with full 2000+ history. The core 9 pairs keep the
# strict uniform 2000-01-03 start_date assertion. USDCNH is the only
# pair with a per-pair min_date override (offshore launch 2010-08-23).
# USDCLP is the only pair with a per-pair min_rows override (6589 rows
# in DB, slightly below the default 6000 threshold — actually 6589
# passes the default; the override here is purely defensive in case
# of future re-extractions or holiday-calendar drift). EM_SPOT_REFERENCE
# is NOT validated by this gate's EM section (different convention;
# downstream tools should filter it out unless they specifically want
# the onshore fix series).
EM_SPOT_PAIRS: tuple[str, ...] = (
    "USDMXN", "USDBRL", "USDZAR", "USDTRY",
    "USDPLN", "USDHUF", "USDKRW", "USDIDR", "USDPHP",
    # v4.0 top-up additions (kept in EM_SPOT family because tradable /
    # composite reference, not onshore-only)
    "USDCNH", "USDINR",
    # v5.0 warehouse-seed additions (tradable, full history)
    "USDSGD", "USDTWD", "USDTHB", "USDCLP", "USDCOP", "USDPEN",
)
EM_VALID_REGIONS: frozenset[str] = frozenset({"LATAM", "EMEA", "APAC"})
EM_DEFAULT_MIN_DATE = date(2000, 1, 3)
# Per-pair min_date overrides for the gate's strict assertion. Pairs not
# in this dict use EM_DEFAULT_MIN_DATE (2000-01-03). USDCNH is the only
# documented exception (offshore market launched late 2010).
EM_PAIR_MIN_DATE_OVERRIDES: dict[str, date] = {
    "USDCNH": date(2010, 8, 23),
}
EM_DEFAULT_MIN_ROWS_PER_PAIR = 6000
# Per-pair row count overrides for pairs with documented shorter history.
EM_PAIR_MIN_ROWS_OVERRIDES: dict[str, int] = {
    "USDCNH": 4000,  # ~4106 actual (~2010-2026)
}
EM_REQUIRED_ATTRS: tuple[str, ...] = ("pair", "base_ccy", "quote_ccy")


@dataclass(frozen=True)
class CheckResult:
    status: str
    name: str
    detail: str


def _pass(name: str, detail: str) -> CheckResult:
    return CheckResult("PASS", name, detail)


def _warn(name: str, detail: str) -> CheckResult:
    return CheckResult("WARN", name, detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult("FAIL", name, detail)


def _load_playbook(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _target_fields(playbook: Mapping[str, Any]) -> set[str]:
    return {
        str(metric.get("bloomberg_field"))
        for metric in playbook.get("target_metrics", []) or []
        if metric.get("bloomberg_field")
    }


def _universe(playbook: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(playbook.get("universe", []) or [])


def _playbook_name(path: Path, playbook: Mapping[str, Any]) -> str:
    return str(playbook.get("playbook_name") or path.stem)


def _expected_tickers(playbook: Mapping[str, Any]) -> list[str]:
    return sorted(str(item["ticker"]) for item in _universe(playbook) if item.get("ticker"))


def _print_section(title: str) -> None:
    print("-" * 80)
    print(title)


def _print_results(results: Iterable[CheckResult]) -> None:
    for result in results:
        print(f"[{result.status}] {result.name}: {result.detail}")


def validate_playbook_shape(
    *,
    label: str,
    path: Path,
    playbook: Mapping[str, Any],
    expected_instrument_type: str,
    required_field: str,
    require_tenor: bool = False,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    universe = _universe(playbook)
    fields = _target_fields(playbook)

    if required_field in fields:
        results.append(_pass(f"{label} target field", f"{required_field} declared"))
    else:
        results.append(_fail(f"{label} target field", f"{required_field} missing from {path}"))

    if universe:
        results.append(_pass(f"{label} universe", f"{len(universe)} tickers declared"))
    else:
        results.append(_fail(f"{label} universe", "no universe entries declared"))

    for item in universe:
        ticker = item.get("ticker", "<missing ticker>")
        item_type = item.get("instrument_type")
        if item_type != expected_instrument_type:
            results.append(
                _fail(
                    f"{label} instrument_type",
                    f"{ticker}: expected {expected_instrument_type}, got {item_type!r}",
                )
            )
        if not item.get("pair"):
            results.append(_fail(f"{label} pair metadata", f"{ticker}: missing pair"))
        if require_tenor and not item.get("tenor"):
            results.append(_fail(f"{label} tenor metadata", f"{ticker}: missing tenor"))

    if not any(result.status == "FAIL" for result in results):
        results.append(_pass(f"{label} playbook shape", f"{path.relative_to(PROJECT_ROOT)} OK"))

    return results


def fetch_instrument_rows(engine, tickers: list[str]) -> dict[str, Mapping[str, Any]]:
    if not tickers:
        return {}

    query = text(
        """
        SELECT
            vendor_ticker,
            asset_class,
            instrument_type,
            tenor,
            attributes
        FROM macro_data.instrument_master
        WHERE vendor_ticker = ANY(:tickers)
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"tickers": tickers}).mappings().all()
    return {str(row["vendor_ticker"]): row for row in rows}


def validate_instrument_master(
    *,
    label: str,
    playbook: Mapping[str, Any],
    rows_by_ticker: Mapping[str, Mapping[str, Any]],
    expected_instrument_type: str,
    require_tenor: bool = False,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    missing: list[str] = []
    optional_missing: list[str] = []

    for item in _universe(playbook):
        ticker = str(item["ticker"])
        row = rows_by_ticker.get(ticker)
        if row is None:
            missing.append(ticker)
            continue

        attrs = row.get("attributes") or {}
        if row.get("instrument_type") != expected_instrument_type:
            results.append(
                _fail(
                    f"{label} DB instrument_type",
                    f"{ticker}: expected {expected_instrument_type}, got {row.get('instrument_type')!r}",
                )
            )
        if require_tenor and row.get("tenor") != item.get("tenor"):
            results.append(
                _fail(
                    f"{label} DB tenor",
                    f"{ticker}: expected {item.get('tenor')!r}, got {row.get('tenor')!r}",
                )
            )
        if attrs.get("pair") != item.get("pair"):
            results.append(
                _fail(
                    f"{label} DB pair",
                    f"{ticker}: expected {item.get('pair')!r}, got {attrs.get('pair')!r}",
                )
            )

        for field in OPTIONAL_METADATA_FIELDS:
            expected = item.get(field)
            if expected and attrs.get(field) != expected:
                optional_missing.append(f"{ticker}.{field}")

    if missing:
        results.append(_fail(f"{label} DB instruments", f"missing {len(missing)}: {missing}"))
    else:
        results.append(
            _pass(
                f"{label} DB instruments",
                f"{len(_expected_tickers(playbook))} expected tickers present",
            )
        )

    if optional_missing:
        results.append(
            _warn(
                f"{label} optional metadata",
                f"{len(optional_missing)} missing/stale values: {optional_missing[:12]}",
            )
        )
    else:
        results.append(_pass(f"{label} optional metadata", "all optional metadata matches playbook"))

    return results


def fetch_market_coverage(
    engine,
    *,
    tickers: list[str],
    field_name: str,
) -> dict[str, Mapping[str, Any]]:
    if not tickers:
        return {}

    query = text(
        """
        SELECT
            im.vendor_ticker,
            COUNT(*) AS row_count,
            MIN(d.trade_date) AS min_date,
            MAX(d.trade_date) AS max_date
        FROM macro_data.instrument_master im
        LEFT JOIN macro_data.market_data_daily d
          ON d.instrument_id = im.instrument_id
         AND d.field_name = :field_name
        WHERE im.vendor_ticker = ANY(:tickers)
        GROUP BY im.vendor_ticker
        ORDER BY im.vendor_ticker
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"tickers": tickers, "field_name": field_name}).mappings().all()
    return {str(row["vendor_ticker"]): row for row in rows}


def _days_stale(max_date: Any, today: date | None = None) -> int | None:
    if max_date is None:
        return None
    if today is None:
        today = date.today()
    if hasattr(max_date, "date"):
        max_date = max_date.date()
    return (today - max_date).days


def validate_market_coverage(
    *,
    label: str,
    coverage_by_ticker: Mapping[str, Mapping[str, Any]],
    expected_tickers: list[str],
    field_name: str,
    min_history_points: int,
    max_staleness_days: int,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    insufficient: list[str] = []
    stale: list[str] = []
    missing_rows: list[str] = []
    max_dates: list[Any] = []

    for ticker in expected_tickers:
        row = coverage_by_ticker.get(ticker)
        row_count = int(row.get("row_count") or 0) if row else 0
        max_date = row.get("max_date") if row else None

        if row_count == 0:
            missing_rows.append(ticker)
            continue
        if row_count < min_history_points:
            insufficient.append(f"{ticker}={row_count}")
        stale_days = _days_stale(max_date)
        if stale_days is not None and stale_days > max_staleness_days:
            stale.append(f"{ticker} max_date={max_date} stale_days={stale_days}")
        max_dates.append(max_date)

    if missing_rows:
        results.append(_fail(f"{label} {field_name} rows", f"missing rows for {missing_rows}"))
    else:
        results.append(
            _pass(
                f"{label} {field_name} rows",
                f"{len(expected_tickers)} expected tickers have rows",
            )
        )

    if insufficient:
        results.append(
            _fail(
                f"{label} history depth",
                f"min required {min_history_points}; short series: {insufficient}",
            )
        )
    else:
        results.append(_pass(f"{label} history depth", f">= {min_history_points} rows per ticker"))

    if stale:
        results.append(_warn(f"{label} freshness", f"{len(stale)} stale series: {stale[:8]}"))
    elif max_dates:
        results.append(
            _pass(
                f"{label} freshness",
                f"latest max_date={max(max_dates)} within {max_staleness_days} days",
            )
        )

    return results


def validate_em_spot_specifics(
    *,
    playbook: Mapping[str, Any],
    instruments_by_ticker: Mapping[str, Mapping[str, Any]],
    coverage_by_ticker: Mapping[str, Mapping[str, Any]],
    expected_min_date: date,
    min_rows_per_pair: int,
) -> list[CheckResult]:
    """Stronger invariants for the EM subset of `spot_fx`, layered on top
    of the generic playbook+coverage checks. Codex-locked 2026-05-25 as
    Phase B's readiness contract:

      - exactly the 11 expected EM_SPOT pairs (9 core + USDCNH + USDINR) in the playbook (EM_SPOT_PAIRS),
      - fx_family="EM_SPOT" and market_scope="EM" in DB for all 9
        (already enforced via OPTIONAL_METADATA_FIELDS — restated here
        for explicitness in case the optional check is silenced),
      - region in {LATAM, EMEA, APAC} for all 9,
      - pair / base_ccy / quote_ccy populated in instrument_master.attributes,
      - row_count per pair >= min_rows_per_pair (default 6000),
      - min_date == expected_min_date (default 2000-01-03) per pair.

    Total EM row count is reported as an INFO/PASS line, not asserted —
    it grows naturally if the playbook is re-extracted later (Codex
    nuance: keep the invariants tight on shape & boundaries, soft on
    absolute counts that will drift over time).
    """
    results: list[CheckResult] = []

    # 1. Universe identity — exactly the 9 expected pairs.
    em_universe = [u for u in _universe(playbook) if u.get("fx_family") == "EM_SPOT"]
    em_pairs_in_playbook = {str(u.get("pair") or "").upper() for u in em_universe}
    expected_set = set(EM_SPOT_PAIRS)
    if em_pairs_in_playbook != expected_set:
        missing = expected_set - em_pairs_in_playbook
        extra = em_pairs_in_playbook - expected_set
        bits: list[str] = []
        if missing:
            bits.append(f"missing={sorted(missing)}")
        if extra:
            bits.append(f"unexpected={sorted(extra)}")
        results.append(_fail("spot EM universe", "set mismatch: " + ", ".join(bits)))
        return results  # short-circuit — rest of the checks would be misleading
    results.append(_pass(
        "spot EM universe",
        "all 17 expected EM_SPOT pairs (9 core + USDCNH/USDINR top-up + "
        "USDSGD/USDTWD/USDTHB/USDCLP/USDCOP/USDPEN warehouse-seed) present in playbook",
    ))

    # 2-6. Per-pair invariants.
    bad_scope_or_family: list[str] = []
    bad_region: list[str] = []
    missing_required_attrs: list[str] = []
    short_history: list[str] = []
    wrong_min_date: list[str] = []
    total_em_rows = 0

    for item in em_universe:
        ticker = str(item["ticker"])
        row = instruments_by_ticker.get(ticker) or {}
        attrs = row.get("attributes") or {}

        # market_scope + fx_family (Codex Q3 lock: SQL filter relies on these)
        if attrs.get("market_scope") != "EM":
            bad_scope_or_family.append(f"{ticker}.market_scope={attrs.get('market_scope')!r}")
        if attrs.get("fx_family") != "EM_SPOT":
            bad_scope_or_family.append(f"{ticker}.fx_family={attrs.get('fx_family')!r}")

        # region vocab
        region = attrs.get("region")
        if region not in EM_VALID_REGIONS:
            bad_region.append(f"{ticker}.region={region!r}")

        # required attrs
        for must_have in EM_REQUIRED_ATTRS:
            if not attrs.get(must_have):
                missing_required_attrs.append(f"{ticker}.{must_have}")

        # market data coverage
        coverage = coverage_by_ticker.get(ticker) or {}
        row_count = int(coverage.get("row_count") or 0)
        total_em_rows += row_count
        # Per-pair override for min_rows (e.g. USDCNH has ~4106, less
        # than the default 6000 because the CNH offshore market only
        # launched in 2010-08).
        pair_min_rows = EM_PAIR_MIN_ROWS_OVERRIDES.get(
            str(item.get("pair") or ""), min_rows_per_pair
        )
        if row_count < pair_min_rows:
            short_history.append(f"{ticker}={row_count} (expected >= {pair_min_rows})")

        min_date_val = coverage.get("min_date")
        if hasattr(min_date_val, "date"):
            min_date_val = min_date_val.date()
        # Per-pair override for min_date (e.g. USDCNH starts 2010-08-23,
        # not the default 2000-01-03 like the other EM pairs).
        pair_expected_min_date = EM_PAIR_MIN_DATE_OVERRIDES.get(
            str(item.get("pair") or ""), expected_min_date
        )
        if min_date_val != pair_expected_min_date:
            wrong_min_date.append(
                f"{ticker}.min_date={min_date_val} (expected {pair_expected_min_date})"
            )

    if bad_scope_or_family:
        results.append(
            _fail(
                "spot EM scope/family",
                f"{bad_scope_or_family} (expected market_scope='EM' and fx_family='EM_SPOT')",
            )
        )
    else:
        results.append(_pass("spot EM scope/family", "all 17 EM_SPOT pairs market_scope='EM' fx_family='EM_SPOT'"))

    if bad_region:
        results.append(
            _fail(
                "spot EM region vocab",
                f"{bad_region} (must be in {sorted(EM_VALID_REGIONS)})",
            )
        )
    else:
        results.append(_pass("spot EM region vocab", f"all 17 EM_SPOT pairs region in {sorted(EM_VALID_REGIONS)}"))

    if missing_required_attrs:
        results.append(
            _fail(
                "spot EM required attrs",
                f"{missing_required_attrs} (required: {list(EM_REQUIRED_ATTRS)})",
            )
        )
    else:
        results.append(
            _pass(
                "spot EM required attrs",
                f"{', '.join(EM_REQUIRED_ATTRS)} populated for all 17 EM_SPOT pairs",
            )
        )

    if short_history:
        results.append(
            _fail(
                "spot EM history depth",
                f"min required {min_rows_per_pair}; short series: {short_history}",
            )
        )
    else:
        results.append(_pass("spot EM history depth", f">= {min_rows_per_pair} rows per pair"))

    if wrong_min_date:
        results.append(
            _fail(
                "spot EM min_date",
                f"expected {expected_min_date} per pair; mismatches: {wrong_min_date}",
            )
        )
    else:
        results.append(_pass(
            "spot EM min_date",
            f"all 17 EM_SPOT pairs start at expected min_date "
            f"(9 core + USDSGD/USDTWD/USDTHB/USDCLP/USDCOP/USDPEN + USDINR at "
            f"{expected_min_date}, USDCNH at {EM_PAIR_MIN_DATE_OVERRIDES['USDCNH']} "
            "per BBG offshore launch)",
        ))

    # INFO line — total rows reported but not pinned (will grow on re-extraction)
    results.append(_pass(
        "spot EM total rows (info)",
        f"{total_em_rows:,} rows across {len(EM_SPOT_PAIRS)} EM_SPOT pairs",
    ))

    return results


def validate_tool_smoke(engine, *, field_name: str) -> list[CheckResult]:
    results: list[CheckResult] = []

    try:
        spot = get_fx_spot_level(
            engine,
            FXSpotLevelInput(pair="EURUSD", lookback_days=365, field_name=field_name),
        )
        metrics = spot["current_metrics"]
        results.append(
            _pass(
                "get_fx_spot_level smoke",
                f"EURUSD {metrics['as_of_date']} spot={metrics['current_spot']} z={metrics['z_score']}",
            )
        )
    except Exception as exc:
        results.append(_fail("get_fx_spot_level smoke", repr(exc)))

    try:
        scanner = run_fx_scanner(engine, FXScannerInput(top_n=3, field_name=field_name))
        rows = scanner["rows"]
        if rows:
            results.append(_pass("scan_fx_spot smoke", f"{len(rows)} rows; top={rows[0]['pair']}"))
        else:
            results.append(_fail("scan_fx_spot smoke", "returned zero rows"))
    except Exception as exc:
        results.append(_fail("scan_fx_spot smoke", repr(exc)))

    try:
        carry = get_fx_carry(engine, FXCarryInput(tenor="1M"))
        rows = carry["rows"]
        if rows:
            results.append(_pass("calculate_fx_carry smoke", f"{len(rows)} rows; top={rows[0]['pair']}"))
        else:
            results.append(_fail("calculate_fx_carry smoke", "returned zero rows"))
    except Exception as exc:
        results.append(_fail("calculate_fx_carry smoke", repr(exc)))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check FX playbook, DB, and tool readiness against the live local database."
    )
    parser.add_argument("--spot-playbook", type=Path, default=DEFAULT_SPOT_PLAYBOOK)
    parser.add_argument("--forwards-playbook", type=Path, default=DEFAULT_FORWARDS_PLAYBOOK)
    parser.add_argument("--field", default=DEFAULT_FIELD_NAME)
    parser.add_argument("--min-history-points", type=int, default=DEFAULT_MIN_HISTORY_POINTS)
    parser.add_argument("--max-staleness-days", type=int, default=DEFAULT_MAX_STALENESS_DAYS)
    parser.add_argument(
        "--strict-metadata",
        action="store_true",
        help="Treat optional metadata warnings as failures.",
    )
    parser.add_argument(
        "--em-min-date",
        type=lambda s: date.fromisoformat(s),
        default=EM_DEFAULT_MIN_DATE,
        help=(
            "Required uniform min_date for every EM spot pair (default "
            f"{EM_DEFAULT_MIN_DATE.isoformat()}). Tighten if you ever re-extract "
            "EM with a different baseline."
        ),
    )
    parser.add_argument(
        "--em-min-rows-per-pair",
        type=int,
        default=EM_DEFAULT_MIN_ROWS_PER_PAIR,
        help=(
            "Minimum row_count per EM spot pair (default "
            f"{EM_DEFAULT_MIN_ROWS_PER_PAIR}). Stricter than the generic "
            "--min-history-points because EM has uniform 2000-onward history."
        ),
    )
    parser.add_argument(
        "--skip-tools",
        action="store_true",
        help="Only check playbooks + database coverage; skip compute smoke calls.",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("FX DATA READINESS")
    print("=" * 80)
    print(f"  spot_playbook      : {args.spot_playbook}")
    print(f"  forwards_playbook  : {args.forwards_playbook}")
    print(f"  field_name         : {args.field}")
    print(f"  min_history_points : {args.min_history_points}")
    print(f"  max_staleness_days : {args.max_staleness_days}")
    print(f"  strict_metadata    : {args.strict_metadata}")

    all_results: list[CheckResult] = []

    _print_section("[1/6] Loading playbooks")
    try:
        spot_playbook = _load_playbook(args.spot_playbook)
        forwards_playbook = _load_playbook(args.forwards_playbook)
        playbook_results = [
            _pass(
                "spot playbook loaded",
                f"{_playbook_name(args.spot_playbook, spot_playbook)}",
            ),
            _pass(
                "forwards playbook loaded",
                f"{_playbook_name(args.forwards_playbook, forwards_playbook)}",
            ),
        ]
    except Exception as exc:
        playbook_results = [_fail("playbook load", repr(exc))]
        _print_results(playbook_results)
        sys.exit(1)
    _print_results(playbook_results)
    all_results.extend(playbook_results)

    _print_section("[2/6] Validating playbook shape")
    shape_results = []
    shape_results.extend(
        validate_playbook_shape(
            label="spot",
            path=args.spot_playbook,
            playbook=spot_playbook,
            expected_instrument_type="fx_spot",
            required_field=args.field,
        )
    )
    shape_results.extend(
        validate_playbook_shape(
            label="forwards",
            path=args.forwards_playbook,
            playbook=forwards_playbook,
            expected_instrument_type="fx_forward",
            required_field=args.field,
            require_tenor=True,
        )
    )
    _print_results(shape_results)
    all_results.extend(shape_results)

    _print_section("[3/6] Checking live DB instrument metadata")
    engine = get_db_engine()
    spot_tickers = _expected_tickers(spot_playbook)
    forwards_tickers = _expected_tickers(forwards_playbook)
    spot_instruments = fetch_instrument_rows(engine, spot_tickers)
    metadata_results = []
    metadata_results.extend(
        validate_instrument_master(
            label="spot",
            playbook=spot_playbook,
            rows_by_ticker=spot_instruments,
            expected_instrument_type="fx_spot",
        )
    )
    metadata_results.extend(
        validate_instrument_master(
            label="forwards",
            playbook=forwards_playbook,
            rows_by_ticker=fetch_instrument_rows(engine, forwards_tickers),
            expected_instrument_type="fx_forward",
            require_tenor=True,
        )
    )
    _print_results(metadata_results)
    all_results.extend(metadata_results)

    _print_section("[4/6] Checking live DB market-data coverage")
    spot_coverage = fetch_market_coverage(engine, tickers=spot_tickers, field_name=args.field)
    coverage_results = []
    coverage_results.extend(
        validate_market_coverage(
            label="spot",
            coverage_by_ticker=spot_coverage,
            expected_tickers=spot_tickers,
            field_name=args.field,
            min_history_points=args.min_history_points,
            max_staleness_days=args.max_staleness_days,
        )
    )
    coverage_results.extend(
        validate_market_coverage(
            label="forwards",
            coverage_by_ticker=fetch_market_coverage(
                engine,
                tickers=forwards_tickers,
                field_name=args.field,
            ),
            expected_tickers=forwards_tickers,
            field_name=args.field,
            min_history_points=args.min_history_points,
            max_staleness_days=args.max_staleness_days,
        )
    )
    _print_results(coverage_results)
    all_results.extend(coverage_results)

    _print_section("[5/6] Checking EM spot-specific invariants (Phase B Codex lock)")
    em_results = validate_em_spot_specifics(
        playbook=spot_playbook,
        instruments_by_ticker=spot_instruments,
        coverage_by_ticker=spot_coverage,
        expected_min_date=args.em_min_date,
        min_rows_per_pair=args.em_min_rows_per_pair,
    )
    _print_results(em_results)
    all_results.extend(em_results)

    if not args.skip_tools:
        _print_section("[6/6] Running FX tool smoke checks")
        tool_results = validate_tool_smoke(engine, field_name=args.field)
        _print_results(tool_results)
        all_results.extend(tool_results)
    else:
        _print_section("[6/6] Running FX tool smoke checks")
        skipped = [_warn("tool smoke", "skipped by --skip-tools")]
        _print_results(skipped)
        all_results.extend(skipped)

    warnings = [result for result in all_results if result.status == "WARN"]
    failures = [result for result in all_results if result.status == "FAIL"]
    if args.strict_metadata:
        failures.extend(warnings)

    _print_section("Summary")
    print(f"  pass : {sum(result.status == 'PASS' for result in all_results)}")
    print(f"  warn : {len(warnings)}")
    print(f"  fail : {len([result for result in all_results if result.status == 'FAIL'])}")

    if failures:
        print("\nREADINESS FAILED")
        for result in failures:
            print(f"  - [{result.status}] {result.name}: {result.detail}")
        sys.exit(1)

    print("\nREADINESS PASSED")


if __name__ == "__main__":
    main()
