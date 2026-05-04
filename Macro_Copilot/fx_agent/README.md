# FX Agent

The FX agent owns deterministic tools for spot FX, forwards, carry, realized
volatility, implied-vs-realized volatility, and FX-to-macro overlays.

## Structure

- `spot/`: spot snapshots, stretched-pair scans, and broad USD pressure.
- `forwards/`: forward curves, tenor carry, and carry-basket construction.
- `vol/`: realized volatility and volatility risk premium tools.
- `macro/`: trade setup synthesis, pair comparison, and macro risk overlays.
- `reference/`: shared FX constants and conventions used across tools.
- `playbooks/`: prompt/workflow definitions for orchestrated FX research.

## Current MCP Tools

| Tool | Workspace | Purpose |
| --- | --- | --- |
| `get_fx_spot_level_tool` | yes | Spot level, changes, range, z-score. |
| `get_fx_data_health_tool` | no | FX data coverage, missing series, stale-series diagnostics. |
| `get_fx_carry_tool` | yes | Cross-pair carry ranking for a tenor. |
| `get_fx_forward_curve_tool` | yes | Forward curve and tenor carry for one pair. |
| `get_fx_carry_decay_tool` | no | Front-loaded vs persistent carry diagnosis across tenors. |
| `scan_fx_extremes_tool` | yes | FX spot stretch and momentum scanner. |
| `get_fx_realized_vol_tool` | yes | Realized volatility snapshot and time series. |
| `get_fx_trade_setup_tool` | yes | Deterministic spot/carry/vol/forward signal stack. |
| `get_fx_macro_risk_overlay_tool` | yes | FX setup against DXY, equity, vol, rates vol, gold, oil. |
| `classify_fx_regime_tool` | no | Broad FX regime across USD, risk, vol, and carry. |
| `get_fx_vol_risk_premium_tool` | yes | Implied-vs-realized volatility premium for one pair. |
| `scan_fx_vol_risk_premium_tool` | no | Cross-pair volatility premium scan. |
| `compare_fx_pairs_tool` | no | Relative setup comparison between two pairs. |
| `scan_usd_pressure_tool` | no | Broad USD pressure across G10 spot pairs. |
| `build_fx_carry_basket_tool` | no | Long/short carry basket candidate builder. |

## Conventions

Shared constants live in `fx_agent/reference/conventions.py`. New tools should
reuse those helpers for pair normalization, tenor day-counts, forward-point
conversion, and default market-data fields.

The REST API may expose full chartable payloads for workspace views. The MCP
surface should keep returning compact deterministic JSON for orchestration and
LLM summarization.
