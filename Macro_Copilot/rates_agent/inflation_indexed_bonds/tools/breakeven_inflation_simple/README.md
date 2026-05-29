# calculate_breakeven_inflation_simple_tool

> What inflation rate is the bond market pricing in for the US, UK, France, or Canada? Breakeven inflation — the gap between a nominal government bond yield and the matching inflation-linked bond's real yield — with today's move and how stretched it is vs the past year. (Inflation compensation, not a clean expected-inflation read.)

**Phase-1 Stage-B tool** — brought to full parity with [`get_real_yield_level_tool`](../real_yield_level/README.md) under the Phase-1 standards: [`methodology_exposure.md`](../../../../docs_revamped/03_standards/methodology_exposure.md) (standalone-bridge + per-convention exposure), [`rendering_density.md`](../../../../docs_revamped/03_standards/rendering_density.md) (dual Build-view contract), [`lifecycle_checklist_template.md`](../../../../docs_revamped/03_standards/lifecycle_checklist_template.md) (per-tool progress).

This README is the human-readable consolidation of the per-tool truths — it mirrors the DB, config.yaml, schemas, and tests; it is NOT the source of truth for any field. See [`LIFECYCLE_CHECKLIST.md`](LIFECYCLE_CHECKLIST.md) for closeout status.

---

## For desk users

### What this tool tells you

The current bond-implied breakeven inflation for one same-country pair — UST vs US TIPS, UK Gilt vs index-linked Gilt, French OAT vs OATei, or Canadian government vs RRB — at a chosen tenor. Returns the breakeven in basis points (and percent), the move today / this week / this month (bps), a rolling 252-day z-score against its own history, the trailing 252-day high / low / percentile, AND the two underlying yields used to form it (so the nominal − real decomposition is auditable).

A bond-implied breakeven is computed as `nominal_yield_pct − real_yield_pct`. It is the market's required **compensation** for bearing inflation — NOT a clean forecast of expected inflation. Academically, breakeven = expected inflation + inflation risk premium − relative liquidity premium.

### When to use it

- **Morning briefing** — *"where's US 10Y breakeven this morning?"* Glance the Monitor tile.
- **Inflation pricing** — *"is the market pricing more or less inflation than last month?"* Open the extended view; read the z-score regime + percentile.
- **Real/nominal decomposition** — pair with the real-yield level: real yields up + breakevens down = real tightening; real yields down + breakevens up = reflationary easing.
- **Cross-country comparison** — *"compare US vs UK vs French 10Y breakeven"*. Multi-tool query; each renders as a compact card in a DAG.

### How to read the output

- **`breakeven_bps`** — basis points (e.g. `+246`). The desk-canonical headline.
- **`breakeven_pct`** — the same number ÷ 100 (e.g. `+2.46`).
- **`daily/weekly/monthly_change_bps`** — bps. Positive = breakeven widened (inflation compensation rose).
- **`current_z_score`** — `|z| ≥ 1.5` = Elevated; `|z| ≥ 2.0` = Extreme.
- **`percentile_252d`** — 0–100 within the trailing 252-day range.
- **`nominal_yield_pct` / `real_yield_pct`** — the two legs, for sanity-checking the decomposition.

### Known limitations

Quoted from DB `tool_metadata.known_limitations` ([migration](../../../../database/migrations/2026-05-28_phase1_calculate_breakeven_inflation_simple_curated.sql)):

> Returns INFLATION COMPENSATION, not a clean expected-inflation read — the differential carries an inflation risk premium and a relative liquidity premium. The "simple" suffix is deliberate: unadjusted nominal − real with no carry / seasonality / risk-premium adjustment (a desk-grade expected-inflation read would ship as a separate primitive — TD #32 / TD #33). Same-country construction only (UST + USD_TIPS, UK_GILT + GBP_LINKER, FR_OAT + EUR_FR_LINKER, CANADA_GOVT + CAD_RRB); cross-country pairs are refused at compute time. The instrument_type discriminators (linker = inflation_linker; nominal = sovereign_benchmark) are hard-coded in compute.py — the no-proxy guard. Generic benchmark series only, not bond-level analytics. Wire-frozen `trailing_range_window_days` (252). Three rolling-z-score conventions + field_name exposed as per-call Input overrides; the rest stay YAML-locked.

Per-country caveats surface in the UI via the shared registry at [`UI/.../shared/build/lib/countryCaveats.ts`](../../../../UI/macro-copilot-dashboard-polished/src/components/shared/build/lib/countryCaveats.ts).

---

## For developers

### Theoretical reference

DB-authoritative; quoted from `tool_metadata.theoretical_reference`:

> Tuckman & Serrat (2022), *Fixed Income Securities*, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) — bond-implied breakeven as the nominal-minus-real differential + the decomposition breakeven = expected inflation + inflation risk premium − relative liquidity premium. Per-country primary issuer documentation for the matched nominal + linker pairs (US Treasury / TreasuryDirect; UK DMO; Agence France Trésor; Bank of Canada).

**Source-material verification status:** ⏸ pending (per [`LIFECYCLE_CHECKLIST.md`](LIFECYCLE_CHECKLIST.md) Stage 8).

### Methodology

Conventions live in [`config.yaml`](config.yaml). Each entry carries `value`, `source`, `rationale`, `valid_range`, and an `exposure:` block per [`methodology_exposure.md`](../../../../docs_revamped/03_standards/methodology_exposure.md).

**Exposed conventions** (overridable per call; None → YAML default):

| Convention | Default | Range | Exposed input field |
|---|---|---|---|
| `z_score_window_days` | 252 | [60, 1260] | `z_score_window_days: Optional[int]` |
| `z_score_min_periods` | 60 | [20, 252] | `z_score_min_periods: Optional[int]` |
| `z_score_ddof` | 1 | [0, 1] | `z_score_ddof: Optional[int]` |
| `default_field_name` | YLD_YTM_MID | n/a | `field_name: Optional[str]` |

**YAML-locked conventions:** `z_score_buffer_multiplier`, `daily/weekly/monthly_change_offset_rows`, `trailing_range_window_days` (**wire-frozen**), `ffill_limit_days`, `bps_round_decimals`, `z_score_round_decimals`, `yield_round_decimals`. See each `exposure:` block for the Criterion-A/B rationale.

### Input contract

Pydantic class: [`schemas.py::BreakevenInflationSimpleInput`](schemas.py)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `nominal_curve_family` | `str` | required | UST / UK_GILT / FR_OAT / CANADA_GOVT |
| `linker_curve_family` | `str` | required | USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB |
| `tenor` | `str` | required | e.g. '10Y' (intersection of both legs) |
| `lookback_days` | `int` | 365 | display window |
| `field_name` | `Optional[str]` | `None` → YLD_YTM_MID | Bloomberg field, both legs |
| `z_score_window_days` | `Optional[int]` | `None` → 252 | rolling z window |
| `z_score_min_periods` | `Optional[int]` | `None` → 60 | rolling z min-periods |
| `z_score_ddof` | `Optional[int]` | `None` → 1 | sample (1) vs population (0) |

Validator `_curve_families_must_differ` (structural). Same-country invariant enforced at compute time (`_enforce_same_country_invariant`, DB lookup).

### Output contract

Pydantic class: [`schemas.py::BreakevenInflationSimpleOutput`](schemas.py):

- **`current_metrics: BreakevenInflationSimpleCurrentMetrics`** — snapshot (breakeven pct + bps, changes, z-score, range, the two underlying yields, `methodology_label`).
- **`time_series: List[...TimeSeriesRow]`** — bespoke `{date, breakeven_bps, z_score}` rows.
- **`time_series_breakeven: TimeSeries`** — canonical `TimeSeriesUnits.BPS`.
- **`time_series_zscore: TimeSeries`** — canonical `TimeSeriesUnits.Z_SCORE`.

**DB `output_field_units`** (JSONB): `{"time_series_breakeven": "bps", "time_series_zscore": "z_score"}`.

### Testing

- **Compute tests**: [`tests/test_breakeven_inflation_simple_compute.py`](../../../../tests/test_breakeven_inflation_simple_compute.py) — includes `TestInputOverrides` + `TestExposureBlockContract` (Phase-1 exposure surface).
- **Parity fixture**: [`tests/test_breakeven_inflation_simple_parity.py`](../../../../tests/test_breakeven_inflation_simple_parity.py) — default behaviour replayed (unchanged by the exposure work).
- **SQL ground-truth**: [`tests/test_breakeven_inflation_simple_sql_validation.py`](../../../../tests/test_breakeven_inflation_simple_sql_validation.py).
- **Wiring tests**: [`tests/test_breakeven_inflation_simple_wiring.py`](../../../../tests/test_breakeven_inflation_simple_wiring.py) — MCP wrapper (some require the `mcp` package; env-dependent).
- **Source-material verification**: DB `source_material_verified` NULL (⏸ pending, Stage 8).

### Frontend surfaces

- **Module folder**: [`UI/.../src/modules/primitives/calculate_breakeven_inflation_simple_tool/`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/)
  - `module.ts` — `tiers: [generic_runnable, custom_build_surface, monitor_surface]`
  - `THESIS.md` — five-question design doc
  - `surfaces/BuildExtended.tsx` — full canvas (single Country-Pair dropdown)
  - `surfaces/BuildCompact.tsx` — grid card (multi-tool DAGs)
  - `surfaces/monitor/BreakevenInflationWidget.tsx` — Monitor tile
  - `surfaces/breakevenShared.ts` — data hook + descriptor builders + pair metadata
- **Typed-detail endpoint**: [`api/routes/rates/detail.py::breakeven_detail`](../../../../api/routes/rates/detail.py) — `/api/v1/rates/detail/breakeven`
- **Service helper**: `fetchDetailBreakeven`; **frontend type**: `BreakevenInflationSimpleOutput`
- **Surface contract row**: [`surface_contract.md`](../../../../docs_revamped/02_components/surface_contract.md) §4 + §10

---

## Mockup-first design workflow

The Build dual-view design was captured as PNG mockups in [`mockups/`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_breakeven_inflation_simple_tool/mockups/) (Compact.png + Extended.png) before implementation. Future maintainers compare rendered surfaces against the mockups for visual-fidelity regression checks.

## Known data-quality caveats

The frontend ships a defensive sanity filter at `[-300, +900]` bps in `surfaces/breakevenShared.ts` to reject Bloomberg generic-roll artifacts on either leg; the structural fix lives in the data pipeline (TD #26 / TD #31).
