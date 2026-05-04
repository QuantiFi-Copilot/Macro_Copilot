from __future__ import annotations

from fx_agent.macro.tools.pair_compare.schemas import (
    FXPairCompareInput,
    FXPairCompareOutput,
    FXPairCompareRow,
)
from fx_agent.macro.tools.risk_overlay import (
    FXMacroRiskOverlayInput,
    get_fx_macro_risk_overlay,
)
from fx_agent.macro.tools.trade_setup import FXTradeSetupInput, get_fx_trade_setup
from fx_agent.vol.tools.vol_risk_premium import (
    FXVolRiskPremiumInput,
    get_fx_vol_risk_premium,
)


def _score_row(pair: str, params: FXPairCompareInput) -> FXPairCompareRow:
    setup = get_fx_trade_setup(
        FXTradeSetupInput(
            pair=pair,
            tenor=params.tenor,
            vol_window_observations=params.vol_window_observations,
            lookback_days=params.lookback_days,
        )
    )
    overlay = get_fx_macro_risk_overlay(
        FXMacroRiskOverlayInput(pair=pair, lookback_days=params.lookback_days)
    )
    try:
        vol_premium = get_fx_vol_risk_premium(
            FXVolRiskPremiumInput(
                pair=pair,
                tenor="1M",
                realized_window_observations=params.vol_window_observations,
                lookback_days=params.lookback_days,
            )
        ).current_metrics
        premium = vol_premium.vol_risk_premium_pct
    except Exception:
        premium = None

    return FXPairCompareRow(
        pair=setup.pair,
        direction=setup.direction,
        confidence=setup.confidence,
        trade_setup_score=setup.total_score,
        spot=float(setup.spot_snapshot["current_spot"]),
        carry_annualized_pct=(
            None
            if setup.carry_snapshot is None
            else float(setup.carry_snapshot["carry_annualized_pct"])
        ),
        realized_vol_annualized_pct=setup.realized_vol_snapshot.get(
            "realized_vol_annualized_pct"
        ),
        vol_risk_premium_pct=premium,
        macro_regime=overlay.risk_regime,
        macro_regime_score=overlay.regime_score,
    )


def compare_fx_pairs(params: FXPairCompareInput) -> FXPairCompareOutput:
    pair_1 = params.pair_1.upper().replace("/", "").strip()
    pair_2 = params.pair_2.upper().replace("/", "").strip()

    rows = [_score_row(pair_1, params), _score_row(pair_2, params)]

    # Higher absolute setup score wins only if direction is non-neutral; otherwise
    # use macro score as a small tiebreaker.
    ranked = sorted(
        rows,
        key=lambda row: (abs(row.trade_setup_score), abs(row.macro_regime_score)),
        reverse=True,
    )
    if abs(ranked[0].trade_setup_score - ranked[1].trade_setup_score) < 0.25:
        preferred = None
        rationale = "No clean preference; setup scores are too close."
    else:
        preferred = ranked[0].pair
        rationale = (
            f"{ranked[0].pair} has the cleaner expression: setup score "
            f"{ranked[0].trade_setup_score:+.2f} versus "
            f"{ranked[1].trade_setup_score:+.2f} for {ranked[1].pair}."
        )

    return FXPairCompareOutput(
        pair_1=pair_1,
        pair_2=pair_2,
        preferred_pair=preferred,
        preference_rationale=rationale,
        rows=rows,
    )
