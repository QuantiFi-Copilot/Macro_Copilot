"""
compute.py — INGEST primitive: per-meeting WIRP pricing snapshot
=================================================================

For one central bank (FOMC / ECB / BOE / BOJ), pulls the latest
WIRP snapshot per scheduled meeting and surfaces:

  - Bloomberg-ingested fields (INGEST per ADR 0009 §1, P12 boundary):
      * implied_policy_rate_pct  ← WIRP_IMPLIED_RATE
      * signed_move_prob_pct      ← WIRP_MOVE_PROB
      * num_25bp_moves_priced     ← WIRP_NUM_MOVES
      * rate_change_native        ← WIRP_RATE_CHANGE (native units)

  - Desk-recognised IDENTITY-derived fields from the signed move
    probability (NOT recomputed quantities — the single signed
    Bloomberg field carries the information; the derivation is
    arithmetic by definition):
      * hike_prob_pct = max(signed_move_prob_pct, 0)
      * cut_prob_pct  = max(-signed_move_prob_pct, 0)
      * hold_prob_pct = 100 - |signed_move_prob_pct|

  - Per-field Bloomberg-ticker provenance from
    instrument_master.attributes per ADR 0009 §1.

P12 (Bloomberg Accuracy Boundary) — MANDATORY disclosure per the brief
---------------------------------------------------------------------
This is the load-bearing P12 boundary the brief calls out
explicitly: WIRP is INGESTED verbatim from Bloomberg's WIRP screen
via ADR 0009.  This primitive does NOT recompute the implied rate
from STIR futures, OIS swaps, or any other source.  The four
Bloomberg fields are surfaced as-is; the hike/cut/hold derivation
is the desk-recognised IDENTITY interpretation of Bloomberg's
single signed WIRP_MOVE_PROB, not a recomputation of a different
Bloomberg quantity.  ``methodology_note`` surfaces all of this at
the user-facing layer (PR10).

P8 closed-family — central_bank set wire-locked
-----------------------------------------------
``supported_central_banks`` is a YAML-locked comma-separated string
(``"FOMC,ECB,BOE,BOJ"``).  Compute splits on comma and compares
against the input; any other value returns an error envelope
listing the supported set (no silent fall-through, P6).

PR14 — wire-format honesty
--------------------------
The four ``*_field`` conventions
(``implied_rate_field``, ``move_prob_field``, ``num_moves_field``,
``rate_change_field``) are categorical conventions whose accepted
values are wire-frozen.  Each is guarded with NotImplementedError
if the YAML value drifts from the ADR-0009 metric → field mapping;
the wire field names in the output schema (``hike_prob_pct``,
``rate_change_native``, etc.) embed the unit / derivation
convention and a field-rename would silently lie.

Test seam
---------
Tests patch ``fetch_wirp_meeting_snapshots`` and ``date`` at this
module's namespace (``...wirp_meeting_pricing.compute.X``) — same
pattern as the other event-derived primitives.  The package
``__init__.py`` re-exports only public symbols.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.wirp_meeting_pricing.schemas import (
    WirpMeetingPricingCurrentMetrics,
    WirpMeetingPricingInput,
    WirpMeetingPricingOutput,
    WirpMeetingSnapshot,
)
from shared.analytics.rates_fetch import fetch_wirp_meeting_snapshots
from shared.config import ToolConfig, load_tool_config


# Bundled config — relative to this file.  Public symbol so external
# callers (mcp_server, REST routes, tests, parity-fixture capture)
# can build a ToolConfig from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Per PR14: each of the four WIRP field-name conventions is wire-
# frozen.  The accepted values mirror ADR 0009 §1's metric → field
# mapping exactly.  A YAML edit that drifts from these would silently
# read a non-existent column from market_data_daily and return empty
# results — the NotImplementedError guard makes the breakage loud.
_SUPPORTED_IMPLIED_RATE_FIELD: str = "WIRP_IMPLIED_RATE"
_SUPPORTED_MOVE_PROB_FIELD: str = "WIRP_MOVE_PROB"
_SUPPORTED_NUM_MOVES_FIELD: str = "WIRP_NUM_MOVES"
_SUPPORTED_RATE_CHANGE_FIELD: str = "WIRP_RATE_CHANGE"


# Per PR10: methodology_note surfaces the MANDATORY P5 disclosure
# from the brief at the user-facing layer.  String literal (structural
# constant, unchanged across calls); keeping it here in code matches
# PR7's "structural constants in code" — the YAML disclosure lives in
# methodology.assumptions / citations; this constant is the runtime
# user-facing echo of those.
_METHODOLOGY_NOTE: str = (
    "Source: Bloomberg WIRP screen as ingested per ADR 0009 (D-wirp "
    "playbook → macro_data.market_data_daily, instrument_type = "
    "'wirp_meeting').  NOT recomputed from STIR futures or OIS — "
    "this is an INGEST primitive (P12 boundary): the four WIRP "
    "fields (WIRP_IMPLIED_RATE / WIRP_MOVE_PROB / WIRP_NUM_MOVES / "
    "WIRP_RATE_CHANGE) are surfaced verbatim.  WIRP definition of "
    "hike-probability anchored at consensus 25bp move: Bloomberg's "
    "WIRP_MOVE_PROB is the SIGNED probability of a single 25bp "
    "move (+ = hike, − = cut, in percent).  The separate hike / "
    "cut / hold probabilities surfaced here are IDENTITY-derived "
    "from this single signed field: hike_prob = max(p, 0), "
    "cut_prob = max(-p, 0), hold_prob = 100 - |p|.  This is NOT "
    "a recomputation of a Bloomberg-supplied quantity; it is the "
    "desk's standard interpretation of WIRP's single signed "
    "probability.  ``rate_change_native`` is in NATIVE Bloomberg "
    "units per ADR 0009 §1 — the unit (bp vs percent vs decimal) "
    "is NOT confirmed and no conversion is applied; downstream "
    "consumers must NOT assume the unit and should consult the "
    "Stage-B observed-value-range disclosure when it lands."
)


# ============================================================================
# CONVENTION VALIDATION
# ============================================================================

def _validate_conventions(config: ToolConfig) -> None:
    """Refuse loudly when a wire-frozen convention is set to a value
    V1 does not yet support (PR14 + PR11)."""
    field_checks = (
        ("implied_rate_field", _SUPPORTED_IMPLIED_RATE_FIELD),
        ("move_prob_field", _SUPPORTED_MOVE_PROB_FIELD),
        ("num_moves_field", _SUPPORTED_NUM_MOVES_FIELD),
        ("rate_change_field", _SUPPORTED_RATE_CHANGE_FIELD),
    )
    for convention_key, supported in field_checks:
        actual = config.convention_value(convention_key)
        if actual != supported:
            raise NotImplementedError(
                f"{convention_key}={actual!r} is wire-frozen at "
                f"{supported!r} per ADR 0009 §1's metric → field "
                f"mapping.  Changing this convention without an "
                f"accompanying playbook + schema migration would "
                f"silently read the wrong column from "
                f"market_data_daily.  See "
                f"methodology.planned_extensions for the path to "
                f"widening."
            )


def _supported_central_banks(config: ToolConfig) -> List[str]:
    """Parse the comma-separated YAML convention into a list.

    Scalar-only Convention values force this shape (same pattern as
    the brief's other "single string of comma-separated values"
    expressions across the catalogue).
    """
    raw = str(config.convention_value("supported_central_banks"))
    return [s.strip() for s in raw.split(",") if s.strip()]


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_wirp_meeting_pricing(
    engine: Engine,
    params: WirpMeetingPricingInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """
    Compute the per-meeting WIRP pricing snapshot for one central
    bank.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        Live SQLAlchemy engine.
    params : WirpMeetingPricingInput
        Validated input.  ``central_bank`` is the instrument
        selector; ``selection_mode`` + dependent ``n_meetings`` /
        ``meeting_date`` form the cohesive central methodology
        surface (PR8).
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None.  Tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``WirpMeetingPricingOutput`` with
        ``current_metrics``, ``meetings`` (list of snapshots), and
        the MANDATORY ``methodology_note``.

        On unsupported central_bank or empty result, returns a
        controlled ``{"error": "..."}`` envelope.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    _validate_conventions(config)

    # ------------------------------------------------------------------
    # Validate central_bank against the YAML-locked supported set
    # ------------------------------------------------------------------
    supported = _supported_central_banks(config)
    if params.central_bank not in supported:
        return {
            "error": (
                f"Unsupported central_bank={params.central_bank!r}.  "
                f"Supported set (per ``supported_central_banks`` YAML "
                f"convention + ADR 0009 §2 region prefixes): "
                f"{supported}.  Adding a central bank requires "
                f"extending rates_agent/playbooks/wirp.yml AND the "
                f"YAML convention in the same PR."
            )
        }

    # ------------------------------------------------------------------
    # Pull conventions for rounding + selection
    # ------------------------------------------------------------------
    default_n_meetings = int(config.convention_value("default_n_meetings"))
    rate_round = int(config.convention_value("rate_round_decimals"))
    prob_round = int(config.convention_value("prob_round_decimals"))
    num_moves_round = int(config.convention_value("num_moves_round_decimals"))

    # ------------------------------------------------------------------
    # Determine the meeting-date filter window from selection_mode
    # ------------------------------------------------------------------
    today = date.today()
    if params.selection_mode == "next_n_meetings":
        # Forward window — from today to a far enough horizon to
        # capture n_meetings.  ADR 0009 caps the horizon at the
        # Stage-B verified band; 1500 days ≈ 4 years is past any
        # reasonable WIRP horizon so the fetch will return at most
        # all available meetings.
        earliest_meeting_date = today
        latest_meeting_date = today + timedelta(days=1500)
        effective_n_meetings = (
            params.n_meetings if params.n_meetings is not None
            else default_n_meetings
        )
        requested_meeting_date_iso: Optional[str] = None
    else:
        # specific_meeting_date — exact single-day window
        assert params.meeting_date is not None  # enforced by @model_validator
        earliest_meeting_date = params.meeting_date
        latest_meeting_date = params.meeting_date
        effective_n_meetings = None
        requested_meeting_date_iso = params.meeting_date.isoformat()

    # ------------------------------------------------------------------
    # Fetch via the shared analytics helper — returns one row per
    # (instrument_id, field_name) at the latest trade_date.
    # ------------------------------------------------------------------
    raw_df = fetch_wirp_meeting_snapshots(
        engine=engine,
        central_bank=params.central_bank,
        earliest_meeting_date=earliest_meeting_date,
        latest_meeting_date=latest_meeting_date,
    )

    if raw_df.empty:
        if params.selection_mode == "specific_meeting_date":
            return {
                "error": (
                    f"No WIRP meeting found for central_bank="
                    f"{params.central_bank!r}, meeting_date="
                    f"{requested_meeting_date_iso!r}.  Either the "
                    f"date does not match a scheduled meeting in "
                    f"instrument_master (synthetic ``wirp_meeting`` "
                    f"rows per ADR 0009 §1), or the meeting is "
                    f"outside the Stage-B-verified WIRP horizon "
                    f"(ADR 0009 §3)."
                )
            }
        return {
            "error": (
                f"No forward WIRP meetings ingested for central_bank="
                f"{params.central_bank!r} from {today.isoformat()} "
                f"forward.  The WIRP horizon (ADR 0009 §3) may not "
                f"have advanced yet, or the D-wirp playbook has not "
                f"been run for this central bank."
            )
        }

    # ------------------------------------------------------------------
    # Pivot to wide: one row per (instrument_id, meeting_date) with
    # one column per WIRP field.  The fetcher returns one row per
    # (instrument_id, field_name) so the pivot is a clean wide-format
    # transform.
    # ------------------------------------------------------------------
    df = raw_df.copy()
    # Coerce numeric (Decimal → float).
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df["meeting_date"] = pd.to_datetime(df["meeting_date"]).dt.date

    # Pivot: index=instrument_id + identity cols, columns=field_name, values=field_value
    pivot_index_cols = [
        "instrument_id",
        "vendor_ticker",
        "meeting_date",
        "central_bank",
        "meeting_token",
        "bloomberg_ticker_fr",
        "bloomberg_ticker_pr",
        "bloomberg_ticker_nm",
        "bloomberg_ticker_ch",
    ]
    # Each meeting has up to 4 distinct as_of_dates (one per field) —
    # but the fetcher's DISTINCT ON gives the latest per field.  Most
    # meetings share the same as_of_date across all four fields; we
    # surface the MAX(as_of_date) per meeting so the snapshot
    # honestly reflects "latest available for any field".  An
    # alternative is per-field as_of_date in the output; deferred to
    # a planned extension.
    wide_value = df.pivot_table(
        index=pivot_index_cols,
        columns="field_name",
        values="field_value",
        aggfunc="first",
    ).reset_index()
    wide_asof = df.groupby(pivot_index_cols)["as_of_date"].max().reset_index()
    wide_asof["as_of_date"] = pd.to_datetime(wide_asof["as_of_date"]).dt.date
    wide = wide_value.merge(wide_asof, on=pivot_index_cols, how="left")

    # Ensure expected columns exist even if a meeting is missing one
    # field (the available=false opt-out shape — ADR 0009 §4).
    for col in (
        _SUPPORTED_IMPLIED_RATE_FIELD,
        _SUPPORTED_MOVE_PROB_FIELD,
        _SUPPORTED_NUM_MOVES_FIELD,
        _SUPPORTED_RATE_CHANGE_FIELD,
    ):
        if col not in wide.columns:
            wide[col] = pd.NA

    wide = wide.sort_values("meeting_date").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Apply selection_mode trimming
    # ------------------------------------------------------------------
    if params.selection_mode == "next_n_meetings":
        # Already filtered to today-forward in the fetcher; just take
        # the first n_meetings (sorted ascending).
        display = wide.head(effective_n_meetings).copy()
    else:
        # specific_meeting_date — the fetcher returned exactly one
        # meeting; display is the whole frame.
        display = wide.copy()

    if display.empty:
        return {
            "error": (
                f"No matching meetings after trim for "
                f"central_bank={params.central_bank!r}, "
                f"selection_mode={params.selection_mode!r}."
            )
        }

    # ------------------------------------------------------------------
    # Build the meetings list — one WirpMeetingSnapshot per row
    # ------------------------------------------------------------------
    meetings: List[WirpMeetingSnapshot] = []
    for row in display.itertuples():
        implied_rate = _safe_float(getattr(row, _SUPPORTED_IMPLIED_RATE_FIELD), rate_round)
        signed_prob = _safe_float(getattr(row, _SUPPORTED_MOVE_PROB_FIELD), prob_round)
        num_moves = _safe_float(getattr(row, _SUPPORTED_NUM_MOVES_FIELD), num_moves_round)
        rate_change = _safe_float(getattr(row, _SUPPORTED_RATE_CHANGE_FIELD), rate_round)

        # Identity-derived hike/cut/hold from signed_prob.  None
        # propagates honestly when signed_prob is None.
        if signed_prob is None:
            hike_prob = None
            cut_prob = None
            hold_prob = None
        else:
            hike_prob = round(max(signed_prob, 0.0), prob_round)
            cut_prob = round(max(-signed_prob, 0.0), prob_round)
            hold_prob = round(100.0 - abs(signed_prob), prob_round)

        meetings.append(WirpMeetingSnapshot(
            central_bank=str(row.central_bank),
            meeting_date=_iso_date(row.meeting_date),
            meeting_token=_str_or_none(row.meeting_token),
            as_of_date=_iso_date(row.as_of_date),
            implied_policy_rate_pct=implied_rate,
            signed_move_prob_pct=signed_prob,
            num_25bp_moves_priced=num_moves,
            rate_change_native=rate_change,
            hike_prob_pct=hike_prob,
            cut_prob_pct=cut_prob,
            hold_prob_pct=hold_prob,
            vendor_ticker=str(row.vendor_ticker),
            bloomberg_ticker_implied_rate=_str_or_none(row.bloomberg_ticker_fr),
            bloomberg_ticker_move_prob=_str_or_none(row.bloomberg_ticker_pr),
            bloomberg_ticker_num_moves=_str_or_none(row.bloomberg_ticker_nm),
            bloomberg_ticker_rate_change=_str_or_none(row.bloomberg_ticker_ch),
        ))

    # ------------------------------------------------------------------
    # Build current_metrics from the first meeting
    # ------------------------------------------------------------------
    first = meetings[0]
    current_metrics = WirpMeetingPricingCurrentMetrics(
        central_bank=params.central_bank,
        selection_mode=params.selection_mode,
        n_meetings_returned=len(meetings),
        n_meetings_requested=effective_n_meetings,
        requested_meeting_date=requested_meeting_date_iso,
        next_meeting_date=first.meeting_date,
        next_implied_policy_rate_pct=first.implied_policy_rate_pct,
        next_signed_move_prob_pct=first.signed_move_prob_pct,
        next_hike_prob_pct=first.hike_prob_pct,
        next_cut_prob_pct=first.cut_prob_pct,
        next_hold_prob_pct=first.hold_prob_pct,
        next_as_of_date=first.as_of_date,
    )

    output = WirpMeetingPricingOutput(
        current_metrics=current_metrics,
        meetings=meetings,
        methodology_note=_METHODOLOGY_NOTE,
    )
    return output.model_dump()


# ============================================================================
# Small typed coercions
# ============================================================================

def _iso_date(value: Any) -> str:
    """Coerce ``date`` / ``Timestamp`` / ISO string to canonical
    ``YYYY-MM-DD``.  Required value; raises TypeError on None."""
    if value is None:
        raise TypeError(
            "date column is NOT NULL in macro_data.instrument_master "
            "/ market_data_daily — None should never reach this coercion."
        )
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def _str_or_none(value: Any) -> Optional[str]:
    """Coerce nullable string columns to Optional[str]."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s if s else None


def _safe_float(value: Any, decimals: int) -> Optional[float]:
    """Coerce a nullable numeric to Optional[float] rounded to
    ``decimals``.  None / NaN → None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return round(f, decimals)
