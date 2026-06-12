# fable_build DEFER LOG (tmp/fable_plan_2.md Appendix E)

Every Track-A tool that was NOT built, with the gate that fired, evidence, and the
unblock condition. *Defer, never proxy.* Entries are appended as gates fire during the
build; the §7 C-DEFER items are entered with **live-DB evidence** (the data substrate
has grown since the plan was written — see `tmp/fable_build/BASELINE.md` and the
data-reality section below).

## Live data reality (verified 2026-06-11, container `macro-tsdb`)

`macro_data.instrument_master`: 335 instruments / 10 types — the plan's §9 six core
types (sovereign_benchmark 72, ois_swap 78, inflation_linker 24, inflation_swap 21,
bond_future 19, policy_future 24; 2005→2026-05-22) PLUS, beyond §9:
`wirp_meeting` (73; WIRP_IMPLIED_RATE/MOVE_PROB/NUM_MOVES/RATE_CHANGE; 2024-02→),
`sovereign_cash_bond` (14; PX_CLEAN_MID/PX_DIRTY_MID/RISK_MID/YLD_YTM_MID/
ASSET_SWAP_SPD_MID; 2024-11→), `overnight_rfr` (4; PX_LAST; 2005→),
`inflation_reference` (6; PX_LAST monthly; 2005→). Auxiliary tables now loaded:
`event_calendar` (521 rows), `futures_deliverables` (9,339 rows), `otr_history`
(14 rows), `instrument_metadata_history` (6,501 rows). `cusip`/`isin` populated for
the 14 cash bonds. sovereign_benchmark now also carries YLD_YTM_BID/ASK.

Defer reasoning below cites the LIVE state, not the plan's stale §9 claims, per plan
§0.7 (live code/data is the truth).

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
  tracking error is the trivial existing chain) + OPR6/P9 (the only delta a
  dedicated operator would add is √252 annualization — a trading-calendar
  convention, i.e. finance math, which may not live in a finance-blind operator).
- Evidence: `series_arithmetic(op='subtract')` → `summarize_series(statistic='std')`
  produces exactly the unannualized tracking error as a ScalarMetric, with full
  parameter freedom.  The √252 annualization factor presumes a 252-trading-day
  year — an asset-class/calendar convention (OPR6: "day-count … = finance-aware =
  primitive territory").
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
- Evidence: the plan row reads "annualized rolling σ of returns"; annualization
  presumes a 252-trading-day year (OPR6: "day-count … = finance-aware = primitive
  territory"); with annualization stripped, nothing distinct remains that the
  live two-node chain does not compose cleanly.
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

(entries appended per-tool as gates fire)
