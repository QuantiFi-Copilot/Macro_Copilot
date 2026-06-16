"""
compute.py — INGEST primitive: per-meeting WIRP pricing snapshot
=================================================================

For one central bank (FOMC / ECB / BOE / BOJ), pulls the latest
WIRP snapshot per scheduled meeting and surfaces ONLY the raw
Bloomberg-ingested fields verbatim (P12 boundary per ADR 0009 §1):

  - implied_policy_rate_pct   ← WIRP_IMPLIED_RATE
  - cumulative_move_prob_pct  ← WIRP_MOVE_PROB (CUMULATIVE — see
                                disclosure block below)
  - num_25bp_moves_priced     ← WIRP_NUM_MOVES
  - rate_change_native        ← WIRP_RATE_CHANGE (percentage points,
                                Bloomberg native units)

Plus per-field Bloomberg-ticker provenance from
``instrument_master.attributes`` per ADR 0009 §1.

NO derived hike / cut / hold probability fields are emitted.  See
the Codex P0 correction block below — WIRP_MOVE_PROB is cumulative,
and any single-event identity would be empirically wrong.

P12 (Bloomberg Accuracy Boundary) — MANDATORY disclosure per the brief
---------------------------------------------------------------------
This is the load-bearing P12 boundary the brief calls out
explicitly: WIRP is INGESTED verbatim from Bloomberg's WIRP screen
via ADR 0009.  This primitive does NOT recompute the implied rate
from STIR futures, OIS swaps, or any other source.  All four
Bloomberg fields (WIRP_IMPLIED_RATE / WIRP_MOVE_PROB /
WIRP_NUM_MOVES / WIRP_RATE_CHANGE) are surfaced as-is.

Codex P0 correction (PR #190 review)
------------------------------------
The original draft of this primitive emitted derived hike / hold /
cut probability fields using the identity
``hike = max(p, 0)``, ``cut = max(-p, 0)``, ``hold = 100 - |p|``
on WIRP_MOVE_PROB.  Codex correctly identified that this identity
DOES NOT hold: ``rates_agent/playbooks/wirp.yml`` documents that
WIRP_MOVE_PROB is **CUMULATIVE** (observed range -360.1 .. 548.0),
not a single-event probability bounded in [0, 100].  Empirically
BOE 2025-05-08 = -104.9% → the derivation produced
hold_prob = -4.9% (impossible).

The derived fields have been removed entirely.  The primitive
surfaces only the four raw Bloomberg fields, with the
``cumulative_move_prob_pct`` name making the cumulative semantics
loud on the wire.  A correct hike-step probability surface would
require a verified joint-distribution mapping between WIRP_NUM_MOVES
and WIRP_MOVE_PROB (an AC8 design question deferred to a
follow-up primitive — documented in methodology.planned_extensions).

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
the wire field names in the output schema
(``cumulative_move_prob_pct``, ``rate_change_native``, etc.) embed
the unit / semantics convention and a field-rename would silently
lie.

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
from shared.analytics.rates_fetch import (
    fetch_wirp_meeting_snapshots,
    latest_trade_date,
)
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
    "WIRP_RATE_CHANGE) are surfaced verbatim.  "
    "IMPORTANT: WIRP_MOVE_PROB is CUMULATIVE (per "
    "rates_agent/playbooks/wirp.yml — observed live-DB range "
    "-360.1 .. 548.0%), NOT a single-event probability bounded in "
    "[0, 100].  Values can exceed ±100 when the market prices "
    "more than one 25bp move.  Naive single-event-probability "
    "interpretations (hike/hold/cut decomposition by "
    "``max(p, 0)`` / ``max(-p, 0)`` / ``100 - |p|``) DO NOT HOLD "
    "and produce impossible values — this primitive therefore "
    "SURFACES the raw value as ``cumulative_move_prob_pct`` and "
    "DOES NOT emit derived hike / hold / cut probability fields.  "
    "WIRP_NUM_MOVES is similarly the signed count of 25bp moves "
    "priced (observed -9.57 .. 6.729) and can be beyond ±1.  "
    "WIRP_RATE_CHANGE is in PERCENTAGE POINTS per wirp.yml's "
    "Stage-B verification (observed -2.392 .. 1.013; the bp "
    "vs pct ambiguity ADR 0009 v2 disclaimed was resolved by the "
    "probe).  Field name retains the ``_native`` suffix because "
    "no conversion is applied — Bloomberg's value is stored "
    "verbatim per P12."
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
    # Pull conventions for rounding + selection + horizon (YAML-locked
    # per Codex P3 finding — wirp.yml's Stage-B-verified horizons
    # mirrored into config.yaml so the primitive's behaviour is
    # entirely YAML-discoverable, not buried in a code constant).
    # ------------------------------------------------------------------
    default_n_meetings = int(config.convention_value("default_n_meetings"))
    rate_round = int(config.convention_value("rate_round_decimals"))
    prob_round = int(config.convention_value("prob_round_decimals"))
    num_moves_round = int(config.convention_value("num_moves_round_decimals"))
    forward_horizon_days = int(config.convention_value("forward_horizon_days"))
    past_horizon_days = int(config.convention_value("past_horizon_days"))

    # ------------------------------------------------------------------
    # Resolve the as-of observation anchor (optional historical replay).
    #
    #   anchor = as_of_date  →  latest_trade_date(wirp_meeting)  →  today
    #
    # The anchor serves two roles:
    #   1. ``end_date=anchor`` caps the WIRP OBSERVATION trade_date in the
    #      fetcher's DISTINCT ON, so the per-meeting snapshot is the latest
    #      read on or before the anchor (deterministic replay).
    #   2. For ``next_n_meetings`` it becomes the reference "today" for the
    #      forward meeting-date window, so a historical as_of view returns
    #      the meetings that were forward AS OF that date.
    #
    # ZERO-REGRESSION: when as_of_date is None, anchor resolves to the
    # latest available trade_date (the global wirp_meeting max), so
    # ``end_date=anchor`` drops zero rows (no observation is newer than the
    # latest) and the forward window's reference stays at the current
    # trade-date head — behaviour is byte-identical to the prior
    # ``date.today()`` window for the live snapshot.  Under the offline
    # unit tests (mocked fetcher, MagicMock/None engine) the probe returns
    # None and the anchor falls back to the patched ``date.today()``.
    # ------------------------------------------------------------------
    anchor = (
        params.as_of_date
        or latest_trade_date(engine, instrument_type="wirp_meeting")
        or date.today()
    )

    # ------------------------------------------------------------------
    # Determine the meeting-date filter window from selection_mode
    # ------------------------------------------------------------------
    today = params.as_of_date or date.today()
    if params.selection_mode == "next_n_meetings":
        # Forward window — from today to ``forward_horizon_days`` per
        # config (sourced from wirp.yml's Stage-B-verified band per
        # ADR 0009 §3).  YAML-locked horizon — no hidden constants.
        earliest_meeting_date = today
        latest_meeting_date = today + timedelta(days=forward_horizon_days)
        effective_n_meetings = (
            params.n_meetings if params.n_meetings is not None
            else default_n_meetings
        )
        requested_meeting_date_iso: Optional[str] = None
    else:
        # specific_meeting_date — exact single-day window.  The
        # ``past_horizon_days`` convention is not used here (the
        # window collapses to a single date by design), but it
        # bounds the supported-date range a future "any meeting" mode
        # would respect.
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
        end_date=anchor,
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
    # Build the meetings list — one WirpMeetingSnapshot per row.
    # All four Bloomberg fields surfaced verbatim (P12).  NO derived
    # hike/hold/cut probabilities are emitted — per the Codex P0
    # finding (PR #190), WIRP_MOVE_PROB is CUMULATIVE and a naive
    # single-event-probability decomposition does not hold.  See
    # the module docstring's "Codex P0 correction" section + the
    # methodology_note's CUMULATIVE disclosure.
    # ------------------------------------------------------------------
    meetings: List[WirpMeetingSnapshot] = []
    for row in display.itertuples():
        implied_rate = _safe_float(getattr(row, _SUPPORTED_IMPLIED_RATE_FIELD), rate_round)
        cumulative_prob = _safe_float(getattr(row, _SUPPORTED_MOVE_PROB_FIELD), prob_round)
        num_moves = _safe_float(getattr(row, _SUPPORTED_NUM_MOVES_FIELD), num_moves_round)
        rate_change = _safe_float(getattr(row, _SUPPORTED_RATE_CHANGE_FIELD), rate_round)

        meetings.append(WirpMeetingSnapshot(
            central_bank=str(row.central_bank),
            meeting_date=_iso_date(row.meeting_date),
            meeting_token=_str_or_none(row.meeting_token),
            as_of_date=_iso_date(row.as_of_date),
            implied_policy_rate_pct=implied_rate,
            cumulative_move_prob_pct=cumulative_prob,
            num_25bp_moves_priced=num_moves,
            rate_change_native=rate_change,
            vendor_ticker=str(row.vendor_ticker),
            bloomberg_ticker_implied_rate=_str_or_none(row.bloomberg_ticker_fr),
            bloomberg_ticker_move_prob=_str_or_none(row.bloomberg_ticker_pr),
            bloomberg_ticker_num_moves=_str_or_none(row.bloomberg_ticker_nm),
            bloomberg_ticker_rate_change=_str_or_none(row.bloomberg_ticker_ch),
        ))

    # ------------------------------------------------------------------
    # Build current_metrics from the first meeting.  Convenience
    # snapshot fields mirror the four raw Bloomberg fields of the
    # next-up (or requested) meeting.  No derived hike/hold/cut.
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
        next_cumulative_move_prob_pct=first.cumulative_move_prob_pct,
        next_num_25bp_moves_priced=first.num_25bp_moves_priced,
        next_rate_change_native=first.rate_change_native,
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
