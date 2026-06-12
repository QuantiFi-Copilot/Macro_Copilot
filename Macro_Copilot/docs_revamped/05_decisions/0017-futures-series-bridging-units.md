# ADR 0017 — Futures series bridging: `PRICE` + `CONTRACTS` units and canonical TimeSeries exports on the futures history primitives

**Status:** Accepted
**Date:** 2026-06-12
**Builds on:** ADR 0013 (futures domain agents — sanctioned the bond_futures / policy_futures primitives with bespoke `time_series` row shapes precisely because `TimeSeriesUnits` had no honest member for native-quote prices or contract counts), ADR 0016 (Decision 4 — operators never silently convert units; `convert_units` is the sole conversion site).
**Operationalises principles:** P5 (no hidden methodology — the unit declared on the wire must be the unit the numbers are in), P8 (closed-family discipline — this is the documented decision the family extension requires per [`../03_standards/closed_family_discipline.md`](../03_standards/closed_family_discipline.md)).
**Scope:** Extends the closed `TimeSeriesUnits` enum with two members and adds canonical `TimeSeries` export fields to the five futures primitives that return dated history, so the open-DAG lane can bind them as Series leaves. Snapshot-only futures primitives stay `TERMINAL_ONLY_SNAPSHOT`.

---

## Context

The composability audit (`orchestrator/open_dag/composability_audit.py`) classifies a
primitive `BRIDGEABLE_SERIES` only when it declares non-empty `output_field_units`,
and the Series bridge (`shared/artifacts/adapters/from_time_series.py`) only lifts
canonical `shared.schemas.time_series.TimeSeries` fields. The futures history
primitives (bond_futures price level / volume-OI; policy_futures price level /
calendar spread / volume-OI) were registered with `output_field_units={}` because
their honest unit spaces — native quoted price (TY1 points, RX1 % of par, SFR
`100 - rate`) and contract counts — had no member in the closed `TimeSeriesUnits`
family, and ADR 0013 did not authorise the extension. Consequence: every
price-history / spread-history / volume-OI-history question routed to the open-DAG
lane refused honestly at the catalogue level (campaign failures k03, k04, l01, l03).

## Decision

1. **Extend `TimeSeriesUnits` with two members** (single source of truth,
   `shared/schemas/time_series.py`; `shared/artifacts/units.py` re-exports):
   - `PRICE = "price"` — a quoted futures/contract price in the contract's NATIVE
     quote space. The emitting tool's `quote_units` snapshot field remains the
     authoritative disclosure of the exact space; `PRICE` deliberately does NOT
     claim a dimensional identity (points vs % of par vs `100 - rate`).
   - `CONTRACTS = "contracts"` — futures contract counts (trade volume, open
     interest). Distinct from `COUNT` (observation counts / quality flags) so a
     volume series cannot be confused with an observation-count diagnostic.
2. **Neither member gets a `convert_units` factor.** There is no exact dimensional
   factor from native-quote price or contract counts to percent/bps; per ADR 0016
   Decision 4 cross-unit operations refuse, and `convert_units` raises
   `NotImplementedError` for undeclared pairs by design. No table change.
3. **Canonical TimeSeries exports on the five dated-history futures primitives**,
   following the already-established side-by-side pattern of
   `futures_butterfly_simple` (bespoke wire rows for the frontend + canonical
   `TimeSeries` fields for the substrate, built from the SAME display slices so
   they cannot drift):
   - bond_futures `futures_price_level` → `time_series_price` (`price`)
   - bond_futures `futures_volume_oi` → `time_series_volume`,
     `time_series_open_interest` (both `contracts`)
   - policy_futures `futures_price_level` → `time_series_implied_rate` (`percent`)
   - policy_futures `futures_calendar_spread` → `time_series_spread_implied_rate`
     (`percent`)
   - policy_futures `volume_open_interest_snapshot` → `time_series_volume`,
     `time_series_open_interest` (both `contracts`)
   Each gains the matching `output_field_units` registration in
   `rates_agent/workflows/__init__.py`. Only the canonical fields are declared in
   `output_field_units` (the bespoke `time_series` row lists stay undeclared so the
   validator refuses a binding the bridge cannot lift).
4. **What stays `TERMINAL_ONLY_SNAPSHOT`:** `futures_strip_snapshot` (one row per
   strip position, all at a single `as_of_date` — no dated history; flat output
   without `current_metrics`), every `scan_*_extremes` scanner, and
   `volume_open_interest`/price snapshot facets that are point-in-time only.
   Raw-price facets of the policy primitives are NOT exported canonically — the
   desk-recognised read is the implied rate; the raw price stays on the bespoke
   rows with `quote_units` disclosure.

## Consequences

- The open-DAG Selector catalogue now lists the five primitives with exactly the
  canonical fields above (`orchestrator/selectors.py` `_series_typed_fields`
  introspection); k03 / k04 / l01 / l03-class questions can compose on them.
- Operators that do unit algebra treat the new members generically (same-unit
  checks, diff preserves units, correlation → `ratio`); `series_arithmetic` on a
  `price` vs `percent` pair refuses, which is the correct behaviour.
- Frontend treats units as string tags (no closed TS union to extend); the new
  Output fields are additive on the wire.
