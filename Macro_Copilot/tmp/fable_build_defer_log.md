# fable_build DEFER LOG (tmp/fable_plan_2.md Appendix E)

Every Track-A tool that was NOT built, with the gate that fired, evidence, and the
unblock condition. *Defer, never proxy.* Entries are appended as gates fire during the
build; the §7 C-DEFER items are entered with **live-DB evidence** (the data substrate
has grown since the plan was written — see `tmp/fable_build/BASELINE.md` and the
data-reality section below).

## Live data reality (verified 2026-06-16, container `macro-tsdb`, DB `macrodata`)

`macro_data.instrument_master`: **238 instruments / 6 instrument types** — exactly the
plan's §9 six core types and no others: sovereign_benchmark 72, ois_swap 78,
inflation_linker 24, inflation_swap 21, bond_future 19, policy_future 24.  All 19
bond_future + 24 policy_future are `is_rolling_contract = TRUE` generic stems (TY1,
BTS1, …); there are zero dated-contract instruments.

`macro_data` holds **3 base tables** — `instrument_master`, `market_data_daily`,
`load_audit` — plus 3 read views (`v_market_data_daily_enriched`,
`v_rates_instruments`, `v_sovereign_curves`).  `market_data_daily` carries **7 field
names**: OPEN_INT, PX_ASK, PX_BID, PX_LAST, PX_MID, PX_VOLUME, YLD_YTM_MID
(2,221,455 rows; span **2005-01-03 → 2026-04-09**).  `instrument_master` has 18
columns and contains **no `cusip`/`isin`** (it does carry `is_rolling_contract`).
The ingestion ledger `macro_data.load_audit` records **7 dataset_names only**
(bond_futures, inflation_indexed_bonds, inflation_swaps, ois_curves, policy_futures,
rates, sovereign_bonds; latest `ingested_at` 2026-04-20).

NOT present — verified absent, never loaded (no `load_audit` record of any such load,
ever): the instrument types `wirp_meeting` / `sovereign_cash_bond` / `overnight_rfr` /
`inflation_reference` (0 rows each); the fields RISK_MID / ASSET_SWAP_SPD_MID /
PX_CLEAN_MID / PX_DIRTY_MID / YLD_YTM_BID / YLD_YTM_ASK / WIRP_IMPLIED_RATE (0 rows
DB-wide); the auxiliary tables `event_calendar` / `futures_deliverables` /
`otr_history` / `instrument_metadata_history` (each `to_regclass → NULL`, i.e. the
table does not exist); and the `cusip`/`isin` columns.  An earlier draft of this
header asserted a 335-instrument / 10-type substrate with those four extra types, the
four aux tables, RISK_MID and cusip/isin — that substrate does not exist and the
`load_audit` ledger proves it was never loaded; the cited `tmp/fable_build/BASELINE.md`
makes no such claim.  The census above is the corrected, verified truth.

Defer reasoning below cites this LIVE state, per plan §0.7 (live code/data is the
truth).

---

### rolling_beta  (framework: OP)
- Gate that fired: OPR4 (toolbox admission — composability check: "do not add an
  operator whose effect is one trivial existing chain").
- Evidence: the live `rolling_regression` operator already emits a
  `SeriesSet{beta, alpha, r_squared}` of windowed coefficient paths;
  `rolling_beta(lhs, rhs) ≡ rolling_regression(lhs, rhs) →
  select_from_series_set(series_key='beta')` with identical parameter freedom
  (window, min_periods flow through unchanged).  The rolling_regression registry
  card already documents `select_from_series_set(series_key='beta')` as the
  canonical wiring for "the rolling beta the user usually asks for".
- What would unblock it: a genuine algorithmic extension that is not a pure
  composition (e.g. Kalman time-varying beta — which is the separate Phase-2
  `fit_kalman` engine in plan §7 A4 — or multi-regressor beta paths).
- Disposition: deferred (not built); no proxy shipped.  The two-node chain is
  idiomatic in the open DAG and the validator proves composability.

### tracking_error  (framework: OP)
- Gate that fired: OPR4 (toolbox admission — no distinct method: the unannualized
  tracking error is the trivial existing chain) + OPR6/P9 + P5 (the only delta a
  dedicated operator would add is √252 annualization, which is mechanically
  expressible but smuggles an undisclosed trading-calendar convention through
  dimensionally-dishonest units — finance math that belongs in a finance-aware
  primitive, not a finance-blind operator).
- Evidence: `series_arithmetic(op='subtract')` → `summarize_series(statistic='std')`
  produces exactly the unannualized tracking error as a ScalarMetric, with full
  parameter freedom.  The √252 annualization is NOT inexpressible — it is one more
  node, `series_arithmetic(op='multiply', right=√252)` — but expressing it that way
  is dishonest on two counts: (1) it hard-codes a 252-trading-day-year convention at
  the call site with no disclosure surface (no YAML `trading_days_per_year`, no
  methodology tag), violating P5; and (2) `series_arithmetic` scalar-multiply
  PRESERVES the input's units (verified live: output units = left.units for scalar
  multiply), so the annualized value carries the un-annualized series' unit label —
  dimensionally dishonest.  The convention is exactly the kind of asset-class/calendar
  finance math OPR6 reserves for primitives ("day-count … = finance-aware = primitive
  territory"); the codebase already routes √252 out of operators consistently
  (`ewm_statistic`/`fit_garch` configs: annualization "is calendar finance math … let
  a finance-aware primitive own the annualization").
- What would unblock it: a finance-aware Bucket-1B primitive (e.g.
  `rates_agent/<domain>/tools/tracking_error/`) that wraps the two-node
  composition and applies the annualization convention with an honest,
  YAML-disclosed `trading_days_per_year` convention + registered source tag —
  if desk usage justifies the blessed, named version (PR4 criteria).
- Disposition: deferred (not built); no proxy shipped.  The two-node chain is
  idiomatic and the validator proves composability.

### distance_correlation / mutual_information  (framework: OP) — SEQUENCING NOTE, not a defer
- Status: plan §7-A2 marks both "lower priority"; the §0.3 gate does NOT fire
  (pure-numpy / scipy implementations available; genuine toolbox-membership —
  nonlinear-dependence detection that no linear-correlation composition can
  express; finance-blind; ScalarMetric output fits the closed family).
- Disposition: BUILD, sequenced after the A2 core + A3/A7/A1/A6/A5 high-priority
  families and the A4 model engines; revisit before §12 close-out.  If the run
  ends before they are built, this entry converts to an honest defer-by-time
  with no principle violated.

### cross_sectional_percentile  (framework: OP)
- Gate that fired: OPR4 (non-overlap clause — "if an existing operator's variant
  set could cover it, extend that operator instead"; here the variant ALREADY
  exists).
- Evidence: the committed `cross_sectional_rank` operator's
  `rank_method='normalized'` variant emits exactly the within-date percentile of
  each member (pandas ``rank(axis=1, pct=True) × 100``, PCT_RANK 0–100 units) —
  the same formula and semantics a standalone percentile operator would ship.
  No interpolation-based quantile delta exists in the plan's row.
- What would unblock it: a genuinely different percentile semantic (e.g.
  interpolated quantile positioning against a fitted distribution) — which would
  be a new method, not this row.
- Disposition: deferred-as-covered (not built); use
  `cross_sectional_rank(rank_method='normalized')`.

### realized_volatility  (framework: OP per plan §7-A1 — re-ruled)
- Gate that fired: OPR6/P9 (the row's defining "annualized" component is √252
  trading-calendar finance math — the tracking_error doctrine) + OPR4 (the
  unannualized core is the trivial existing chain
  `series_arithmetic(op='diff'|'pct_change')` → `rolling_statistic(statistic='std')`).
- Evidence: the plan row reads "annualized rolling σ of returns".  With annualization
  stripped, nothing distinct remains that the live two-node chain does not compose
  cleanly.  The √252 annualization itself is NOT inexpressible — it is one more node,
  `series_arithmetic(op='multiply', right=√252)` — but, exactly as in tracking_error,
  expressing it in the operator layer is dishonest: it hard-codes a 252-trading-day-year
  convention with no disclosure surface (P5) and, because scalar-multiply preserves the
  input's units, mislabels the annualized value with the un-annualized series' unit
  (dimensionally dishonest).  That convention is asset-class/calendar finance math OPR6
  reserves for primitives ("day-count … = finance-aware = primitive territory"), which
  is why the honest home is a finance-aware Bucket-1B primitive that discloses the
  convention, not a finance-blind operator.
- What would unblock it: a finance-aware Bucket-1B primitive
  (`rates_agent/<domain>/tools/realized_volatility/`) wrapping the chain with a
  YAML-disclosed `trading_days_per_year` convention + registered source tag.
- Disposition: deferred (not built as an operator); no proxy shipped.

### drawdown  (framework: OP per plan §7-A1)
- Gate that fired: OPR4 (trivial-chain doctrine — the running peak-to-trough is
  exactly the two-node chain `cumulative(statistic=max)` →
  `series_arithmetic(op=subtract: x − cummax(x))`; relative drawdown adds one
  `series_arithmetic(op=divide)` node).
- Evidence: `cumulative` shipped on fable_build with this exact shape
  documented as its card example ("x − cummax(x) for drawdown shapes").
- What would unblock it: drawdown DURATION / time-under-water analytics
  (consecutive-rows-below-peak bookkeeping that no chain provides) — a
  distinct core that would justify a named operator in Phase 2.
- Disposition: deferred-as-covered (use the chain); no proxy shipped.

### crossover_events  (framework: OP per plan §7-A6)
- Gate that fired: OPR4/P9 (trivial-chain doctrine — the MEMBERSHIP
  semantics of a cross of two series is exactly
  `series_arithmetic(op=subtract: left − right)` →
  `threshold_events(threshold=0)`: every date the spread is on one side,
  with threshold_events' strict-equality tie-breaking and look-ahead
  discipline already adjudicated there).
- Evidence: Warden ruling — "the cross chain composes transparently in the
  open DAG; a standalone cross detector would duplicate threshold_events'
  tie-breaking and look-ahead discipline and would need its own alignment
  surface (a third copy of OPR11 flags) for zero analytical gain."
- Honest scope (critic-qualified): the chain covers MEMBERSHIP ("dates left
  was above right"), not onset-collapse ("the dates the cross HAPPENED") —
  threshold_events today emits every qualifying date; collapsing runs to
  transition events awaits its planned event_dedup/consecutive_collapse
  extension, which is the named unblock path for true cross-DATE asks.
- What would unblock it: threshold_events' event_dedup extension (covers
  onset collapse there, keeping one event surface), or a cross semantics
  that surface cannot express (e.g. simultaneous multi-pair crossing
  matrices) — none identified.
- Disposition: deferred-as-covered for membership semantics; onset-collapse
  pending the threshold_events extension; no proxy shipped.

### empirical_quantile / historical_VaR  (framework: OP per plan §7-A1)
- Gate that fired: OPR4 (non-overlap clause — "if an existing operator's variant
  set could cover it, extend that operator instead"; here the variant ALREADY
  exists).
- Evidence: the committed `summarize_series` operator's `statistic='quantile'`
  variant (v1.1.0) emits exactly the q-th full-sample empirical quantile as a
  ScalarMetric in the input's units (type-7 linear interpolation, the pandas
  default), parameterised by the `q` quantile-level field — its config documents
  "0.05 for the downside tail — the desk's historical-VaR-style read — 0.95 for
  the upside".  That IS the descriptive tail/VaR read the plan's A1 row asks for;
  a standalone operator would ship the identical formula and semantics.
- Why finance-blind: "historical_VaR" is a finance NAME, but the operation (the
  p-th empirical quantile of a series) is pure; `summarize_series` carries it
  with zero finance vocabulary.
- What would unblock a distinct operator: a genuinely different tail semantic
  (e.g. a fitted-distribution / parametric VaR, or expected-shortfall averaging
  beyond the quantile) — a new method, not this row.
- Disposition: deferred-as-covered (not built); use
  `summarize_series(statistic='quantile', q=...)`.

### term_premium_decomposition  (framework: B2 / sovereign_bonds; uses shared/quant/dns.py [not built])
- Gate that fired: SCOPE BOUNDARY §104 "we describe, we do NOT forecast / no
  AR/ARIMA forecasting" (PRIMARY) + §104/P12 Bloomberg-accuracy boundary "don't
  recompute what the terminal does better" (INDEPENDENT, reinforcing).  NOT a
  complexity defer (the A4 engines were all new shared/quant math); NOT ART4
  (lineage-as-model-state fits the closed family); NOT a data defer
  (sovereign_benchmark 72 tenors 2005→ + overnight_rfr short-rate proxy 2005→
  exist live).
- Evidence: ACM splits y(t,n) = E_t[avg future short rate] + term_premium.  The
  expected-rates leg is by construction a physical-measure PROJECTION of future
  short rates, produced by an estimated VAR(1) on the yield factors — an
  autoregressive forecasting model of the state.  The term-premium leg is
  defined ONLY relative to that forecast; "is TP rich" (the plan's desk
  workflow) cannot be emitted without emitting the forward expectation.  This is
  the opposite of the allowed A4 reads (HMM=regime NOW, GARCH=current vol,
  OU=current z-to-equilibrium, rolling_pca=current loadings — all current-state,
  no future projection).  Separately, ACM term premia are a published canonical
  series (NY Fed / Bloomberg); re-estimating a no-arbitrage term-structure model
  on our generic GT* curve is the "full curve bootstrap" class of forbidden
  recomputation (P12).  Dynamic-Nelson-Siegel does NOT rescue the full tool: a
  DNS curve fit is descriptive, but the TP split still needs a separate forward
  expectations model — the same boundary.
- What would unblock it: (a) a thesis-level ADR widening the platform scope to
  permit a physical-measure expectations / forward-projection model (lifts the
  forecast gate), AND (b) an ADR / ingest decision to treat published ACM term
  premia as an INGESTED series rather than an in-house recompute (resolves the
  Bloomberg gate) — surface the NY Fed / terminal series, never re-derive it.
  Absent both, defer.
- Disposition: deferred (not built); no proxy shipped.

### fit_dynamic_nelson_siegel  (framework: QLIB+OP) — SEQUENCING NOTE, not a defer
- Status: the salvageable DESCRIPTIVE subset of the term_premium row.  The §0.3
  gate does NOT fire on a pure per-date NS curve FIT: fit the 3 Nelson-Siegel
  factor betas (level/slope/curvature) to the observed tenor cross-section via
  OLS on the FIXED parametric Laguerre loadings [1, (1−e^{−λτ})/(λτ),
  (1−e^{−λτ})/(λτ) − e^{−λτ}] for a fixed decay λ.  Emit the factor paths as a
  SeriesSet{level,slope,curvature} (+ the fitted/residual curve).  This is a
  descriptive current-state decomposition of the curve we already hold — no
  expectations model, no VAR, no future projection (the §104-allowed class,
  alongside PCA).
- Genuine OPR4 distinctness from pca_decompose: REAL.  PCA loadings are EMPIRICAL
  (sample-derived eigenvectors, sign/rotation-ambiguous, sample-varying); NS
  imposes a FIXED parametric loading shape with a fixed λ, giving stable,
  interpretable level/slope/curvature factors by construction, identical across
  dates/universes.  Different inputs (PCA=panel covariance; NS=per-date
  cross-section), different parameterization (λ), different invariant; the open
  DAG cannot express the Laguerre-loading fit by composing existing operators.
  Not "just PCA" — the parametric counterpart.
- BOUNDARY it must respect: stop at the descriptive fit.  Bolting an expectations
  model onto it to back out term premium re-enters the forecast gate above.
- Disposition: BUILD candidate for the A-family descriptive-engine backlog (gate
  via §0.3 on its own turn); if the run ends before it is built, this converts to
  an honest defer-by-time with no principle violated.

### cross_market_fair_value  (framework: B2 / sovereign_bonds; plan §7-B)
- Gate that fired: PR4 (parsimony / composability — the output is produced by
  composing two EXISTING desk-blessed sovereign_bonds primitives; no defensible
  accuracy/efficiency/interpretability/provenance/LLM-clarity advantage) + PR4
  §225 routing-overlap (a new tool whose scope overlaps beta_adjusted_spread +
  half_life misroutes under load).  Same shape as the rolling_beta /
  cross_sectional_percentile defer-as-covered precedents.
- Evidence: the driver-model fair-value DEVIATION + its Z are already emitted by
  beta_adjusted_spread (time_series_residual [bps] + time_series_residual_z_score
  + current_residual_bps / current_residual_z_score / β / α / R²).  The OU
  HALF-LIFE + the full OU model state (half_life + CI, long_run_mean μ,
  current_deviation z-to-equilibrium, β→κ/θ, is_mean_reverting, R²) are already
  emitted by half_life, whose pasted_series input is EXPLICITLY documented for
  "chaining the output of a prior tool (e.g., a residual) into this tool."  So:
    cross_market_fair_value ≡
      beta_adjusted_spread(target, regressor) → time_series_residual
        → half_life(pasted_series=residual)
  with full parameter freedom flowing through unchanged.  fit_ou adds nothing
  half_life does not already surface as NAMED fields (with CIs the bare operator
  lacks); the only OU read fit_ou withholds — the z-to-equilibrium SERIES — is the
  SEPARATE unbuilt ou_zscore operator, which a primitive could not surface either.
- What would unblock it: (a) ou_zscore (the time-varying OU z-to-equilibrium
  series) shipping AND a desk workflow that demands residual+z+half-life+z-series
  as one atomic read the chain cannot express; or (b) a genuine added-finance
  element absent from both tools (e.g. a multi-driver / panel fair-value model
  with DV01-weighted driver legs, which beta_adjusted_spread's bivariate OLS
  cannot express).  Absent these, defer.
- Disposition: deferred-as-covered (not built); no proxy shipped.  Use the
  two-primitive chain beta_adjusted_spread → half_life(pasted_series).

### forward_spread  (framework: 1B / ois; plan §7-C)
- Gate that fired: PR4 parsimony / composability — a forward−forward (or
  forward−spot) spread is the idiomatic two-node open-DAG chain
  `calculate_ois_forward_rate(window A)` + `calculate_ois_forward_rate(window B)`
  → `series_arithmetic(op='subtract')`, with full parameter freedom.  Same
  defer-as-covered shape as rolling_beta / cross_market_fair_value.
- Evidence: the existing forward_rate tool emits a `time_series_forward`
  Series per window; series_arithmetic consumes the two directly.  The
  spread adds no finance the chain doesn't already express.  (The sibling
  `implied_forward_curve` — the whole forward STRIP as one SeriesSet — IS
  built; only the trivial two-forward spread defers.)
- What would unblock it: a forward_spread needing a joint re-anchoring the
  chain cannot express (e.g. a single shared as-of anchor across both legs).
- Disposition: deferred-as-covered (not built); use
  `forward_rate ×2 → series_arithmetic(subtract)`.

### cross_market_dv01_spread  (framework: 1B / sovereign_bonds+ois; plan §7-C)
- Gate that fired: PR6 / P12 — required data missing; no proxy.  The
  DV01-weighting is the entire delta over the existing par-par cross-market
  spreads, and analytic swap DV01 is NOT computable from the data we hold
  without recomputing a full curve (the P12 "no full curve bootstrap"
  boundary).
- Evidence: the matched-tenor cross-market differential already ships
  (swap_spread = sovereign yield − OIS rate par-par ASW; cross_market_spread;
  beta_adjusted_spread).  NO DV01/PV01/annuity math exists anywhere
  (curve_bootstrap.py has discount factors but no annuity/sensitivity
  helper).  A true swap DV01 = the fixed-leg annuity Σ DF(t_i)·τ_i, needing a
  bootstrapped DF curve + the swap's payment schedule; deriving it from the
  par-as-zero approximation + a synthesized schedule is exactly the forbidden
  "full curve bootstrap" proxy (swap_spread's own PR21 disclosure says the
  DV01-aware ASW "requires bond-level metadata not yet ingested").  And there
  is no vendor risk field to consume instead: a DB-wide probe returns 0 rows
  for RISK_MID / ASSET_SWAP_SPD_MID and there are no `sovereign_cash_bond`
  instruments (and no cusip/isin columns) — so no ingested DV01/annuity series
  exists anywhere to short-circuit the recompute.
- What would unblock it: (a) ingest a trusted analytic swap DV01 / annuity
  series (or a vendor risk field for the swap universe), OR (b) an ADR
  admitting an in-house annuity bootstrap as inside the Bloomberg boundary +
  a shared/quant DV01 engine.  Reinforces the existing futures-CTD-DV01
  C-DEFER.
- Disposition: deferred (not built); no proxy shipped.  Use swap_spread /
  beta_adjusted_spread for matched-tenor cross-market RV.

### futures_roll_adjust  (framework: 1B / bond_futures+policy_futures; plan §7-C)
- Gate that fired: PR6 / P12 — required data missing; the back-adjustment
  roll GAP is not computable without a proxy.
- Evidence: back-adjustment is DOUBLY blocked — there is no roll CALENDAR and
  there are no dated-contract prices.  (1) No roll calendar: there is no SCD2
  `instrument_metadata_history` table at all (verified live,
  `to_regclass('macro_data.instrument_metadata_history') → NULL`; it is not
  among the 3 base tables instrument_master / market_data_daily / load_audit),
  so no per-stem → dated-underlying effective_from/to mapping exists and the
  roll DATES are not knowable from the substrate.  (2) No dated prices: all 19
  bond_future + 24 policy_future instruments are `is_rolling_contract = TRUE`
  generic stems (TY1, BTS1, …) with ZERO dated contracts in
  instrument_master; market_data_daily holds prices ONLY for the generic
  stems, never the dated underlyings (TYH6, BTSZ10) — the DB stores the
  already-spliced front series.  Even if roll dates existed, the old-vs-new
  front prices needed on each roll date do not.  Inferring the gap from the
  generic series' own jump at the roll boundary conflates the true roll gap
  with that day's market move — a proxy P12 forbids.
- What would unblock it: ingest BOTH (a) per-dated-contract price history (the
  individual TYH6/TYM6/… contracts as priced instruments) so the
  old-vs-new front prices exist on each roll date, AND (b) the roll
  schedule — either an explicit roll-calendar table (the absent
  `instrument_metadata_history`) or roll dates derivable from the dated-contract
  series themselves — then the splice/back-adjust is a pure deterministic
  compute.  Data-gated, not scope-gated (plan flags it "needed later by
  Track-B").
- Disposition: deferred (not built); no proxy shipped.

### CleanSingleSeriesV1(ffill_limit=5) missingness label — cross-tool convention (C5.3/m32)
- Gate that fired: OPR11/P5 cross-tool convention — NOT a single-tool fix.
  rates_vol_regime (and the sibling sovereign_curve_regime) stamp
  ``RawNoCleaning()`` on a yield-CHANGE series derived from LEVELS that
  panel_assembly forward-filled (ffill limit=5) before differencing. The
  differencing step itself adds no imputation, so RawNoCleaning is locally
  defensible — but the underlying levels WERE cleaned, and the strictly
  honest platform label would be ``CleanSingleSeriesV1(ffill_limit=5)``
  applied uniformly across every tool that ffills levels then differences.
- Evidence: rates_vol_regime/compute.py:145 stamps RawNoCleaning on
  ``returns = levels.diff()`` where ``levels = raw[col]`` and ``raw`` came
  from ``fetch_instrument_panel(..., ffill_limit_days=5)``. Same pattern in
  sovereign_curve_regime.  Changing the label is a CROSS-TOOL convention
  decision (touches every ffill-then-difference primitive + the
  MissingnessPolicy discriminated-union semantics + any consumer that
  branches on the policy tag) — out of scope for C5.3.
- What was done in C5.3 (the in-scope lighter option): a one-line P5
  disclosure note added to rates_vol_regime/config.yaml's
  ``ffill_limit_days`` rationale (and to compute.py's stamp site) stating
  the upstream ffill(limit=5) on the levels and that RawNoCleaning refers
  only to the differencing step.  The runtime output is unchanged.
- What would unblock it: a platform-wide ADR/decision on the canonical
  missingness label for ffill-then-difference change series, applied in
  lock-step across all such primitives.
- Disposition: platform label DEFERRED (not relabelled); disclosure note
  shipped in C5.3.  No behaviour change; no proxy.

### volume_open_interest_snapshot → volume_open_interest_history rename (C5.3/m36)
- Gate that fired: PR14 wire-format honesty — the "snapshot" name
  under-describes a tool that emits a 252-row DATED HISTORY (correctly
  bridged). The honest rename to ``volume_open_interest_history`` is a
  PR14 schema/wire-format migration.
- Evidence: rates_agent/policy_futures/tools/volume_open_interest_snapshot/
  (the dir + tool_name) + the ``policy_futures_get_volume_open_interest_
  snapshot_tool`` PrimitiveSpec in rates_agent/workflows/__init__.py emit
  ``time_series`` + two canonical CONTRACTS TimeSeries companions (dated
  history), not a single current row.
- What was done in C5.3 (the in-scope disclosure-only option): a one-line
  "Naming honesty (P5 / PR1)" disclosure added to the tool's compute.py
  module docstring + the PrimitiveSpec comment extended to call the rename
  an acknowledged future migration.  The naming honesty was ALREADY
  partially disclosed in the PrimitiveSpec comment + ADR 0017 §3/§4.
- What would unblock it: a PR14 rename PR touching the frontend tool
  registry, the parity fixtures, the PrimitiveSpec key/tool_name, and the
  tool directory — landed as one schema-migration diff.
- Disposition: rename DEFERRED (NOT done — frontend + fixtures touching,
  out of scope); naming honesty DISCLOSED in C5.3.  No behaviour change.

(entries appended per-tool as gates fire)
