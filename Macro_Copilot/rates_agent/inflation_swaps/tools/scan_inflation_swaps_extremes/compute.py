"""
compute.py — Config-driven ZCIS universe-wide extremes scan (V1)
==========================================================================

Universe-wide ZCIS quoted-rate sweep — ranks every
``(curve_family, tenor)`` ZCIS pillar in the inflation_swaps
universe by absolute 252-day z-score of its quoted-rate LEVEL.
First universe-scan primitive in the ``inflation_swaps`` domain
(catalog id ``inflation_swaps__scan_inflation_swaps_extremes``,
build_order 25).

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field AND every
result row carries the SAME disclosure (mirrors the linker / bond_futures
scanner precedent).  The disclosure includes:

  - the universe-wide ZCIS rate-LEVEL label,
  - the explicit z-score lookback window (read from YAML at runtime;
    not hardcoded — mirrors the linker / bond_futures scanner
    pattern),
  - the INDEX-FAMILY MISMATCH caveat (CPI-U / HICP / RPI underlying-
    index heterogeneity across USD_ZCIS / EUR_ZCIS / GBP_ZCIS),
  - the MARKET-STRUCTURE MISMATCH caveat (index-lag differences:
    USD/EUR 3M lag, GBP 2M lag; daily vs monthly interpolation
    conventions; dealer-quote liquidity asymmetry),
  - the "morning screen, NOT a tactical trade signal" scope
    statement.

DB access — read-only
---------------------
Two helpers from ``shared.analytics.rates_fetch``:

  - ``fetch_scan_universe(instrument_type='inflation_swap',
    field_name='PX_MID', ...)`` returns the long-format
    market-data rows.  Same canonical fetcher the sovereign / OIS /
    linker scanners use; ``instrument_type='inflation_swap'`` is
    the hard guard that the scan reads inflation-swap rows, NOT
    nominal sovereign / linker / OIS rows.
  - ``fetch_scan_universe_reference(instrument_type=
    'inflation_swap', curve_families=...)`` returns one row per
    ``(curve_family, tenor, contract_code)`` with the per-row
    reference columns (``maturity_date``, ``country``,
    ``vendor_ticker``, ``underlying_index``) that
    ``fetch_scan_universe`` does NOT surface.  The helper's
    projection was EXTENDED in this PR with ``underlying_index``
    (already exposed natively by
    ``v_market_data_daily_enriched``); the linker scanner's call
    site is unaffected because it only reads the columns it
    needs.

Test seam
---------
``fetch_scan_universe``, ``fetch_scan_universe_reference``, and
``date`` are imported here at module level; tests patch them via
``patch("rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes.compute.X")``.

Determinism + as-of anchor
--------------------------
The scan is anchored to ``params.as_of_date`` when explicitly
supplied, and to the most-recent shared trading day across the
fetched universe (max trade_date across per-stem cleaned series)
when omitted — same pattern as the linker / bond_futures scanner
and the upstream ``inflation_swap_rate_level`` primitive.  The
fetch window is methodology — derived from YAML conventions
(``z_score_window_days * z_score_buffer_multiplier``, currently
252 * 1.5 = 378 calendar days).  Per-stem series are CAPPED at the
resolved as-of date before z-scoring so a stray future-dated row
cannot contaminate the trailing rolling stats.

Per-stem ranking + scoring
--------------------------
1. Fetch market-data rows for the universe scope via
   ``fetch_scan_universe``.
2. Fetch per-(curve_family, tenor, contract_code) reference rows
   via ``fetch_scan_universe_reference``.
3. Group market data by (curve_family, tenor).
4. For each (curve_family, tenor) stem:
   a. Clean the per-stem series (ffill 5 days; drop duplicates;
      sort).
   b. Cap at the resolved as_of_date.
   c. Compute the rolling 252d z-score on the cleaned series.
   d. Snapshot values (latest cleaned row): zcis_rate_pct (latest
      rate, rounded to ``yield_round_decimals``),
      daily_change_zcis_rate_bps (1-day Δ × 100),
      monthly_change_zcis_rate_bps (22-day Δ × 100).
   e. Skip stems with fewer than ``z_score_min_periods`` cleaned
      observations — never silently demote to z=None (the rank
      ordering would be undefined).
5. Filter by |z| >= ``min_abs_z_score`` (input).
6. Sort by |z| desc, ties broken by ``curve_family`` asc then
   ``tenor`` asc (deterministic).
7. Take the top ``top_n``.
8. Attach per-row reference columns
   (maturity_date / underlying_index / vendor_ticker) from the
   reference fetch keyed on (curve_family, tenor).  Missing
   reference rows leave the per-row reference columns as None
   (defensive).

Frozen-window guard
-------------------
PR14: the z-score lookback window is wire-frozen at 252 days for
V1 (the methodology disclosure mentions "252 trading days"
verbatim).  A future window rename is caught at import time via
the ``_validate_window_252d`` guard below — same pattern the
linker scanner uses for its 252d z-score window.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes.schemas import (
    ScanInflationSwapsExtremesInput,
    ScanInflationSwapsExtremesOutput,
    ScanInflationSwapsExtremesResultRow,
)
from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import (
    fetch_scan_universe,
    fetch_scan_universe_reference,
    latest_trade_date,
)
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# instrument_type filter passed to fetch_scan_universe.  Code-level
# invariant — this primitive owns the ZCIS-universe concept and
# must NEVER silently fall through to nominal sovereign / linker /
# OIS rows under a ZCIS-rate label.  Putting this in YAML would let
# a YAML edit silently switch the scan over to a different
# instrument type; per DESIGN_PRINCIPLES.md §5, structural identity
# stays in code.
_INFLATION_SWAP_INSTRUMENT_TYPE: str = "inflation_swap"


# Wire-frozen z-score lookback window for V1.  The methodology
# disclosure template mentions "252 trading days" verbatim; a future
# rename of the YAML's ``z_score_window_days`` to a different value
# would mean the disclosure lies about the actual window.  The guard
# fires at compute time so a YAML edit gets a loud
# ``NotImplementedError`` rather than producing a methodology
# disclosure labelled "252 trading days" against a different window.
# Mirrors the ``_validate_window_252d`` guard in the linker scanner.
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
# injected at runtime from the YAML (mirrors the linker /
# bond_futures scanner's pattern) — the guard above ensures the
# YAML value is the V1-frozen 252 so the verbatim "252 trading
# days" text is honest.
_METHODOLOGY_DISCLOSURE_TEMPLATE: str = (
    "Universe-wide ZCIS quoted-rate LEVEL scan. Z-score lookback "
    "= {z_window} trading days. This is a ZCIS (zero-coupon "
    "inflation swap) QUOTED-RATE level read, NOT a breakeven "
    "scan, NOT a linker real-yield scan, NOT a curve-shape scan, "
    "NOT a forward-inflation scan. INDEX-FAMILY MISMATCH "
    "(load-bearing): the three ZCIS markets reference DIFFERENT "
    "underlying inflation indices — USD_ZCIS references US CPI-U "
    "non-seasonally adjusted (CPURNSA Index), EUR_ZCIS references "
    "Eurozone HICP ex-tobacco (CPTFEMU Index), GBP_ZCIS "
    "references UK RPI (UKRPI Index). The ranking spans this "
    "heterogeneity; each output row's underlying_index field "
    "names the exact reference index. MARKET-STRUCTURE MISMATCH "
    "(load-bearing): ZCIS conventions differ across currencies "
    "in index-publication-lag (USD_ZCIS 3M lag with daily "
    "interpolation, EUR_ZCIS 3M lag with monthly interpolation, "
    "GBP_ZCIS 2M lag with monthly interpolation), dealer-quote "
    "liquidity, and structural basis vs the bond-implied "
    "breakeven curve; an extreme may reflect liquidity / "
    "structural moves rather than pure inflation-expectations "
    "divergence. This is a morning screen, NOT a tactical trade "
    "signal — use the sibling inflation_swap_curve_spread / "
    "inflation_swap_forward / inflation_swap_butterfly / "
    "cross_market_inflation_swap_spread / "
    "swap_breakeven_basis_simple primitives for further "
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
    """Resolve the LLM's optional ``curve_families`` input against
    the YAML's ``inflation_swap_curve_families`` whitelist.

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
    zcis_rates: pd.Series,
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
    cleaned ZCIS quoted-rate series.

    Returns a dict carrying the latest cleaned snapshot values + the
    per-stem level z-score, or None if the stem has fewer than
    ``z_min_periods`` cleaned observations (in which case the stem
    is honestly skipped rather than promoted to z=None).
    """
    if len(zcis_rates) < z_min_periods:
        return None

    as_of_ts = zcis_rates.index[-1]
    as_of_str = as_of_ts.date().strftime("%Y-%m-%d")
    current_rate_raw = zcis_rates.iloc[-1]
    current_rate = safe_float(
        current_rate_raw, decimals=yield_round_decimals,
    )

    # 1-day change in BPS (×100; matches the per-pillar
    # inflation_swap_rate_level primitive's daily_change_bps).
    daily_change_bps: Optional[float]
    if len(zcis_rates) >= daily_change_offset_rows:
        cur_raw = zcis_rates.iloc[-1]
        prev_raw = zcis_rates.iloc[-daily_change_offset_rows]
        if pd.isna(cur_raw) or pd.isna(prev_raw):
            daily_change_bps = None
        else:
            daily_change_bps = round(
                (float(cur_raw) - float(prev_raw)) * 100.0,
                bps_change_round_decimals,
            )
    else:
        daily_change_bps = None

    # 22-day change in BPS (~1 month; matches the per-pillar
    # inflation_swap_rate_level primitive's monthly_change_bps).
    monthly_change_bps: Optional[float]
    if len(zcis_rates) >= monthly_change_offset_rows:
        cur_raw = zcis_rates.iloc[-1]
        prev_raw = zcis_rates.iloc[-monthly_change_offset_rows]
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
        zcis_rates,
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
        "zcis_rate_pct": current_rate,
        "daily_change_zcis_rate_bps": daily_change_bps,
        "monthly_change_zcis_rate_bps": monthly_change_bps,
        "z_score_zcis_rate": current_z,
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_scan_inflation_swaps_extremes(
    engine: Engine,
    params: ScanInflationSwapsExtremesInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Scan the ZCIS universe and return the top-N extremes by
    absolute 252-day ZCIS-rate z-score.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : ScanInflationSwapsExtremesInput
        Validated input.  ``curve_families=None`` means scan the
        full universe per the YAML's
        ``inflation_swap_curve_families`` whitelist.
    config : ToolConfig, optional
        Bundled ``config.yaml`` auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``ScanInflationSwapsExtremesOutput``, or
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
    field_name = config.convention_value("default_zcis_rate_field")
    daily_offset = config.convention_value("daily_change_offset_rows")
    monthly_offset = config.convention_value("monthly_change_offset_rows")
    yield_round = config.convention_value("yield_round_decimals")
    z_round = config.convention_value("z_score_round_decimals")
    bps_round = config.convention_value("bps_change_round_decimals")
    whitelist_csv = config.convention_value(
        "inflation_swap_curve_families",
    )

    # PR14 wire-freeze guard.
    _validate_window_252d(z_window)

    # Resolve None-sentinel display thresholds against the YAML
    # defaults (PR9 / PR10 — the default lives in config.yaml, not
    # in the schema's Field default).
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
    # When no explicit as_of_date is supplied, anchor the fetch window to
    # the ZCIS universe's latest available trade_date (not date.today())
    # so a stale snapshot still yields a full z-score window of data;
    # falls back to today only when the universe has no rows.  Filters
    # mirror the fetch_scan_universe read (instrument_type + field_name)
    # so the probe surfaces the same latest date the scan will see.  An
    # explicit as_of_date still anchors there (the future-anchor guard
    # below rejects anchors beyond the universe's last observed date).
    fetch_anchor: date = (
        requested_as_of
        if requested_as_of is not None
        else (
            latest_trade_date(
                engine,
                field_name=field_name,
                instrument_type=_INFLATION_SWAP_INSTRUMENT_TYPE,
            )
            or date.today()
        )
    )
    start_date = fetch_anchor - timedelta(
        days=fetch_window_days + int(ffill_limit)
    )

    # ------------------------------------------------------------------
    # 2. Fetch market data — instrument_type='inflation_swap' is the
    #    load-bearing guarantee that the scan cannot silently sweep
    #    linker / nominal-sovereign / OIS rows under a ZCIS label.
    # ------------------------------------------------------------------
    raw_df = fetch_scan_universe(
        engine=engine,
        instrument_type=_INFLATION_SWAP_INSTRUMENT_TYPE,
        field_name=field_name,
        start_date=start_date,
        curve_families=curve_families,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No ZCIS rate data found for "
                f"field='{field_name}' "
                f"(instrument_type='{_INFLATION_SWAP_INSTRUMENT_TYPE}') "
                f"since {start_date.isoformat()} across "
                f"curve_families={curve_families}.  Verify the "
                "ZCIS universe is ingested (see "
                "rates_agent/playbooks/inflation_swaps.yml)."
            )
        }

    # ------------------------------------------------------------------
    # 2a. Future-anchor guard — when the LLM supplies an explicit
    #     ``as_of_date`` that lies beyond the ZCIS universe's last
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
                f"ZCIS universe's last observed trade_date="
                f"{universe_max_trade_date.isoformat()}.  The "
                "scan refuses to silently re-label an unbounded "
                "ranking as a future-anchored read."
            )
        }

    # ------------------------------------------------------------------
    # 3. Fetch per-row reference columns (maturity_date /
    #    underlying_index / vendor_ticker).  Scoped to the SAME
    #    curve_families as the market-data fetch so the join is
    #    tight.
    # ------------------------------------------------------------------
    ref_df = fetch_scan_universe_reference(
        engine=engine,
        instrument_type=_INFLATION_SWAP_INSTRUMENT_TYPE,
        curve_families=curve_families,
    )

    # Index reference rows by (curve_family, tenor) for O(1) lookup
    # per-stem.  ZCIS have NULL contract_code (the playbook does
    # not set one — they are cash inflation swaps, not roll
    # contracts) so (curve_family, tenor) is the unique key for the
    # ZCIS universe; the helper still selects contract_code so it's
    # universal across other instrument_types, but we collapse on
    # (curve_family, tenor) here.
    ref_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not ref_df.empty:
        for _, r in ref_df.iterrows():
            key = (str(r["curve_family"]), str(r["tenor"]))
            # If a future schema change produces multiple rows for
            # a single (curve_family, tenor), keep the FIRST
            # observed — deterministic, and the test layer pins the
            # expected mapping.
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
                "underlying_index": (
                    str(r["underlying_index"])
                    if pd.notna(r["underlying_index"])
                    else None
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
                "ZCIS universe scan found 0 (curve_family, tenor) "
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
        # row cannot pollute the rolling stats.  When
        # ``requested_as_of`` is None, no cap is applied (the
        # per-stem anchor falls out of the data's last row).
        if requested_as_of is not None:
            cap_ts = pd.Timestamp(requested_as_of)
            series = series.loc[series.index <= cap_ts]
        if len(series) == 0:
            continue

        scored = _compute_stem_metrics(
            zcis_rates=series,
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

        # Attach reference columns (None when reference row is
        # missing — defensive; for the ZCIS universe on the current
        # DB snapshot every (curve_family, tenor) has a reference
        # row).
        ref = ref_lookup.get((str(cf), str(tenor)), {})
        scored["curve_family"] = str(cf)
        scored["tenor"] = str(tenor)
        scored["maturity_date"] = ref.get("maturity_date")
        scored["underlying_index"] = ref.get("underlying_index")
        scored["vendor_ticker"] = ref.get("vendor_ticker")
        per_stem_metrics.append(scored)

    if not per_stem_metrics:
        as_of_clause = (
            f" with as_of_date={requested_as_of.isoformat()}"
            if requested_as_of is not None else ""
        )
        return {
            "error": (
                f"Scanned {universe_count} ZCIS stems but none had "
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
        z = stem_metrics["z_score_zcis_rate"]
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

    output_rows: List[ScanInflationSwapsExtremesResultRow] = []
    for rank_idx, (z, stem_metrics) in enumerate(top_rows, start=1):
        output_rows.append(
            ScanInflationSwapsExtremesResultRow(
                rank=rank_idx,
                curve_family=stem_metrics["curve_family"],
                tenor=stem_metrics["tenor"],
                as_of_date=stem_metrics["as_of_date"],
                zcis_rate_pct=stem_metrics["zcis_rate_pct"],
                daily_change_zcis_rate_bps=(
                    stem_metrics["daily_change_zcis_rate_bps"]
                ),
                monthly_change_zcis_rate_bps=(
                    stem_metrics["monthly_change_zcis_rate_bps"]
                ),
                z_score_zcis_rate=round(float(z), z_round),
                signal="EXTREME_HIGH" if z > 0 else "EXTREME_LOW",
                maturity_date=stem_metrics["maturity_date"],
                underlying_index=stem_metrics["underlying_index"],
                vendor_ticker=stem_metrics["vendor_ticker"],
                methodology_disclosure=methodology_disclosure,
            )
        )

    if not output_rows:
        return {
            "error": (
                f"Scanned {universe_count} ZCIS stems "
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
        f"Scanned {universe_count} ZCIS stems "
        f"({len(per_stem_metrics)} scoreable). "
        f"Stems with |z| >= {effective_min_abs_z}: {passing_count}. "
        f"Showing top {len(output_rows)} by absolute ZCIS-rate "
        f"z-score. {as_of_clause}."
    )

    output = ScanInflationSwapsExtremesOutput(
        scan_summary=scan_summary,
        results=output_rows,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()
