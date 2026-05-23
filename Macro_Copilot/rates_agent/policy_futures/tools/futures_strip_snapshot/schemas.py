"""Pydantic schemas for the policy_futures futures_strip_snapshot tool.

V1 whole-strip snapshot primitive for the ``policy_futures`` domain
(ADR 0011) — one row per configured strip position on ONE
``curve_family`` (e.g. SOFR_FUT positions 1..8 ⇒ SFR1..SFR8 side-by-
side). The desk-recognised STIR-strip object is the whole strip
("steep / flat / inverted") — not eight individual outright reads —
so this primitive owns the concept; it is NOT a composition of eight
``futures_price_level`` calls.

Why a new primitive rather than eight ``futures_price_level`` calls
------------------------------------------------------------------
Composing eight outright reads loses the aligned-date guarantee:
each ``futures_price_level`` call anchors at its own per-leg
data-max ``trade_date``, so an eight-call composition can return
rows on slightly different anchor dates. The strip-snapshot's
desk-recognised reading is the WHOLE STRIP on a SINGLE aligned
``as_of_date`` — that is the date on which "the strip is steep /
flat / inverted" makes sense. This primitive enforces the
intersection-of-trading-days alignment up front, so the per-row
implied rates / z-scores / 1-day changes / open-interest are all
read against the same date. Catalog PR4 — composing 8 reads is
structurally worse than one strip-aware primitive on accuracy of
the implied-rate alignment, provenance, and LLM clarity.

Closed-family discipline (P8 / PR8)
-----------------------------------
``curve_family`` is a closed ``Literal[...]`` mirroring the V1
``policy_futures.yml`` universe (SOFR_FUT, EUR_SHORT_RATE_FUT,
SONIA_FUT). An unknown value raises ``pydantic.ValidationError`` at
the schema layer (NOT at the compute layer); a future direct-
priced family lands via a new ADR + YAML/playbook entry + a Literal
extension, NEVER as a silent passthrough.

Why a bespoke ``snapshot`` row shape (NOT canonical TimeSeries)
---------------------------------------------------------------
Each row in ``snapshot`` carries MULTIPLE unit spaces side-by-side:
the raw futures price (no clean ``TimeSeriesUnits`` member — SFR / ER
/ SFI quote ``100 - rate``), the implied rate in PERCENT, the
1-trading-day change in PERCENT POINTS, the rolling z-score
(``Z_SCORE``), and the open interest in CONTRACTS (no
``TimeSeriesUnits`` member for contracts either). Emitting one
canonical TimeSeries forces a single unit declaration; the bespoke
``{strip_position, raw_price, implied_rate_pct, daily_change_*,
z_score_*, open_interest, row_methodology_card, ...}`` shape keeps
every read honest, mirrors the sibling bond_futures
``scan_bond_futures_extremes`` choice and the policy_futures
``futures_price_level`` exempt pattern, and is the same exempt
discipline documented at ``shared/workflow/validate.py:368``
(``output_field_units={}`` on the workflow registration).

Per-row methodology card (catalog standardness guardrail)
---------------------------------------------------------
Every row carries its OWN ``row_methodology_card`` string disclosing
the underlying short-rate regime (RFR / IBOR) and the implied-rate
conversion rule for the row's curve_family. This is required by the
catalog's standardness guardrail — a desk consumer that copies a
single row out of the snapshot must still see the regime label and
the conversion rule on that row. The output-level
``methodology_disclosure`` is also REQUIRED on every response so the
P5 caveat (rolling-generic strip-snapshot scope-limit; NOT a
CTD-of-futures-of-OIS read) is never dropped when relaying the
snapshot as a whole.

Field-name discipline on the snapshot (PR14 frozen)
---------------------------------------------------
- ``raw_price`` — latest aligned cleaned, ffilled price in the
  contract's native quote space (for SFR / ER / SFI: ``100 - rate``).
- ``implied_rate_pct`` — desk-recognised implied rate in PERCENT.
  Catalog v2.1 PR14 freezes this name; the sibling policy_futures
  primitives (futures_price_level, futures_calendar_spread,
  futures_butterfly_simple, futures_cross_market_spread,
  futures_pack_average_simple, scan_policy_futures_extremes) all
  reference it. Do NOT rename to ``implied_rate`` /
  ``implied_rate_percent`` / ``rate_pct``.
- ``daily_change_implied_rate_pct`` — one-trading-day raw subtraction
  on the implied-rate axis (NOT *100; the implied-rate delta is in
  PERCENT POINTS, not bps).
- ``z_score_implied_rate`` — rolling 252-trading-day z-score of the
  per-leg implied-rate level series.
- ``open_interest`` — latest end-of-day open interest in CONTRACTS at
  the snapshot's ``as_of_date``. NULL when the OI series has no
  observation aligned to the snapshot's as_of (defensive — STIR OI
  is published daily so this should rarely fire in V1).
- ``contract_code`` (master stem, e.g. SFR1) and
  ``underlying_contract_code`` (current-front, e.g. SFRM26) disclosed
  side-by-side per row so the reader knows BOTH the strip slot AND
  the underlying contract the slot resolves to today.

Why the input set is minimal (PR8 input-schema overreach guard)
---------------------------------------------------------------
Per the catalog (build_order 27) and the build prompt's
non-negotiables: ONLY four inputs are legitimate per-query knobs:

  - ``curve_family`` — the central user-facing choice (which strip).
  - ``as_of_date`` — optional anchor (None = data-max).
  - ``last_price_field_name`` — optional Bloomberg field override.
  - ``open_interest_field_name`` — optional Bloomberg field override.

Methodology defaults (z window, strip-positions list, rounding,
inverse-pricing-by-family, regime labels) live in YAML (PR9 / PR10);
mathematical invariants (the ``implied_rate_pct = 100 - raw_price``
inversion, the intersection-of-trading-days alignment, the z-score
recipe) live in compute.py. Exposing any of those as inputs would be
input-schema overreach (PR8).

Validation layering
-------------------
- ``last_price_field_name`` / ``open_interest_field_name`` default to
  ``None`` — the sentinel that means "use the YAML's
  ``default_price_field`` / ``default_open_interest_field``
  convention". MCP / HTTP wrappers translate their wire-level
  empty-string sentinel to None before constructing this input.

- ``as_of_date`` defaults to ``None`` — the sentinel that means
  "anchor at the universe's last observed ``trade_date`` for the
  intersection of all configured strip positions on the requested
  curve_family". When supplied AND beyond any leg's data max, the
  compute layer returns the documented controlled-error envelope per
  P6 / PR8 / PR16 honest disclosure (no silent re-labelling of an
  unbounded read as a future-anchored read).

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary discipline
  (``docs_revamped/03_standards/typed_boundary_discipline.md`` §1).
  None of the four models on this primitive carry pandas / numpy
  payloads, so ``arbitrary_types_allowed`` is intentionally
  omitted.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# Closed-family Literal mirroring rates_agent/playbooks/policy_futures.yml
# (V1 universe). Adding a new curve_family REQUIRES an ADR + playbook
# entry + this Literal extension + a regime-map YAML entry (P8 / PR8).
PolicyFuturesCurveFamily = Literal[
    "SOFR_FUT",
    "EUR_SHORT_RATE_FUT",
    "SONIA_FUT",
]


class FuturesStripSnapshotInput(BaseModel):
    """Parameters the LLM extracts to query a policy-futures whole-
    strip snapshot.

    Keyed by ``curve_family`` only — the YAML owns the list of strip
    positions included in the snapshot (V1 default: 1..8). See module
    docstring for the input-schema discipline rationale (PR8).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: PolicyFuturesCurveFamily = Field(
        ...,
        description=(
            "Policy-futures curve family. Closed Literal mirroring the "
            "V1 playbook universe: 'SOFR_FUT' (US Fed SOFR strip, RFR "
            "regime), 'EUR_SHORT_RATE_FUT' (ECB Euribor strip, IBOR "
            "regime), 'SONIA_FUT' (BOE SONIA strip, RFR regime). An "
            "unknown value raises a Pydantic ValidationError at the "
            "schema layer — NOT silently passed through to the compute "
            "layer (P8 / PR8 closed-family discipline). Do NOT pass "
            "bond-futures curve families (UST_FUT / DE_FUT / UK_FUT / "
            "JP_FUT / ...) — those route to the bond_futures agent."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "As-of date for the snapshot. When omitted (default), the "
            "monitor anchors at the universe's last observed "
            "``trade_date`` where ALL configured strip positions have "
            "a value after cleaning + intersection (post-fetch data-"
            "max anchor — same pattern as the sibling "
            "futures_butterfly_simple). When supplied AND BEYOND the "
            "universe's last observed ``trade_date`` on ANY configured "
            "strip position, the monitor returns the documented "
            "controlled-error envelope (``{\"error\": \"no scoreable "
            "strip: ...\"}``). When supplied AND within the universe "
            "range, the fetch is anchored at this date so the "
            "snapshot is DETERMINISTIC across runs (same as_of_date + "
            "same DB state ⇒ same numbers)."
        ),
    )
    last_price_field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field mnemonic for the per-leg "
            "price series. When None (default), the tool falls "
            "through to ``default_price_field`` from config.yaml "
            "(currently 'PX_LAST'). Pass an explicit field name to "
            "override per query. LLM/HTTP wrappers MUST translate "
            "their wire-level sentinel (empty string for MCP, missing "
            "param for FastAPI) to None before constructing this "
            "input — otherwise the YAML default is silently shadowed "
            "(same wrapper-shadowing pattern fixed for sovereign "
            "curve_move_classifier in commit b2605ee)."
        ),
    )
    open_interest_field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field mnemonic for the per-leg "
            "open-interest series. When None (default), the tool "
            "falls through to ``default_open_interest_field`` from "
            "config.yaml (currently 'OPEN_INT'). Same sentinel-"
            "coalescing discipline as ``last_price_field_name``."
        ),
    )


class FuturesStripSnapshotRow(BaseModel):
    """One row in the strip snapshot — the per-strip-position view at
    the snapshot's aligned ``as_of_date``.

    Carries the per-leg raw price + implied rate + 1-day change +
    rolling z-score + open interest + the SCD2 disclosure block + a
    per-row methodology card. Field rounding agrees with the YAML
    conventions so consumers can round-trip the row without losing
    precision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strip_position: int = Field(
        ..., ge=1,
        description=(
            "1-based strip slot index. 1 = front contract; higher "
            "numbers = quarterly forwards down the strip."
        ),
    )
    contract_code: str = Field(
        ...,
        description=(
            "Strip-slot master stem from the playbook universe (e.g. "
            "``'SFR1'``, ``'ER2'``, ``'SFI1'``). Stable across rolls — "
            "see ``underlying_contract_code`` for the current front."
        ),
    )
    underlying_contract_code: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract that this strip slot "
            "resolves to AS OF the snapshot's ``as_of_date`` (e.g. "
            "``'SFRM26'`` for SFR1). Read off the SCD2 "
            "``instrument_metadata_history`` row whose effective "
            "window contains ``as_of_date``."
        ),
    )
    security_name: Optional[str] = Field(
        None,
        description=(
            "Latest-effective ``SECURITY_DES`` from the SCD2 rolling-"
            "contract metadata history bounded by ``as_of_date`` — "
            "names the underlying front contract (e.g. "
            "``'SFRM26 COMB'``)."
        ),
    )
    expiry_date: Optional[str] = Field(
        None,
        description=(
            "``LAST_TRADEABLE_DT`` for the current-front contract "
            "(== ``FUT_DLV_DT_LAST`` for STIR cash-settle). "
            "YYYY-MM-DD."
        ),
    )
    contract_size: Optional[float] = Field(
        None,
        description=(
            "``FUT_CONT_SIZE`` for the current-front contract — "
            "notional per contract in the curve's home currency."
        ),
    )
    raw_price: float = Field(
        ...,
        description=(
            "Latest aligned cleaned, ffilled price in the contract's "
            "native quote space. NOT a rate. Rounded with "
            "``raw_price_round_decimals`` from config.yaml."
        ),
    )
    implied_rate_pct: float = Field(
        ...,
        description=(
            "Desk-recognised implied rate in PERCENT, derived from "
            "``raw_price`` per the per-strip ``inverse_priced`` flag "
            "(when inverse: ``implied_rate_pct = 100 - raw_price``). "
            "PR14 frozen name; do NOT rename."
        ),
    )
    daily_change_implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in ``implied_rate_pct`` (raw "
            "subtraction in PERCENT POINTS; NOT multiplied by 100 to "
            "bps). For inverse-priced strips, this equals "
            "``-(raw_price_today - raw_price_prev)`` by construction."
        ),
    )
    z_score_implied_rate: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the per-leg implied-"
            "rate level series. The z lives on the rate axis (not "
            "the raw-price axis) because the desk-recognised level "
            "IS the rate; z-scoring the inverse-priced raw price "
            "would flip the sign of every 'extreme' reading relative "
            "to the rate."
        ),
    )
    open_interest: Optional[float] = Field(
        None,
        description=(
            "Latest end-of-day open interest in CONTRACTS at the "
            "snapshot's ``as_of_date``. NOT pre-multiplied to "
            "notional — consumers do the conversion via the row's "
            "``contract_size``. ``None`` when the OI series has no "
            "observation aligned to the snapshot's as_of_date after "
            "cleaning."
        ),
    )
    row_methodology_card: str = Field(
        ...,
        min_length=1,
        description=(
            "Per-row P5 / ADR 0011 disclosure. Names the underlying "
            "short-rate regime (RFR vs IBOR) for this row's "
            "curve_family AND the implied-rate conversion rule "
            "(``implied_rate_pct = 100 - raw_price`` for inverse-"
            "priced strips). Required so a desk consumer copying a "
            "single row out of the snapshot still sees the regime "
            "label + conversion rule on that row (catalog "
            "standardness guardrail)."
        ),
    )


class FuturesStripSnapshotOutput(BaseModel):
    """Top-level response for the policy_futures futures_strip_snapshot
    tool.

    Wire shape:
      - ``as_of_date``: aligned anchor date (YYYY-MM-DD); every row's
        raw_price / implied_rate_pct / open_interest is taken at this
        date.
      - ``curve_family``: echoed for disclosure.
      - ``inverse_priced``: shared flag (verified equal across all
        configured strip positions on this curve_family).
      - ``short_rate_regime``: RFR / IBOR label from the YAML.
      - ``quote_units``: ``'100 - rate'`` for inverse-priced /
        ``'rate (%)'`` for direct-priced.
      - ``strip_positions``: the list of strip positions included in
        the snapshot (echoes the YAML).
      - ``snapshot``: one row per configured strip position.
      - ``observation_count``: count of aligned trading days within
        the rolling z-score window — used so a desk reader can see
        whether the z-score is fully populated.
      - ``methodology_disclosure``: the output-level P5 / ADR 0011
        caveat carried on every response.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str = Field(
        ...,
        description=(
            "Most recent trade date on which ALL configured strip "
            "positions have a value after cleaning + intersection "
            "(YYYY-MM-DD). The snapshot is anchored to the "
            "intersection so the row set cannot pair fresh quotes on "
            "some strips with a stale quote on others."
        ),
    )
    curve_family: str = Field(..., description="Policy-futures curve family.")
    inverse_priced: bool = Field(
        ...,
        description=(
            "Inverse-pricing flag shared by every configured strip "
            "position on this ``curve_family``. Verified equal across "
            "all legs at compute time; a mixed-flag universe is "
            "refused with the controlled-error envelope. Drives the "
            "per-leg conversion off METADATA, NOT a hardcoded list "
            "(PR8 / P6)."
        ),
    )
    short_rate_regime: str = Field(
        ...,
        description=(
            "Methodology disclosure label for the underlying short-"
            "rate regime: ``'RFR'`` (compounded daily risk-free rate "
            "— SOFR / SONIA) or ``'IBOR'`` (unsecured term IBOR — "
            "Euribor). Resolved from the YAML's "
            "``short_rate_regime_map`` convention. Carried verbatim "
            "on the methodology disclosure string per P5 + ADR 0011."
        ),
    )
    quote_units: str = Field(
        ...,
        description=(
            "Quote-unit label for the per-row ``raw_price`` field "
            "(``'100 - rate'`` for inverse-priced; ``'rate (%)'`` "
            "for direct-priced). Carried inline so the consumer "
            "cannot misread a raw price as a rate."
        ),
    )
    strip_positions: List[int] = Field(
        ...,
        description=(
            "Ordered list of strip positions included in the snapshot. "
            "Echoes the YAML's ``strip_positions`` convention so a "
            "consumer can audit which positions were requested vs "
            "returned (no silent padding / truncation)."
        ),
    )
    snapshot: List[FuturesStripSnapshotRow] = Field(
        default_factory=list,
        description=(
            "One row per configured strip position. The ``snapshot`` "
            "list ordering matches the ``strip_positions`` list "
            "exactly so consumers can zip the two."
        ),
    )
    observation_count: int = Field(
        ...,
        description=(
            "Count of aligned trading days within the rolling z-score "
            "window. Used so a desk reader can see whether the per-"
            "leg z-scores are fully populated (>= z_score_min_periods)."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "Output-level P5 / ADR 0011 caveat carried on every "
            "response. Includes the curve_family, the inverse-"
            "pricing rule, the short-rate regime label (RFR vs "
            "IBOR), the z-score lookback window, the strip-positions "
            "list, and the rolling-generic-strip-snapshot scope-limit "
            "caveat (NOT a CTD-of-futures-of-OIS read; rolls "
            "quarterly; ADR 0011 V1 monitors-only). Required so "
            "consumers cannot drop the disclosure when relaying the "
            "snapshot."
        ),
    )


__all__ = [
    "PolicyFuturesCurveFamily",
    "FuturesStripSnapshotInput",
    "FuturesStripSnapshotRow",
    "FuturesStripSnapshotOutput",
]
