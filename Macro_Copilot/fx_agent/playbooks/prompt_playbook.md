# FX Prompt Playbook

Use these prompts to exercise the FX agent as a macro research assistant. The
goal is to validate or challenge market theses with deterministic tools, not to
produce trade recommendations.

## Data Readiness

```text
Check FX data health. Tell me which FX spot pairs, forward tenors, vol series
and macro risk proxies are available, missing or stale.
```

Expected tools:
- `get_fx_data_health_tool`

What to watch:
- `status`
- `missing_pairs`
- missing forward / vol tenors
- `stale_series`

## Regime Snapshot

```text
Classify the FX regime and explain whether the current setup is USD strength,
USD weakness, risk-on, risk-off, carry-friendly or vol-stressed.
```

Expected tools:
- `classify_fx_regime_tool`
- optional `scan_usd_pressure_tool`

What to watch:
- `overall_regime`
- `usd_regime`
- `risk_regime`
- `vol_regime`
- `carry_regime`
- `confidence`

## Long / Short Currency Thesis

```text
Stress-test a long USD view. Is the thesis supportive, mixed or hostile? Show
the confirming signals, challenges, best expressions and invalidation signals.
```

```text
Stress-test a long AUD view across G10 FX. Which pairs confirm it, which pairs
challenge it, and what should I monitor next?
```

```text
Monitor a short JPY thesis using currency pressure, stretched pairs and
available FX breadth.
```

Expected tools:
- `get_fx_currency_thesis_monitor_tool`
- optional `get_fx_usd_thesis_monitor_tool`
- optional `scan_fx_currency_pressure_tool`
- optional `scan_fx_extremes_tool`

What to watch:
- `thesis_status`
- `confirmation_score`
- `currency_pressure_score_pct`
- `currency_rank`
- `best_expressions`
- `stretched_counter_moves`
- `invalidation_signals`

## Currency Strength Ranking

```text
Rank G10 currencies by FX pressure and explain which currencies are strongest
and weakest right now.
```

Expected tools:
- `scan_fx_currency_pressure_tool`

What to watch:
- `strongest_currency`
- `weakest_currency`
- pressure score by currency
- confirming and challenging pairs

## Pair Trade Setup

```text
Build an EURUSD trade setup using spot, carry, forward curve and realized vol.
Tell me whether it is bullish, bearish or neutral and why.
```

```text
Compare EURUSD and GBPUSD as expressions of a USD view. Which one is cleaner
and what are the risks?
```

Expected tools:
- `get_fx_trade_setup_tool`
- `compare_fx_pairs_tool`
- optional `get_fx_carry_decay_tool`

What to watch:
- `direction`
- `confidence`
- `total_score`
- spot momentum / stretch
- carry contribution
- realized vol contribution
- forward curve shape

## Macro Driver / Beta

```text
What is EURUSD most sensitive to right now? Show correlation, beta and dominant
macro driver.
```

```text
Does EURUSD beta to DXY, SPX, VIX, MOVE, gold and oil support a long USD view?
```

Expected tools:
- `get_fx_correlation_beta_tool`
- optional `get_fx_macro_risk_overlay_tool`

What to watch:
- `dominant_driver`
- `dominant_correlation`
- beta
- R-squared
- high-beta risk warnings

## Macro Risk Overlay

```text
Show the EURUSD macro risk overlay. Does DXY, SPX, VIX, MOVE, gold or oil
confirm or challenge the FX setup?
```

Expected tools:
- `get_fx_macro_risk_overlay_tool`

What to watch:
- `risk_regime`
- `regime_score`
- DXY monthly change
- VIX / MOVE z-score
- proxy correlations to the pair

## Vol And Carry

```text
Is EURUSD carry front-loaded or persistent across the forward curve?
```

```text
Scan FX vol risk premium. Which pairs have rich or cheap implied volatility
versus realized volatility?
```

Expected tools:
- `get_fx_carry_decay_tool`
- `get_fx_vol_risk_premium_tool`
- `scan_fx_vol_risk_premium_tool`

What to watch:
- carry decay label
- best tenor
- vol risk premium
- premium z-score
- suggested vol expression

## Cross-Desk FX / Rates

```text
Compare EURUSD setup with UST-DE_BUND 2Y differential. If Rates data is
missing, explain exactly what is missing and what to re-run after ingestion.
```

Expected tools:
- `get_fx_rates_differential_overlay_tool`
- optional Rates tools when Rates data is available

What to watch:
- FX setup score
- rates status
- missing data diagnostics
- consistency label

## Demo Sequence

1. `Check FX data health.`
2. `Classify the FX regime.`
3. `Rank G10 currencies by FX pressure.`
4. `Stress-test a long USD view.`
5. `Build an EURUSD trade setup.`
6. `What is EURUSD most sensitive to right now?`
7. `Compare EURUSD setup with UST-DE_BUND 2Y differential.`

