# Backtest Archetype

Phase 1 closing PR (PR 21) documents the canonical signal-driven
backtest workflow.  This file is the authoritative reference for
what the archetype computes, what it explicitly does NOT compute,
and which methodology disclosures the workspace card surfaces.

## Position in the closed-family archetype list

The backtest archetype is the 5th member of the closed
``WorkflowArchetype`` Literal in
[`shared/workflow/template.py`](../../shared/workflow/template.py),
alongside ``event_study`` / ``regime_conditioned_relationship`` /
``attribution_decomposition`` / ``cross_sectional_screen``.

Adding it to the closed family required:

  * extending the ``WorkflowArchetype`` Literal + ``WORKFLOW_ARCHETYPES``
    tuple (PR 19)
  * adding the no-op sentinel migration ``0008_backtest_archetype.py``
    (PR 19)
  * adding ``TradeSet`` to ``ARTIFACT_TYPE_NAMES`` (PR 20) so the
    workflow executor can label the intermediate ``construct_trades``
    output correctly

These three additions are the closed-family-extension paper trail —
every step has its own discipline file.

## DAG topology — locked

```
signal ──→ events ──→ trades ──┐
                                ├──→ evaluate ──→ summarize  (terminal)
   price_panel ────────────────┤
   financing_rate ─────────────┘
```

Seven nodes, six edges.  The two parallel upstream branches
(`signal → events → trades` and `price_panel`+`financing_rate`)
fan into `evaluate_trades`, which produces the per-trade P&L Panel
that `summarize_trades` reduces to the terminal BacktestReport Panel.

### Node-by-node

| Node | Kind | Tool / Operator | Output artifact |
|---|---|---|---|
| signal | primitive | caller-bound (e.g. `calculate_zscore_custom_tool`) | Series (Z_SCORE or BPS) |
| events | operator | `threshold_events` | EventSet |
| trades | operator | `construct_trades` | TradeSet |
| price_panel | primitive | `build_sovereign_yield_panel_tool` | Panel (yield columns) |
| financing | primitive | `compute_financing_rate_tool` | Panel (single rate column) |
| evaluate | operator | `evaluate_trades` | Panel (per-trade P&L) |
| **summarize** | operator | `summarize_trades` | Panel (1 row × N metrics) ← TERMINAL |

The terminal Panel's columns are the V1 metric set: `hit_rate`,
`mean_pnl`, `sharpe_annualized`, `max_drawdown`, `p10_pnl`,
`p50_pnl`, `p90_pnl`.  Their units (`RATIO` / `BPS`) propagate
through `Panel.units_by_column` so the methodology renderer reads
them correctly.

## Slot schema

```
signal_tool_name                str   required
signal_params                   dict  required
signal_output_field             str   required
signal_threshold                float required

long_leg_curve_family           str   required
long_leg_tenor                  str   required
long_leg_instrument_key         str   required   <- pre-computed by caller
long_leg_weight                 float optional (default -1.0)

short_leg_curve_family          str   required
short_leg_tenor                 str   required
short_leg_instrument_key        str   required   <- pre-computed by caller
short_leg_weight                float optional (default +1.0)

holding_window_days             int   optional (default 20)

start_date                      str   required (YYYY-MM-DD)
end_date                        str   required (YYYY-MM-DD)

financing_method                str   optional (default "overnight_index_proxy")
financing_proxy_curve           str   optional (required when method=overnight_index_proxy)
financing_constant_rate_pct     float optional (required when method=constant_rate)
financing_basis                 str   optional (default "act_360")
```

The slot binder does NOT support nested format-string substitution
(only direct ``{$slot: name}`` refs), so the caller pre-computes
`long_leg_instrument_key` / `short_leg_instrument_key` to match
the price panel's column names (e.g. ``"USD_TIPS_10Y"``).  The
backtest_workflow MCP wrapper auto-composes these from
`{curve_family}_{tenor}` when the LLM caller omits them.

## Canonical TIPS-vs-Nominal binding

```yaml
signal_tool_name: calculate_zscore_custom_tool
signal_params:
  curve_family: UST
  tenor: 2Y
  z_score_window_days: 252
  lookback_days: 1825
signal_output_field: time_series
signal_threshold: 1.5

long_leg_curve_family: USD_TIPS
long_leg_tenor: 10Y
long_leg_instrument_key: USD_TIPS_10Y
long_leg_weight: -1.0          # bet on TIPS yield falling

short_leg_curve_family: UST
short_leg_tenor: 2Y
short_leg_instrument_key: UST_2Y
short_leg_weight: 1.0          # bet on UST 2Y yield rising

holding_window_days: 125       # ~6 months
start_date: "2019-01-01"
end_date: "2024-12-31"

financing_method: overnight_index_proxy
financing_proxy_curve: USD_SOFR_OIS
financing_basis: act_360
```

## Methodology disclosures (V1)

The workspace methodology card MUST surface every disclosure below.
Lineage steps fold each into their `params` dict so the workspace
renderer reads them off the artifact's lineage chain (no out-of-band
state).

### Yield-change P&L (NOT dollar P&L)

`evaluate_trades` computes per-trade P&L as
`leg.weight × (yield[t] − yield[entry])`.  This is **yield-change
accounting in basis points**, NOT DV01-weighted dollar P&L.

* A 1bp yield move on a 10Y leg contributes the same to the P&L
  panel as a 1bp move on a 2Y leg.
* In actual dollar P&L the 10Y move is ~4x more impactful because
  modified duration scales linearly with tenor.
* Cross-tenor long-short comparisons in the output are NOT
  risk-equivalent.
* Defensible V1 convention because it uses ONLY data we have
  (yields).  Dollar-P&L conversion would require modified duration
  and dirty price for each instrument — not currently ingested.

This disclosure lives on `shared/operators/evaluate_trades/config.yaml`
under `methodology.what_it_does` and propagates via the workspace
methodology renderer.

### Frictionless V1

* Mid-yield pricing only — no bid/ask.
* No transaction costs, no slippage.
* No borrow-availability constraints for short legs.

### Financing via OIS overnight-rate proxy

When `financing_method = overnight_index_proxy`, financing carry
is read from the **shortest-available OIS tenor** of the user-
specified proxy curve (`USD_SOFR_OIS`, `EUR_ESTR_OIS`, etc.).  Today
that shortest available tenor is **1W**, not the true overnight rate
— true O/N OIS quotes are not yet ingested.  Error magnitude is
small (sub-bp per day under normal yield-curve shapes) but the
methodology card surfaces this caveat explicitly.

When `financing_method = constant_rate`, the caller supplies the
rate explicitly via `financing_constant_rate_pct`.  No default is
applied; the Pydantic validator on `FinancingRateInput` rejects
calls that omit the value.

`term_repo_curve` and `gc_special_blend` are declared in the
`FinancingMethod` Literal for forward-compat but raise
`NotImplementedError` at compute time — true repo data is not yet
ingested.

### Fixed-tenor generic curves (no OTR resolution)

The `sovereign_yield_panel` primitive reads yields from Bloomberg
generic benchmarks (`GT2 Govt`, `GT10 Govt`, `GTII10 Govt`, …).
These are rolling synthetic series — they do not pin to a specific
CUSIP at any point in time.  A backtest that says "long the
on-the-run 10Y from 2019 through 2024" actually trades the rolling
benchmark series, NOT the specific bonds that were OTR at each
historical date.

Real OTR resolution requires an `otr_history` table mapping
`(benchmark_family, date) → CUSIP` — Phase 2 data-infrastructure
work.

### No TIPS CPI seasonal carry

The TIPS leg's P&L is the change in TIPS real-yield × weight.
This MISSES the seasonal carry component: TIPS principal accrues
with the CPI-U NSA index, which has a well-documented seasonal
pattern (gasoline, summer-heating, etc.) and a 2-month publication
lag.

A complete TIPS backtest would include this seasonal accrual as a
separate P&L component.  V1 omits it because CPI data isn't
ingested.  The long-leg TIPS P&L is therefore **understated by the
seasonal accrual magnitude** — typically 5-20bp per year depending
on the holding period.

Phase 2 plan: ingest CPI-U NSA + seasonal-factor data, add a
`tips_carry_seasonal` primitive, extend the workflow template to
include the carry component when the long leg is a TIPS instrument.

## What the backtest archetype is and is not

**IS**: a deterministic, content-addressed, replay-faithful
record of "what would have happened to a yield-change-space P&L
metric if a specific signal triggered specific long-short trades
between two specific dates."

**IS NOT**: a recommendation engine.  IS NOT a dollar-P&L simulator.
IS NOT a substitute for backtest software with full bond pricing.

The brief's stress-tested acceptance criterion is: "External
practitioner sign-off — no recommendation actions, just 'this is
what it computed and the assumptions are honestly disclosed'."
This document IS that disclosure.

## Replay determinism

Every node's output artifact carries a lineage chain with content-
addressed step hashes.  Two callers running the same template with
the same slot values produce the same terminal artifact hash, even
across server restarts.  The PR 20 hash-stability vectors
(`EXPECTED_SOVEREIGN_YIELD_PANEL_HASH`,
`EXPECTED_FINANCING_RATE_HASH`,
`EXPECTED_BREAKEVEN_INFLATION_HASH`,
`EXPECTED_SUMMARIZE_TRADES_TERMINAL_HASH`) anchor this guarantee
at the `_compute_step_hash` canonicalization level.

The `tests/test_workflow_backtest_parity.py` fixture pins the
terminal summary metrics for a canonical input set — if any compute
drift happens, the canary trips loudly.

## Where the code lives

* Template YAML — `rates_agent/workflows/backtest/template.yaml`
* Template registration — `rates_agent/workflows/backtest/__init__.py`
* Workflow MCP wrapper — `rates_agent/workflows/mcp_server.py:backtest_workflow`
* Primitive registrations — `rates_agent/workflows/__init__.py:_PRIMITIVE_SPECS`
* Operator registrations — `shared/workflow/registry.py:OPERATOR_REGISTRY`
* Synthetic test — `tests/test_workflow_backtest_synthetic.py`
* End-to-end executor test — `tests/test_workflow_backtest_e2e.py`
* Parity canary — `tests/test_workflow_backtest_parity.py`

## What's deferred (with explicit data prerequisites)

| Feature | Prerequisite |
|---|---|
| Dollar P&L mode | Bloomberg `MOD_DUR_MID` + `PX_DIRTY` per instrument |
| TIPS seasonal carry | CPI-U NSA data + seasonal-factor table ingestion |
| Real OTR resolution | `otr_history` table mapping benchmark+date → CUSIP |
| Real repo financing | term-repo + GC-special data ingestion |
| Asset-swap-spread (present-value mode) | Bond-level coupon + accrued interest + day-count |
| `per_meeting_pricing` (OIS) | Bloomberg WIRP data ingestion |
| `policy_path_since_event` (OIS) | Same WIRP gap, OR scope-narrow to existing OIS deltas |
| `carry_and_roll` (sovereign) | Bond-level coupon, duration, day-count, accrued — not ingestable from rolling generics alone |

See [`docs/technical_debt.md`](../technical_debt.md) for the full
deferred-item register with effort + gating notes per item.
