"""Pydantic schemas for the scan_policy_futures_extremes tool.

V1 monitor for the ``policy_futures`` domain (ADR 0011) — universe-
wide strip-position policy-futures sweep ranked by absolute 252-day
z-score across four metrics (implied-rate level, 1-day implied-rate
change in bps, daily traded volume, end-of-day open-interest level).

Why four metrics, not one
-------------------------
The catalog's V1 scope ("implied rate, Δ, volume, OI") binds this
primitive to emit four ranked top-N lists in a single call. Each
metric answers a distinct STIR-desk question:

  - ``implied_rate_level`` LEVEL z-score — "where is the strip
    universe stretched in rate space right now?" (PERCENT axis;
    z lives on the IMPLIED-RATE axis because the desk reads the
    rate, not the inverse-priced raw price)
  - ``implied_rate_change`` 1-day Δ z-score — "where did implied
    rates move most today relative to their own daily distribution?"
    (BPS axis; matches the bps wire convention sibling tools use
    for change-metric output)
  - ``volume_level`` LEVEL z-score — "where is daily activity
    elevated?"
  - ``open_interest_level`` LEVEL z-score — "where is positioning
    stretched?"

Why no ``field_name`` input
---------------------------
The three field mnemonics (PX_LAST / PX_VOLUME / OPEN_INT) ARE the
field-name convention this scanner reads from. Exposing them as
per-query inputs would be input-schema overreach (PR8 / OPR8 —
methodology knob masquerading as a per-query choice). The three
defaults (``default_price_field`` / ``default_volume_field`` /
``default_open_interest_field``) are YAML-locked conventions so an
ingestion-side rename can be absorbed by a YAML edit; the LLM
cannot ask for a non-standard set of fields per query.

Why ``top_n`` / ``min_abs_z_score`` / ``metrics`` are None-sentinel
optionals
-------------------------------------------------------------------
All three are DISPLAY thresholds — legitimate per-query inputs — AND
their defaults are methodology choices that PR9 / PR10 require to
live in ``config.yaml`` rather than baked into Python defaults. A
concrete Field default would duplicate the YAML's authority and
cause silent drift when the YAML is edited. We resolve this honestly:
the schema accepts ``None`` and compute falls through to
``config.convention_value("default_top_n")`` /
``config.convention_value("default_min_abs_z_score")`` /
``config.convention_value("default_metrics")``. The validation bounds
(``ge=1, le=50`` and ``ge=0.0``) stay on the Field so a malformed
LLM-supplied value still fails at the schema layer — methodology in
YAML, invariants in code (PR9 / PR10). Mirrors the precedent set by
scan_bond_futures_extremes / scan_inflation_swaps_extremes.

Why ``as_of_date`` (and why no ``lookback_days``)
-------------------------------------------------
``as_of_date`` is the legitimate per-query input that anchors the
scan to a specific trading day. The fetch window is METHODOLOGY —
derived from YAML (``z_score_window_days *
z_score_buffer_multiplier``) so the rolling 252d z-score is fully
populated for every stem on the anchor date. Exposing
``lookback_days`` would be a PR8 / OPR8 input-schema overreach
(methodology knob masquerading as a per-query choice) — the
bond_futures / inflation_swaps scanners removed it for the same
reason. When ``as_of_date`` is omitted, compute resolves to the
most-recent shared trading day in the fetched universe (max
trade_date observed across the three field series).

Why no canonical ``TimeSeries`` output
--------------------------------------
The scan is a SNAPSHOT object — a ranked top-N per metric on the
as-of date, not a historical series. Per-strip history lives on the
sibling primitives (``futures_price_level``,
``volume_open_interest_snapshot``,
``futures_strip_snapshot``); composing those is the honest path
for time-series consumers. The closed-enum
``shared.schemas.time_series.TimeSeriesUnits`` family has neither a
``PRICE`` nor a ``CONTRACTS`` member, so a canonical TimeSeries on
the per-stem snapshot rows would force a unit-honesty violation
(P8) the same way the sibling primitives' bespoke row shapes do.
The exempt ``output_field_units={}`` mode on the workflow
registration is the honest path until a future ADR extends
``TimeSeriesUnits``.

Field-name discipline on each result row
----------------------------------------
- ``metric`` is a closed-enum ``Literal[...]`` of the four metrics
  — P8 closed-family discipline. Adding a fifth metric requires an
  ADR + schema migration.

- ``current_raw_price`` / ``implied_rate_pct`` /
  ``daily_change_implied_rate_bps`` / ``current_volume`` /
  ``current_open_interest`` / ``delta_open_interest_1d`` are
  surfaced on every row so the consumer can read the per-strip
  snapshot WITHOUT a second tool call. Values rounded with the
  same per-field decimals the sibling per-strip monitors use — so
  a "SFR1 implied_rate_pct = 95.6700" from the scan equals the
  ``futures_price_level`` snapshot's ``implied_rate_pct`` for SFR1
  on the same as-of date.

- ``implied_rate_pct`` is the PR14-frozen wire field name for the
  rate LEVEL across the policy_futures domain. Mirroring the
  sibling tools' frozen-field discipline — NOT renamed to
  ``implied_rate`` / ``_bps`` / ``_implied`` / etc.

- ``daily_change_implied_rate_bps`` is the bps-axis 1-day change
  on the implied-rate series (Δ × 100). The change metric's
  z-score (``z_score_implied_rate_change_bps``) is computed on the
  rolling sequence of these 1-day bps changes.

- ``z_score`` is the z-score that put this row in this metric (not
  a composite across metrics; not the z-score of a different
  metric on the same stem). The wire field name does not embed
  "252d" because the lookback window is surfaced explicitly via
  ``methodology_disclosure`` rather than encoded in the field
  name — PR14 wire-format-honesty satisfied by disclosure, not
  field-name embedding.

- ``signal`` is a closed enum ``Literal["EXTREME_HIGH",
  "EXTREME_LOW"]``. EXTREME_HIGH when z > 0; EXTREME_LOW when
  z < 0. A z = 0 row would not pass the ``min_abs_z_score``
  filter, so the enum is exhaustive on the rows that reach the
  output.

- ``short_rate_regime`` is the per-row RFR-vs-IBOR disclosure
  required by ADR 0011. SOFR_FUT and SONIA_FUT rows carry
  ``"RFR"``; EUR_SHORT_RATE_FUT rows carry ``"IBOR"``. The scan
  ranks across this heterogeneity and the desk reader must be
  able to see which contracts are RFR-anchored vs IBOR-anchored.

- ``methodology_disclosure`` is REQUIRED on every row (NOT only on
  the response) per the catalog guardrail. Repeating the
  disclosure per row costs a few hundred bytes per response but
  guarantees the disclosure survives any downstream consumer that
  flattens / re-orders / paginates the results list.

Why ``curve_families`` is a list not a single string
----------------------------------------------------
A desk asks "where are SOFR and Euribor futures stretched?" as
ONE question, not two. Allowing
``curve_families=['SOFR_FUT', 'EUR_SHORT_RATE_FUT']`` as one call
avoids forcing the LLM to assemble two scans and merge their
rankings. The list is validated against the closed-family
whitelist (``policy_futures_curve_families`` convention); a
bond-futures / sovereign / OIS / inflation stem is REFUSED at
schema-validation time rather than silently included in the scan.

Validation layering
-------------------
- ``curve_families``: a model-level validator (post-construction)
  enforces the whitelist by reading the bundled ``config.yaml``'s
  ``policy_futures_curve_families`` convention. Kept in the schema
  — not in compute — so the refusal fires at API boundary, not
  deep inside compute.

- ``metrics``: same model-level validator pattern enforces the
  closed ScanMetric Literal whitelist on the input list.

- ``min_abs_z_score >= 0`` enforced by ``Field(ge=0.0)`` (a
  negative filter threshold is structurally meaningless).

- ``top_n`` is bounded ``[1, 50]`` to keep the response payload
  reasonable for an LLM context window; 50 per metric × 4 metrics
  = 200 rows max, an honest hard ceiling.

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary
  discipline (``docs_revamped/03_standards/typed_boundary_discipline.md``
  §1). None of the models carry pandas / numpy payloads, so
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


def _policy_futures_curve_families_whitelist() -> frozenset[str]:
    """Load the closed-family whitelist of policy-futures curve
    families from the bundled ``config.yaml``.

    Read once at module import time (the YAML is immutable; the
    lint + config-load tests validate its shape). Returning a
    frozenset keeps the membership check O(1) per validation call
    and prevents accidental mutation.

    Why read from YAML here rather than hardcode in the schema:
    PR7 (configuration offloading) — the whitelist IS a
    methodology convention (which curve families are policy
    futures vs bond futures vs sovereign vs OIS vs inflation),
    and it belongs in the YAML alongside the rest of the
    conventions. The schema validator just references the same
    source of truth.
    """
    with _CONFIG_PATH.open() as f:
        raw = yaml.safe_load(f)
    csv_value = raw["conventions"]["policy_futures_curve_families"]["value"]
    return frozenset(s.strip() for s in csv_value.split(",") if s.strip())


_POLICY_FUTURES_CURVE_FAMILIES: frozenset[str] = (
    _policy_futures_curve_families_whitelist()
)


# Closed-enum Literal over the three policy-futures curve families
# admitted by the V1 catalog (ADR 0011). Adding a new family
# requires the same ADR + schema migration as adding a new metric
# (P8 closed-family discipline).
PolicyFuturesScanCurveFamily = Literal[
    "SOFR_FUT",
    "EUR_SHORT_RATE_FUT",
    "SONIA_FUT",
]


# The four metrics ARE the concept (see module docstring); the
# closed-enum Literal pins them at the type level so a
# schema-evolution PR that adds / removes a metric is loud (the
# union type changes; every consumer recompiles against the new
# shape). Adding a fifth metric requires an ADR.
ScanMetric = Literal[
    "implied_rate_level",
    "implied_rate_change",
    "volume_level",
    "open_interest_level",
]


# All four metrics — used to validate the ``metrics`` input list at
# the schema layer (a value outside this set is rejected before
# compute() runs).
_VALID_METRICS: frozenset[str] = frozenset(
    {
        "implied_rate_level",
        "implied_rate_change",
        "volume_level",
        "open_interest_level",
    }
)


# ============================================================================
# INPUT
# ============================================================================

class ScanPolicyFuturesExtremesInput(BaseModel):
    """Parameters the LLM extracts to scope the policy-futures
    universe scan.

    No ``field_name`` input — the three field mnemonics are
    YAML-owned conventions; the scan reads PX_LAST / PX_VOLUME /
    OPEN_INT by construction. See module docstring for the
    input-schema-overreach rationale.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_families: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional scope filter. None = scan the full "
            "policy-futures universe (every curve family in "
            "``policy_futures_curve_families`` from config.yaml — "
            "currently SOFR_FUT, EUR_SHORT_RATE_FUT, SONIA_FUT). "
            "Pass a list to narrow (e.g. ['SOFR_FUT', "
            "'EUR_SHORT_RATE_FUT'] for a USD + EUR STIR sweep). "
            "Every entry MUST be a policy-futures curve family per "
            "ADR 0011 — passing a bond-futures curve (UST_FUT / "
            "DE_FUT / ...) or a non-futures curve (sovereign / OIS "
            "/ inflation) is REFUSED at schema-validation time; "
            "route those to the bond_futures / sovereign_bonds / "
            "ois / inflation_indexed_bonds / inflation_swaps "
            "agents respectively."
        ),
    )
    top_n: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of extreme stems to return PER METRIC. When "
            "None (default), compute resolves to the YAML's "
            "``default_top_n`` convention (currently 5) — keeps "
            "the default YAML-locked per PR9 / PR10. Bounded "
            "[1, 50] at the schema layer to keep the response "
            "payload reasonable for an LLM context window: 50 per "
            "metric × 4 metrics = 200 rows hard ceiling. An LLM "
            "may still pass an explicit integer in-bounds to "
            "override the YAML default per query."
        ),
    )
    min_abs_z_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold; stems below the "
            "threshold are filtered OUT of the ranking. When None "
            "(default), compute resolves to the YAML's "
            "``default_min_abs_z_score`` convention (currently "
            "1.5 — matches the bond_futures / sovereign / OIS "
            "scanner conventions) — keeps the default YAML-locked "
            "per PR9 / PR10. The non-negative bound (ge=0.0) "
            "stays in code as a structural invariant. The filter "
            "is applied PER METRIC (a stem may pass the threshold "
            "on implied_rate_level but fail on volume_level; it "
            "appears in the implied_rate_level top-N only)."
        ),
    )
    metrics: Optional[List[ScanMetric]] = Field(
        default=None,
        description=(
            "Optional subset of the four metrics to rank. When "
            "None (default), compute resolves to the YAML's "
            "``default_metrics`` convention — all four metrics "
            "(implied_rate_level, implied_rate_change, "
            "volume_level, open_interest_level). Pass a list to "
            "narrow (e.g. ``['implied_rate_level']`` for a rate-"
            "only screen). Each entry MUST be a member of the "
            "closed ScanMetric Literal — values outside the four "
            "are REFUSED at schema-validation time."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "As-of date for the scan. When omitted (default), the "
            "scan uses the most-recent shared trading day "
            "available in the DB across the universe (max "
            "trade_date observed across the per-stem aligned "
            "series) — same as-of resolution pattern as "
            "scan_bond_futures_extremes / "
            "scan_inflation_swaps_extremes. When provided, the "
            "scan's per-stem time series are CAPPED at the "
            "supplied date before z-scoring, so the ranking is "
            "deterministic across runs (same as_of_date + same DB "
            "state ⇒ same ranking). The fetch window itself is "
            "methodology — derived from YAML "
            "(``z_score_window_days`` * "
            "``z_score_buffer_multiplier``, currently 252 * 1.5 "
            "= 378 days) — and is NOT an LLM input. An "
            "as_of_date BEYOND the policy-futures universe's last "
            "observed trade_date returns the documented "
            "controlled-error envelope "
            "(``{\"error\": \"no scoreable stems: ...\"}``) with "
            "NO top-N rankings and NO scan_summary — the scan "
            "refuses to silently re-label an unbounded ranking "
            "as a future-anchored read (P5 / PR8 honest-"
            "disclosure). An as_of_date BEFORE any ingested data "
            "returns the same controlled-error envelope (no "
            "aligned observations) — both off-data anchors raise "
            "no exception."
        ),
    )

    @model_validator(mode="after")
    def _validate_curve_families_whitelist(
        self,
    ) -> "ScanPolicyFuturesExtremesInput":
        """Refuse non-policy-futures curve families at
        schema-validation time. See module docstring for the
        whitelist rationale."""
        if self.curve_families is None:
            return self
        if len(self.curve_families) == 0:
            raise ValueError(
                "curve_families must be None (full universe) or a "
                "non-empty list; got [] — pass None instead to "
                "scan the full policy-futures universe."
            )
        invalid = [
            cf for cf in self.curve_families
            if cf not in _POLICY_FUTURES_CURVE_FAMILIES
        ]
        if invalid:
            allowed = sorted(_POLICY_FUTURES_CURVE_FAMILIES)
            raise ValueError(
                f"curve_families={invalid!r} are not in the "
                f"policy-futures closed-family whitelist (per ADR "
                f"0011 V1 monitors-only scope). Allowed: "
                f"{allowed!r}. Bond-futures curves (UST_FUT / "
                "DE_FUT / ...) route to the bond_futures agent. "
                "Sovereign curves (UST / DE_BUND / ...) route to "
                "the sovereign_bonds agent. OIS curves route to "
                "the ois agent. Inflation curves route to the "
                "inflation_indexed_bonds / inflation_swaps "
                "agents."
            )
        return self

    @model_validator(mode="after")
    def _validate_metrics_whitelist(
        self,
    ) -> "ScanPolicyFuturesExtremesInput":
        """Refuse unknown metric identifiers. The closed Literal
        ScanMetric already enforces this at the field-type level
        (Pydantic rejects values outside the union), but an empty
        list is a separate degenerate case that the Literal does
        not catch — handle it explicitly."""
        if self.metrics is None:
            return self
        if len(self.metrics) == 0:
            raise ValueError(
                "metrics must be None (rank all four metrics) or "
                "a non-empty list; got [] — pass None instead to "
                "rank the full ScanMetric set."
            )
        # Defensive: Pydantic's Literal validation already rejects
        # invalid identifiers, but a sanity check here keeps the
        # error message honest (the Literal error path produces a
        # less-readable enumeration of allowed values than this).
        invalid = [m for m in self.metrics if m not in _VALID_METRICS]
        if invalid:
            allowed = sorted(_VALID_METRICS)
            raise ValueError(
                f"metrics={invalid!r} are not in the closed "
                f"ScanMetric whitelist. Allowed: {allowed!r}."
            )
        return self


# ============================================================================
# RESULT ROW
# ============================================================================

class ScanPolicyFuturesExtremesResultRow(BaseModel):
    """One ranked extreme on one metric.

    Snapshot fields (``current_raw_price`` / ``implied_rate_pct`` /
    ``daily_change_implied_rate_bps`` / ``current_volume`` /
    ``current_open_interest`` / ``delta_open_interest_1d``) are
    surfaced on every row so a consumer reading the scan does not
    need a second tool call to see the per-strip context that
    produced the z-score.

    The ``z_score`` field is the z-score of THIS row's ``metric``
    (not a composite, not a different metric's z). The ``signal``
    is derived from ``z_score``'s sign on a row that passed the
    ``min_abs_z_score`` filter.

    The ``short_rate_regime`` field carries the per-row RFR-vs-IBOR
    disclosure (ADR 0011) so a desk consumer copying ONE row out
    of the scan still sees the regime label.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = Field(
        ...,
        ge=1,
        description=(
            "Rank WITHIN this metric's top-N (1 = most extreme by "
            "absolute z-score for this metric). NOT a global rank "
            "across metrics — a stem may rank #1 on "
            "implied_rate_level and #3 on volume_level in the "
            "same response."
        ),
    )
    metric: ScanMetric = Field(
        ...,
        description=(
            "Which of the four metrics this row is ranked on — "
            "'implied_rate_level' (level z in PERCENT), "
            "'implied_rate_change' (1-day Δ z in BPS), "
            "'volume_level' (level z in CONTRACTS), "
            "'open_interest_level' (level z in CONTRACTS). Closed "
            "enum; see module docstring for the four-metric "
            "concept."
        ),
    )
    curve_family: PolicyFuturesScanCurveFamily = Field(
        ...,
        description=(
            "Policy-futures curve family (SOFR_FUT / "
            "EUR_SHORT_RATE_FUT / SONIA_FUT)."
        ),
    )
    strip_position: int = Field(
        ...,
        ge=1,
        le=8,
        description=(
            "1-based strip position (1 = front contract; 2..8 = "
            "quarterly forwards). The canonical disambiguator for "
            "the policy-futures universe — (curve_family, "
            "strip_position) uniquely identifies one stem on the "
            "V1 playbook."
        ),
    )
    contract_code: str = Field(
        ...,
        description=(
            "Master rolling-generic stem from instrument_master "
            "(e.g. 'SFR1' / 'ER1' / 'SFI1' / 'SFR2' / ...). NOT "
            "the per-window underlying contract code (SFRH6 / "
            "ERM6 / ...) — that lives on "
            "``underlying_contract_code``."
        ),
    )
    underlying_contract_code: Optional[str] = Field(
        None,
        description=(
            "Current-front underlying contract code that the strip "
            "slot resolves to AS OF the snapshot's as_of_date "
            "(from the SCD2 history bounded by as_of). E.g. "
            "'SFRH6 COMB' for the March-2026 SFR1 read on a "
            "2026-04-08 anchor. None when the SCD2 history has no "
            "row for this stem on the anchor date."
        ),
    )
    security_name: Optional[str] = Field(
        None,
        description=(
            "Current-front security name from the SCD2 history "
            "(when populated). Mirrors the policy_futures sibling "
            "tools' disclosure block — surfaces honestly even "
            "when None (a column-of-Nones would be a P5 "
            "violation; surfacing what IS populated is the honest "
            "path)."
        ),
    )
    expiry_date: Optional[str] = Field(
        None,
        description=(
            "Current-front expiry date from the SCD2 history "
            "(YYYY-MM-DD or None). Surfaces the strip slot's "
            "current underlying-contract expiry on the as_of_date."
        ),
    )
    contract_size: Optional[float] = Field(
        None,
        description=(
            "Current-front contract size in the contract's native "
            "notional units (None when the SCD2 history lacks the "
            "row). E.g. 2500.0 for SOFR_FUT, 1000000.0 for "
            "EUR_SHORT_RATE_FUT / SONIA_FUT."
        ),
    )
    inverse_priced: bool = Field(
        ...,
        description=(
            "Per-stem inverse-pricing flag from "
            "``instrument_master.attributes->>'inverse_pricing'``."
            " When true (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT "
            "in V1): ``implied_rate_pct = 100 - raw_price``. When "
            "false (theoretical direct-priced family — not in V1): "
            "``implied_rate_pct = raw_price``."
        ),
    )
    short_rate_regime: Literal["RFR", "IBOR"] = Field(
        ...,
        description=(
            "Per-row short-rate regime disclosure (ADR 0011). "
            "'RFR' = compounded daily risk-free rate (SOFR_FUT / "
            "SONIA_FUT); 'IBOR' = unsecured 3M term IBOR "
            "(EUR_SHORT_RATE_FUT). The scan ranks across this "
            "heterogeneity — desk consumers must see the regime "
            "label inline so they cannot mistake an IBOR z-score "
            "for an RFR z-score."
        ),
    )
    quote_units: str = Field(
        ...,
        description=(
            "Quoted-units disclosure for the raw_price axis on "
            "this row. '100 - rate' for inverse-priced strips; "
            "'rate (%)' for direct-priced strips. Mirrors the "
            "sibling futures_price_level / futures_strip_snapshot "
            "convention."
        ),
    )
    as_of_date: str = Field(
        ...,
        description=(
            "Most recent trading date on which all three series "
            "(price + volume + OI) for this stem have a value "
            "after cleaning (YYYY-MM-DD). Anchored per-stem so "
            "the snapshot row cannot pair a fresh implied rate "
            "with a stale OI reading."
        ),
    )
    current_raw_price: Optional[float] = Field(
        None,
        description=(
            "Latest cleaned, ffilled raw price in the contract's "
            "native quote space (for inverse-priced strips: "
            "``100 - rate``). Rounded with "
            "``raw_price_round_decimals`` from config.yaml. "
            "Equals the futures_price_level snapshot's "
            "``raw_price`` for the same stem on the same "
            "as_of_date."
        ),
    )
    implied_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Latest implied rate in PERCENT, derived from "
            "``current_raw_price`` per the per-stem "
            "``inverse_priced`` flag. PR14 wire-frozen field name "
            "— do NOT rename to ``implied_rate`` / ``_bps`` / "
            "etc. across the policy_futures domain. Rounded with "
            "``implied_rate_round_decimals`` from config.yaml. "
            "Equals the futures_price_level snapshot's "
            "``implied_rate_pct`` for the same stem on the same "
            "as_of_date."
        ),
    )
    daily_change_implied_rate_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day raw subtraction on the implied-rate "
            "axis, multiplied by 100 to land in BPS space (matches "
            "the scan_inflation_swaps_extremes /"
            " inflation_swap_rate_level bps wire convention). The "
            "implied_rate_change metric's z-score is computed on "
            "the rolling sequence of these 1-day bps changes; the "
            "latest sample is surfaced here so consumers can read "
            "the per-strip move alongside its z-score."
        ),
    )
    current_volume: Optional[float] = Field(
        None,
        description=(
            "Latest daily traded volume in CONTRACTS (NOT "
            "notional). Rounded with ``volume_round_decimals`` "
            "from config.yaml. Equals the "
            "volume_open_interest_snapshot snapshot's "
            "``current_volume`` for the same stem on the same "
            "as_of_date."
        ),
    )
    current_open_interest: Optional[float] = Field(
        None,
        description=(
            "Latest end-of-day open interest in CONTRACTS (NOT "
            "notional). Rounded with ``oi_round_decimals`` from "
            "config.yaml. Equals the volume_open_interest_snapshot"
            " snapshot's ``current_open_interest`` for the same "
            "stem on the same as_of_date."
        ),
    )
    delta_open_interest_1d: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in OI level (raw subtraction; "
            "NOT multiplied by 100). Whole-contract delta. "
            "Surfaced for context — the open_interest_level "
            "metric ranks on the LEVEL z-score, not the Δ "
            "z-score."
        ),
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of THIS row's "
            "metric (see ``metric``). The ranking key for this "
            "row's place within its metric's top-N. The lookback "
            "window is disclosed verbatim on "
            "``methodology_disclosure`` per the catalog "
            "guardrail (it is NOT embedded in the field name — "
            "see module docstring)."
        ),
    )
    signal: Literal["EXTREME_HIGH", "EXTREME_LOW"] = Field(
        ...,
        description=(
            "Derived from z_score's sign: EXTREME_HIGH when "
            "z > 0, EXTREME_LOW when z < 0. Rows with |z| < "
            "min_abs_z_score are filtered out before ranking, so "
            "z != 0 on every row reaching the output."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0011 / catalog-guardrail disclosure — "
            "REQUIRED on every row (not just on the response). "
            "Includes the universe-wide strip-scan label, the "
            "explicit z-score lookback window, the per-row "
            "RFR-vs-IBOR regime caveat, the inverse-pricing rule, "
            "the rolling-generic strip caveat (per-contract "
            "underlying rolls quarterly), and the non-CTD / "
            "non-OIS / non-policy-path / non-pack-average / "
            "morning-screen-not-tactical-signal scope statement."
        ),
    )


# ============================================================================
# OUTPUT
# ============================================================================

class ScanPolicyFuturesExtremesOutput(BaseModel):
    """Top-level response for the scan_policy_futures_extremes
    tool.

    Wire shape:
      - ``scan_summary``: human-readable one-line summary of how
        many stems were scanned, how many passed the threshold per
        metric, and the as_of_date span observed across stems.
      - ``results``: ranked rows, top ``top_n`` per metric,
        ordered by ``metric`` then ``rank``.
      - ``methodology_disclosure``: the P5 / ADR 0011 caveat
        carried on the response level. REQUIRED so consumers
        cannot drop the disclosure when relaying the response.
        (The per-row disclosure on each result is also required;
        the response-level field is a belt-and-braces safeguard.)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scan_summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable one-line summary (e.g. 'Scanned 24 "
            "policy-futures stems. Top 5 per metric "
            "(implied_rate_level, implied_rate_change, "
            "volume_level, open_interest_level) with |z| >= 1.5. "
            "as_of 2026-04-08.')."
        ),
    )
    results: List[ScanPolicyFuturesExtremesResultRow] = Field(
        default_factory=list,
        description=(
            "Ranked result rows, top ``top_n`` per metric. "
            "Ordered by metric (implied_rate_level, "
            "implied_rate_change, volume_level, "
            "open_interest_level) then by rank within metric "
            "(1 = most extreme). May be shorter than "
            "``top_n * 4`` if some metrics have fewer stems "
            "passing the threshold."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / ADR 0011 / catalog-guardrail disclosure carried "
            "on the response level: includes the universe-wide "
            "strip-scan label, the explicit z-score lookback "
            "window (per the catalog's methodology guardrail), "
            "the RFR-vs-IBOR regime caveat per curve_family, the "
            "inverse-pricing rule, the rolling-generic strip "
            "caveat, and the morning-screen-not-tactical-signal "
            "scope statement. REQUIRED so consumers cannot drop "
            "the disclosure when relaying the response."
        ),
    )


__all__ = [
    "ScanMetric",
    "PolicyFuturesScanCurveFamily",
    "ScanPolicyFuturesExtremesInput",
    "ScanPolicyFuturesExtremesResultRow",
    "ScanPolicyFuturesExtremesOutput",
]
