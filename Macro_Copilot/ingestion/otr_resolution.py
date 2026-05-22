"""ingestion/otr_resolution.py — pure-Python helpers for the OTR-resolution
ingestion flow (work order A4-4 resolver, ADR 0007).

The incremental extractor emits a SEPARATE resolution artifact (one row per
(country, tenor) slot, parquet under ``gs://<bucket>/otr_resolution/``). Local
ingestion folds that artifact into ``macro_data.otr_history`` (SCD2). This
module holds the two pieces of that flow that are pure functions of their
inputs — no Bloomberg, no GCS, no DB — so they are unit-testable in isolation:

  * :func:`parquet_to_resolution_records` — parse the resolution parquet into
    clean per-slot observation dicts (dates normalised to ISO strings).
  * :func:`plan_otr_transition` — the false-roll-protected SCD2 state machine.
    Given the current open ``otr_history`` row for a slot and one resolver
    observation, decide what (if anything) to write. The I/O shell in
    ``ingest_parquet._process_otr_resolution_blob`` applies the returned plan
    via the existing ``upsert_otr_history`` / ``close_open_otr_window`` /
    ``upsert_instrument_master`` helpers inside one transaction.

Design rationale (ADR 0007):
  * The resolver writes DATA, never the git playbook. The OTR mapping over
    time is data and lives in ``otr_history``.
  * false-roll protection: a roll (close the open OTR window, open the next)
    is recorded only after ``confirmation_runs`` resolver observations on
    DISTINCT dates agree on the same new ISIN — one transient bad Bloomberg
    ``bdp`` print can never close an OTR window. The unconfirmed candidate is
    parked in the open row's ``attributes`` JSONB (``pending_candidate``); no
    schema change, no staging table.
  * The first observation of a slot with no history is a BOOTSTRAP insert — it
    establishes the baseline and closes nothing, so it is not confirmation-
    gated. A bad bootstrap self-heals: the next genuine ISIN reads as a
    candidate and rolls to the correct bond once confirmed.

Dates are handled as ISO ``YYYY-MM-DD`` strings throughout the planner —
they sort lexicographically == chronologically, so ordering comparisons need
no date parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

# ============================================================================
# Plan actions — the closed set of decisions plan_otr_transition can return.
# ============================================================================
ACTION_BOOTSTRAP = "BOOTSTRAP"              # no open window — insert the baseline
ACTION_NO_CHANGE = "NO_CHANGE"              # resolved bond already the open OTR
ACTION_CLEAR_CANDIDATE = "CLEAR_CANDIDATE"  # resolver reverted — drop unconfirmed candidate
ACTION_RECORD_CANDIDATE = "RECORD_CANDIDATE"  # candidate seen, not yet confirmed
ACTION_CONFIRM_ROLL = "CONFIRM_ROLL"        # candidate confirmed — close + open
ACTION_SKIP_STALE = "SKIP_STALE"            # observation predates recorded state

# Columns the resolution parquet is expected to carry (per-slot row shape).
RESOLUTION_COLUMNS = (
    "slot_id",
    "generic_ticker",
    "country",
    "currency",
    "curve_family",
    "tenor",
    "resolved_isin",
    "resolved_cusip",
    "security_name",
    "coupon",
    "maturity_date",
    "issue_date",
    "instrument_ticker",
    "resolution_date",
    "resolution_timestamp",
    "status",
    "error",
)


@dataclass
class OtrTransitionPlan:
    """The decision returned by :func:`plan_otr_transition`.

    ``action`` is one of the ``ACTION_*`` constants above. The remaining
    fields are populated only for the actions that need them:

      * BOOTSTRAP / CONFIRM_ROLL  -> ``new_effective_from``,
        ``new_otr_instrument_id``, ``new_attributes`` describe the new open
        ``otr_history`` row to insert.
      * CONFIRM_ROLL              -> ``close_prior_at`` is the effective_from
        of the new window; the prior open window is closed at
        ``close_prior_at - 1 day`` by ``close_open_otr_window``.
      * RECORD_CANDIDATE / CLEAR_CANDIDATE -> ``open_row_attributes`` is the
        full new ``attributes`` JSONB to stamp back onto the (still-open) row.
    """

    action: str
    reason: str
    new_effective_from: Optional[str] = None
    new_otr_instrument_id: Optional[int] = None
    new_attributes: Optional[Dict[str, Any]] = None
    close_prior_at: Optional[str] = None
    open_row_attributes: Optional[Dict[str, Any]] = None


# ============================================================================
# Parquet parsing
# ============================================================================
def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_iso_date(value: Any) -> Optional[str]:
    """Normalise any date-ish value (Timestamp, str, date) to ``YYYY-MM-DD``.

    Returns ``None`` for missing / unparseable values rather than raising —
    the caller treats an observation with a missing required date as
    non-ingestable, which is honest rather than a hard failure.
    """
    if value is None:
        return None
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    ts = pd.to_datetime(value, errors="coerce")
    if ts is None or pd.isna(ts):
        return None
    return ts.date().isoformat()


def parquet_to_resolution_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Parse a resolution-artifact DataFrame into per-slot observation dicts.

    One dict per row, dates normalised to ISO strings, ``status`` lower-cased.
    Every row is returned (including ``status != 'ok'`` rows) so the caller can
    log a complete ok/failed/rejected breakdown; :func:`is_ingestable_observation`
    is the gate for which rows actually drive an ``otr_history`` write.
    """
    records: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        rec: Dict[str, Any] = {
            "slot_id": _str_or_none(row.get("slot_id")),
            "generic_ticker": _str_or_none(row.get("generic_ticker")),
            "country": _str_or_none(row.get("country")),
            "currency": _str_or_none(row.get("currency")),
            "curve_family": _str_or_none(row.get("curve_family")),
            "tenor": _str_or_none(row.get("tenor")),
            "resolved_isin": _str_or_none(row.get("resolved_isin")),
            "resolved_cusip": _str_or_none(row.get("resolved_cusip")),
            "security_name": _str_or_none(row.get("security_name")),
            "coupon": _float_or_none(row.get("coupon")),
            "maturity_date": _to_iso_date(row.get("maturity_date")),
            "issue_date": _to_iso_date(row.get("issue_date")),
            "instrument_ticker": _str_or_none(row.get("instrument_ticker")),
            "resolution_date": _to_iso_date(row.get("resolution_date")),
            "status": (_str_or_none(row.get("status")) or "").lower(),
            "error": _str_or_none(row.get("error")),
        }
        # Defence in depth: derive the canonical /isin/ ticker if the extractor
        # did not stamp it. Keeps instrument_master identity consistent with
        # the market-data universe convention (sovereign_cash_bonds.yml).
        if not rec["instrument_ticker"] and rec["resolved_isin"]:
            rec["instrument_ticker"] = f"/isin/{rec['resolved_isin']}"
        records.append(rec)
    return records


def is_ingestable_observation(rec: Dict[str, Any]) -> bool:
    """True iff a parsed observation carries everything an ``otr_history``
    write needs. A row that is ``status != 'ok'`` or missing a required field
    is skipped — honest non-action, never a guess."""
    if rec.get("status") != "ok":
        return False
    required = ("country", "tenor", "resolved_isin", "instrument_ticker", "resolution_date")
    return all(rec.get(key) for key in required)


# ============================================================================
# The false-roll-protected SCD2 state machine
# ============================================================================
def plan_otr_transition(
    open_row: Optional[Dict[str, Any]],
    resolution_date: str,
    resolved_instrument_id: int,
    resolved_isin: str,
    confirmation_runs: int = 2,
) -> OtrTransitionPlan:
    """Decide what to write to ``otr_history`` for one slot, given one
    resolver observation.

    Parameters
    ----------
    open_row
        The currently-open ``otr_history`` row for this (country, tenor) slot
        as a dict (keys ``otr_instrument_id``, ``effective_from``,
        ``attributes`` …), or ``None`` if the slot has no history yet.
    resolution_date
        ISO ``YYYY-MM-DD`` date the resolver ran (the observation's date).
    resolved_instrument_id
        ``instrument_master.instrument_id`` of the bond the resolver found
        on-the-run (the caller resolves this via ``upsert_instrument_master``
        before calling).
    resolved_isin
        The resolved bond's ISIN — recorded in the ``pending_candidate`` blob
        for human-readable provenance.
    confirmation_runs
        Number of distinct-date observations that must agree on a new ISIN
        before a roll is recorded. ``< 1`` is clamped to 1 (immediate roll,
        no confirmation).

    Returns
    -------
    OtrTransitionPlan
        A pure description of the intended write. No side effects.
    """
    rd = str(resolution_date)
    threshold = max(1, int(confirmation_runs))

    # --- No open window: bootstrap the baseline (not confirmation-gated) -----
    if open_row is None:
        return OtrTransitionPlan(
            action=ACTION_BOOTSTRAP,
            reason="no open OTR window for slot — establishing the baseline",
            new_effective_from=rd,
            new_otr_instrument_id=int(resolved_instrument_id),
            new_attributes={"otr_resolution": {"mode": "bootstrap", "observed_on": rd}},
        )

    open_attrs: Dict[str, Any] = dict(open_row.get("attributes") or {})
    pending = open_attrs.get("pending_candidate")
    open_instrument_id = int(open_row["otr_instrument_id"])

    # --- Resolved bond is already the open OTR ------------------------------
    if int(resolved_instrument_id) == open_instrument_id:
        if pending:
            cleared = dict(open_attrs)
            cleared.pop("pending_candidate", None)
            return OtrTransitionPlan(
                action=ACTION_CLEAR_CANDIDATE,
                reason="resolver reverted to the open OTR bond — discarding the "
                "unconfirmed candidate (transient blip)",
                open_row_attributes=cleared,
            )
        return OtrTransitionPlan(
            action=ACTION_NO_CHANGE,
            reason="resolved bond matches the open OTR window — nothing to do",
        )

    # --- Resolved bond differs from the open window: candidate roll ---------
    # Stale-artifact guard (applies whether or not a candidate is pending): an
    # observation dated on/before the open window's own effective_from cannot
    # legitimately open a candidate against a bond different from the open OTR
    # — it is an out-of-order / replayed artifact and must never rewind state.
    # The same-candidate branch below additionally guards staleness against the
    # candidate's own last-observed date.
    open_effective_from = _to_iso_date(open_row.get("effective_from"))
    if open_effective_from is not None and rd <= open_effective_from:
        return OtrTransitionPlan(
            action=ACTION_SKIP_STALE,
            reason=f"observation date {rd} is on/before the open OTR window's "
            f"effective_from {open_effective_from} — out-of-order artifact, skipping",
        )

    if pending and int(pending.get("instrument_id", -1)) == int(resolved_instrument_id):
        # Same candidate as previously parked — advance its observation count,
        # but only on a strictly later calendar date (distinct-date rule).
        last_observed = str(pending.get("last_observed"))
        if rd < last_observed:
            return OtrTransitionPlan(
                action=ACTION_SKIP_STALE,
                reason=f"observation date {rd} predates the last recorded candidate "
                f"observation {last_observed} — stale artifact, skipping",
            )
        if rd == last_observed:
            return OtrTransitionPlan(
                action=ACTION_NO_CHANGE,
                reason="re-ingest of an already-recorded candidate observation "
                "(same date) — confirmation count not advanced",
            )
        observation_count = int(pending.get("observation_count", 1)) + 1
        first_observed = str(pending.get("first_observed", rd))
    else:
        # Brand-new candidate (no pending, or pending was a different bond).
        observation_count = 1
        first_observed = rd

    # --- Confirmed? close the open window and open the new one --------------
    if observation_count >= threshold:
        return OtrTransitionPlan(
            action=ACTION_CONFIRM_ROLL,
            reason=f"candidate ISIN confirmed by {observation_count} distinct-date "
            f"observation(s) (threshold {threshold}) — recording the OTR roll",
            close_prior_at=first_observed,
            new_effective_from=first_observed,
            new_otr_instrument_id=int(resolved_instrument_id),
            new_attributes={
                "otr_resolution": {
                    "mode": "roll",
                    "first_observed": first_observed,
                    "confirmed_on": rd,
                    "observation_count": observation_count,
                    "confirmation_runs": threshold,
                    "previous_otr_instrument_id": open_instrument_id,
                }
            },
        )

    # --- Not yet confirmed: park / update the candidate on the open row ----
    updated_attrs = dict(open_attrs)
    updated_attrs["pending_candidate"] = {
        "instrument_id": int(resolved_instrument_id),
        "isin": str(resolved_isin),
        "first_observed": first_observed,
        "last_observed": rd,
        "observation_count": observation_count,
    }
    return OtrTransitionPlan(
        action=ACTION_RECORD_CANDIDATE,
        reason=f"candidate ISIN observed {observation_count}/{threshold} time(s) — "
        "awaiting confirmation before recording a roll",
        open_row_attributes=updated_attrs,
    )


def build_otr_instrument_record(obs: Dict[str, Any]) -> Dict[str, Any]:
    """Build an ``instrument_master`` upsert record for a resolved OTR bond.

    ``vendor_ticker`` is the canonical ``/isin/<ISIN>`` form — IDENTICAL to the
    convention ``sovereign_cash_bonds.yml`` uses for its universe rows — so the
    resolution-side upsert and the market-data-side upsert converge on ONE
    ``instrument_master`` row (one ``instrument_id``) for the bond. Without
    that, ``otr_history`` and ``market_data_daily`` could point at two
    different rows for the same bond.
    """
    attributes = {
        key: value
        for key, value in {
            "security_name": obs.get("security_name"),
            "coupon": obs.get("coupon"),
            "issue_date": obs.get("issue_date"),
            "resolved_from_generic": obs.get("generic_ticker"),
            "source": "otr_resolver",
        }.items()
        if value is not None
    }
    return {
        "vendor": "BLOOMBERG",
        "vendor_ticker": obs["instrument_ticker"],
        "asset_class": "rates",
        "instrument_type": "sovereign_cash_bond",
        "curve_family": obs.get("curve_family"),
        "country": obs.get("country"),
        "currency": obs.get("currency"),
        "tenor": obs.get("tenor"),
        "cusip": obs.get("resolved_cusip"),
        "isin": obs.get("resolved_isin"),
        "maturity_date": obs.get("maturity_date"),
        "is_rolling_contract": False,
        "is_active": True,
        "attributes": attributes,
    }
