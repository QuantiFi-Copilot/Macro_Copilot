from __future__ import annotations

from statistics import mean

from fx_agent.forwards.tools.carry_basket import FXCarryBasketInput, build_fx_carry_basket
from fx_agent.macro.tools.regime_classifier.schemas import (
    FXRegimeClassifierInput,
    FXRegimeClassifierOutput,
    FXRegimeComponent,
)
from fx_agent.macro.tools.risk_overlay import (
    FXMacroRiskOverlayInput,
    get_fx_macro_risk_overlay,
)
from fx_agent.reference.conventions import normalize_pair, normalize_tenor
from fx_agent.spot.tools.usd_pressure import FXUSDPressureInput, scan_usd_pressure
from fx_agent.vol.tools.vol_risk_premium_scanner import (
    FXVolRiskPremiumScannerInput,
    scan_fx_vol_risk_premium,
)


def _usd_component(pressure_score: float, regime: str) -> FXRegimeComponent:
    if pressure_score <= -0.5:
        label = "USD weakness"
        score = 1.0
    elif pressure_score >= 0.5:
        label = "USD strength"
        score = -1.0
    else:
        label = "mixed USD"
        score = 0.0
    return FXRegimeComponent(
        name="USD",
        label=label,
        score=score,
        summary=f"{regime} with aggregate USD pressure score {pressure_score:+.2f}.",
    )


def _risk_component(regime: str, score_value: float) -> FXRegimeComponent:
    if score_value >= 1.0:
        label = "risk-on"
        score = 1.0
    elif score_value <= -1.0:
        label = "risk-off"
        score = -1.0
    else:
        label = "mixed risk"
        score = 0.0
    return FXRegimeComponent(
        name="Risk",
        label=label,
        score=score,
        summary=f"{regime} with macro risk score {score_value:+.2f}.",
    )


def _vol_component(premiums: list[float]) -> FXRegimeComponent:
    if not premiums:
        return FXRegimeComponent(
            name="Vol",
            label="vol data unavailable",
            score=0.0,
            summary="No usable implied-vs-realized volatility premium rows.",
        )

    avg_premium = mean(premiums)
    if avg_premium >= 2.0:
        label = "vol rich"
        score = -0.6
    elif avg_premium <= 0.25:
        label = "vol cheap/calm"
        score = 0.5
    else:
        label = "normal vol premium"
        score = 0.1
    return FXRegimeComponent(
        name="Vol",
        label=label,
        score=score,
        summary=f"Average scanned vol risk premium is {avg_premium:.2f} vol points.",
    )


def _carry_component(long_count: int, excluded_count: int) -> FXRegimeComponent:
    if long_count >= 2 and excluded_count <= 2:
        label = "carry-friendly"
        score = 0.8
    elif long_count >= 1:
        label = "selective carry"
        score = 0.3
    else:
        label = "carry unattractive"
        score = -0.5
    return FXRegimeComponent(
        name="Carry",
        label=label,
        score=score,
        summary=f"{long_count} long-carry candidates after risk filters; {excluded_count} exclusions.",
    )


def _confidence(total_score: float, components: list[FXRegimeComponent]) -> str:
    active = [component for component in components if abs(component.score) >= 0.5]
    if len(active) >= 3 and abs(total_score) >= 2.0:
        return "high"
    if len(active) >= 2 and abs(total_score) >= 1.0:
        return "medium"
    return "low"


def _overall(usd: str, risk: str, vol: str, carry: str, total_score: float) -> str:
    parts: list[str] = []
    if risk in {"risk-on", "risk-off"}:
        parts.append(risk)
    if usd in {"USD weakness", "USD strength"}:
        parts.append(usd)
    if carry in {"carry-friendly", "selective carry", "carry unattractive"}:
        parts.append(carry)
    if vol in {"vol rich", "vol cheap/calm"}:
        parts.append(vol)
    if parts:
        return ", ".join(parts)
    if total_score > 0:
        return "mildly constructive FX regime"
    if total_score < 0:
        return "mildly defensive FX regime"
    return "mixed FX regime"


def classify_fx_regime(params: FXRegimeClassifierInput) -> FXRegimeClassifierOutput:
    anchor_pair = normalize_pair(params.anchor_pair)
    tenor = normalize_tenor(params.tenor)

    usd = scan_usd_pressure(
        FXUSDPressureInput(
            lookback_days=params.lookback_days,
            field_name=params.field_name,
        )
    )
    risk = get_fx_macro_risk_overlay(
        FXMacroRiskOverlayInput(
            pair=anchor_pair,
            lookback_days=params.lookback_days,
            correlation_window_observations=params.correlation_window_observations,
            field_name=params.field_name,
        )
    )
    vol_scan = scan_fx_vol_risk_premium(
        FXVolRiskPremiumScannerInput(
            tenor=tenor,
            realized_window_observations=params.realized_window_observations,
            lookback_days=params.lookback_days,
            top_n=10,
        )
    )
    carry = build_fx_carry_basket(
        FXCarryBasketInput(
            tenor=tenor,
            basket_size=2,
            lookback_days=params.lookback_days,
        )
    )

    vol_premiums = [
        row.vol_risk_premium_pct
        for row in vol_scan.rows
        if row.vol_risk_premium_pct is not None
    ]

    components = [
        _usd_component(usd.usd_pressure_score, usd.pressure_regime),
        _risk_component(risk.risk_regime, risk.regime_score),
        _vol_component(vol_premiums),
        _carry_component(len(carry.long_legs), len(carry.excluded_pairs)),
    ]
    total_score = round(sum(component.score for component in components), 2)
    component_by_name = {component.name: component for component in components}
    overall = _overall(
        usd=component_by_name["USD"].label,
        risk=component_by_name["Risk"].label,
        vol=component_by_name["Vol"].label,
        carry=component_by_name["Carry"].label,
        total_score=total_score,
    )

    drivers = [
        component.summary
        for component in components
        if component.score > 0
    ]
    risks = [
        component.summary
        for component in components
        if component.score < 0
    ]
    if usd.dxy_monthly_change_pct is not None:
        drivers.append(f"DXY 1M change is {usd.dxy_monthly_change_pct:+.2f}%.")

    return FXRegimeClassifierOutput(
        as_of_date=risk.as_of_date or usd.as_of_date,
        anchor_pair=anchor_pair,
        overall_regime=overall,
        confidence=_confidence(total_score, components),
        total_score=total_score,
        usd_regime=component_by_name["USD"].label,
        risk_regime=component_by_name["Risk"].label,
        vol_regime=component_by_name["Vol"].label,
        carry_regime=component_by_name["Carry"].label,
        components=components,
        drivers=drivers,
        risks=risks,
        follow_ups=[
            f"Show me the {anchor_pair} macro risk overlay.",
            "Scan broad USD pressure.",
            f"Show {anchor_pair} carry decay.",
            "Scan FX vol risk premium.",
        ],
    )

