# FX Universe Map

The FX agent is organized around four research surfaces:

| Surface | Data Family | Current Coverage | Main Outputs |
| --- | --- | --- | --- |
| Spot | `fx_spot` | G10 USD pairs and selected crosses | Levels, momentum, z-scores, stretch scans |
| Forwards | `fx_forward` | 1W, 1M, 3M, 6M where available | Forward points, outrights, annualized carry |
| Volatility | `fx_vol` | ATM implied vol plus spot history | Realized vol, implied-realized premium |
| Macro Overlay | `risk_proxy` + FX | DXY, SPX, VIX, MOVE, gold, oil | Trade context, risk confirmation/divergence |

## Long-Term Coverage Targets

- Add explicit G10 vs EMFX universes once the database includes broader pairs.
- Split forwards into NDF/deliverable conventions if EMFX is added.
- Add fixing calendars, holiday conventions, and broken-date support only when
  the product surface needs them.
- Keep deterministic analytics inside tools; use the orchestrator for synthesis
  and cross-agent routing.

