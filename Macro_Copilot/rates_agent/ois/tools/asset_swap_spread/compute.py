"""
compute.py — Config-driven per-bond INGESTED Bloomberg ASW tool
================================================================

Pure INGEST primitive (P12).  Surfaces the vendor's per-bond
``ASSET_SWAP_SPD_MID`` value VERBATIM (no recomputation, no
rounding of the raw value) with optional level-stat decoration
(period changes, z-score, trailing range).

PR4 DIFFERENTIATION (load-bearing — catalog guardrail)
------------------------------------------------------
This tool is DISTINCT from ``rates_agent/ois/tools/swap_spread/``.
BOTH methodology cards cite each other (this side under
``methodology.related_primitives`` in config.yaml; swap_spread
side ditto).

  THIS tool (asset_swap_spread)            | swap_spread
  -----------------------------------------+----------------------------
  Source: vendor-ingested Bloomberg        | Computed in-process
          ``ASSET_SWAP_SPD_MID`` per       | as ``(sov_yield - ois) * 100``
          bond                             | par-par approximation
  Granularity: per-bond (vendor_ticker)    | per (curve_family, tenor)
  RAW asw_spread: bit-exact from DB        | rounded per YAML
  NULL handling: typed exception (P6)      | ffill / error envelope

Typed P6 refusal — ``AssetSwapSpreadUnavailableError``
------------------------------------------------------
Per the catalog guardrail "On NULL asw_spread for a bond/date,
raise a typed exception per P6 — do NOT fall back to computing
the spread", compute() raises ``AssetSwapSpreadUnavailableError``
on:
  - ``vendor_ticker`` not in instrument_master with
    ``instrument_type='sovereign_cash_bond'``
  - NULL ``ASSET_SWAP_SPD_MID`` on the requested ``as_of_date``
    (no ffill on the explicit-date path)
  - empty result set for the lookback window

The MCP wrapper at the transport boundary converts the typed
exception to the controlled error envelope per P6's transport-
boundary rule; tests assert both the raise (compute layer) and
the envelope (transport layer).

No-rounding discipline for the raw ASW
--------------------------------------
``current_asw_spread_bps`` and every row of the canonical
``time_series`` carry the RAW Bloomberg-ingested value preserved
bit-exact from ``macro_data.market_data_daily``.  This is
DELIBERATE — the SQL-validation runner asserts 1e-9 parity
against a raw SELECT, which only holds when the Python output is
unrounded.  Level-stat decoration (z-score, period changes,
high/low/percentile) IS rounded per YAML.

Test seam
---------
Tests patch ``fetch_single_bond_series`` and ``date`` at this
module's namespace (``...asset_swap_spread.compute.X``) — same
pattern as the other ingest primitives.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.asset_swap_spread.schemas import (
    AssetSwapSpreadInput,
    AssetSwapSpreadMetrics,
    AssetSwapSpreadOutput,
    AssetSwapSpreadUnavailableError,
)
from shared.analytics.levels import (
    clean_single_series,
    compute_level_metrics,
)
from shared.analytics.rates_fetch import fetch_single_bond_series
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Trailing-range window is wire-frozen at 252 in V1; see schemas.py
# and config.yaml's ``planned_extensions``.  Mirrors the same guard
# in yield_levels, real_yield_level, OIS rate_level, swap_spread.
_FROZEN_TRAILING_WINDOW: int = 252


# instrument_type filter — code-level invariant per
# DESIGN_PRINCIPLES.md §5 ("YAML owns conventions; code owns
# invariants").  The whole point of this primitive owning a
# separate concept is that it operates on sovereign cash bond
# rows.  Mirrors real_yield_level's ``_LINKER_INSTRUMENT_TYPE``.
_SOVEREIGN_CASH_BOND_INSTRUMENT_TYPE: str = "sovereign_cash_bond"


# Data-source policy — the load-bearing P12 commitment.  Locked at
# the single supported value in V1; widening goes through
# methodology.planned_extensions.
_SUPPORTED_DATA_SOURCE_POLICY: str = "bloomberg_ingested_asw"


# Methodology note surfaced in the output payload at runtime (PR10
# non-obvious-methodology row — the P12 vendor-ingest discipline +
# CAD ASW sparsity caveat + security_name absence are material to
# interpreting the output and are not derivable from the config
# alone).
_METHODOLOGY_NOTE: str = (
    "Source: ``ASSET_SWAP_SPD_MID`` ingested from Bloomberg per the "
    "``sovereign_cash_bonds`` playbook (verified 2026-05-21, A4-4 probe). "
    "P12 INGEST primitive — the per-bond ASW value is the vendor's "
    "source-of-record number, surfaced VERBATIM with no rounding "
    "(SQL parity within 1e-9 holds).  NEVER recomputed from OIS "
    "forwards / discount factors / par-par arithmetic.  Distinct from "
    "``rates_agent/ois/tools/swap_spread/`` which IS a par-par "
    "approximation over OIS forwards (see this tool's config.yaml "
    "header for the side-by-side and the symmetric ``related_primitives`` "
    "cite in swap_spread/config.yaml).  CAD government bonds carry "
    "sparse ASW data (~2% of trading days per playbook header note 4); "
    "the rolling z-score may be empty for CAD when the 60-observation "
    "floor is not met — that is honest absence (P5), not a pipeline "
    "error.  NULL ``ASSET_SWAP_SPD_MID`` on an explicit ``as_of_date`` "
    "raises ``AssetSwapSpreadUnavailableError`` (typed P6 refusal — "
    "no ffill, no proxy, no fall-through).  ``security_name`` and "
    "``issuer`` reference fields are universally NULL on cash-bond "
    "rows in the live SCD2 substrate — P5 disclosed in "
    "``methodology.field_availability_caveat`` of config.yaml; "
    "callers can derive a bond label from vendor_ticker + country + "
    "maturity_date."
)


# Bond-identity lookup against ``macro_data.instrument_master``.
# Joined once per primitive call to surface the catalog's
# ``required_reference_metrics`` for the bond.  Also validates
# the vendor_ticker exists with instrument_type='sovereign_cash_bond'
# — returns None when not found, triggering an
# AssetSwapSpreadUnavailableError in compute().
_BOND_IDENTITY_SQL = text(
    """
    SELECT
        country,
        currency,
        cusip,
        isin,
        maturity_date,
        instrument_type
    FROM macro_data.instrument_master
    WHERE vendor_ticker = :vendor_ticker
    LIMIT 1
    """
)


def _fetch_bond_identity(
    engine: Engine,
    *,
    vendor_ticker: str,
) -> Optional[Dict[str, Any]]:
    """Pull the bond's identity fields from ``instrument_master``.

    Returns a dict carrying ``country`` / ``currency`` / ``cusip`` /
    ``isin`` / ``maturity_date`` / ``instrument_type`` for the matched
    vendor_ticker row.  Returns ``None`` when no row matches — caller
    raises ``AssetSwapSpreadUnavailableError`` on the None branch.
    """
    with engine.connect() as conn:
        row = (
            conn.execute(
                _BOND_IDENTITY_SQL,
                {"vendor_ticker": vendor_ticker},
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return {
        "country": row.get("country"),
        "currency": row.get("currency"),
        "cusip": row.get("cusip"),
        "isin": row.get("isin"),
        "maturity_date": (
            row["maturity_date"].isoformat()
            if row.get("maturity_date") is not None
            else None
        ),
        "instrument_type": row.get("instrument_type"),
    }


# ============================================================================
# CONVENTION VALIDATION
# ============================================================================


def _validate_conventions(config: ToolConfig) -> None:
    """Refuse loudly when a wire-frozen convention is set to a value
    V1 does not yet support (PR14 + PR11).

    Raises ``NotImplementedError`` naming the offending value and
    pointing at ``methodology.planned_extensions``.
    """
    trailing = config.convention_value("trailing_range_window_days")
    if trailing != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            f"trailing_range_window_days={trailing!r} is documented in "
            f"this tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented. "
            f"V1 supports only {_FROZEN_TRAILING_WINDOW} because the "
            f"output field names (high_252d_bps, low_252d_bps, "
            f"percentile_252d) embed that number on the wire.  Either "
            f"restore the value to {_FROZEN_TRAILING_WINDOW} or "
            f"implement the schema rename + frontend update documented "
            f"in planned_extensions."
        )

    data_source = config.convention_value("data_source_policy")
    if data_source != _SUPPORTED_DATA_SOURCE_POLICY:
        raise NotImplementedError(
            f"data_source_policy={data_source!r} is documented in this "
            f"tool's config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented. "
            f"V1 supports only {_SUPPORTED_DATA_SOURCE_POLICY!r} — the "
            f"vendor-ingested Bloomberg ASW.  Widening to a second "
            f"adapter's ingested ASW is documented in "
            f"methodology.planned_extensions and requires an additive "
            f"branch in this primitive's compute path."
        )


def _conventions_from_config(config: ToolConfig) -> dict:
    """Pull the methodology kwargs ``compute_level_metrics`` needs from
    a ToolConfig.  The RAW asw_spread is NOT routed through this dict
    (it is surfaced bit-exact, not via compute_level_metrics' rounding).
    """
    _validate_conventions(config)
    return {
        "z_window": config.convention_value("z_score_window_days"),
        "z_min_periods": config.convention_value("z_score_min_periods"),
        "z_ddof": config.convention_value("z_score_ddof"),
        "period_offsets": {
            "daily": config.convention_value("daily_change_offset_rows"),
            "weekly": config.convention_value("weekly_change_offset_rows"),
            "monthly": config.convention_value("monthly_change_offset_rows"),
        },
        "trailing_window": config.convention_value("trailing_range_window_days"),
        # Period changes + trailing range use bps_round_decimals.
        # ``yield_round_decimals`` is the kwarg name compute_level_metrics
        # expects for the value-side rounding — we pass bps_round_decimals
        # here so the period-change and trailing-range fields get
        # bps-precision.  The snapshot's RAW current_asw_spread_bps is
        # NOT taken from compute_level_metrics' ``current_value`` (which
        # gets rounded); see the snapshot-building block in
        # get_asset_swap_spread() below.
        "yield_round_decimals": config.convention_value("bps_round_decimals"),
        "z_score_round_decimals": config.convention_value("z_score_round_decimals"),
        "high_low_round_decimals": config.convention_value("bps_round_decimals"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================


def get_asset_swap_spread(
    engine: Engine,
    params: AssetSwapSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return the INGESTED Bloomberg ASW spread + level-stat
    decoration for one sovereign cash bond identified by
    ``vendor_ticker``.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : AssetSwapSpreadInput
        Validated input.  ``field_name=None`` resolves against the
        YAML's ``default_field_name`` convention.  When
        ``as_of_date`` is supplied, the snapshot is for THAT date —
        and NULL ASW on that date raises
        ``AssetSwapSpreadUnavailableError`` (no ffill).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``AssetSwapSpreadOutput`` with the RAW ASW value
        preserved bit-exact for SQL parity.

    Raises
    ------
    AssetSwapSpreadUnavailableError
        Typed P6 refusal per the catalog guardrail.  Raised when:

        - ``vendor_ticker`` not in instrument_master with
          ``instrument_type='sovereign_cash_bond'``
        - NULL ``ASSET_SWAP_SPD_MID`` on the requested ``as_of_date``
        - empty result set for the lookback window
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    z_window = config.convention_value("z_score_window_days")
    buffer_mult = config.convention_value("z_score_buffer_multiplier")
    ffill_limit = config.convention_value("ffill_limit_days")
    default_field_name = config.convention_value("default_asw_field_name")
    metrics_kwargs = _conventions_from_config(config)

    # Resolve field_name: caller's explicit value wins; None falls
    # through to the YAML default.
    field_name_resolved = (
        params.field_name if params.field_name is not None else default_field_name
    )

    # ------------------------------------------------------------------
    # 1. Bond identity — validates vendor_ticker exists with the
    # required instrument_type.  RAISES (not envelope) on miss per
    # the catalog guardrail.
    # ------------------------------------------------------------------
    identity = _fetch_bond_identity(
        engine=engine,
        vendor_ticker=params.vendor_ticker,
    )
    if identity is None:
        raise AssetSwapSpreadUnavailableError(
            vendor_ticker=params.vendor_ticker,
            reason=(
                "vendor_ticker not found in macro_data.instrument_master. "
                "Confirm the per-bond key (e.g. /isin/<ISIN>) is in the "
                "sovereign_cash_bonds playbook universe and that the "
                "playbook has been ingested (load_audit SUCCESS row "
                "required)."
            ),
            as_of_date=params.as_of_date,
        )
    if identity["instrument_type"] != _SOVEREIGN_CASH_BOND_INSTRUMENT_TYPE:
        raise AssetSwapSpreadUnavailableError(
            vendor_ticker=params.vendor_ticker,
            reason=(
                f"instrument_type={identity['instrument_type']!r} does not "
                f"match {_SOVEREIGN_CASH_BOND_INSTRUMENT_TYPE!r}.  This "
                f"primitive only surfaces ASW for sovereign cash bonds "
                f"(P12 + P11 — never proxy a non-cash-bond instrument)."
            ),
            as_of_date=params.as_of_date,
        )

    # ------------------------------------------------------------------
    # 2. Date window — anchored to as_of_date when supplied, else to
    # the data's latest observation (resolved after the fetch).
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult)
    if params.as_of_date is not None:
        # Explicit as_of_date: window is [as_of_date - (lookback + buffer),
        # as_of_date].  No future data fetched.
        start_date = params.as_of_date - timedelta(
            days=params.lookback_days + buffer_calendar_days
        )
        end_date: Optional[date] = params.as_of_date
    else:
        # Latest mode: window starts from today - (lookback + buffer),
        # no upper bound — the snapshot resolves to the most recent
        # observation in the result.  date.today() chosen as the
        # OUTER cap of the fetch (the actual snapshot anchors to
        # data-latest, not date.today, per the methodology card).
        start_date = date.today() - timedelta(
            days=params.lookback_days + buffer_calendar_days
        )
        end_date = None

    # ------------------------------------------------------------------
    # 3. Fetch — through the shared analytics helper.  Passes the
    # instrument_type filter as defence-in-depth.
    # ------------------------------------------------------------------
    raw_df = fetch_single_bond_series(
        engine=engine,
        vendor_ticker=params.vendor_ticker,
        field_name=field_name_resolved,
        start_date=start_date,
        end_date=end_date,
        instrument_type=_SOVEREIGN_CASH_BOND_INSTRUMENT_TYPE,
    )

    if raw_df.empty:
        raise AssetSwapSpreadUnavailableError(
            vendor_ticker=params.vendor_ticker,
            reason=(
                f"No ASW observations found for vendor_ticker on "
                f"field={field_name_resolved!r} in the lookback window "
                f"[{start_date.isoformat()}, "
                f"{end_date.isoformat() if end_date else 'open'}].  "
                f"CAD government bonds are verifiably sparse (~2% of "
                f"trading days per playbook header note 4); for "
                f"non-CAD bonds an empty result indicates the bond's "
                f"ASW has not been ingested yet."
            ),
            as_of_date=params.as_of_date,
        )

    # ------------------------------------------------------------------
    # 4. RAW-value preservation path.
    #
    # Two parallel uses of the fetched data:
    #
    #   (a) The RAW Series ``raw_spreads`` — keeps the Bloomberg-
    #       ingested values verbatim (no ffill, no rounding) for the
    #       snapshot's ``current_asw_spread_bps`` and the canonical
    #       TimeSeries rows.  Used for SQL parity within 1e-9.
    #
    #   (b) The cleaned ``spreads`` Series — ffill'd per YAML for the
    #       level-stat decoration (z-score, period changes, trailing
    #       range).  These ARE rounded per YAML.
    # ------------------------------------------------------------------
    raw_df = raw_df.copy()
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df = raw_df.set_index("trade_date").sort_index()
    raw_spreads = raw_df["field_value"].astype(float)  # NO ffill, NO rounding

    # ------------------------------------------------------------------
    # 5. Snapshot date resolution + RAW ASW lookup.
    # ------------------------------------------------------------------
    if params.as_of_date is not None:
        # Caller asked for THIS date specifically.  NULL on that date
        # is a typed refusal (no ffill).
        as_of_ts = pd.Timestamp(params.as_of_date)
        if as_of_ts not in raw_spreads.index:
            raise AssetSwapSpreadUnavailableError(
                vendor_ticker=params.vendor_ticker,
                reason=(
                    f"No ASW row for the requested as_of_date — the "
                    f"vendor's source-of-record does not publish "
                    f"``ASSET_SWAP_SPD_MID`` on this date.  Per the "
                    f"catalog guardrail, no ffill / no proxy."
                ),
                as_of_date=params.as_of_date,
            )
        raw_value = raw_spreads.loc[as_of_ts]
        if pd.isna(raw_value):
            raise AssetSwapSpreadUnavailableError(
                vendor_ticker=params.vendor_ticker,
                reason=(
                    f"ASW is NULL on the requested as_of_date — vendor "
                    f"published the row with a null value.  Per the "
                    f"catalog guardrail, no ffill / no proxy / no "
                    f"swap_spread fall-through."
                ),
                as_of_date=params.as_of_date,
            )
        snapshot_date = as_of_ts
    else:
        # Latest mode: the most recent non-NULL observation.
        non_null = raw_spreads.dropna()
        if non_null.empty:
            raise AssetSwapSpreadUnavailableError(
                vendor_ticker=params.vendor_ticker,
                reason=(
                    "All ASW observations in the lookback window are "
                    "NULL.  See CAD sparsity caveat in methodology_note "
                    "of successful responses."
                ),
                as_of_date=None,
            )
        snapshot_date = non_null.index[-1]
        raw_value = non_null.iloc[-1]

    # ------------------------------------------------------------------
    # 6. Level-stat decoration on the CLEANED series (ffill per YAML).
    # These ARE rounded; the raw snapshot value above is NOT.
    # ------------------------------------------------------------------
    clean_df = clean_single_series(raw_df.reset_index(), ffill_limit=ffill_limit)
    decoration: Dict[str, Any]
    if clean_df.empty:
        # No non-NULL cleaned data — the snapshot above already
        # raised in this case for as_of_date; for latest mode we
        # would also have raised.  Defensive empty-decoration shape.
        decoration = {
            "period_changes": {"daily": None, "weekly": None, "monthly": None},
            "z_score": None,
            "high": None,
            "low": None,
            "percentile": None,
        }
    else:
        cleaned_spreads = clean_df["field_value"]
        decoration = compute_level_metrics(cleaned_spreads, **metrics_kwargs)

    # ------------------------------------------------------------------
    # 7. Observation count — anchored to the snapshot_date.
    # ------------------------------------------------------------------
    cutoff = snapshot_date - pd.Timedelta(days=params.lookback_days)
    display_raw = raw_spreads.loc[
        (raw_spreads.index >= cutoff) & (raw_spreads.index <= snapshot_date)
    ]
    obs_count = int(display_raw.dropna().shape[0])

    # ------------------------------------------------------------------
    # 8. Build snapshot — current_asw_spread_bps is the RAW value
    # (NOT rounded).  Decoration fields use the rounded values from
    # compute_level_metrics.
    # ------------------------------------------------------------------
    metrics = AssetSwapSpreadMetrics(
        as_of_date=snapshot_date.strftime("%Y-%m-%d"),
        vendor_ticker=params.vendor_ticker,
        country=identity["country"],
        currency=identity["currency"],
        cusip=identity["cusip"],
        isin=identity["isin"],
        maturity_date=identity["maturity_date"],
        current_asw_spread_bps=float(raw_value),  # RAW, unrounded.
        daily_change_bps=decoration["period_changes"].get("daily"),
        weekly_change_bps=decoration["period_changes"].get("weekly"),
        monthly_change_bps=decoration["period_changes"].get("monthly"),
        z_score=decoration.get("z_score"),
        high_252d_bps=decoration.get("high"),
        low_252d_bps=decoration.get("low"),
        percentile_252d=decoration.get("percentile"),
        observation_count=obs_count,
        lookback_days=params.lookback_days,
    )

    # ------------------------------------------------------------------
    # 9. Canonical TimeSeries — RAW values, no rounding, for parity.
    # ------------------------------------------------------------------
    canonical_series = _build_canonical_asw_time_series(
        display_raw,
        vendor_ticker=params.vendor_ticker,
    )

    output = AssetSwapSpreadOutput(
        current_metrics=metrics,
        time_series=canonical_series,
        methodology_note=_METHODOLOGY_NOTE,
    )
    return output.model_dump()


# ============================================================================
# CANONICAL TIME-SERIES BUILDER
# ============================================================================


def _sanitise_vendor_ticker(vendor_ticker: str) -> str:
    """Convert vendor_ticker to a safe series_name suffix.

    ``/isin/DE000BU22130`` → ``isin_de000bu22130``.  Removes leading /
    and replaces internal / with _; lowercases.  Stable across pandas
    / numpy versions because it is plain Python string manipulation.
    """
    s = vendor_ticker.strip().lstrip("/").lower().replace("/", "_")
    return s


def _build_canonical_asw_time_series(
    display_spreads: pd.Series,
    *,
    vendor_ticker: str,
) -> TimeSeries:
    """Convert the in-window ASW series into the canonical
    ``TimeSeries`` shape with closed-enum units (BPS).

    Each row carries the RAW Bloomberg-ingested value (NO rounding)
    so the snapshot's ``current_asw_spread_bps`` equals
    ``time_series.rows[-1].value`` STRICTLY at the snapshot date,
    and the SQL-validation runner can assert 1e-9 parity.

    Naming convention: ``asw_spread_<sanitised_vendor_ticker>``.
    """
    series_name = f"asw_spread_{_sanitise_vendor_ticker(vendor_ticker)}"
    rows = [
        TimeSeriesRow(
            date=ts.strftime("%Y-%m-%d"),
            value=(float(v) if pd.notna(v) else None),
        )
        for ts, v in display_spreads.items()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.BPS,
        description=(
            f"INGESTED Bloomberg asset-swap spread (ASSET_SWAP_SPD_MID) "
            f"for {vendor_ticker} over the display window.  RAW values "
            f"preserved bit-exact from macro_data.market_data_daily — "
            f"no rounding so SQL parity within 1e-9 holds against a "
            f"raw SELECT.  P12 INGEST — never recomputed."
        ),
        rows=rows,
    )
