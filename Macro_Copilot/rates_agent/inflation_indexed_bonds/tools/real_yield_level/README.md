# get_real_yield_level_tool

> Where do real yields sit right now for US TIPS, UK linkers, French OATei, or Canadian RRBs? See the level, today's move, and how stretched it is vs the past year — at a glance.

**Phase-1 pilot tool** under the Phase-1 standards: [`methodology_exposure.md`](../../../../docs_revamped/03_standards/methodology_exposure.md) (standalone-bridge + per-convention exposure decisions), [`rendering_density.md`](../../../../docs_revamped/03_standards/rendering_density.md) (dual Build-view contract), [`lifecycle_checklist_template.md`](../../../../docs_revamped/03_standards/lifecycle_checklist_template.md) (per-tool progress).

This README is the human-readable consolidation of the per-tool truths — it mirrors the DB, config.yaml, schemas, and tests; it is NOT the source of truth for any field. See [`LIFECYCLE_CHECKLIST.md`](LIFECYCLE_CHECKLIST.md) for closeout status.

---

## For desk users

### What this tool tells you

The current real yield on one sovereign-linker bond — chosen by curve family (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) and tenor (5Y / 10Y / 20Y / 30Y, varies by family). Returns the level in percent, the move today / this week / this month in basis points, a rolling 252-day z-score against the series' own history, and the trailing 252-day high / low / percentile.

A linker's real yield is the yield-to-maturity computed on cash flows that have been indexed to the relevant consumer price index (CPI-U NSA for US TIPS; RPI for UK linkers — transitioning to RPI aligned with CPIH from 2030; HICP excluding tobacco for French OATei; Canadian CPI all-items NSA for RRB). It represents the market's required real rate of return AFTER inflation indexation — not a forecast of realized real economic growth.

### When to use it

- **Morning briefing** — *"where are real yields this morning?"* Glance the Monitor tile on the rates dashboard.
- **Macro framing** — *"is the move tightening or easing real-rate conditions?"* Open the extended view; the four-quadrant pairing with breakeven (real yields up + breakevens down = real tightening; real yields down + breakevens up = reflationary easing) is the canonical desk read.
- **Cross-country comparison** — *"compare TIPS 10Y vs UK linker 10Y vs OATei 10Y"*. Multi-tool query; each tool renders as a compact card in a DAG layout.
- **Stretch check** — *"is the current level extreme vs trailing year?"* Z-score regime band (Normal / Elevated / Extreme) + 252d percentile bucket (Low / Normal / High) are surfaced inline.

### How to read the output

- **`real_yield_pct`** — percent (e.g. `+2.14`). Can be negative (UK linkers were widely negative post-COVID).
- **`daily/weekly/monthly_change_bps`** — basis points. Positive = real yields up = tightening (coral colour); negative = real yields down = easing (mint colour).
- **`z_score`** — dimensionless. `|z| ≥ 1.5` = Elevated (amber); `|z| ≥ 2.0` = Extreme (coral if up / mint if down).
- **`percentile_252d`** — 0–100 within the trailing 252-day range. `≥ 80th` = High; `≤ 20th` = Low.
- **`high_252d_pct` / `low_252d_pct`** — bounds of the trailing range.
- **`observation_count`** — number of trading days the displayed window covers.

### Known limitations

Quoted verbatim from DB `tool_metadata.known_limitations` ([`database/migrations/2026-05-28_phase1_get_real_yield_level_curated.sql`](../../../../database/migrations/2026-05-28_phase1_get_real_yield_level_curated.sql)):

> Generic real-yield benchmarks only — not actual bond-level analytics. Exact carry, settlement, clean-vs-dirty real price, index ratio, accrued interest, and deflation-floor terms require actual bond-level data which this tool does not consume. Universe is 24 instruments across 4 countries: US TIPS (5Y/10Y/20Y/30Y; new 20Y issuance discontinued 2009), UK linkers (1Y..50Y; mixed 3-month and 8-month indexation lag within the same curve), French OATei (2Y/5Y/7Y/10Y/15Y; HICPxT-referenced, NOT French-CPI-referenced OATi), Canadian RRBs (5Y..30Y; no new issuance since November 2022 — declining-liquidity legacy market). The UK RPI → CPIH transition in 2030 bifurcates the linker curve; post-2030-maturity gilt cash flows blend pre/post reform and historical z-scores spanning the transition mix two methodology regimes. The wire-frozen `trailing_range_window_days` (locked at 252 in V1; output field names embed the number) requires a schema migration to unlock. The `instrument_type='inflation_linker'` filter at the fetch boundary is hard-coded in `compute.py` per the no-proxy guard. Three rolling-z-score conventions (`z_score_window_days` / `z_score_min_periods` / `z_score_ddof`) are exposed as per-call Pydantic Input overrides; the remaining nine conventions stay YAML-locked.

Per-country caveats surface in the UI inline (Monitor tile + Build compact + Build extended); registry at [`UI/.../shared/build/lib/countryCaveats.ts`](../../../../UI/macro-copilot-dashboard-polished/src/components/shared/build/lib/countryCaveats.ts).

---

## For developers

### Theoretical reference

DB-authoritative; quoted verbatim from `tool_metadata.theoretical_reference`:

> Tuckman & Serrat (2022), *Fixed Income Securities: Tools for Today's Markets*, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) — textbook framing of real yield, indexation mechanics (CPI-U / RPI / HICP-ex-tobacco / Canadian CPI; 3-month and 8-month indexation lag; daily interpolation), and breakeven decomposition. Per-country primary issuer documentation: US Treasury / TreasuryDirect (TIPS issuance + mechanics; 20Y discontinuation 2009); UK Debt Management Office (Index-Linked Gilts; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (OATei mechanics; HICPxT reference); Bank of Canada (Real Return Bonds technical documentation; November 2022 issuance cessation). The level-stat methodology (rolling 252-day z-score, period changes, trailing range) mirrors sovereign `get_yield_levels_tool` / OIS `get_ois_rate_level_tool`; convention values are pinned by the cross-config lint per [`methodology_disclosure.md`](../../../../docs_revamped/03_standards/methodology_disclosure.md).

**Source-material verification status:** ⏸ pending (per [`LIFECYCLE_CHECKLIST.md`](LIFECYCLE_CHECKLIST.md) Stage 8).

### Methodology

Conventions live in [`config.yaml`](config.yaml). Each entry carries `value`, `source`, `rationale`, `valid_range`, and an `exposure:` block per [`methodology_exposure.md`](../../../../docs_revamped/03_standards/methodology_exposure.md).

**Exposed conventions** (overridable per call via the Pydantic Input; default flows through to YAML when None):

| Convention | Default | Range | Exposed input field |
|---|---|---|---|
| `z_score_window_days` | 252 | [60, 1260] | `z_score_window_days: Optional[int]` |
| `z_score_min_periods` | 60 | [20, 252] | `z_score_min_periods: Optional[int]` |
| `z_score_ddof` | 1 | [0, 1] | `z_score_ddof: Optional[int]` |
| `default_field_name` | YLD_YTM_MID | n/a | `field_name: Optional[str]` |

**YAML-locked conventions** (system constants; documented `expose: false` per Criterion A/B):

| Convention | Default | Rationale category |
|---|---|---|
| `z_score_buffer_multiplier` | 1.5 | Fetch-window math; doesn't change output values |
| `daily_change_offset_rows` | 2 | Canonical desk-language anchor (wire-encoded in field name) |
| `weekly_change_offset_rows` | 6 | Same |
| `monthly_change_offset_rows` | 22 | Same |
| `trailing_range_window_days` | 252 | **Wire-frozen** — output field names embed the number; `NotImplementedError` guard if changed |
| `ffill_limit_days` | 5 | Data hygiene (borderline; defaults to locked per protocol §2.3) |
| `yield_round_decimals` | 4 | Display precision (Criterion A fails) |
| `z_score_round_decimals` | 4 | Same |
| `high_low_round_decimals` | 4 | Same |

See each convention's `exposure:` block in [`config.yaml`](config.yaml) for full per-decision rationale.

### Input contract

Pydantic class: [`schemas.py::RealYieldLevelInput`](schemas.py)

| Field | Type | Default | Constraints | Meaning |
|---|---|---|---|---|
| `curve_family` | `str` | required | none (validated downstream) | USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB |
| `tenor` | `str` | required | none (validated downstream) | Curve-family-specific (e.g. USD_TIPS exposes 5Y/10Y/20Y/30Y) |
| `lookback_days` | `int` | 365 | 30 ≤ x ≤ 7300 | Display window; does NOT control rolling stats |
| `field_name` | `Optional[str]` | `None` (sentinel → YAML default `YLD_YTM_MID`) | — | Bloomberg field mnemonic |
| `z_score_window_days` | `Optional[int]` | `None` (→ YAML 252) | 60 ≤ x ≤ 1260 | Rolling z-score window override |
| `z_score_min_periods` | `Optional[int]` | `None` (→ YAML 60) | 20 ≤ x ≤ 252 | Rolling z-score min-periods override |
| `z_score_ddof` | `Optional[int]` | `None` (→ YAML 1) | 0 ≤ x ≤ 1 | Sample (1) vs population (0) std override |

### Output contract

Pydantic class: [`schemas.py::RealYieldLevelOutput`](schemas.py) — compound:

**`current_metrics: RealYieldLevelMetrics`** (snapshot):

| Field | Type | Unit |
|---|---|---|
| `as_of_date` | `str` (YYYY-MM-DD) | — |
| `curve_family` | `str` | — |
| `tenor` | `str` | — |
| `real_yield_pct` | `float` | percent |
| `daily_change_bps` | `Optional[float]` | bps |
| `weekly_change_bps` | `Optional[float]` | bps |
| `monthly_change_bps` | `Optional[float]` | bps |
| `z_score` | `Optional[float]` | z-score (dimensionless) |
| `high_252d_pct` | `Optional[float]` | percent |
| `low_252d_pct` | `Optional[float]` | percent |
| `percentile_252d` | `Optional[float]` | percentile (0–100) |
| `observation_count` | `int` | count |

**`time_series: TimeSeries`** — canonical closed-family shape:

- `series_name`: `<curve_family_lower>_<tenor_lower>_real_yield` (suffix is load-bearing; distinguishes from nominal sovereign yield series downstream)
- `units`: `TimeSeriesUnits.PERCENT`
- `rows`: chronological list of `{date, value}` pairs; last row's `value` strictly equals `current_metrics.real_yield_pct` (byte-for-byte, not just within tolerance — both routed through the same `yield_round_decimals` convention).

**DB `output_field_units`** (JSONB): `{"time_series": "percent"}`.

### Testing

- **Unit / compute tests**: [`tests/test_real_yield_level_compute.py`](../../../../tests/test_real_yield_level_compute.py) — 51 tests across 8 classes (bundled config, happy path, convention overrides, Phase-1 Input overrides, exposure-block contract, trailing-window guard, schema layer, canonical TimeSeries, instrument-type guard).
- **Wiring tests**: [`tests/test_real_yield_level_wiring.py`](../../../../tests/test_real_yield_level_wiring.py) — 16 tests across 5 classes (MCP wrapper, ConfigPath public symbol, response-model validation, workflow registration, MCP instrument-type guard). 6 require the `mcp` Python package (env-dependent).
- **Parity fixture**: [`tests/fixtures/real_yield_level_v1/`](../../../../tests/fixtures/real_yield_level_v1/) — 3 captured fixtures (USD_TIPS 10Y 365d, GBP_LINKER 10Y 730d, EUR_FR_LINKER 5Y 365d) replayed at 1e-9 tolerance.
- **SQL ground-truth**: [`tests/test_real_yield_level_sql_validation.py`](../../../../tests/test_real_yield_level_sql_validation.py) — standalone CLI runner; 4 regression cases (one per linker family) + random sample.
- **Source-material verification**: tracked in DB `tool_metadata.source_material_verified`; currently NULL (⏸ pending per Stage 8).

### Frontend surfaces

- **Module folder**: [`UI/.../src/modules/primitives/get_real_yield_level_tool/`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/)
  - `module.ts` — `tiers: [generic_runnable, custom_build_surface, monitor_surface]`
  - `THESIS.md` — five-question design doc (FM10 + rendering_density §11)
  - `surfaces/BuildExtended.tsx` — full canvas (focused-mode collapses side panels)
  - `surfaces/BuildCompact.tsx` — grid card (mounted in multi-tool DAGs)
  - `surfaces/monitor/RealYieldLevelWidget.tsx` — Monitor bento tile
  - `surfaces/realYieldShared.ts` — per-tool data hook + descriptor builders + outlier sanitiser
  - `mockups/` — Compact.png + Extended.png + multi-tool view mockups (committed per the mockup-first workflow)
- **Shared shell components** (finance-blind, reusable): [`UI/.../src/components/shared/build/`](../../../../UI/macro-copilot-dashboard-polished/src/components/shared/build/)
- **Typed-detail bridge endpoint**: [`api/routes/rates/detail.py::real_yield_detail`](../../../../api/routes/rates/detail.py) — mounted at `/api/v1/rates/detail/real_yield`
- **Service helper**: [`UI/.../src/services/ratesApi.ts::fetchDetailRealYield`](../../../../UI/macro-copilot-dashboard-polished/src/services/ratesApi.ts)
- **Frontend type**: [`UI/.../src/types/rates.ts::RealYieldLevelOutput`](../../../../UI/macro-copilot-dashboard-polished/src/types/rates.ts)
- **Surface contract row**: [`surface_contract.md §4.3`](../../../../docs_revamped/02_components/surface_contract.md) + lifecycle row at §10.3

---

## Mockup-first design workflow

The Build dual-view design was captured as PNG mockups committed to [`UI/.../mockups/`](../../../../UI/macro-copilot-dashboard-polished/src/modules/primitives/get_real_yield_level_tool/mockups/) before implementation began. Future maintainers can compare the rendered surfaces against the mockups for visual-fidelity regression checks. Future tool authors use the same workflow.

## Known data-quality caveats

See [TD #31](../../../../docs/technical_debt.md) — Bloomberg generic-ticker roll artifacts (linker front-end). The frontend ships a defensive sanity filter at `±6%` real-yield bounds in `surfaces/realYieldShared.ts`; the proper structural fix (rolling median-deviation filter in `shared.analytics.levels.clean_single_series`) is queued per TD #26 / TD #31.
