from __future__ import annotations

from fx_agent.forwards.tools.forward_curve import (
    FXForwardCurveInput,
    get_fx_forward_curve,
)
from fx_agent.forwards.tools.fx_carry import FXCarryInput, get_fx_carry
from fx_agent.macro.tools.trade_setup.schemas import (
    FXTradeSetupInput,
    FXTradeSetupOutput,
    FXTradeSetupSignal,
)
from fx_agent.spot.tools.spot_levels import FXSpotLevelInput, get_fx_spot_level
from fx_agent.vol.tools.realized_vol import (
    FXRealizedVolInput,
    get_fx_realized_vol,
)


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _stance(score: float) -> str:
    if score > 0.35:
        return "bullish"
    if score < -0.35:
        return "bearish"
    return "neutral"


def _confidence(score: float) -> str:
    abs_score = abs(score)
    if abs_score >= 2.0:
        return "high"
    if abs_score >= 0.9:
        return "medium"
    return "low"


def _spot_score(z_score: float | None, monthly_change_pct: float | None) -> float:
    score = 0.0
    if z_score is not None:
        if z_score >= 1.5:
            score -= 0.8
        elif z_score <= -1.5:
            score += 0.8
        else:
            score += z_score * 0.25
    if monthly_change_pct is not None:
        score += max(min(monthly_change_pct / 3.0, 0.75), -0.75)
    return score


def _carry_score(carry_annualized_pct: float | None) -> float:
    if carry_annualized_pct is None:
        return 0.0
    return max(min(carry_annualized_pct / 2.0, 1.0), -1.0)


def _vol_score(vol_z_score: float | None) -> float:
    if vol_z_score is None:
        return 0.0
    if vol_z_score >= 1.5:
        return -0.7
    if vol_z_score <= -1.0:
        return 0.35
    return 0.0


def _curve_score(forward_rows: list[dict]) -> float:
    if len(forward_rows) < 2:
        return 0.0
    first = forward_rows[0].get("carry_annualized_pct")
    last = forward_rows[-1].get("carry_annualized_pct")
    if first is None or last is None:
        return 0.0
    slope = float(last) - float(first)
    return max(min(slope / 1.5, 0.5), -0.5)


def get_fx_trade_setup(params: FXTradeSetupInput) -> FXTradeSetupOutput:
    pair = params.pair.upper().replace("/", "").strip()
    tenor = params.tenor.upper().strip()

    spot = get_fx_spot_level(
        FXSpotLevelInput(
            pair=pair,
            lookback_days=params.lookback_days,
            field_name="PX_LAST",
        )
    )
    carry = get_fx_carry(FXCarryInput(tenor=tenor))
    forward_curve = get_fx_forward_curve(FXForwardCurveInput(pair=pair))
    vol = get_fx_realized_vol(
        FXRealizedVolInput(
            pair=pair,
            window_observations=params.vol_window_observations,
            lookback_days=params.lookback_days,
            return_type="log_return",
            field_name="PX_LAST",
        )
    )

    spot_metrics = spot.current_metrics
    vol_metrics = vol.current_metrics
    carry_row = next((row for row in carry.rows if row.pair == pair), None)
    curve_rows = [row.model_dump() for row in forward_curve.rows]

    spot_component = _spot_score(
        spot_metrics.z_score,
        spot_metrics.monthly_change_pct,
    )
    carry_component = _carry_score(
        carry_row.carry_annualized_pct if carry_row is not None else None
    )
    vol_component = _vol_score(vol_metrics.realized_vol_z_score)
    curve_component = _curve_score(curve_rows)
    total_score = round(
        spot_component + carry_component + vol_component + curve_component,
        2,
    )

    direction = _stance(total_score)
    confidence = _confidence(total_score)

    signals = [
        FXTradeSetupSignal(
            name="Spot momentum / stretch",
            score=round(spot_component, 2),
            stance=_stance(spot_component),
            description=(
                f"Spot {spot_metrics.current_spot:.4f}, "
                f"1M move {_fmt(spot_metrics.monthly_change_pct)}%, "
                f"z-score {_fmt(spot_metrics.z_score)}."
            ),
        ),
        FXTradeSetupSignal(
            name=f"{tenor} carry",
            score=round(carry_component, 2),
            stance=_stance(carry_component),
            description=(
                "Carry unavailable for this pair/tenor."
                if carry_row is None
                else f"{tenor} carry {_fmt(carry_row.carry_annualized_pct)}% annualized."
            ),
        ),
        FXTradeSetupSignal(
            name="Realized volatility",
            score=round(vol_component, 2),
            stance=_stance(vol_component),
            description=(
                f"{params.vol_window_observations}d realized vol "
                f"{_fmt(vol_metrics.realized_vol_annualized_pct)}%, "
                f"vol z-score {_fmt(vol_metrics.realized_vol_z_score)}."
            ),
        ),
        FXTradeSetupSignal(
            name="Forward curve shape",
            score=round(curve_component, 2),
            stance=_stance(curve_component),
            description=(
                "Forward curve unavailable."
                if not curve_rows
                else (
                    f"Curve spans {curve_rows[0]['tenor']} to "
                    f"{curve_rows[-1]['tenor']} across available forwards."
                )
            ),
        ),
    ]

    key_drivers = [
        signals[0].description,
        signals[1].description,
        signals[2].description,
    ]
    if curve_rows:
        key_drivers.append(signals[3].description)

    risks = []
    if spot_metrics.z_score is not None and abs(spot_metrics.z_score) >= 1.5:
        risks.append("Spot already looks stretched versus its trailing distribution.")
    if (
        vol_metrics.realized_vol_z_score is not None
        and vol_metrics.realized_vol_z_score >= 1.5
    ):
        risks.append("Realized volatility is elevated, so entry timing risk is higher.")
    if carry_row is None:
        risks.append("Carry signal is unavailable for the selected pair and tenor.")
    if not risks:
        risks.append("No single signal is extreme; conviction depends on macro catalyst.")

    summary = (
        f"{pair} trade setup is {direction} with {confidence} confidence "
        f"(score {total_score:+.2f})."
    )

    return FXTradeSetupOutput(
        pair=pair,
        as_of_date=spot_metrics.as_of_date,
        tenor=tenor,
        direction=direction,
        confidence=confidence,
        total_score=total_score,
        summary=summary,
        key_drivers=key_drivers,
        risks=risks,
        follow_up_questions=[
            f"Show me the {pair} forward curve.",
            f"What is {pair} {params.vol_window_observations}d realized vol?",
            "Scan FX for stretched pairs.",
            f"Compare this {pair} view with front-end rates pricing.",
        ],
        signals=signals,
        spot_snapshot=spot_metrics.model_dump(),
        carry_snapshot=None if carry_row is None else carry_row.model_dump(),
        forward_curve=curve_rows,
        realized_vol_snapshot=vol_metrics.model_dump(),
    )
