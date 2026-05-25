"""
compute.py — Config-driven bond-futures universe-wide extremes scan (V1)
==========================================================================

Universe-wide front-month bond-futures sweep — ranks every
``(curve_family, contract_code)`` rolling-generic stem in the
bond_futures universe by absolute 252-day z-score across four metrics
(price LEVEL, 1-day price CHANGE, volume LEVEL, open-interest LEVEL).
Third primitive under the ``bond_futures`` domain (ADR 0013) — V1
monitors-only; replaces the Phase-4 inter-commodity DV01-weighted
spread stack per ADR 0013's V1 scope.

Methodology disclosure
----------------------
Every response carries a ``methodology_disclosure`` field AND every
result row carries the SAME disclosure (per the catalog's methodology
guardrail wording: "Output rows MUST include the methodology
disclosure tag so downstream consumers cannot mistake the read for a
tenor-anchored yield call"). The disclosure includes:

  - the universe-wide front-month sweep label,
  - the explicit z-score lookback window (read from YAML at runtime;
    not hardcoded — mirrors the futures_volume_oi pattern),
  - the rolling-generic-price / non-DV01-spread / non-tenor-anchored-
    yield-call caveats per ADR 0013 V1 scope.

DB access
---------
Reaches the DB through the shared
``fetch_rolling_generic_universe_series`` helper — a NEW helper added
to ``shared/analytics/rates_fetch.py`` for this primitive. The
existing per-stem ``fetch_rolling_generic_series`` would have forced
one round-trip per stem per field (19 stems × 3 fields = 57 round-
trips per scan); the universe helper does it in 3 queries total.

Why a new helper (rather than iterate): the catalog allows iteration,
but a universe scan that conceptually wants ONE consistent snapshot
across the universe is structurally better served by one query per
field. The new helper is the read-side mirror of
``fetch_rolling_generic_series``, scaled to N stems in one query —
P10 single-source-of-truth (no raw SQL in compute) and
``fetch_scan_universe``-equivalent for the rolling-generic direct-
join path.

The helper filters on ``i.tenor IS NOT NULL`` (cheapest correct
guard against accidentally including a policy-futures strip-keyed
stem if a caller's curve_families list mis-routes) and on the
``bond_futures_curve_families`` whitelist from this tool's config
(refused at schema-validation time too — belt-and-braces).

Test seam
---------
``fetch_rolling_generic_universe_series`` and ``date`` are imported
at module level; tests patch them via
``patch("rates_agent.bond_futures.tools.scan_bond_futures_extremes.compute.X")``.

Determinism + as-of anchor
--------------------------
Reviewer round-1 mandatory-fix #2: the previous build anchored the
scan to ``date.today()`` for both the fetch window and the per-stem
ranking, which made the output non-deterministic across days. Round
2 anchors the scan to ``params.as_of_date`` when explicitly supplied,
and to the most-recent shared trading day across the fetched
universe (max trade_date across per-stem aligned series) when
omitted — same "as_of = data's latest observation date, NOT
date.today()" pattern futures_price_level / futures_volume_oi use
for their per-contract as_of anchor.

The fetch window is methodology — derived from YAML conventions
(``z_score_window_days * z_score_buffer_multiplier``, currently
252 * 1.5 = 378 calendar days). Removing the previous
``lookback_days`` LLM input closed the PR8 / OPR8 input-schema-
overreach gap the reviewer flagged. Per-stem series are CAPPED at
the resolved as-of date before z-scoring so a stray future-dated
tick cannot contaminate the trailing rolling stats.

Per-stem alignment + scoring
-----------------------------
1. Group the universe-wide DataFrames by (curve_family, contract_code).
2. For each stem:
   a. Clean each of price / volume / OI series (ffill 5 days; drop
      duplicates; sort).
   b. Intersect the three series on the shared trading days.
   c. Compute, on the intersection:
      - rolling 252d z-score on the price series → ``price`` metric
      - rolling 252d z-score on the diff(price, offset=1) series →
        ``price_change`` metric (the "Δ" in the catalog wording)
      - rolling 252d z-score on the volume series → ``volume`` metric
      - rolling 252d z-score on the OI series → ``open_interest``
        metric
   d. Snapshot values (latest aligned row): current_price,
      daily_price_change (raw 1-day diff), current_volume,
      current_open_interest, delta_open_interest_1d.
   e. Stems with fewer than ``z_score_min_periods`` aligned
      observations are SKIPPED for that metric — never silently
      promoted to a z=None row (the rank ordering would be undefined).
3. For each metric independently:
   a. Filter by |z| >= ``min_abs_z_score`` (input).
   b. Sort by |z| desc, ties broken by ``contract_code`` ascending
      for deterministic ordering.
   c. Take the top ``top_n`` rows.
4. Concatenate the per-metric top-N lists into one ordered
   ``results`` list (price first, then price_change, then volume,
   then open_interest — stable for downstream consumers).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.bond_futures.tools.scan_bond_futures_extremes.schemas import (
    ScanBondFuturesExtremesInput,
    ScanBondFuturesExtremesOutput,
    ScanBondFuturesExtremesResultRow,
    ScanMetric,
)
from shared.analytics.levels import clean_single_series
from shared.analytics.rates_fetch import (
    fetch_rolling_generic_universe_max_date,
    fetch_rolling_generic_universe_series,
)
from shared.analytics.spreads import rolling_zscore, safe_float
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Ordering for the per-metric top-N concatenation. Stable so downstream
# consumers can rely on the price rows always coming first, etc.
_METRIC_ORDER: Tuple[ScanMetric, ...] = (
    "price",
    "price_change",
    "volume",
    "open_interest",
)


# P5 / ADR 0013 / catalog-guardrail disclosure template. Emitted on
# every response AND every result row so the consumer cannot strip
# the disclosure by flattening / paginating. The z-score lookback is
# injected at runtime from the YAML (mirrors futures_volume_oi).
_METHODOLOGY_DISCLOSURE_TEMPLATE: str = (
    "Universe-wide front-month bond-futures sweep. Z-score lookback "
    "= {z_window} trading days. This is rolling-generic price; the "
    "CTD-implied yield is not yet a primitive in this build, and this "
    "is NOT an inter-commodity DV01-weighted spread, NOT a basis "
    "trade, and NOT a tenor-anchored yield call. The CTD "
    "identification, gross/net basis, implied repo, and DV01-weighted "
    "inter-commodity RV stack are Phase-4 work gated on D-repo + "
    "D-deliverable data ingestion (ADR 0013 — bond_futures V1 ships "
    "monitors only)."
)


def _build_methodology_disclosure(z_window: int) -> str:
    """Compose the disclosure string with the runtime z-score window
    so consumers see the exact lookback that produced the scan
    (catalog methodology guardrail)."""
    return _METHODOLOGY_DISCLOSURE_TEMPLATE.format(z_window=z_window)


def _resolve_curve_families(
    requested: Optional[Sequence[str]],
    whitelist_csv: str,
) -> List[str]:
    """Resolve the LLM's optional ``curve_families`` input against the
    YAML's ``bond_futures_curve_families`` whitelist.

    When ``requested`` is None, returns the full whitelist (full
    universe scan). When ``requested`` is a list, the input schema
    already validated it against the whitelist, so this is a no-op
    pass-through that returns the request as-is.
    """
    whitelist = [s.strip() for s in whitelist_csv.split(",") if s.strip()]
    if requested is None:
        return whitelist
    return list(requested)


# ============================================================================
# PER-STEM COMPUTATION
# ============================================================================

def _build_stem_aligned_series(
    *,
    price_df: pd.DataFrame,
    volume_df: pd.DataFrame,
    oi_df: pd.DataFrame,
    ffill_limit: int,
    cap_at: Optional[date] = None,
) -> Optional[pd.DataFrame]:
    """Clean each of the three per-stem long-format slices and align
    them on the intersection of trading days.

    Returns a wide DataFrame with columns ``['price', 'volume',
    'open_interest']`` indexed by date, or None if any of the three
    series is empty after cleaning or the intersection is empty.

    Each input slice is the per-stem subset of the universe-wide
    DataFrame for one field (PX_LAST / PX_VOLUME / OPEN_INT). The
    cleaner uses the existing ``clean_single_series`` helper, which
    coerces types, drops NaN, deduplicates dates, sorts, and ffills
    up to ``ffill_limit`` consecutive missing values.

    ``cap_at`` truncates the per-stem aligned series to rows on or
    before the supplied date. Used to honour an LLM-supplied
    ``as_of_date`` deterministically (reviewer round-1 mandatory-fix
    #2) — a stray future-dated tick beyond the requested as-of date
    cannot influence the trailing rolling z-score this way.
    """
    if price_df.empty or volume_df.empty or oi_df.empty:
        return None
    clean_price = clean_single_series(price_df, ffill_limit=ffill_limit)
    clean_volume = clean_single_series(volume_df, ffill_limit=ffill_limit)
    clean_oi = clean_single_series(oi_df, ffill_limit=ffill_limit)
    if clean_price.empty or clean_volume.empty or clean_oi.empty:
        return None
    price_s = clean_price["field_value"]
    volume_s = clean_volume["field_value"]
    oi_s = clean_oi["field_value"]
    common_idx = (
        price_s.index.intersection(volume_s.index)
        .intersection(oi_s.index)
        .sort_values()
    )
    if cap_at is not None:
        cap_ts = pd.Timestamp(cap_at)
        common_idx = common_idx[common_idx <= cap_ts]
    if len(common_idx) == 0:
        return None
    return pd.DataFrame(
        {
            "price": price_s.loc[common_idx],
            "volume": volume_s.loc[common_idx],
            "open_interest": oi_s.loc[common_idx],
        }
    )


def _compute_stem_metrics(
    *,
    curve_family: str,
    contract_code: str,
    tenor: str,
    aligned: pd.DataFrame,
    z_window: int,
    z_min_periods: int,
    z_ddof: int,
    z_score_round_decimals: int,
    price_round_decimals: int,
    volume_round_decimals: int,
    oi_round_decimals: int,
    price_change_offset_rows: int,
) -> Optional[Dict[str, Any]]:
    """Compute the four-metric scoring for one stem.

    Returns a dict carrying the snapshot values + four per-metric
    z-scores, or None if the stem has fewer than ``z_min_periods``
    aligned observations (in which case NONE of the metrics is
    rankable for this stem; honest skip rather than per-metric z=None
    that would break the rank ordering).

    Z-scores are computed via the shared ``rolling_zscore`` helper —
    same primitive the per-contract monitors use, so a TY1 price z on
    the scan equals the TY1 price z from futures_price_level on the
    same as-of date. The price_change metric's z-score is the rolling
    z of the 1-day price diff series; daily_price_change snapshot is
    the raw latest 1-day diff (NOT multiplied by 100; NOT a bps
    quantity).
    """
    if len(aligned) < z_min_periods:
        return None

    price = aligned["price"]
    volume = aligned["volume"]
    open_interest = aligned["open_interest"]

    # ------------------------------------------------------------------
    # Snapshot values (latest aligned row)
    # ------------------------------------------------------------------
    as_of_ts = aligned.index[-1]
    as_of_date = as_of_ts.date().strftime("%Y-%m-%d")
    current_price = safe_float(price.iloc[-1], decimals=price_round_decimals)
    current_volume = safe_float(volume.iloc[-1], decimals=volume_round_decimals)
    current_oi = safe_float(open_interest.iloc[-1], decimals=oi_round_decimals)

    # 1-day raw price change (NOT *100; NOT a bps quantity — bond-
    # futures prices are in contract-native quote_units, not yield
    # percent). Matches futures_price_level's daily_change_price.
    daily_price_change: Optional[float]
    if len(price) >= price_change_offset_rows:
        cur_raw = price.iloc[-1]
        prev_raw = price.iloc[-price_change_offset_rows]
        if pd.isna(cur_raw) or pd.isna(prev_raw):
            daily_price_change = None
        else:
            daily_price_change = round(
                float(cur_raw) - float(prev_raw), price_round_decimals,
            )
    else:
        daily_price_change = None

    # 1-day raw OI change (NOT *100; matches futures_volume_oi's
    # delta_open_interest_1d).
    delta_oi_1d: Optional[float]
    if len(open_interest) >= price_change_offset_rows:
        cur_oi_raw = open_interest.iloc[-1]
        prev_oi_raw = open_interest.iloc[-price_change_offset_rows]
        if pd.isna(cur_oi_raw) or pd.isna(prev_oi_raw):
            delta_oi_1d = None
        else:
            delta_oi_1d = round(
                float(cur_oi_raw) - float(prev_oi_raw), oi_round_decimals,
            )
    else:
        delta_oi_1d = None

    # ------------------------------------------------------------------
    # Per-metric z-scores. All four use the SAME window / min_periods /
    # ddof; the metric distinction is in the SERIES the z is computed
    # on, not the method.
    # ------------------------------------------------------------------
    def _latest_z(series: pd.Series) -> Optional[float]:
        z = rolling_zscore(
            series,
            window=z_window,
            min_periods=z_min_periods,
            ddof=z_ddof,
            round_decimals=z_score_round_decimals,
        )
        return safe_float(z.iloc[-1], decimals=z_score_round_decimals)

    price_z = _latest_z(price)

    # The "Δ" metric's z is the rolling z of the 1-day diff series.
    # Use diff(1) (NOT diff(price_change_offset_rows)) for the
    # underlying CHANGE series so the metric corresponds to "1-trading-
    # day change" across the full history, not just the latest sample.
    # The change distribution dropna() because diff(1) leaves the
    # first row NaN; rolling_zscore handles NaN-padding internally
    # but starting clean is honest.
    price_change_series = price.diff(1).dropna()
    price_change_z = (
        _latest_z(price_change_series)
        if len(price_change_series) >= z_min_periods
        else None
    )

    volume_z = _latest_z(volume)
    oi_z = _latest_z(open_interest)

    return {
        "curve_family": curve_family,
        "contract_code": contract_code,
        "tenor": tenor,
        "as_of_date": as_of_date,
        "current_price": current_price,
        "daily_price_change": daily_price_change,
        "current_volume": current_volume,
        "current_open_interest": current_oi,
        "delta_open_interest_1d": delta_oi_1d,
        "z_by_metric": {
            "price": price_z,
            "price_change": price_change_z,
            "volume": volume_z,
            "open_interest": oi_z,
        },
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_scan_bond_futures_extremes(
    engine: Engine,
    params: ScanBondFuturesExtremesInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Scan the bond-futures rolling-generic universe and return the
    top-N extremes per metric (price / price_change / volume /
    open_interest), each tagged with the per-row methodology
    disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : ScanBondFuturesExtremesInput
        Validated input. ``curve_families=None`` means scan the full
        universe per the YAML's ``bond_futures_curve_families``
        whitelist.
    config : ToolConfig, optional
        Bundled ``config.yaml`` auto-loaded when None. Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``ScanBondFuturesExtremesOutput``, or
        ``{"error": "..."}`` on recoverable failure (empty universe,
        all stems filtered out, etc.).
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
    price_field = config.convention_value("default_price_field")
    volume_field = config.convention_value("default_volume_field")
    oi_field = config.convention_value("default_open_interest_field")
    price_change_offset_rows = config.convention_value(
        "price_change_offset_rows"
    )
    price_round_decimals = config.convention_value("price_round_decimals")
    volume_round_decimals = config.convention_value("volume_round_decimals")
    oi_round_decimals = config.convention_value("oi_round_decimals")
    z_score_round_decimals = config.convention_value("z_score_round_decimals")
    whitelist_csv = config.convention_value("bond_futures_curve_families")

    # Resolve None-sentinel display thresholds against the YAML
    # defaults (PR9 / PR10 — reviewer round-1 mandatory-fix #1: the
    # default lives in config.yaml, not in the schema's Field
    # default). Explicit casts protect against an off-type YAML edit
    # surviving lint.
    effective_top_n = (
        int(params.top_n)
        if params.top_n is not None
        else int(config.convention_value("default_top_n"))
    )
    effective_min_abs_z_score = (
        float(params.min_abs_z_score)
        if params.min_abs_z_score is not None
        else float(config.convention_value("default_min_abs_z_score"))
    )

    # Resolve the universe scope (LLM's input, validated against
    # YAML whitelist at schema time).
    curve_families = _resolve_curve_families(params.curve_families, whitelist_csv)

    # ------------------------------------------------------------------
    # 1. Date window — fetch start is derived from YAML, NOT from an
    #    LLM input (reviewer round-1 mandatory-fix #2: removed the
    #    ``lookback_days`` PR8 / OPR8 input-schema overreach). The
    #    buffer covers the rolling z-score window plus a calendar-day
    #    cushion for weekends + holidays. When ``as_of_date`` is
    #    supplied, the fetch is anchored to that date so we read only
    #    rows up to and including the anchor. When ``as_of_date`` is
    #    None, we fetch up to the wall-clock day (``date.today()``);
    #    the per-stem anchor is then resolved against the actual data
    #    after fetch (max trade_date observed across the universe)
    #    so the response remains deterministic given a fixed DB
    #    snapshot. A small extra buffer (``ffill_limit_days``) is
    #    added to the calendar window so the ffill bridge has full
    #    coverage on the leading edge.
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
    # 1a. Future-anchor guard — when the LLM supplies an explicit
    #     ``as_of_date`` that lies beyond the bond-futures universe's
    #     last ingested ``trade_date``, the schema documents (P5 /
    #     PR8 / PR16 honest disclosure) that the scan returns the
    #     controlled-error envelope rather than silently delivering a
    #     normal scan computed on only the actually-available rows
    #     (the within-data ``cap_at`` truncation is a no-op when the
    #     cap is in the future of the data max — every row passes it,
    #     so the per-stem rolling stats would be the unbounded run's
    #     rankings under a different anchor label, which is exactly
    #     the "mislabeled output" pattern PR8 / P5 prohibit).
    #
    #     Implemented as a cheap MAX(trade_date) probe over the same
    #     join the series fetcher uses (single source of truth — the
    #     two helpers must agree about what "the universe's last
    #     trading day" is). Skipped when ``requested_as_of`` is None
    #     (no anchor was supplied; the scanner resolves the anchor
    #     post-fetch from the actual data max, which is honest by
    #     construction).
    # ------------------------------------------------------------------
    if requested_as_of is not None:
        universe_max_trade_date = fetch_rolling_generic_universe_max_date(
            engine=engine,
            curve_families=curve_families,
        )
        if (
            universe_max_trade_date is not None
            and requested_as_of > universe_max_trade_date
        ):
            return {
                "error": (
                    f"no scoreable stems: as_of_date="
                    f"{requested_as_of.isoformat()} is beyond the "
                    f"bond-futures universe's last observed "
                    f"trade_date="
                    f"{universe_max_trade_date.isoformat()}. The "
                    "scan refuses to silently re-label an unbounded "
                    "ranking as a future-anchored read."
                )
            }

    # ------------------------------------------------------------------
    # 2. Fetch the three field series across the universe — 3 queries
    #    total. The helper's `tenor IS NOT NULL` filter ensures a
    #    policy-futures stem (NULL tenor on instrument_master) cannot
    #    leak in even if the whitelist is mis-edited. ``end_date`` is
    #    set to the resolved fetch anchor so the SQL bounds the
    #    returned rows at the requested as-of — belt-and-braces with
    #    the per-stem ``cap_at`` step (the per-stem cap also enforces
    #    the upper bound on the cleaned/ffilled series); the SQL upper
    #    bound is the deterministic-anchor scope for Layer-B
    #    validation.
    # ------------------------------------------------------------------
    raw_price = fetch_rolling_generic_universe_series(
        engine=engine,
        curve_families=curve_families,
        field_name=price_field,
        start_date=start_date,
        end_date=fetch_anchor,
    )
    raw_volume = fetch_rolling_generic_universe_series(
        engine=engine,
        curve_families=curve_families,
        field_name=volume_field,
        start_date=start_date,
        end_date=fetch_anchor,
    )
    raw_oi = fetch_rolling_generic_universe_series(
        engine=engine,
        curve_families=curve_families,
        field_name=oi_field,
        start_date=start_date,
        end_date=fetch_anchor,
    )

    if raw_price.empty or raw_volume.empty or raw_oi.empty:
        return {
            "error": (
                "No bond-futures universe data found for at least one "
                f"of the required fields ({price_field}, {volume_field}, "
                f"{oi_field}) since {start_date.isoformat()} across "
                f"curve_families={curve_families}. Verify the rolling-"
                "generic universe is ingested with all three fields."
            )
        }

    # ------------------------------------------------------------------
    # 3. Coerce types universe-wide BEFORE per-stem groupby. Drop
    #    rows where field_value coerced to NaN (defensive — the helper
    #    excludes NULLs at the SQL level but a string "NaN" could
    #    survive depending on driver behaviour). Tenor is stable per
    #    stem on the master row, so picking it from the first row
    #    of each stem's group is correct.
    # ------------------------------------------------------------------
    for df in (raw_price, raw_volume, raw_oi):
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")

    # ------------------------------------------------------------------
    # 4. Per-stem materialisation + scoring.
    # ------------------------------------------------------------------
    # Universe of stems = union of stems appearing in ANY of the three
    # fields. A stem missing from one field cannot be ranked (its
    # alignment will be empty), but using the union here keeps the
    # universe count accurate in the scan_summary and surfaces the
    # data-coverage gap honestly.
    universe_stems: set[Tuple[str, str, str]] = set()
    for df in (raw_price, raw_volume, raw_oi):
        if df.empty:
            continue
        for (cf, cc, tenor), _ in df.groupby(
            ["curve_family", "contract_code", "tenor"], dropna=False,
        ):
            universe_stems.add((cf, cc, tenor))
    universe_count = len(universe_stems)
    if universe_count == 0:
        return {
            "error": (
                "Bond-futures universe scan found 0 stems with data — "
                f"check {price_field} / {volume_field} / {oi_field} "
                f"ingestion for curve_families={curve_families}."
            )
        }

    # Group raw frames by stem ONCE; the per-stem inner loop indexes
    # into these. groupby with dropna=False so stems with NULL tenor
    # (policy-futures leak; should be impossible after the SQL filter,
    # but defensive) are visible in the universe count.
    price_groups = {
        (cf, cc): g for (cf, cc, _), g in raw_price.groupby(
            ["curve_family", "contract_code", "tenor"], dropna=False,
        )
    }
    volume_groups = {
        (cf, cc): g for (cf, cc, _), g in raw_volume.groupby(
            ["curve_family", "contract_code", "tenor"], dropna=False,
        )
    }
    oi_groups = {
        (cf, cc): g for (cf, cc, _), g in raw_oi.groupby(
            ["curve_family", "contract_code", "tenor"], dropna=False,
        )
    }

    per_stem_metrics: List[Dict[str, Any]] = []
    for (cf, cc, tenor) in sorted(
        universe_stems, key=lambda s: (s[0] or "", s[1] or "", s[2] or "")
    ):
        if tenor is None:
            # Belt-and-braces — SQL filter excludes these.
            continue
        key = (cf, cc)
        price_slice = price_groups.get(key)
        volume_slice = volume_groups.get(key)
        oi_slice = oi_groups.get(key)
        if (
            price_slice is None
            or volume_slice is None
            or oi_slice is None
            or price_slice.empty
            or volume_slice.empty
            or oi_slice.empty
        ):
            # Stem missing from one of the three fields — can't score.
            continue
        aligned = _build_stem_aligned_series(
            price_df=price_slice[["trade_date", "field_value"]],
            volume_df=volume_slice[["trade_date", "field_value"]],
            oi_df=oi_slice[["trade_date", "field_value"]],
            ffill_limit=ffill_limit,
            # Cap per-stem rows at the LLM-supplied as_of_date so a
            # stray future-dated tick cannot pollute the rolling
            # stats. When ``requested_as_of`` is None, no cap is
            # applied (the anchor is then resolved post-fetch from
            # the actual data max).
            cap_at=requested_as_of,
        )
        if aligned is None:
            continue
        scored = _compute_stem_metrics(
            curve_family=cf,
            contract_code=cc,
            tenor=tenor,
            aligned=aligned,
            z_window=z_window,
            z_min_periods=z_min_periods,
            z_ddof=z_ddof,
            z_score_round_decimals=z_score_round_decimals,
            price_round_decimals=price_round_decimals,
            volume_round_decimals=volume_round_decimals,
            oi_round_decimals=oi_round_decimals,
            price_change_offset_rows=price_change_offset_rows,
        )
        if scored is None:
            continue
        per_stem_metrics.append(scored)

    if not per_stem_metrics:
        # When ``requested_as_of`` is in the future or beyond the DB's
        # last trading day, the per-stem cap leaves every stem with
        # zero aligned rows — same controlled-error shape as the
        # generic "no scoreable stems" exit. Honest skip; no
        # exception.
        as_of_clause = (
            f" with as_of_date={requested_as_of.isoformat()}"
            if requested_as_of is not None else ""
        )
        return {
            "error": (
                f"Scanned {universe_count} bond-futures stems but none "
                f"had >= {z_min_periods} aligned observations to compute "
                f"a meaningful z-score{as_of_clause}. Verify the "
                "universe's data coverage or pass an as_of_date within "
                "the ingested range."
            )
        }

    # ------------------------------------------------------------------
    # 5. Per-metric ranking + top-N + threshold filter.
    # ------------------------------------------------------------------
    methodology_disclosure = _build_methodology_disclosure(z_window)

    per_metric_pass_counts: Dict[ScanMetric, int] = {m: 0 for m in _METRIC_ORDER}
    output_rows: List[ScanBondFuturesExtremesResultRow] = []
    for metric in _METRIC_ORDER:
        scored_for_metric: List[Tuple[float, Dict[str, Any]]] = []
        for stem_metrics in per_stem_metrics:
            z_value = stem_metrics["z_by_metric"][metric]
            if z_value is None:
                continue
            if abs(z_value) < effective_min_abs_z_score:
                continue
            scored_for_metric.append((z_value, stem_metrics))
        # Sort by |z| desc, then contract_code asc for deterministic
        # ordering when two stems share the same |z|.
        scored_for_metric.sort(
            key=lambda zm: (-abs(zm[0]), zm[1]["contract_code"])
        )
        per_metric_pass_counts[metric] = len(scored_for_metric)
        top_for_metric = scored_for_metric[:effective_top_n]
        for rank_idx, (z_value, stem_metrics) in enumerate(top_for_metric, start=1):
            output_rows.append(
                ScanBondFuturesExtremesResultRow(
                    rank=rank_idx,
                    metric=metric,
                    curve_family=stem_metrics["curve_family"],
                    contract_code=stem_metrics["contract_code"],
                    tenor=stem_metrics["tenor"],
                    as_of_date=stem_metrics["as_of_date"],
                    current_price=stem_metrics["current_price"],
                    daily_price_change=stem_metrics["daily_price_change"],
                    current_volume=stem_metrics["current_volume"],
                    current_open_interest=stem_metrics["current_open_interest"],
                    delta_open_interest_1d=stem_metrics["delta_open_interest_1d"],
                    z_score=round(float(z_value), z_score_round_decimals),
                    signal="EXTREME_HIGH" if z_value > 0 else "EXTREME_LOW",
                    methodology_disclosure=methodology_disclosure,
                )
            )

    if not output_rows:
        return {
            "error": (
                f"Scanned {universe_count} bond-futures stems "
                f"({len(per_stem_metrics)} with enough history to "
                f"score) but none passed |z| >= "
                f"{effective_min_abs_z_score} on any metric. Try "
                "lowering min_abs_z_score (or omit it to fall through "
                "to the YAML default)."
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
    per_metric_clause = ", ".join(
        f"{m}={per_metric_pass_counts[m]}" for m in _METRIC_ORDER
    )
    scan_summary = (
        f"Scanned {universe_count} bond-futures stems "
        f"({len(per_stem_metrics)} scoreable). "
        f"Stems with |z| >= {effective_min_abs_z_score} per metric: "
        f"{per_metric_clause}. "
        f"Showing top {effective_top_n} per metric ({len(output_rows)} "
        f"rows). {as_of_clause}."
    )

    output = ScanBondFuturesExtremesOutput(
        scan_summary=scan_summary,
        results=output_rows,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()
