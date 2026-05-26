"""Pydantic schemas for the scan_inflation_linkers_extremes tool.

Universe-wide linker real-yield extremes scan — ranks every
``(curve_family, tenor)`` linker observation by absolute 252-day
z-score of its real-yield LEVEL and returns the top-N extremes.
Catalog id ``inflation_linkers__scan_inflation_linkers_extremes``
(build_order 24).

Single-metric ranking
---------------------
The catalog's literal wording is "ranked by absolute 252-day z-score
of real yields" — a single metric (real-yield LEVEL z-score). The
sibling sovereign / OIS scanners ship single-metric rankings on the
level z-score. The bond_futures scanner is multi-metric only because
its catalog enumerated four metrics (price / Δprice / volume / OI);
the linker catalog enumerates one (``yield_mid``), so this scan
ranks on the level z-score only. ``daily_change_bps`` and
``monthly_change_bps`` are surfaced as per-row snapshot context
(matching the sovereign / OIS scanner row shape) but are NOT
separate rankings.

Per-row reference columns
-------------------------
Per the catalog's ``required_reference_metrics`` field, every output
row carries:

  - ``maturity_date`` — instrument maturity (joined from the enriched
    view via ``fetch_scan_universe_reference``).
  - ``country`` — country identifier (e.g. ``'US'``, ``'UK'``).
  - ``vendor_ticker`` — Bloomberg-grade desk identifier
    (e.g. ``'GTII10 Govt'``).

``security_name`` (third item in the catalog's
``required_reference_metrics``) is NOT in the output row schema:
the linker universe's
``instrument_metadata_history.security_name`` is universally NULL
on the current DB snapshot. Surfacing NULL on every row would be
a dead field; surfacing ``vendor_ticker`` under the
``security_name`` label would be a proxy violation per the no-
proxy rule. The compute layer attaches ``vendor_ticker`` under its
own name as the canonical desk identifier; the BUILDER REPORT
documents the missing-metadata path honestly.

Why no ``field_name`` input
---------------------------
The catalog enumerates the single upstream metric (``yield_mid``);
the Bloomberg mnemonic the linker playbook ingests under is
``YLD_YTM_MID``, which is locked as the YAML's ``default_field_name``
convention. Exposing it as a per-query input would be input-schema
overreach (PR8 / OPR8 — methodology knob masquerading as a per-query
choice).

Why ``top_n`` / ``min_abs_z_score`` are None-sentinel optionals
---------------------------------------------------------------
Both are DISPLAY thresholds — legitimate per-query inputs — AND
their defaults are methodology choices that the YAML owns
(``default_top_n`` / ``default_min_abs_z_score``).  A concrete
Field default would duplicate the YAML's authority and cause
silent drift on a YAML edit.  We resolve this honestly: the schema
accepts ``None`` and compute falls through to
``config.convention_value("default_top_n")`` /
``config.convention_value("default_min_abs_z_score")``.  The
validation bounds (``ge=1, le=50`` and ``ge=0.0``) stay on the
Field so a malformed LLM-supplied value still fails at the schema
layer — methodology in YAML, invariants in code (PR9 / PR10).
Mirrors the bond_futures scanner's None-sentinel optionals.

Why ``as_of_date`` (and why no ``lookback_days``)
-------------------------------------------------
``as_of_date`` is the legitimate per-query input that anchors the
scan to a specific trading day. The fetch window is METHODOLOGY —
derived from YAML (``z_score_window_days *
z_score_buffer_multiplier``) so the rolling 252d z-score is fully
populated for every stem on the anchor date. Exposing
``lookback_days`` as an LLM input would be a PR8 / OPR8 input-
schema overreach AND would make the output non-deterministic
across days when omitted.  When ``as_of_date`` is None, compute
resolves to the most-recent shared trading day in the fetched
universe (max trade_date observed across per-stem cleaned series)
— same pattern as the bond_futures scanner and the per-bond
``real_yield_level`` primitive's as_of resolution.

Field-name discipline on each result row
----------------------------------------
- ``real_yield_pct`` is the latest cleaned real yield in PERCENT
  (matches the upstream ``real_yield_level`` primitive's
  ``real_yield_pct`` field exactly — same data, same rounding).

- ``z_score_real_yield`` is the rolling 252d z-score of the real-
  yield LEVEL — the ranking key for this row.  The wire field name
  embeds ``real_yield`` to surface the metric explicitly (NOT
  generic ``z_score``) — the catalog's methodology guardrail says
  the disclosure must show why a bond ranked extreme, and the
  field name itself helps with that.  The 252d window itself is
  surfaced via ``methodology_disclosure`` rather than embedded in
  the field name (mirrors the bond_futures scanner's z_score
  naming convention — PR14 wire-format honesty satisfied by
  disclosure, not field-name embedding).

- ``signal`` is a closed enum ``Literal["EXTREME_HIGH",
  "EXTREME_LOW"]`` matching the sovereign / OIS / bond_futures
  scanner shape. EXTREME_HIGH when z > 0; EXTREME_LOW when z < 0.
  A z = 0 row would not pass the ``min_abs_z_score`` filter, so
  the enum is exhaustive on the rows that reach the output.

- ``methodology_disclosure`` is REQUIRED on every row (NOT only on
  the response) per the catalog guardrail.  Repeating the
  disclosure per row guarantees it survives any downstream
  consumer that flattens / re-orders / paginates the results.

Why ``curve_families`` is a list, not a single string
-----------------------------------------------------
A desk asks "where are the USD_TIPS and GBP_LINKER curves stretched?"
as ONE question, not two. Allowing
``curve_families=['USD_TIPS', 'GBP_LINKER']`` as one call avoids
forcing the LLM to assemble two scans. The list is validated
against the closed-family whitelist
(``inflation_linker_curve_families`` convention); a non-linker
curve_family (e.g. sovereign ``'UST'``) is REFUSED at schema-
validation time rather than silently included.

Validation layering
-------------------
- ``curve_families``: a model-level validator (post-construction)
  enforces the whitelist by reading the bundled ``config.yaml``'s
  ``inflation_linker_curve_families`` convention. Kept in the
  schema (not in compute) so the refusal fires at API boundary,
  not deep inside compute.

- ``min_abs_z_score >= 0`` enforced by ``Field(ge=0.0)`` (a
  negative filter threshold is structurally meaningless).

- ``top_n`` is bounded ``[1, 50]`` to keep the response payload
  reasonable for an LLM context window.

- ``frozen=True`` + ``extra="forbid"`` per typed-boundary
  discipline (``docs_revamped/03_standards/
  typed_boundary_discipline.md`` §1). No model carries
  pandas / numpy payloads, so ``arbitrary_types_allowed`` is
  intentionally omitted.
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


def _inflation_linker_curve_families_whitelist() -> frozenset[str]:
    """Load the closed-family whitelist of linker curve families from
    the bundled ``config.yaml``.

    Read once at module import time (the YAML is immutable; the lint +
    config-load tests validate its shape). Returning a frozenset keeps
    the membership check O(1) per validation call and prevents
    accidental mutation.

    Why read from YAML here rather than hardcode in the schema:
    PR7 (configuration offloading) — the whitelist IS a methodology
    convention (which curve families count as ``inflation_linker``
    vs nominal sovereign vs inflation swap), and it belongs in the
    YAML alongside the rest of the conventions. The schema validator
    references the same source of truth.
    """
    with _CONFIG_PATH.open() as f:
        raw = yaml.safe_load(f)
    csv_value = raw["conventions"]["inflation_linker_curve_families"]["value"]
    return frozenset(s.strip() for s in csv_value.split(",") if s.strip())


_INFLATION_LINKER_CURVE_FAMILIES: frozenset[str] = (
    _inflation_linker_curve_families_whitelist()
)


# ============================================================================
# INPUT
# ============================================================================

class ScanInflationLinkersExtremesInput(BaseModel):
    """Parameters the LLM extracts to scope the linker universe scan.

    No ``field_name`` / ``lookback_days`` / ``metrics`` input — the
    one metric (real-yield LEVEL z-score) IS the concept and the
    field mnemonic is YAML-owned.  See module docstring for the
    input-schema-overreach rationale.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_families: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional scope filter. None = scan the full linker "
            "universe (every curve family in "
            "``inflation_linker_curve_families`` from config.yaml — "
            "currently USD_TIPS, GBP_LINKER, EUR_FR_LINKER, "
            "CAD_RRB). Pass a list to narrow (e.g. "
            "['USD_TIPS', 'GBP_LINKER'] for a US + UK sweep). Every "
            "entry MUST be a linker curve family — passing a "
            "nominal sovereign ('UST', 'DE_BUND') or an inflation-"
            "swap curve family is REFUSED at schema-validation "
            "time; route those to the sovereign_bonds / "
            "inflation_swaps agents respectively."
        ),
    )
    top_n: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description=(
            "Number of extreme stems to return. When None (default), "
            "compute resolves to the YAML's ``default_top_n`` "
            "convention (currently 5) — keeps the default YAML-"
            "locked per PR9 / PR10.  Bounded [1, 50] at the schema "
            "layer to keep the response payload reasonable for an "
            "LLM context window.  An LLM may still pass an explicit "
            "integer in-bounds to override the YAML default per "
            "query."
        ),
    )
    min_abs_z_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold; stems below the "
            "threshold are filtered OUT of the ranking.  When None "
            "(default), compute resolves to the YAML's "
            "``default_min_abs_z_score`` convention (currently 1.5) "
            "— keeps the default YAML-locked per PR9 / PR10.  The "
            "non-negative bound (ge=0.0) stays in code as a "
            "structural invariant."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=(
            "As-of date for the scan.  When omitted (default), the "
            "scan uses the most-recent shared trading day available "
            "in the DB across the universe (max trade_date observed "
            "across per-stem cleaned series) — same as_of resolution "
            "pattern as the upstream ``real_yield_level`` primitive "
            "and the bond_futures scanner.  When provided, the "
            "scan's per-stem time series are CAPPED at the supplied "
            "date before z-scoring, so the ranking is deterministic "
            "across runs (same as_of_date + same DB state ⇒ same "
            "ranking).  The fetch window itself is methodology — "
            "derived from YAML "
            "(``z_score_window_days`` * "
            "``z_score_buffer_multiplier``, currently 252 * 1.5 = "
            "378 days) — and is NOT an LLM input.  An as_of_date "
            "BEYOND the linker universe's last observed trade_date "
            "returns the documented controlled-error envelope "
            "({\"error\": \"no scoreable stems: ...\"}) with NO "
            "rankings and NO scan_summary — the scan refuses to "
            "silently re-label an unbounded ranking as a future-"
            "anchored read (P5 / PR8 honest-disclosure)."
        ),
    )

    @model_validator(mode="after")
    def _validate_curve_families_whitelist(
        self,
    ) -> "ScanInflationLinkersExtremesInput":
        """Refuse non-linker curve families at schema-validation time.
        See module docstring for the whitelist rationale."""
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
            if cf not in _INFLATION_LINKER_CURVE_FAMILIES
        ]
        if invalid:
            allowed = sorted(_INFLATION_LINKER_CURVE_FAMILIES)
            raise ValueError(
                f"curve_families={invalid!r} are not in the "
                f"inflation_indexed_bonds closed-family whitelist. "
                f"Allowed: {allowed!r}.  Nominal sovereign curves "
                "('UST', 'DE_BUND', 'UK_GILT', etc.) route to the "
                "sovereign_bonds agent's scan_extremes_tool; "
                "inflation-swap curve families route to the "
                "inflation_swaps agent."
            )
        return self


# ============================================================================
# RESULT ROW
# ============================================================================

class ScanInflationLinkersExtremesResultRow(BaseModel):
    """One ranked extreme on the linker real-yield universe scan.

    Snapshot fields (``real_yield_pct`` / ``daily_change_bps`` /
    ``monthly_change_bps``) are surfaced on every row so a consumer
    reading the scan does not need a second tool call to see the
    per-bond context that produced the z-score.  Per-row reference
    columns (``maturity_date`` / ``country`` / ``vendor_ticker``)
    answer "which security ranked extreme?" at a desk-recognisable
    level.

    The ``z_score_real_yield`` field is the z-score of THIS row's
    real-yield level — the ranking key.  ``signal`` is derived from
    ``z_score_real_yield``'s sign on a row that passed the
    ``min_abs_z_score`` filter (the filter rejects |z| < threshold,
    so z != 0 on every row that reaches the output).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = Field(
        ...,
        ge=1,
        description=(
            "Rank within the top-N (1 = most extreme by absolute "
            "real-yield z-score).  Single-metric ranking per the "
            "catalog."
        ),
    )
    curve_family: str = Field(
        ...,
        description=(
            "Linker curve family identifier (e.g. 'USD_TIPS', "
            "'GBP_LINKER', 'EUR_FR_LINKER', 'CAD_RRB')."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor pillar (e.g. '5Y', '10Y', '30Y').  Together with "
            "``curve_family`` uniquely identifies the linker on the "
            "universe scan."
        ),
    )
    as_of_date: str = Field(
        ...,
        description=(
            "Latest cleaned trading date for this stem (YYYY-MM-DD).  "
            "Per-stem — different linkers can have different latest "
            "observations on the same scan run because of country-"
            "specific holiday calendars; the snapshot row anchors "
            "the displayed real-yield level / changes / z-score to "
            "THIS stem's latest trading day."
        ),
    )
    real_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest cleaned real yield in PERCENT.  Equals the "
            "upstream ``real_yield_level`` primitive's "
            "``real_yield_pct`` for the same stem on the same "
            "as_of_date (same data, same rounding)."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None,
        description=(
            "1-trading-day change in basis points.  Per-row snapshot "
            "context (matches the sovereign / OIS scanner row "
            "shape); NOT a separate ranking metric.  None when the "
            "stem has fewer than 2 cleaned observations."
        ),
    )
    monthly_change_bps: Optional[float] = Field(
        None,
        description=(
            "22-trading-day (~1 month) change in basis points.  "
            "Per-row snapshot context (matches the per-bond "
            "real_yield_level primitive's ``monthly_change_bps``); "
            "NOT a separate ranking metric.  None when the stem has "
            "fewer than 22 cleaned observations."
        ),
    )
    z_score_real_yield: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the real-yield "
            "LEVEL — the ranking key for this row.  The lookback "
            "window is disclosed verbatim on "
            "``methodology_disclosure`` per the catalog guardrail "
            "(it is NOT embedded in the field name — see module "
            "docstring)."
        ),
    )
    signal: Literal["EXTREME_HIGH", "EXTREME_LOW"] = Field(
        ...,
        description=(
            "Derived from z_score_real_yield's sign: EXTREME_HIGH "
            "when z > 0, EXTREME_LOW when z < 0.  Rows with |z| < "
            "min_abs_z_score are filtered out before ranking, so "
            "z != 0 on every row reaching the output."
        ),
    )
    maturity_date: Optional[str] = Field(
        None,
        description=(
            "Linker maturity (YYYY-MM-DD).  Per-row reference column "
            "joined from the enriched view via "
            "``fetch_scan_universe_reference``.  May be None if the "
            "instrument lacks a fixed maturity (defensive — every "
            "linker on the current snapshot has a maturity)."
        ),
    )
    country: Optional[str] = Field(
        None,
        description=(
            "Country identifier (e.g. 'US', 'UK', 'France', "
            "'Canada').  Per-row reference column joined from the "
            "enriched view.  Surfaces the index-family the linker "
            "references (CPI-U / RPI / HICP / CAN_CPI) — the "
            "methodology disclosure explains why this matters."
        ),
    )
    vendor_ticker: Optional[str] = Field(
        None,
        description=(
            "Bloomberg-grade desk identifier "
            "(e.g. 'GTII10 Govt' for the US 10Y TIPS generic).  "
            "Per-row reference column joined from the enriched "
            "view; surfaces the canonical Bloomberg identifier of "
            "the linker.  ``security_name`` (third item in the "
            "catalog's required_reference_metrics) is NOT surfaced "
            "because the linker universe's "
            "instrument_metadata_history.security_name is "
            "universally NULL on the current DB snapshot — the "
            "honest no-proxy substitute is this Bloomberg ticker, "
            "under its own name (NOT relabelled as "
            "``security_name``)."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / catalog-guardrail disclosure — REQUIRED on every "
            "row (not just on the response) per the catalog "
            "wording: 'Output rows MUST include the methodology "
            "disclosure tag so downstream consumers see why a bond "
            "ranked extreme.' Includes the universe-wide linker "
            "real-yield label, the explicit z-score lookback "
            "window, the INDEX-FAMILY + MARKET-STRUCTURE caveats, "
            "and the 'morning screen, NOT a tactical signal' "
            "scope statement."
        ),
    )


# ============================================================================
# OUTPUT
# ============================================================================

class ScanInflationLinkersExtremesOutput(BaseModel):
    """Top-level response for the scan_inflation_linkers_extremes tool.

    Wire shape:
      - ``scan_summary``: human-readable one-line summary of how
        many stems were scanned, how many scoreable, how many
        passed the threshold, and the as_of_date span observed.
      - ``results``: ranked rows, top ``top_n`` by absolute
        z-score.
      - ``methodology_disclosure``: the P5 / catalog-guardrail
        caveat carried on the response level — includes the
        explicit z-score lookback window per the catalog's
        methodology guardrail.  REQUIRED so consumers cannot drop
        the disclosure when relaying the response. (The per-row
        disclosure on each result is also required; the response-
        level field is a belt-and-braces safeguard.)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scan_summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable one-line summary (e.g. 'Scanned 24 "
            "linker stems (22 scoreable). Stems with |z| >= 1.5: 7. "
            "Showing top 5 by absolute real-yield z-score. as_of "
            "dates span 2026-04-07 to 2026-04-08.')."
        ),
    )
    results: List[ScanInflationLinkersExtremesResultRow] = Field(
        default_factory=list,
        description=(
            "Ranked result rows, top ``top_n`` by |z_score_real_yield|.  "
            "May be shorter than ``top_n`` if fewer stems pass the "
            "threshold."
        ),
    )
    methodology_disclosure: str = Field(
        ...,
        min_length=1,
        description=(
            "P5 / catalog-guardrail disclosure carried on the "
            "response level: includes the universe-wide linker "
            "real-yield label, the explicit z-score lookback "
            "window, the INDEX-FAMILY + MARKET-STRUCTURE caveats, "
            "and the 'morning screen, NOT a tactical signal' "
            "scope statement.  REQUIRED so consumers cannot drop "
            "the disclosure when relaying the response."
        ),
    )


__all__ = [
    "ScanInflationLinkersExtremesInput",
    "ScanInflationLinkersExtremesResultRow",
    "ScanInflationLinkersExtremesOutput",
]
