"""
compute.py — Config-driven linker universe-wide extremes scan (V1)
==========================================================================

Universe-wide linker real-yield sweep — ranks every
``(curve_family, tenor)`` linker observation in the
inflation_indexed_bonds universe by absolute 252-day z-score of its
real-yield LEVEL.  First universe-scan primitive in the
``inflation_indexed_bonds`` domain.

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field AND every
result row carries the SAME disclosure (per the catalog's
methodology guardrail wording: "Output rows must carry the per-bond
methodology disclosure tag so downstream consumers see why a bond
ranked extreme").  The disclosure includes:

  - the universe-wide linker real-yield-LEVEL label,
  - the explicit z-score lookback window (read from YAML at runtime;
    not hardcoded — mirrors the bond_futures scanner pattern),
  - the INDEX-FAMILY MISMATCH caveat (CPI-U / HICP / RPI / CAN_CPI
    heterogeneity across linker markets),
  - the MARKET-STRUCTURE MISMATCH caveat (cross-country linker-
    liquidity / issuance-size differences),
  - the "morning screen, NOT a tactical trade signal" scope
    statement.

DB access — read-only
---------------------
Two helpers from ``shared.analytics.rates_fetch``:

  - ``fetch_scan_universe(instrument_type='inflation_linker',
    field_name='YLD_YTM_MID', ...)`` returns the long-format
    market-data rows.  Same canonical fetcher the sovereign / OIS
    scanners use; ``instrument_type='inflation_linker'`` is the
    hard guard that the scan reads linker rows, not nominal
    sovereign rows.
  - ``fetch_scan_universe_reference(instrument_type=
    'inflation_linker', curve_families=...)`` returns one row per
    ``(curve_family, tenor, contract_code)`` with the per-row
    reference columns (``maturity_date``, ``country``,
    ``vendor_ticker``) that ``fetch_scan_universe`` does NOT
    surface.  NEW helper added to ``rates_fetch.py`` for this
    scanner — kept tightly-scoped per the prompt's option (c)
    discipline (the default is NOT to extend rates_fetch.py, but
    the per-row reference-metadata join honestly requires it
    because the catalog's ``required_reference_metrics`` list is
    not on the existing fetcher's projection).

Test seam
---------
``fetch_scan_universe``, ``fetch_scan_universe_reference``, and
``date`` are imported here at module level; tests patch them via
``patch("rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes.compute.X")``.

Determinism + as-of anchor
--------------------------
The scan is anchored to ``params.as_of_date`` when explicitly
supplied, and to the most-recent shared trading day across the
fetched universe (max trade_date across per-stem cleaned series)
when omitted — same pattern as the bond_futures scanner and the
upstream ``real_yield_level`` primitive.  The fetch window is
methodology — derived from YAML conventions
(``z_score_window_days * z_score_buffer_multiplier``, currently
252 * 1.5 = 378 calendar days).  Per-stem series are CAPPED at the
resolved as-of date before z-scoring so a stray future-dated row
cannot contaminate the trailing rolling stats.

Per-stem ranking + scoring
--------------------------
1. Fetch market-data rows for the universe scope via
   ``fetch_scan_universe``.
2. Fetch per-(curve_family, tenor, contract_code) reference rows via
   ``fetch_scan_universe_reference``.
3. Group market data by (curve_family, tenor).
4. For each (curve_family, tenor) stem:
   a. Clean the per-stem series (ffill 5 days; drop duplicates;
      sort).
   b. Cap at the resolved as_of_date.
   c. Compute the rolling 252d z-score on the cleaned series.
   d. Snapshot values (latest cleaned row): real_yield_pct (latest
      yield, rounded to ``yield_round_decimals``), daily_change_bps
      (1-day Δ × 100), monthly_change_bps (22-day Δ × 100).
   e. Skip stems with fewer than ``z_score_min_periods`` cleaned
      observations — never silently demote to z=None (the rank
      ordering would be undefined).
5. Filter by |z| >= ``min_abs_z_score`` (input).
6. Sort by |z| desc, ties broken by ``curve_family`` asc then
   ``tenor`` asc (deterministic).
7. Take the top ``top_n``.
8. Attach per-row reference columns
   (maturity_date / country / vendor_ticker) from the reference
   fetch keyed on (curve_family, tenor).  Missing reference rows
   leave the per-row reference columns as None (defensive).

Frozen-window guard
-------------------
PR14: the z-score lookback window is wire-frozen at 252 days for V1
(the methodology disclosure mentions "252 trading days" verbatim).
A future window rename is caught at import time via the
``_validate_window_252d`` guard below — same pattern the per-bond
``real_yield_level`` primitive uses for its 252d trailing range
window.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes.schemas import (
    ScanInflationLinkersExtremesInput,
    ScanInflationLinkersExtremesOutput,
    ScanInflationLinkersExtremesResultRow,
)
from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import (
    fetch_scan_universe,
    fetch_scan_universe_reference,
)
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# instrument_type filter passed to fetch_scan_universe.  Code-level
# invariant — this primitive owns the linker-universe concept and
# must NEVER silently fall through to nominal sovereign rows under
# a real-yield label.  Putting this in YAML would let a YAML edit
# silently switch the scan over to nominal data; per
# DESIGN_PRINCIPLES.md §5, structural identity stays in code.
_LINKER_INSTRUMENT_TYPE: str = "inflation_linker"


# Wire-frozen z-score lookback window for V1.  The methodology
# disclosure template mentions "252 trading days" verbatim; a future
# rename of the YAML's ``z_score_window_days`` to a different value
# would mean the disclosure lies about the actual window.  The guard
# fires at compute time so a YAML edit gets a loud
# ``NotImplementedError`` rather than producing a methodology
# disclosure labelled "252 trading days" against a different window.
# Mirrors the ``_FROZEN_TRAILING_WINDOW`` guard in the per-bond
# ``real_yield_level`` primitive.
_FROZEN_Z_WINDOW: int = 252


def _validate_window_252d(z_window: int) -> None:
    """Wire-freeze guard for the 252d z-score lookback window."""
    if z_window != _FROZEN_Z_WINDOW:
        raise NotImplementedError(
            f"z_score_window_days={z_window!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet "
            f"implemented.  V1 supports only {_FROZEN_Z_WINDOW} "
            f"because the methodology disclosure mentions '252 "
            f"trading days' verbatim per the catalog's methodology "
            f"guardrail.  Either restore the value to "
            f"{_FROZEN_Z_WINDOW} or implement the disclosure-"
            f"template rename to read the window from YAML so a "
            f"different window can be disclosed honestly."
        )


# P5 / catalog-guardrail disclosure template.  Emitted on every
# response AND every result row so the consumer cannot strip the
# disclosure by flattening / paginating.  The z-score lookback is
# injected at runtime from the YAML (mirrors the bond_futures
# scanner's pattern) — the guard above ensures the YAML value is
# the V1-frozen 252 so the verbatim "252 trading days" text is honest.
_METHODOLOGY_DISCLOSURE_TEMPLATE: str = (
    "Universe-wide linker real-yield LEVEL scan. Z-score lookback "
    "= {z_window} trading days. This is a REAL-YIELD level read, "
    "NOT a breakeven scan, NOT a nominal-yield scan, NOT an "
    "inflation-compensation scan. INDEX-FAMILY MISMATCH "
    "(load-bearing): different countries' linkers reference "
    "different inflation indices — USD TIPS reference CPI-U "
    "non-seasonally adjusted, EUR-area linkers reference HICP "
    "ex-tobacco, UK linkers reference RPI (legacy) or CPIH (newer "
    "issues), Canadian RRBs reference Canada CPI. The ranking "
    "spans this heterogeneity. MARKET-STRUCTURE MISMATCH "
    "(load-bearing): linker markets differ in benchmark "
    "availability at the same tenor pillar, issuance size, "
    "liquidity premium, and deflation-floor treatment; an extreme "
    "may reflect liquidity / structural moves rather than pure "
    "real-rate divergence. This is a morning screen, NOT a "
    "tactical trade signal — use the sibling per-bond / curve-"
    "shape / cross-country / breakeven primitives for further "
    "decomposition."
)


def _build_methodology_disclosure(z_window: int) -> str:
    """Compose the disclosure string with the runtime z-score window
    so consumers see the exact lookback that produced the scan
    (catalog methodology guardrail)."""
    return _METHODOLOGY_DISCLOSURE_TEMPLATE.format(z_window=z_window)


def _resolve_curve_families(
    requested: Optional[List[str]],
    whitelist_csv: str,
) -> List[str]:
    """Resolve the LLM's optional ``curve_families`` input against the
    YAML's ``inflation_linker_curve_families`` whitelist.

    When ``requested`` is None, returns the full whitelist (full
    universe scan).  When ``requested`` is a list, the input schema
    already validated it against the whitelist, so this is a no-op
    pass-through.
    """
    whitelist = [s.strip() for s in whitelist_csv.split(",") if s.strip()]
    if requested is None:
        return whitelist
    return list(requested)


# ============================================================================
# PER-STEM SCORING
# ============================================================================

def _compute_stem_metrics(
    *,
    real_yields: pd.Series,
    z_window: int,
    z_min_periods: int,
    z_ddof: int,
    z_score_round_decimals: int,
    yield_round_decimals: int,
    bps_change_round_decimals: int,
    daily_change_offset_rows: int,
    monthly_change_offset_rows: int,
) -> Optional[Dict[str, Any]]:
    """Compute the LEVEL z-score + snapshot context for one stem's
    cleaned real-yield series.

    Returns a dict carrying the latest cleaned snapshot values + the
    per-stem level z-score, or None if the stem has fewer than
    ``z_min_periods`` cleaned observations (in which case the stem
    is honestly skipped rather than promoted to z=None).
    """
    if len(real_yields) < z_min_periods:
        return None

    as_of_ts = real_yields.index[-1]
    as_of_str = as_of_ts.date().strftime("%Y-%m-%d")
    current_yield_raw = real_yields.iloc[-1]
    current_yield = safe_float(
        current_yield_raw, decimals=yield_round_decimals,
    )

    # 1-day change in BPS (×100; matches the per-bond
    # real_yield_level primitive's daily_change_bps).
    daily_change_bps: Optional[float]
    if len(real_yields) >= daily_change_offset_rows:
        cur_raw = real_yields.iloc[-1]
        prev_raw = real_yields.iloc[-daily_change_offset_rows]
        if pd.isna(cur_raw) or pd.isna(prev_raw):
            daily_change_bps = None
        else:
            daily_change_bps = round(
                (float(cur_raw) - float(prev_raw)) * 100.0,
                bps_change_round_decimals,
            )
    else:
        daily_change_bps = None

    # 22-day change in BPS (~1 month; matches the per-bond
    # real_yield_level primitive's monthly_change_bps).
    monthly_change_bps: Optional[float]
    if len(real_yields) >= monthly_change_offset_rows:
        cur_raw = real_yields.iloc[-1]
        prev_raw = real_yields.iloc[-monthly_change_offset_rows]
        if pd.isna(cur_raw) or pd.isna(prev_raw):
            monthly_change_bps = None
        else:
            monthly_change_bps = round(
                (float(cur_raw) - float(prev_raw)) * 100.0,
                bps_change_round_decimals,
            )
    else:
        monthly_change_bps = None

    # Rolling 252d z-score on the LEVEL series — the single ranking
    # metric per the catalog wording.
    z_series = rolling_zscore(
        real_yields,
        window=z_window,
        min_periods=z_min_periods,
        ddof=z_ddof,
        round_decimals=z_score_round_decimals,
    )
    current_z = safe_float(
        z_series.iloc[-1], decimals=z_score_round_decimals,
    )
    if current_z is None:
        return None

    return {
        "as_of_date": as_of_str,
        "real_yield_pct": current_yield,
        "daily_change_bps": daily_change_bps,
        "monthly_change_bps": monthly_change_bps,
        "z_score_real_yield": current_z,
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_scan_inflation_linkers_extremes(
    engine: Engine,
    params: ScanInflationLinkersExtremesInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Scan the linker universe and return the top-N extremes by
    absolute 252-day real-yield z-score.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : ScanInflationLinkersExtremesInput
        Validated input.  ``curve_families=None`` means scan the full
        universe per the YAML's ``inflation_linker_curve_families``
        whitelist.
    config : ToolConfig, optional
        Bundled ``config.yaml`` auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``ScanInflationLinkersExtremesOutput``, or
        ``{"error": "..."}`` on recoverable failure (empty universe,
        all stems filtered out, future as_of anchor, etc.).
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    z_window = config.convention_value("z_score_window_days")
    z_min_periods = config.convention_value("z_score_min_periods")
    z_ddof = config.convention_value("z_score_ddof")
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    ffill_limit = config.convention_value("ffill_limit_days")
    field_name = config.convention_value("default_field_name")
    daily_offset = config.convention_value("daily_change_offset_rows")
    monthly_offset = config.convention_value("monthly_change_offset_rows")
    yield_round = config.convention_value("yield_round_decimals")
    z_round = config.convention_value("z_score_round_decimals")
    bps_round = config.convention_value("bps_change_round_decimals")
    whitelist_csv = config.convention_value(
        "inflation_linker_curve_families",
    )

    # PR14 wire-freeze guard.
    _validate_window_252d(z_window)

    # Resolve None-sentinel display thresholds against the YAML
    # defaults (PR9 / PR10 — the default lives in config.yaml, not in
    # the schema's Field default).
    effective_top_n = (
        int(params.top_n)
        if params.top_n is not None
        else int(config.convention_value("default_top_n"))
    )
    effective_min_abs_z = (
        float(params.min_abs_z_score)
        if params.min_abs_z_score is not None
        else float(config.convention_value("default_min_abs_z_score"))
    )

    # Resolve the universe scope (LLM's input, validated against the
    # YAML whitelist at schema time).
    curve_families = _resolve_curve_families(
        params.curve_families, whitelist_csv,
    )

    # ------------------------------------------------------------------
    # 1. Date window — fetch start is derived from YAML (PR8 /
    #    OPR8: no LLM input controls the fetch window).  When the
    #    LLM supplies ``as_of_date`` the scan is anchored there;
    #    otherwise the anchor is resolved from the actual data max
    #    after fetch.  A small extra buffer
    #    (``ffill_limit_days``) is added to the calendar window so
    #    the ffill bridge has full coverage on the leading edge.
    # ------------------------------------------------------------------
    fetch_window_days = int(z_window * buffer_mult)
    requested_as_of: Optional[date] = params.as_of_date
    fetch_anchor: date = (
        requested_as_of if requested_as_of is not None else date.today()
    )
    start_date = fetch_anchor - timedelta(
        days=fetch_window_days + int(ffill_limit)
    )

    # ------------------------------------------------------------------
    # 2. Fetch market data — instrument_type='inflation_linker' is
    #    the load-bearing guarantee that the scan cannot silently
    #    sweep nominal sovereign rows under a real-yield label.
    # ------------------------------------------------------------------
    raw_df = fetch_scan_universe(
        engine=engine,
        instrument_type=_LINKER_INSTRUMENT_TYPE,
        field_name=field_name,
        start_date=start_date,
        curve_families=curve_families,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No linker real-yield data found for "
                f"field='{field_name}' "
                f"(instrument_type='{_LINKER_INSTRUMENT_TYPE}') "
                f"since {start_date.isoformat()} across "
                f"curve_families={curve_families}.  Verify the "
                "linker universe is ingested (see "
                "rates_agent/playbooks/inflation_indexed_bonds.yml)."
            )
        }

    # ------------------------------------------------------------------
    # 2a. Future-anchor guard — when the LLM supplies an explicit
    #     ``as_of_date`` that lies beyond the linker universe's last
    #     ingested ``trade_date``, the controlled-error envelope is
    #     returned rather than silently delivering an unbounded
    #     ranking under a future-anchored label (P5 / PR8 honest
    #     disclosure).  Implemented as an in-Python check against
    #     the fetched data's max trade_date — single source of
    #     truth, no separate MAX(trade_date) probe needed because
    #     the universe fetch has already happened.
    # ------------------------------------------------------------------
    raw_df = raw_df.copy()
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(
        raw_df["field_value"], errors="coerce",
    )
    raw_df = raw_df.dropna(subset=["field_value"])
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "curve_family", "tenor"], keep="last",
    )

    universe_max_trade_date = raw_df["trade_date"].max().date()
    if (
        requested_as_of is not None
        and requested_as_of > universe_max_trade_date
    ):
        return {
            "error": (
                f"no scoreable stems: as_of_date="
                f"{requested_as_of.isoformat()} is beyond the "
                f"linker universe's last observed trade_date="
                f"{universe_max_trade_date.isoformat()}.  The "
                "scan refuses to silently re-label an unbounded "
                "ranking as a future-anchored read."
            )
        }

    # ------------------------------------------------------------------
    # 3. Fetch per-row reference columns (maturity_date / country /
    #    vendor_ticker).  Scoped to the SAME curve_families as the
    #    market-data fetch so the join is tight.
    # ------------------------------------------------------------------
    ref_df = fetch_scan_universe_reference(
        engine=engine,
        instrument_type=_LINKER_INSTRUMENT_TYPE,
        curve_families=curve_families,
    )

    # Index reference rows by (curve_family, tenor) for O(1) lookup
    # per-stem.  Linkers have NULL contract_code (see the prompt's
    # verified DB shape) so (curve_family, tenor) is the unique key
    # for the linker universe; the helper still selects contract_code
    # so it's universal across other instrument_types, but we
    # collapse on (curve_family, tenor) here.
    ref_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not ref_df.empty:
        for _, r in ref_df.iterrows():
            key = (str(r["curve_family"]), str(r["tenor"]))
            # If a future schema change produces multiple rows for a
            # single (curve_family, tenor) (e.g. on-the-run rolls
            # exposing the column), keep the FIRST observed —
            # deterministic, and the test layer pins the expected
            # mapping.
            if key in ref_lookup:
                continue
            ref_lookup[key] = {
                "maturity_date": (
                    r["maturity_date"].strftime("%Y-%m-%d")
                    if pd.notna(r["maturity_date"])
                    and hasattr(r["maturity_date"], "strftime")
                    else (
                        str(r["maturity_date"])
                        if pd.notna(r["maturity_date"])
                        else None
                    )
                ),
                "country": (
                    str(r["country"]) if pd.notna(r["country"]) else None
                ),
                "vendor_ticker": (
                    str(r["vendor_ticker"])
                    if pd.notna(r["vendor_ticker"])
                    else None
                ),
            }

    # ------------------------------------------------------------------
    # 4. Per-stem materialisation + scoring.
    # ------------------------------------------------------------------
    universe_stems: set[Tuple[str, str]] = set(
        (str(cf), str(t))
        for (cf, t), _ in raw_df.groupby(
            ["curve_family", "tenor"], dropna=False,
        )
    )
    universe_count = len(universe_stems)
    if universe_count == 0:
        return {
            "error": (
                "Linker universe scan found 0 (curve_family, tenor) "
                "stems with data — check "
                f"{field_name} ingestion for "
                f"curve_families={curve_families}."
            )
        }

    per_stem_metrics: List[Dict[str, Any]] = []
    for (cf, tenor), group in raw_df.groupby(
        ["curve_family", "tenor"], dropna=False,
    ):
        # Clean per-stem.
        stem_df = group[["trade_date", "field_value"]]
        clean_df = clean_single_series(stem_df, ffill_limit=ffill_limit)
        if clean_df.empty:
            continue
        series = clean_df["field_value"]

        # Cap at the LLM-supplied as_of_date so a stray future-dated
        # row cannot pollute the rolling stats.  When ``requested_as_of``
        # is None, no cap is applied (the per-stem anchor falls out
        # of the data's last row).
        if requested_as_of is not None:
            cap_ts = pd.Timestamp(requested_as_of)
            series = series.loc[series.index <= cap_ts]
        if len(series) == 0:
            continue

        scored = _compute_stem_metrics(
            real_yields=series,
            z_window=z_window,
            z_min_periods=z_min_periods,
            z_ddof=z_ddof,
            z_score_round_decimals=z_round,
            yield_round_decimals=yield_round,
            bps_change_round_decimals=bps_round,
            daily_change_offset_rows=daily_offset,
            monthly_change_offset_rows=monthly_offset,
        )
        if scored is None:
            continue

        # Attach reference columns (None when reference row is missing
        # — defensive; for the linker universe on the current DB
        # snapshot every (curve_family, tenor) has a reference row).
        ref = ref_lookup.get((str(cf), str(tenor)), {})
        scored["curve_family"] = str(cf)
        scored["tenor"] = str(tenor)
        scored["maturity_date"] = ref.get("maturity_date")
        scored["country"] = ref.get("country")
        scored["vendor_ticker"] = ref.get("vendor_ticker")
        per_stem_metrics.append(scored)

    if not per_stem_metrics:
        as_of_clause = (
            f" with as_of_date={requested_as_of.isoformat()}"
            if requested_as_of is not None else ""
        )
        return {
            "error": (
                f"Scanned {universe_count} linker stems but none had "
                f">= {z_min_periods} aligned observations to compute "
                f"a meaningful z-score{as_of_clause}.  Verify the "
                "universe's data coverage or pass an as_of_date "
                "within the ingested range."
            )
        }

    # ------------------------------------------------------------------
    # 5. Ranking + top-N + threshold filter.
    # ------------------------------------------------------------------
    methodology_disclosure = _build_methodology_disclosure(z_window)

    # Filter by |z| threshold; preserve deterministic tie-break by
    # curve_family asc then tenor asc.
    filtered: List[Tuple[float, Dict[str, Any]]] = []
    for stem_metrics in per_stem_metrics:
        z = stem_metrics["z_score_real_yield"]
        if z is None:
            continue
        if abs(z) < effective_min_abs_z:
            continue
        filtered.append((z, stem_metrics))

    filtered.sort(
        key=lambda zm: (
            -abs(zm[0]),
            zm[1]["curve_family"],
            zm[1]["tenor"],
        )
    )
    passing_count = len(filtered)
    top_rows = filtered[:effective_top_n]

    output_rows: List[ScanInflationLinkersExtremesResultRow] = []
    for rank_idx, (z, stem_metrics) in enumerate(top_rows, start=1):
        output_rows.append(
            ScanInflationLinkersExtremesResultRow(
                rank=rank_idx,
                curve_family=stem_metrics["curve_family"],
                tenor=stem_metrics["tenor"],
                as_of_date=stem_metrics["as_of_date"],
                real_yield_pct=stem_metrics["real_yield_pct"],
                daily_change_bps=stem_metrics["daily_change_bps"],
                monthly_change_bps=stem_metrics["monthly_change_bps"],
                z_score_real_yield=round(float(z), z_round),
                signal="EXTREME_HIGH" if z > 0 else "EXTREME_LOW",
                maturity_date=stem_metrics["maturity_date"],
                country=stem_metrics["country"],
                vendor_ticker=stem_metrics["vendor_ticker"],
                methodology_disclosure=methodology_disclosure,
            )
        )

    if not output_rows:
        return {
            "error": (
                f"Scanned {universe_count} linker stems "
                f"({len(per_stem_metrics)} scoreable) but none "
                f"passed |z| >= {effective_min_abs_z}.  Try lowering "
                "min_abs_z_score (or omit it to fall through to the "
                "YAML default)."
            )
        }

    # ------------------------------------------------------------------
    # 6. scan_summary — one line summarising the scan state.
    # ------------------------------------------------------------------
    as_of_dates = sorted({m["as_of_date"] for m in per_stem_metrics})
    if len(as_of_dates) == 1:
        as_of_clause = f"as_of {as_of_dates[0]}"
    else:
        as_of_clause = (
            f"as_of dates span {as_of_dates[0]} to {as_of_dates[-1]}"
        )
    scan_summary = (
        f"Scanned {universe_count} linker stems "
        f"({len(per_stem_metrics)} scoreable). "
        f"Stems with |z| >= {effective_min_abs_z}: {passing_count}. "
        f"Showing top {len(output_rows)} by absolute real-yield "
        f"z-score. {as_of_clause}."
    )

    output = ScanInflationLinkersExtremesOutput(
        scan_summary=scan_summary,
        results=output_rows,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()
