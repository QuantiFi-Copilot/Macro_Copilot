# calculate_real_yield_curve_spread_tool

> How steep is a country's real-yield curve right now? The real-yield curve spread between two tenors of US TIPS, UK linkers, French OATei, or Canadian RRBs (e.g. TIPS 5s10s) — the curve shape, today's move, and how stretched it is vs the past year.

**Phase-1 Stage-C tool** — brought to full parity with [`get_real_yield_level_tool`](../real_yield_level/README.md) under the Phase-1 standards: [`methodology_exposure.md`](../../../../docs_revamped/03_standards/methodology_exposure.md) (standalone-bridge + per-convention exposure), [`rendering_density.md`](../../../../docs_revamped/03_standards/rendering_density.md) (dual Build-view contract), [`lifecycle_checklist_template.md`](../../../../docs_revamped/03_standards/lifecycle_checklist_template.md).

This README mirrors the DB, config.yaml, schemas, and tests; it is NOT the source of truth for any field. See [`LIFECYCLE_CHECKLIST.md`](LIFECYCLE_CHECKLIST.md) for closeout status.

---

## For desk users

### What this tool tells you

The current real-yield curve spread between two tenors of ONE sovereign linker curve — e.g. US TIPS 5s10s, UK linker 2s10s, French OATei 5s10s, or Canadian RRB 5s30s. Returns the spread in **percent** (`long_real_yield_pct − short_real_yield_pct`), the move today / this week / this month in **basis points**, a rolling 252-day z-score against its own history, the trailing 252-day high / low / percentile, the two endpoint real yields, and the year fractions.

This is the SHAPE of the real-yield curve — the real-rate term structure — a distinct object from a breakeven curve spread (inflation-compensation term structure) and from a nominal sovereign curve spread.

### When to use it

- **Real-curve shape** — *"is the TIPS 5s30s real curve steep or flat?"* Open the extended view.
- **Curve moves** — *"how much has the UK 2s10s real curve steepened this week?"* Read the bps changes.
- **Real vs nominal decomposition** — the real curve moves differently from the nominal curve when the inflation-compensation term structure shifts; read the two together.
- **Cross-country comparison** — multi-tool query; each renders as a compact card in a DAG.

### How to read the output

- **`current_spread_pct`** — percent (e.g. `+0.45`). Positive = upward-sloping real curve. Can be negative (inversion).
- **`daily/weekly/monthly_change_bps`** — bps. Positive = the real curve steepened on the day.
- **`current_z_score`** — signed; a steepening vs flattening read differently (the extended view renders a signed 5-zone regime slider). `|z| ≥ 1.5` Elevated; `|z| ≥ 2.0` Extreme.
- **`percentile_252d`** — 0–100 within the trailing 252-day range.
- **`short_real_yield_pct` / `long_real_yield_pct`** — the endpoints, for sanity-checking.

### Known limitations

Quoted from DB `tool_metadata.known_limitations` ([migration](../../../../database/migrations/2026-05-28_phase1_calculate_real_yield_curve_spread_curated.sql)):

> Term structure of REAL YIELDS (real-yield curve shape), distinct from a breakeven curve spread and a nominal sovereign curve spread. Same-country / same-curve-family construction only (single linker curve_family); cross-curve spreads are not expressible. Composed from two `get_real_yield_level` calls; a non-linker curve_family at either endpoint returns a controlled error. Strict inner-join alignment — no synthetic spread points. Tenor ordering structural (long > short). Generic benchmark series only. Wire-frozen `trailing_range_window_days` (252). Spread in PERCENT, changes in bps. Spreads can be negative (inversion). The three rolling-z-score Input overrides apply to the spread's own z-score; the inner endpoint level calls use the YAML-default z-score.

Per-country caveats surface via the shared registry at [`countryCaveats.ts`](../../../../UI/macro-copilot-dashboard-polished/src/components/shared/build/lib/countryCaveats.ts).

---

## For developers

### Theoretical reference

DB-authoritative; quoted from `tool_metadata.theoretical_reference`:

> Tuckman & Serrat (2022), *Fixed Income Securities*, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) for the real-yield definition + Ch. 5 (term structure / curve shape) for the spread framing. Per-country primary issuer documentation (US Treasury / TreasuryDirect; UK DMO; Agence France Trésor; Bank of Canada). Composed from two `get_real_yield_level` calls so the no-proxy guard is inherited transitively.

**Source-material verification status:** ⏸ pending (per [`LIFECYCLE_CHECKLIST.md`](LIFECYCLE_CHECKLIST.md) Stage 8).

### Methodology

Conventions live in [`config.yaml`](config.yaml). Each entry carries `value`, `source`, `rationale`, `valid_range`, and an `exposure:` block.

**Exposed conventions** (overridable per call; None → YAML default):

| Convention | Default | Range | Exposed input field |
|---|---|---|---|
| `z_score_window_days` | 252 | [60, 1260] | `z_score_window_days: Optional[int]` |
| `z_score_min_periods` | 60 | [20, 252] | `z_score_min_periods: Optional[int]` |
| `z_score_ddof` | 1 | [0, 1] | `z_score_ddof: Optional[int]` |
| `default_field_name` | YLD_YTM_MID | n/a | `field_name: Optional[str]` |

The three rolling-z-score overrides apply to the **spread's own** rolling z-score + the fetch-window buffer; the inner endpoint level calls use the YAML default (their z-score is not consumed).

**YAML-locked conventions:** `z_score_buffer_multiplier`, `daily/weekly/monthly_change_offset_rows`, `trailing_range_window_days` (**wire-frozen**), `ffill_limit_days`, `yield_round_decimals`, `bps_round_decimals`, `z_score_round_decimals`, `window_years_round_decimals`, `high_low_round_decimals`.

### Input contract

Pydantic class: [`schemas.py::RealYieldCurveSpreadInput`](schemas.py) (`extra='forbid'`)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `curve_family` | `str` | required | USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB |
| `short_tenor` | `str` | required | e.g. '5Y' — must map to a strictly smaller year fraction than long |
| `long_tenor` | `str` | required | e.g. '10Y' |
| `lookback_days` | `int` | 365 | display window |
| `field_name` | `Optional[str]` | `None` → YLD_YTM_MID | Bloomberg field, both endpoints |
| `z_score_window_days` | `Optional[int]` | `None` → 252 | rolling z window |
| `z_score_min_periods` | `Optional[int]` | `None` → 60 | rolling z min-periods |
| `z_score_ddof` | `Optional[int]` | `None` → 1 | sample (1) vs population (0) |

Validators `_tenors_must_differ` + `_short_must_precede_long` (structural). Same-curve identity re-asserted at compute time (`_enforce_same_curve_family_identity`).

### Output contract

Pydantic class: [`schemas.py::RealYieldCurveSpreadOutput`](schemas.py):

- **`current_metrics: RealYieldCurveSpreadCurrentMetrics`** — snapshot (spread pct, changes, z, range, endpoint yields, year fractions, country/currency, `methodology_label`).
- **`time_series: List[...TimeSeriesRow]`** — bespoke `{date, spread_pct, z_score}` rows.
- **`time_series_spread: TimeSeries`** — canonical `TimeSeriesUnits.PERCENT`.
- **`time_series_zscore: TimeSeries`** — canonical `TimeSeriesUnits.Z_SCORE`.

**DB `output_field_units`** (JSONB): `{"time_series_spread": "percent", "time_series_zscore": "z_score"}`.

### Testing

- **Compute tests**: [`tests/test_real_yield_curve_spread_compute.py`](../../../../tests/test_real_yield_curve_spread_compute.py) — includes `TestInputOverrides` + `TestExposureBlockContract`.
- **Parity fixture**: [`tests/test_real_yield_curve_spread_parity.py`](../../../../tests/test_real_yield_curve_spread_parity.py).
- **SQL ground-truth**: [`tests/test_real_yield_curve_spread_sql_validation.py`](../../../../tests/test_real_yield_curve_spread_sql_validation.py).
- **Wiring tests**: [`tests/test_real_yield_curve_spread_wiring.py`](../../../../tests/test_real_yield_curve_spread_wiring.py) (some require the `mcp` package).
- **Source-material verification**: DB `source_material_verified` NULL (⏸ pending, Stage 8).

### Frontend surfaces

- **Module folder**: [`UI/.../src/modules/primitives/calculate_real_yield_curve_spread_tool/`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_real_yield_curve_spread_tool/)
  - `module.ts` — `tiers: [generic_runnable, custom_build_surface, monitor_surface]`
  - `THESIS.md` — five-question design doc
  - `surfaces/BuildExtended.tsx` — full canvas (separate short/long tenor dropdowns + signed 5-zone regime slider)
  - `surfaces/BuildCompact.tsx` — grid card
  - `surfaces/monitor/RealYieldCurveSpreadWidget.tsx` — Monitor tile
  - `surfaces/curveSpreadShared.ts` — data hook + descriptor builders + tenor parser
- **Typed-detail endpoint**: [`api/routes/rates/detail.py::real_yield_curve_spread_detail`](../../../../api/routes/rates/detail.py) — `/api/v1/rates/detail/real_yield_curve_spread`
- **Service helper**: `fetchDetailRealYieldCurveSpread`; **frontend type**: `RealYieldCurveSpreadOutput`
- **Surface contract row**: [`surface_contract.md`](../../../../docs_revamped/02_components/surface_contract.md) §4 + §10

---

## Mockup-first design workflow

The Build dual-view design was captured as PNG mockups in [`mockups/`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/calculate_real_yield_curve_spread_tool/mockups/) (Compact.png + Extended.png) before implementation.

## Known data-quality caveats

The frontend ships a defensive sanity filter at `[-3%, +3%]` in `surfaces/curveSpreadShared.ts` to reject generic-roll artifacts on either endpoint; the structural fix lives in the data pipeline (TD #26 / TD #31).
