"""Pydantic schemas for the scan_bond_futures_extremes tool.

V1 monitor #3 for the ``bond_futures`` domain (ADR 0013) — universe-
wide front-month bond-futures sweep ranked by absolute 252-day z-score
across four metrics (price level, 1-day price change, daily traded
volume, end-of-day open-interest level). Replaces the Phase-4 inter-
commodity DV01-weighted spread stack per ADR 0013 (V1 monitors-only).

Why four metrics, not one
-------------------------
The catalog's V1 scope ("price, Δ, volume, OI") binds this primitive to
emit four ranked top-N lists in a single call. Each metric answers a
distinct desk question:

  - ``price`` LEVEL z-score — "where is the universe stretched in
    price right now?"
  - ``price_change`` 1-day Δ z-score — "where did prices move most
    today relative to their own daily-change distribution?" (the
    catalog's "Δ" wording — a 1-day change z-score, NOT a level
    z-score on a change series and NOT a volatility metric)
  - ``volume`` LEVEL z-score — "where is daily activity elevated?"
  - ``open_interest`` LEVEL z-score — "where is positioning
    stretched?"

Why no ``field_name`` or ``metrics`` input
------------------------------------------
The four metrics ARE the concept. Exposing them as a per-query input
would be input-schema overreach (PR9 / OPR8 — methodology knob
masquerading as a per-query choice). The four field mnemonics
(``default_price_field`` / ``default_volume_field`` /
``default_open_interest_field``) are YAML-locked conventions so an
ingestion-side rename can be absorbed by a YAML edit; the LLM cannot
ask for a non-standard set of metrics per query.

Why ``top_n`` / ``min_abs_z_score`` are None-sentinel optionals
---------------------------------------------------------------
Both are DISPLAY thresholds — legitimate per-query inputs — AND
their defaults are methodology choices that the catalog guardrail
(PR9 / PR10 YAML-vs-code split) requires to live in
``config.yaml`` rather than baked into Python defaults. Reviewer
round-1 mandatory-fix #1: a concrete Field default duplicates the
YAML's authority and causes silent drift when the YAML is edited.
We resolve this honestly: the schema accepts ``None`` and compute
falls through to ``config.convention_value("default_top_n")`` /
``config.convention_value("default_min_abs_z_score")``. The
validation bounds (``ge=1, le=50`` and ``ge=0.0``) stay on the
Field so a malformed LLM-supplied value still fails at the schema
layer — methodology in YAML, invariants in code (PR9 / PR10).
Mirrors the ``field_name`` sentinel pattern used by
futures_price_level / futures_volume_oi for their YAML-defaulting
optional inputs.

Why ``as_of_date`` (and why no ``lookback_days``)
-------------------------------------------------
``as_of_date`` is the legitimate per-query input that anchors the
scan to a specific trading day. The fetch window is METHODOLOGY —
it is derived from YAML (``z_score_window_days *
z_score_buffer_multiplier``) so the rolling 252d z-score is fully
populated for every stem on the anchor date. Reviewer round-1
mandatory-fix #2: exposing ``lookback_days`` as an LLM input is a
PR8 / OPR8 input-schema overreach (methodology knob masquerading
as a per-query choice), and anchoring the scan to ``date.today()``
makes the output non-deterministic across days. Removing
``lookback_days`` and adding ``as_of_date`` brings the input
surface in line with the catalog's primitive-specific review
contract: the legitimate per-query inputs are ``curve_families``,
``as_of_date``, ``top_n``, ``min_abs_z_score`` — and nothing
else. When ``as_of_date`` is omitted, compute resolves to the
most-recent shared trading day in the fetched universe (max
trade_date observed across the three field series) — same
"max date in the fetched rows" pattern futures_price_level /
futures_volume_oi use for their per-contract as_of anchor.

Why no canonical ``TimeSeries`` output
--------------------------------------
The scan is a SNAPSHOT object — a ranked top-N per metric on the
as-of date, not a historical series. Per-contract history lives on
the sibling primitives (``futures_price_level``,
``futures_volume_oi``); composing those is the honest path for time-
series consumers. The per-metric history of the rank itself is not
load-bearing for the desk read ("where is the universe stretched
TODAY?") so adding it would inflate the wire without serving a
question the desk asks the scan.

The closed-enum ``shared.schemas.time_series.TimeSeriesUnits`` family
has neither a ``PRICE`` nor a ``CONTRACTS`` member, so a canonical
TimeSeries on the per-stem snapshot rows would force a unit-honesty
violation (P8) the same way the sibling primitives' bespoke row
shapes do. The exempt ``output_field_units={}`` mode on the workflow
registration (see ``rates_agent/workflows/__init__.py``) is the
honest path until a future ADR extends ``TimeSeriesUnits``.

Field-name discipline on each result row
----------------------------------------
- ``metric`` is a closed-enum ``Literal[...]`` of the four metrics —
  P8 closed-family discipline. Adding a fifth metric requires an ADR
  + schema migration.

- ``current_price`` / ``daily_price_change`` / ``current_volume`` /
  ``current_open_interest`` / ``delta_open_interest_1d`` are surfaced
  on every row so the consumer can read the per-contract snapshot
  WITHOUT a second tool call. Values rounded with the same
  ``price_round_decimals`` / ``volume_round_decimals`` /
  ``oi_round_decimals`` conventions the sibling per-contract monitors
  use — so a "TY1 price = 110.453125" from the scan equals
  "TY1 price = 110.453125" from ``futures_price_level`` on the same
  as-of date.

- ``z_score`` is the z-score that put this row in this metric (not a
  composite across metrics; not the z-score of a different metric on
  the same stem). The wire field name does not embed "252d" because
  the lookback window is surfaced explicitly via
  ``methodology_disclosure`` rather than encoded in the field name —
  PR14 wire-format-honesty satisfied by disclosure, not field-name
  embedding.

- ``signal`` is a closed enum ``Literal["EXTREME_HIGH",
  "EXTREME_LOW"]`` matching the sovereign / OIS scanner shape.
  EXTREME_HIGH when z > 0; EXTREME_LOW when z < 0. A z = 0 row would
  not pass the ``min_abs_z_score`` filter, so the enum is exhaustive
  on the rows that reach the output.

- ``methodology_disclosure`` is REQUIRED on every row (NOT only on
  the response) per the catalog guardrail. The catalog wording:
  "Output rows MUST include the methodology disclosure tag so
  downstream consumers cannot mistake the read for a tenor-anchored
  yield call." Repeating the disclosure per row costs a few hundred
  bytes per response but guarantees the disclosure survives any
  downstream consumer that flattens / re-orders / paginates the
  results list.

Why ``curve_families`` is a list not a single string
----------------------------------------------------
A desk asks "where are the UST and Bund futures stretched?" as ONE
question, not two. Allowing ``curve_families=['UST_FUT', 'DE_FUT']``
as one call avoids forcing the LLM to assemble two scans and merge
their rankings. The list is validated against the closed-family
whitelist (``bond_futures_curve_families`` convention); a policy-
futures stem (SOFR_FUT etc.) is REFUSED at schema-validation time
rather than silently included in the scan.

Validation layering
-------------------
- ``curve_families``: a model-level validator (post-construction)
  enforces the whitelist by reading the bundled ``config.yaml``'s
  ``bond_futures_curve_families`` convention. Kept in the schema —
  not in compute — so the refusal fires at API boundary, not deep
  inside compute.

- ``min_abs_z_score >= 0`` enforced by ``Field(ge=0.0)`` (a negative
  filter threshold is structurally meaningless).

- ``top_n`` is bounded ``[1, 50]`` to keep the response payload
  reasonable for an LLM context window; 50 per metric × 4 metrics =
  200 rows max, an honest hard ceiling.

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary discipline
  (``docs_revamped/03_standards/typed_boundary_discipline.md`` §1).
  None of the four models carry pandas / numpy payloads, so
  ``arbitrary_types_allowed`` is intentionally omitted.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


# ============================================================================
# CONFIG-SOURCED ENUMS (closed-family validation)
# ============================================================================

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


def _bond_futures_curve_families_whitelist() -> frozenset[str]:
    """Load the closed-family whitelist of sovereign-bond futures curve
    families from the bundled ``config.yaml``.

    Read once at module import time (the YAML is immutable; the lint +
    config-load tests validate its shape). Returning a frozenset keeps
    the membership check O(1) per validation call and prevents
    accidental mutation.

    Why read from YAML here rather than hardcode in the schema:
    PR7 (configuration offloading) — the whitelist IS a methodology
    convention (which curve families are sovereign-bond futures vs
    policy-futures vs other), and it belongs in the YAML alongside the
    rest of the conventions. The schema validator just references the
    same source of truth.
    """
    with _CONFIG_PATH.open() as f:
        raw = yaml.safe_load(f)
    csv_value = raw["conventions"]["bond_futures_curve_families"]["value"]
    return frozenset(s.strip() for s in csv_value.split(",") if s.strip())


_BOND_FUTURES_CURVE_FAMILIES: frozenset[str] = _bond_futures_curve_families_whitelist()


# The four metrics ARE the concept (see module docstring); the closed-
# enum Literal pins them at the type level so a schema-evolution PR
# that adds / removes a metric is loud (the union type changes; every
# consumer recompiles against the new shape).
ScanMetric = Literal[
    "price",
    "price_change",
    "volume",
    "open_interest",
]


# ============================================================================
# INPUT
# ============================================================================

class ScanBondFuturesExtremesInput(BaseModel):
    """Parameters the LLM extracts to scope the bond-futures universe
    scan.

    No ``field_name`` / ``metrics`` input — the four metrics are the
    primitive's CONCEPT and the four field mnemonics are YAML-owned
    conventions. See module docstring for the input-schema-overreach
    rationale.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_families: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional scope filter. None = scan the full bond-futures "
            "universe (every curve family in ``bond_futures_curve_"
            "families`` from config.yaml — currently UST_FUT, DE_FUT, "
            "UK_FUT, JP_FUT, FR_FUT, IT_FUT, ES_FUT, CA_FUT, AU_FUT). "
            "Pass a list to narrow (e.g. ['UST_FUT', 'DE_FUT'] for a "
            "UST + Bund sweep). Every entry MUST be a sovereign-bond "
            "futures curve family per ADR 0013 — passing a policy-"
            "futures curve (SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT) "
            "is REFUSED at schema-validation time; route those to "
            "the policy_futures agent."
        ),
    )
    top_n: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of extreme stems to return PER METRIC. When None "
            "(default), compute resolves to the YAML's "
            "``default_top_n`` convention (currently 5) — keeps the "
            "default YAML-locked per PR9 / PR10. Bounded [1, 50] at "
            "the schema layer to keep the response payload reasonable "
            "for an LLM context window: 50 per metric × 4 metrics = "
            "200 rows hard ceiling. An LLM may still pass an explicit "
            "integer in-bounds to override the YAML default per query."
        ),
    )
    min_abs_z_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold; stems below the "
            "threshold are filtered OUT of the ranking. When None "
            "(default), compute resolves to the YAML's "
            "``default_min_abs_z_score`` convention (currently 1.5 — "
            "matches the sovereign / OIS scanner conventions) — keeps "
            "the default YAML-locked per PR9 / PR10. The non-negative "
            "bound (ge=0.0) stays in code as a structural invariant. "
            "The filter is applied PER METRIC (a stem may pass the "
            "threshold on price but fail on volume; it appears in the "
            "price top-N only)."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "As-of date for the scan. When omitted (default), the scan "
            "uses the most-recent shared trading day available in the "
            "DB across the universe (max trade_date observed across "
            "the per-stem aligned series) — same as-of resolution "
            "pattern as futures_price_level / futures_volume_oi. When "
            "provided, the scan's per-stem time series are CAPPED at "
            "the supplied date before z-scoring, so the ranking is "
            "deterministic across runs (same as_of_date + same DB "
            "state ⇒ same ranking). The fetch window itself is "
            "methodology — derived from YAML "
            "(``z_score_window_days`` * "
            "``z_score_buffer_multiplier``, currently 252 * 1.5 = "
            "378 days) — and is NOT an LLM input. An as_of_date "
            "BEYOND the bond-futures universe's last observed "
            "trade_date returns the documented controlled-error "
            "envelope ({\"error\": \"no scoreable stems: ...\"}) "
            "with NO top-N rankings and NO scan_summary — the scan "
            "refuses to silently re-label an unbounded ranking as a "
            "future-anchored read (P5 / PR8 honest-disclosure). An "
            "as_of_date BEFORE any ingested data returns the same "
            "controlled-error envelope (no aligned observations) — "
            "both off-data anchors raise no exception."
        ),
    )

    @model_validator(mode="after")
    def _validate_curve_families_whitelist(self) -> "ScanBondFuturesExtremesInput":
        """Refuse policy-futures curve families at schema-validation
        time. See module docstring for the whitelist rationale."""
        if self.curve_families is None:
            return self
        if len(self.curve_families) == 0:
            raise ValueError(
                "curve_families must be None (full universe) or a "
                "non-empty list; got [] — pass None instead to scan "
                "the full universe."
            )
        invalid = [
            cf for cf in self.curve_families
            if cf not in _BOND_FUTURES_CURVE_FAMILIES
        ]
        if invalid:
            allowed = sorted(_BOND_FUTURES_CURVE_FAMILIES)
            raise ValueError(
                f"curve_families={invalid!r} are not in the bond-"
                f"futures closed-family whitelist (per ADR 0013 V1 "
                f"monitors-only scope). Allowed: {allowed!r}. "
                "Policy-futures curves (SOFR_FUT / SONIA_FUT / "
                "EUR_SHORT_RATE_FUT) route to the policy_futures "
                "agent."
            )
        return self


# ============================================================================
# RESULT ROW
# ============================================================================

class ScanBondFuturesExtremesResultRow(BaseModel):
    """One ranked extreme on one metric.

    Snapshot fields (``current_price`` / ``daily_price_change`` /
    ``current_volume`` / ``current_open_interest`` /
    ``delta_open_interest_1d``) are surfaced on every row so a
    consumer reading the scan does not need a second tool call to see
    the per-contract context that produced the z-score.

    The ``z_score`` field is the z-score of THIS row's ``metric``
    (not a composite, not a different metric's z). The ``signal`` is
    derived from ``z_score``'s sign on a row that passed the
    ``min_abs_z_score`` filter (the filter rejects |z| < threshold, so
    z != 0 on every row that reaches the output).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = Field(
        ...,
        ge=1,
        description=(
            "Rank WITHIN this metric's top-N (1 = most extreme by "
            "absolute z-score for this metric). NOT a global rank "
            "across metrics — a stem may rank #1 on price and #3 on "
            "open_interest in the same response."
        ),
    )
    metric: ScanMetric = Field(
        ...,
        description=(
            "Which of the four metrics this row is ranked on — "
            "'price' (level z), 'price_change' (1-day Δ z), 'volume' "
            "(level z), 'open_interest' (level z). Closed enum; see "
            "module docstring for the four-metric concept."
        ),
    )
    curve_family: str = Field(
        ..., description="Bond-futures curve family (e.g. 'UST_FUT')."
    )
    contract_code: str = Field(
        ...,
        description=(
            "Rolling-generic stem (TY1 / UXY1 / US1 / WN1 / RX1 / "
            "JB1 / OAT1 / ...). The canonical disambiguator per "
            "TD#11 — (curve_family, tenor) alone is ambiguous for "
            "TY1/UXY1 (both UST_FUT 10Y) and US1/WN1 (both UST_FUT "
            "30Y)."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor label on the rolling-generic (e.g. '10Y' for "
            "TY1). Surfaced for readability; does NOT identify the "
            "stem on its own (see contract_code)."
        ),
    )
    as_of_date: str = Field(
        ...,
        description=(
            "Most recent trading date on which all three series "
            "(price + volume + OI) for this stem have a value after "
            "cleaning (YYYY-MM-DD). Anchored per-stem so the "
            "snapshot row cannot pair a fresh price with a stale "
            "OI reading."
        ),
    )
    current_price: Optional[float] = Field(
        None,
        description=(
            "Latest cleaned, ffilled price in the contract's native "
            "quote_units (NOT a yield). Rounded with "
            "``price_round_decimals`` from config.yaml. Equals the "
            "futures_price_level snapshot's ``current_price`` for "
            "the same stem on the same as_of_date."
        ),
    )
    daily_price_change: Optional[float] = Field(
        None,
        description=(
            "1-trading-day price change in the contract's native "
            "quote_units (raw subtraction; NOT multiplied by 100; "
            "NOT a bps quantity). The Δ on the 'price_change' "
            "metric is the rolling z-score of THIS series, not a "
            "single snapshot value — but the latest 1-day change is "
            "surfaced here so consumers can read the per-contract "
            "move alongside its z-score."
        ),
    )
    current_volume: Optional[float] = Field(
        None,
        description=(
            "Latest daily traded volume in CONTRACTS (NOT notional). "
            "Rounded with ``volume_round_decimals`` from config.yaml. "
            "Equals the futures_volume_oi snapshot's "
            "``current_volume`` for the same stem on the same "
            "as_of_date."
        ),
    )
    current_open_interest: Optional[float] = Field(
        None,
        description=(
            "Latest end-of-day open interest in CONTRACTS (NOT "
            "notional). Rounded with ``oi_round_decimals`` from "
            "config.yaml. Equals the futures_volume_oi snapshot's "
            "``current_open_interest`` for the same stem on the "
            "same as_of_date."
        ),
    )
    delta_open_interest_1d: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in OI level (raw subtraction; NOT "
            "multiplied by 100; NOT a bps quantity). Whole-contract "
            "delta. Surfaced for context — the open_interest metric "
            "ranks on the LEVEL z-score, not the Δ z-score."
        ),
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of THIS row's metric "
            "(see ``metric``). The ranking key for this row's place "
            "within its metric's top-N. The lookback window is "
            "disclosed verbatim on ``methodology_disclosure`` per "
            "the catalog guardrail (it is NOT embedded in the field "
            "name — see module docstring)."
        ),
    )
    signal: Literal["EXTREME_HIGH", "EXTREME_LOW"] = Field(
        ...,
        description=(
            "Derived from z_score's sign: EXTREME_HIGH when z > 0, "
            "EXTREME_LOW when z < 0. Rows with |z| < min_abs_z_score "
            "are filtered out before ranking, so z != 0 on every "
            "row reaching the output."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0013 / catalog-guardrail disclosure — REQUIRED "
            "on every row (not just on the response) per the "
            "catalog wording: 'Output rows MUST include the "
            "methodology disclosure tag so downstream consumers "
            "cannot mistake the read for a tenor-anchored yield "
            "call.' Includes the universe-wide-sweep label, the "
            "explicit z-score lookback window, and the rolling-"
            "generic-price / non-DV01-spread caveats."
        ),
    )


# ============================================================================
# OUTPUT
# ============================================================================

class ScanBondFuturesExtremesOutput(BaseModel):
    """Top-level response for the scan_bond_futures_extremes tool.

    Wire shape:
      - ``scan_summary``: human-readable one-line summary of how many
        stems were scanned, how many passed the threshold per metric,
        and the as_of_date span observed across stems.
      - ``results``: ranked rows, top ``top_n`` per metric, ordered
        by ``metric`` then ``rank`` (so a reader sees the price
        top-N, then the price_change top-N, then the volume top-N,
        then the open_interest top-N).
      - ``methodology_disclosure``: the P5 / ADR 0013 caveat carried
        on the response level — includes the explicit z-score
        lookback window per the catalog's methodology guardrail.
        REQUIRED so consumers cannot drop the disclosure when
        relaying the response. (The per-row disclosure on each
        result is also required; the response-level field is a
        belt-and-braces safeguard.)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scan_summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable one-line summary (e.g. 'Scanned 19 bond-"
            "futures stems. Top 5 per metric (price, price_change, "
            "volume, open_interest) with |z| >= 1.5. as_of dates "
            "span 2026-05-20 to 2026-05-22.')."
        ),
    )
    results: List[ScanBondFuturesExtremesResultRow] = Field(
        default_factory=list,
        description=(
            "Ranked result rows, top ``top_n`` per metric. Ordered "
            "by metric (price, price_change, volume, open_interest) "
            "then by rank within metric (1 = most extreme). May be "
            "shorter than ``top_n * 4`` if some metrics have fewer "
            "stems passing the threshold."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0013 / catalog-guardrail disclosure carried on "
            "the response level: includes the universe-wide-sweep "
            "label, the explicit z-score lookback window (per the "
            "catalog's methodology guardrail), and the rolling-"
            "generic-price / non-DV01-spread caveats. REQUIRED so "
            "consumers cannot drop the disclosure when relaying the "
            "response."
        ),
    )


__all__ = [
    "ScanMetric",
    "ScanBondFuturesExtremesInput",
    "ScanBondFuturesExtremesResultRow",
    "ScanBondFuturesExtremesOutput",
]
