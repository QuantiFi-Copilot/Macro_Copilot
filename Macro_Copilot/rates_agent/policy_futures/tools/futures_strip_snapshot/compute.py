"""
compute.py — Config-driven policy-futures whole-strip snapshot (V1)
====================================================================

Whole-strip side-by-side snapshot for ONE policy-futures
``curve_family``. Reads all configured strip positions' price +
open-interest series in TWO ``fetch_strip_group`` round-trips,
converts each to implied-rate space per the per-strip
``inverse_priced`` metadata flag, aligns on the intersection of
trading days, and emits one row per configured strip position with:

  - latest aligned ``raw_price`` in the contract's quote space
  - desk-recognised ``implied_rate_pct`` (PERCENT)
  - 1-day raw subtraction on the implied-rate axis
  - rolling 252-day z-score of the per-leg implied-rate level
  - latest ``open_interest`` in CONTRACTS
  - the as_of-bounded SCD2 disclosure block per leg
  - a per-row methodology card disclosing the short-rate regime
    and the implied-rate conversion rule (catalog standardness
    guardrail).

Why one strip-aware primitive instead of composing 8 ``futures_price_level``
----------------------------------------------------------------------------
Composing eight outright reads loses the aligned-date guarantee:
each ``futures_price_level`` call anchors at its own per-leg
data-max ``trade_date``, so an eight-call composition can return
rows on slightly different anchor dates. The desk-recognised
strip-snapshot reading is the WHOLE STRIP on a SINGLE aligned
``as_of_date`` — that is the date on which "the strip is steep /
flat / inverted" makes sense. This primitive enforces the
intersection-of-trading-days alignment up front so the per-row
fields are all read against the same date (PR4 — strip-aware
primitive over per-leg composition for accuracy of alignment,
provenance, and LLM clarity).

Why two ``fetch_strip_group`` round-trips (price + OI)
------------------------------------------------------
Same shape the sibling ``volume_open_interest_snapshot`` uses for
its (volume, OI) pair, generalised to all configured strip positions
in one call each. The function returns the long-format
``(trade_date, strip_position, field_value)`` frame the snapshot
needs; we pivot on ``strip_position`` via
``pivot_and_align_tenors(key_col='strip_position')`` to align across
positions on a wide ``(date × strip_position)`` frame and forward-
fill holiday gaps up to the YAML's ``ffill_limit_days``.

``fetch_strip_group`` does NOT carry an ``end_date`` parameter
today; to keep the deterministic-anchoring contract — explicit
``as_of_date`` ⇒ snapshot anchored at that date — the post-fetch
step filters rows by ``trade_date <= as_of_date`` in pandas.
Mirrors the sibling ``futures_butterfly_simple`` exactly.

Per-leg implied-rate conversion — metadata-driven
-------------------------------------------------
The per-strip ``inverse_priced`` flag from
``instrument_master.attributes`` decides whether
``implied_rate_pct = 100 - raw_price`` (inverse) or
``implied_rate_pct = raw_price`` (direct). NOT a hardcoded list in
compute.py (PR8 / P6 — no hidden methodology choice in code). All
configured strip positions on the requested ``curve_family`` MUST
agree on the flag; a mixed-flag set is refused with the
controlled-error envelope.

Methodology disclosure (per-row + output-level)
-----------------------------------------------
Every response carries an output-level ``methodology_disclosure``
with the curve_family, the inverse-pricing rule, the short-rate
regime label, the z-score lookback window, the strip-positions
list, and the rolling-generic-strip scope-limit caveat. Each row
ALSO carries its own ``row_methodology_card`` disclosing the
regime + conversion rule — required by the catalog's standardness
guardrail so a desk consumer copying a single row out of the
snapshot still sees the relevant disclosure on that row.

Deterministic anchoring (PR8 + PR16)
------------------------------------
The input schema's ``as_of_date`` (default ``None``) anchors the
snapshot:

  - ``None`` → anchor at the universe's last observed ``trade_date``
    where ALL configured strip positions have a value after
    intersection (post-fetch data-max anchor).

  - explicit date BEYOND any leg's last ``trade_date`` → return the
    controlled-error envelope (``{"error": "no scoreable strip:
    as_of_date=... is beyond ..."}``). The future-anchor probe
    (``fetch_strip_position_max_date``) is run per leg; the BINDING
    leg (earliest universe max) drives the envelope so the consumer
    knows which leg is gating the snapshot.

  - explicit date WITHIN the universe range → the post-fetch step
    filters by ``trade_date <= as_of_date`` so the snapshot is
    DETERMINISTIC across runs (same as_of_date + same DB state ⇒
    same numbers).

DB access
---------
Reaches the DB through the shared ``fetch_strip_group`` (price +
OI), ``fetch_strip_position_reference`` (per-leg SCD2 metadata
incl. ``inverse_pricing`` flag, run once per configured strip
position), and ``fetch_strip_position_max_date`` (future-anchor
probe, run once per configured strip position only when
``as_of_date`` is supplied) helpers — single-source-of-truth (P10)
for policy-futures strip-position reads. NO raw SQL in this file.

Test seam
---------
``fetch_strip_group``, ``fetch_strip_position_reference``,
``fetch_strip_position_max_date``, and ``date`` are imported here
at module level; tests patch them via
``patch("rates_agent.policy_futures.tools.futures_strip_snapshot.compute.X")``.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.policy_futures.tools.futures_strip_snapshot.schemas import (
    FuturesStripSnapshotInput,
    FuturesStripSnapshotOutput,
    FuturesStripSnapshotRow,
)
from shared.analytics.rates_fetch import (
    fetch_strip_group,
    fetch_strip_position_max_date,
    fetch_strip_position_reference,
)
from shared.analytics.spreads import (
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config


# Bundled config — public symbol so external callers (mcp_server,
# tests, future REST routes) can build a ToolConfig from the same
# source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# ============================================================================
# CONFIG HELPERS
# ============================================================================

def _parse_regime_map(csv_value: str) -> Mapping[str, str]:
    """Parse the YAML's ``short_rate_regime_map`` CSV into a dict.

    Stored as a CSV of ``KEY=VALUE`` pairs because
    ``Convention.value`` is scalar (str). Defensive against
    whitespace / trailing commas / mis-cased separators.
    """
    out: dict[str, str] = {}
    for chunk in csv_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(
                f"short_rate_regime_map entry {chunk!r} is malformed; "
                f"expected 'CURVE_FAMILY=REGIME' (CSV-joined)."
            )
        key, value = chunk.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _parse_strip_positions(csv_value: str) -> Tuple[int, ...]:
    """Parse the YAML's ``strip_positions`` CSV into an ordered tuple
    of positive integers. Preserves the order the desk author intended
    so the snapshot's row ordering is YAML-driven."""
    out: list[int] = []
    for chunk in csv_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            pos = int(chunk)
        except ValueError as exc:
            raise ValueError(
                f"strip_positions entry {chunk!r} is not an integer; "
                f"expected 'N,N,N,...' (CSV-joined). Detail: {exc}"
            ) from exc
        if pos < 1:
            raise ValueError(
                f"strip_positions entry {pos} is not a positive "
                "integer (1-based strip positions only)."
            )
        out.append(pos)
    if not out:
        raise ValueError(
            "strip_positions is empty; configure at least one strip "
            "position in the YAML (V1 default is '1,2,3,4,5,6,7,8')."
        )
    return tuple(out)


# ============================================================================
# DISCLOSURE BUILDERS
# ============================================================================

def _build_row_methodology_card(
    *,
    curve_family: str,
    inverse_priced: bool,
    short_rate_regime: str,
    z_window_days: int,
) -> str:
    """Per-row methodology card — emitted on every row so a desk
    consumer copying ONE row out of the snapshot still sees the regime
    label + conversion rule. Catalog standardness guardrail."""
    if inverse_priced:
        rule = "implied_rate_pct = 100 - raw_price (inverse-priced strip)"
    else:
        rule = "implied_rate_pct = raw_price (direct-priced strip)"
    return (
        f"{curve_family} row — underlying short-rate regime: "
        f"{short_rate_regime} (RFR = compounded daily risk-free rate; "
        f"IBOR = unsecured 3M term IBOR). Implied-rate conversion: "
        f"{rule}. Per-leg z-score lookback = {z_window_days} trading "
        f"days on the implied-rate level series."
    )


def _build_methodology_disclosure(
    *,
    curve_family: str,
    inverse_priced: bool,
    short_rate_regime: str,
    z_window_days: int,
    strip_positions: Tuple[int, ...],
) -> str:
    """Output-level methodology disclosure — names the curve_family,
    inverse-pricing rule, regime label, z window, configured strip
    positions, and the rolling-generic-strip scope-limit caveat."""
    if inverse_priced:
        rule = (
            "Inverse-priced strip — per leg, implied_rate_pct = 100 - "
            "raw_price; the snapshot's daily-change column is the raw "
            "subtraction on the implied-rate axis (PERCENT POINTS)."
        )
    else:
        rule = (
            "Direct-priced strip — per leg, implied_rate_pct = "
            "raw_price; the snapshot's daily-change column is the raw "
            "subtraction on the implied-rate axis (PERCENT POINTS)."
        )
    positions_csv = ",".join(str(p) for p in strip_positions)
    return (
        f"Rolling-generic strip snapshot for {curve_family} across "
        f"strip_positions=[{positions_csv}]; the desk-recognised "
        f"reading is the WHOLE STRIP on the aligned ``as_of_date``. "
        f"Underlying short-rate regime: {short_rate_regime} (RFR = "
        f"compounded daily risk-free rate; IBOR = unsecured 3M term "
        f"IBOR). {rule} Per-leg z-score lookback = {z_window_days} "
        f"trading days on the implied-rate level series. This is "
        f"NOT a CTD-of-futures-of-OIS strip read; the CTD-implied-OIS "
        f"curve is not yet a primitive in this build (ADR 0011 V1 "
        f"scope — policy_futures ships strip-position-keyed monitors "
        f"only). The per-contract underlying rolls quarterly so each "
        f"strip slot mixes contracts across rolls; this is the "
        f"canonical desk read but it does NOT equal the price of a "
        f"single underlying contract over time."
    )


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_futures_strip_snapshot(
    engine: Engine,
    params: FuturesStripSnapshotInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Return a whole-strip snapshot for one policy-futures
    ``curve_family``: one row per configured strip position, each with
    raw_price + implied_rate_pct + daily_change + 252d z-score +
    open_interest + the SCD2 disclosure block + a per-row methodology
    card, plus the output-level methodology disclosure.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : FuturesStripSnapshotInput
        Validated input. ``last_price_field_name`` /
        ``open_interest_field_name`` default to ``None`` (use YAML
        defaults). ``as_of_date`` default to ``None`` (data-max
        anchor across all configured strip positions).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.

    Returns
    -------
    dict
        Serialised ``FuturesStripSnapshotOutput``, or
        ``{"error": "..."}`` on recoverable failure.
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
    daily_offset_rows = config.convention_value("daily_change_offset_rows")
    ffill_limit = config.convention_value("ffill_limit_days")
    default_price_field = config.convention_value("default_price_field")
    default_oi_field = config.convention_value("default_open_interest_field")
    raw_price_round_decimals = config.convention_value(
        "raw_price_round_decimals"
    )
    implied_rate_round_decimals = config.convention_value(
        "implied_rate_round_decimals"
    )
    z_score_round_decimals = config.convention_value("z_score_round_decimals")
    open_interest_round_decimals = config.convention_value(
        "open_interest_round_decimals"
    )
    regime_map = _parse_regime_map(
        config.convention_value("short_rate_regime_map")
    )
    strip_positions = _parse_strip_positions(
        config.convention_value("strip_positions")
    )

    short_rate_regime = regime_map.get(params.curve_family)
    if short_rate_regime is None:
        return {
            "error": (
                f"Policy-futures curve_family={params.curve_family!r} "
                f"has no regime label in the YAML's "
                "``short_rate_regime_map`` convention. Add the entry "
                "(e.g. 'NEW_FAMILY=RFR') alongside the universe "
                "expansion in policy_futures.yml so the methodology "
                "disclosure remains honest (P5)."
            )
        }

    # Resolve field_name sentinels — caller's explicit value wins; None
    # falls through to the YAML default. Mirrors the sibling
    # futures_price_level / volume_open_interest_snapshot resolution.
    price_field_resolved = (
        params.last_price_field_name
        if params.last_price_field_name is not None
        else default_price_field
    )
    oi_field_resolved = (
        params.open_interest_field_name
        if params.open_interest_field_name is not None
        else default_oi_field
    )

    # ------------------------------------------------------------------
    # 1. Future-anchor guard (PR8 + PR16) — per leg.
    #
    # When an explicit ``as_of_date`` is supplied AND lies beyond ANY
    # configured strip position's last observed ``trade_date`` on the
    # PRICE field, return the controlled-error envelope. The price
    # series is the anchor because it is the headline desk read; the
    # OI series is gated separately on the post-cleaning intersection
    # step (defensive — STIR OI is published daily so this should
    # rarely fire in V1).
    # ------------------------------------------------------------------
    requested_as_of: Optional[date] = params.as_of_date
    if requested_as_of is not None:
        for leg_position in strip_positions:
            leg_max = fetch_strip_position_max_date(
                engine=engine,
                curve_family=params.curve_family,
                strip_position=leg_position,
                field_name=price_field_resolved,
            )
            if leg_max is not None and requested_as_of > leg_max:
                return {
                    "error": (
                        f"no scoreable strip: as_of_date="
                        f"{requested_as_of.isoformat()} is beyond the "
                        f"policy-futures universe's last observed "
                        f"trade_date={leg_max.isoformat()} for "
                        f"{params.curve_family} strip_position="
                        f"{leg_position}. The snapshot refuses to "
                        "silently re-label an unbounded read as a "
                        "future-anchored read."
                    )
                }

    # ------------------------------------------------------------------
    # 2. Date window. The fetch buffer is YAML-derived:
    #    z_score_window_days * z_score_buffer_multiplier (calendar-day
    #    buffer for weekends + holidays) + a fixed 365-day buffer for
    #    the displayed-window slack. The snapshot does NOT take a
    #    lookback_days input (the desk-recognised read is the LATEST
    #    snapshot; the rolling-window stats are config-locked) so the
    #    fetch buffer is fixed in conventions.
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * buffer_mult) + 365
    fetch_anchor: Optional[date] = requested_as_of
    if fetch_anchor is None:
        fetch_anchor = date.today()
    start_date = fetch_anchor - timedelta(days=buffer_calendar_days)

    # ------------------------------------------------------------------
    # 3. Per-leg reference metadata (SCD2 as-of lookup). Verifies the
    #    (curve_family, strip_position) pair exists AND surfaces the
    #    per-strip ``inverse_pricing`` flag for the metadata-driven
    #    implied-rate conversion.
    # ------------------------------------------------------------------
    references: dict[int, dict] = {}
    for leg_position in strip_positions:
        reference = fetch_strip_position_reference(
            engine=engine,
            curve_family=params.curve_family,
            strip_position=leg_position,
            as_of_date=fetch_anchor,
        )
        if reference is None:
            return {
                "error": (
                    f"No policy-futures strip slot found for "
                    f"curve_family='{params.curve_family}', "
                    f"strip_position={leg_position}. Verify the "
                    "(curve_family, strip_position) pair exists on "
                    "instrument_master as a strip-position-keyed "
                    "rolling contract (policy_futures.yml universe)."
                )
            }
        references[leg_position] = reference

    inverse_flags: list[bool] = []
    for leg_position in strip_positions:
        raw_flag = references[leg_position].get("inverse_pricing")
        if raw_flag is None:
            return {
                "error": (
                    f"Policy-futures strip {params.curve_family} "
                    f"strip_position={leg_position} is missing the "
                    "'inverse_pricing' flag on "
                    "instrument_master.attributes. The implied-rate "
                    "conversion rule is metadata-driven (PR8 / P6 — "
                    "no hidden methodology in code); a strip without "
                    "this flag cannot be priced honestly. Surface "
                    "this as a metadata gap to the playbook owner."
                )
            }
        inverse_flags.append(bool(raw_flag))

    distinct_flags = set(inverse_flags)
    if len(distinct_flags) > 1:
        per_position = ", ".join(
            f"strip_position={p}: inverse_pricing={f!r}"
            for p, f in zip(strip_positions, inverse_flags)
        )
        return {
            "error": (
                f"Policy-futures strip positions on "
                f"{params.curve_family} disagree on the "
                f"'inverse_pricing' flag ({per_position}). A whole-"
                "strip snapshot requires every configured strip slot "
                "to use the same price-to-rate convention; refusing "
                "to produce a mixed-convention snapshot (P5)."
            )
        }
    inverse_priced: bool = next(iter(distinct_flags))
    quote_units = "100 - rate" if inverse_priced else "rate (%)"

    # ------------------------------------------------------------------
    # 4. Fetch all strip positions' PRICE series in one DB round-trip.
    # ------------------------------------------------------------------
    raw_price_df = fetch_strip_group(
        engine=engine,
        curve_family=params.curve_family,
        strip_positions=list(strip_positions),
        field_name=price_field_resolved,
        start_date=start_date,
    )
    if raw_price_df.empty:
        return {
            "error": (
                f"No policy-futures price data found for "
                f"curve_family='{params.curve_family}', "
                f"strip_positions={list(strip_positions)}, "
                f"field='{price_field_resolved}' since "
                f"{start_date.isoformat()}. Please verify all "
                "configured strip slots have ingested PX_LAST "
                "observations."
            )
        }

    if requested_as_of is not None:
        raw_price_df = raw_price_df[
            pd.to_datetime(raw_price_df["trade_date"])
            <= pd.Timestamp(requested_as_of)
        ]
        if raw_price_df.empty:
            return {
                "error": (
                    f"No policy-futures price data found for "
                    f"curve_family='{params.curve_family}', "
                    f"strip_positions={list(strip_positions)}, "
                    f"field='{price_field_resolved}' on or before "
                    f"as_of_date={requested_as_of.isoformat()}."
                )
            }

    available_positions = set(int(p) for p in raw_price_df["strip_position"].unique())
    missing_positions = set(strip_positions) - available_positions
    if missing_positions:
        return {
            "error": (
                f"Missing strip_position price data for "
                f"{sorted(missing_positions)} in "
                f"curve_family='{params.curve_family}'. Available "
                f"strip_positions in the query window: "
                f"{sorted(available_positions)}."
            )
        }

    # ------------------------------------------------------------------
    # 5. Pivot prices → wide (date × strip_position), align on the
    #    intersection, ffill holiday gaps. Drops rows where ANY
    #    configured strip is still NaN after ffill — the snapshot's
    #    aligned ``as_of_date`` is the last such row.
    # ------------------------------------------------------------------
    wide_prices = pivot_and_align_tenors(
        raw_price_df,
        required_tenors=strip_positions,
        key_col="strip_position",
        ffill_limit=ffill_limit,
    )
    if wide_prices.empty:
        return {
            "error": (
                f"After aligning dates for strip_positions "
                f"{list(strip_positions)} on "
                f"'{params.curve_family}', no overlapping price "
                "observations remain. Check that all strip slots "
                "publish on the same calendar."
            )
        }

    # ------------------------------------------------------------------
    # 6. Fetch open-interest in one round-trip and pivot to wide.
    # ------------------------------------------------------------------
    raw_oi_df = fetch_strip_group(
        engine=engine,
        curve_family=params.curve_family,
        strip_positions=list(strip_positions),
        field_name=oi_field_resolved,
        start_date=start_date,
    )
    if requested_as_of is not None and not raw_oi_df.empty:
        raw_oi_df = raw_oi_df[
            pd.to_datetime(raw_oi_df["trade_date"])
            <= pd.Timestamp(requested_as_of)
        ]

    if raw_oi_df.empty:
        # OI missing is non-fatal — the snapshot still returns price +
        # implied-rate fields and sets open_interest=None per row.
        # Defensive — STIR OI is published daily so this branch should
        # rarely fire in V1.
        wide_oi: Optional[pd.DataFrame] = None
    else:
        wide_oi = pivot_and_align_tenors(
            raw_oi_df,
            required_tenors=strip_positions,
            key_col="strip_position",
            ffill_limit=ffill_limit,
        )
        if wide_oi.empty:
            wide_oi = None

    # ------------------------------------------------------------------
    # 7. Per-leg implied-rate series + per-leg z-score series.
    # ------------------------------------------------------------------
    implied_rates_by_position: dict[int, pd.Series] = {}
    z_scores_by_position: dict[int, pd.Series] = {}
    for leg_position in strip_positions:
        raw_series = wide_prices[leg_position]
        if inverse_priced:
            implied_series = 100.0 - raw_series
        else:
            implied_series = raw_series.copy()
        implied_rates_by_position[leg_position] = implied_series
        z_scores_by_position[leg_position] = rolling_zscore(
            implied_series,
            window=z_window,
            min_periods=z_min_periods,
            ddof=z_ddof,
            round_decimals=z_score_round_decimals,
        )

    # ------------------------------------------------------------------
    # 8. Snapshot aligned ``as_of_date`` = last row in wide_prices.
    # ------------------------------------------------------------------
    as_of_date_resolved = wide_prices.index[-1].date()

    # ------------------------------------------------------------------
    # 9. observation_count — count of aligned trading days within the
    #    rolling z-score window. A desk reader uses this to see whether
    #    the per-leg z-scores are fully populated.
    # ------------------------------------------------------------------
    z_window_cutoff = pd.Timestamp(
        as_of_date_resolved - timedelta(days=int(z_window * buffer_mult))
    )
    observation_count = int(
        (wide_prices.index >= z_window_cutoff).sum()
    )

    # ------------------------------------------------------------------
    # 10. Build per-row outputs.
    # ------------------------------------------------------------------
    row_methodology = _build_row_methodology_card(
        curve_family=params.curve_family,
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        z_window_days=z_window,
    )

    rows: List[FuturesStripSnapshotRow] = []
    for leg_position in strip_positions:
        reference = references[leg_position]
        raw_price_series = wide_prices[leg_position]
        implied_rate_series = implied_rates_by_position[leg_position]
        z_series = z_scores_by_position[leg_position]

        current_raw = safe_float(
            raw_price_series.iloc[-1], decimals=raw_price_round_decimals,
        )
        current_rate = safe_float(
            implied_rate_series.iloc[-1], decimals=implied_rate_round_decimals,
        )
        current_z = safe_float(
            z_series.iloc[-1], decimals=z_score_round_decimals,
        )

        # 1-day raw subtraction on the implied-rate axis. iloc[-1] vs
        # iloc[-daily_change_offset_rows] = 1 trading day back per the
        # YAML's daily_change_offset_rows convention. Same shape every
        # other policy_futures sibling uses.
        daily_change: Optional[float]
        if len(implied_rate_series) >= daily_offset_rows:
            cur = implied_rate_series.iloc[-1]
            prev = implied_rate_series.iloc[-daily_offset_rows]
            if pd.isna(cur) or pd.isna(prev):
                daily_change = None
            else:
                daily_change = round(
                    float(cur) - float(prev),
                    implied_rate_round_decimals,
                )
        else:
            daily_change = None

        # Open interest — look up by aligned date if available.
        open_interest_value: Optional[float] = None
        if wide_oi is not None and leg_position in wide_oi.columns:
            oi_series = wide_oi[leg_position]
            try:
                oi_at_anchor = oi_series.loc[
                    pd.Timestamp(as_of_date_resolved)
                ]
            except KeyError:
                # OI series doesn't have the exact aligned date —
                # fall back to the latest OI on or before the
                # snapshot's as_of_date.
                truncated = oi_series.loc[
                    oi_series.index <= pd.Timestamp(as_of_date_resolved)
                ]
                if truncated.empty:
                    oi_at_anchor = float("nan")
                else:
                    oi_at_anchor = truncated.iloc[-1]
            if pd.isna(oi_at_anchor):
                open_interest_value = None
            else:
                open_interest_value = round(
                    float(oi_at_anchor), open_interest_round_decimals,
                )

        expiry_raw = reference.get("expiry_date")
        expiry_str: Optional[str]
        if expiry_raw is None:
            expiry_str = None
        elif isinstance(expiry_raw, str):
            expiry_str = expiry_raw
        elif isinstance(expiry_raw, date):
            expiry_str = expiry_raw.strftime("%Y-%m-%d")
        else:
            expiry_str = str(expiry_raw)

        contract_size_raw = reference.get("contract_size")

        rows.append(
            FuturesStripSnapshotRow(
                strip_position=leg_position,
                contract_code=reference["contract_code"],
                underlying_contract_code=reference.get(
                    "underlying_contract_code"
                ),
                security_name=reference.get("security_name"),
                expiry_date=expiry_str,
                contract_size=(
                    float(contract_size_raw)
                    if contract_size_raw is not None
                    else None
                ),
                raw_price=current_raw,
                implied_rate_pct=current_rate,
                daily_change_implied_rate_pct=daily_change,
                z_score_implied_rate=current_z,
                open_interest=open_interest_value,
                row_methodology_card=row_methodology,
            )
        )

    methodology_disclosure = _build_methodology_disclosure(
        curve_family=params.curve_family,
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        z_window_days=z_window,
        strip_positions=strip_positions,
    )

    output = FuturesStripSnapshotOutput(
        as_of_date=as_of_date_resolved.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        inverse_priced=inverse_priced,
        short_rate_regime=short_rate_regime,
        quote_units=quote_units,
        strip_positions=list(strip_positions),
        snapshot=rows,
        observation_count=observation_count,
        methodology_disclosure=methodology_disclosure,
    )
    return output.model_dump()
