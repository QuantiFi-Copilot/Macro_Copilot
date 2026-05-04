# FX Spot, Forwards, Carry, and Volatility

## Instrument Scope

The current FX stack covers spot pairs, FX forward points, ATM implied
volatility, and macro proxy overlays. It is designed to sit beside the Rates
agent in the same orchestrated research workflow.

## Data Requirements

- Spot instruments use `instrument_type = fx_spot` and a `pair` attribute.
- Forward instruments use `instrument_type = fx_forward`, a `pair` attribute,
  and a normalized tenor such as `1W`, `1M`, `3M`, or `6M`.
- Vol instruments use `instrument_type = fx_vol`, a `pair` attribute, and a vol
  tenor where available.
- Market levels use `field_name = PX_LAST` unless a tool explicitly allows an
  override.

## Core Analytics

- Spot level tools calculate latest level, trailing changes, range position,
  and z-score.
- Carry tools convert Bloomberg forward points into spot units, calculate
  outright forwards, and annualize carry by tenor.
- Forward-curve tools expose carry term structure for one pair.
- Realized-vol tools calculate annualized realized volatility from spot returns.
- Vol-risk-premium tools compare implied volatility against realized volatility.
- Macro overlay tools compare a pair setup against DXY, equities, volatility,
  rates volatility, gold, and oil.

## Design Principles

- Keep pair and tenor conventions centralized in `fx_agent/reference`.
- Prefer deterministic outputs that can be charted or summarized downstream.
- Add workspace views for stable user-facing payloads; keep exploratory scans
  MCP-only until their UI contract is clear.
- Match Rates agent manifest and folder conventions so orchestration can route
  across desks cleanly.

