from __future__ import annotations

from fx_agent.macro.tools.correlation_beta import (
    FXCorrelationBetaInput,
    get_fx_correlation_beta,
)
from fx_agent.macro.tools.regime_classifier import (
    FXRegimeClassifierInput,
    classify_fx_regime,
)
from fx_agent.macro.tools.usd_thesis_monitor.schemas import (
    FXUSDThesisExpression,
    FXUSDThesisInput,
    FXUSDThesisMetric,
    FXUSDThesisOutput,
)
from fx_agent.reference.conventions import normalize_pair
from fx_agent.spot.tools.schemas import FXScannerInput
from fx_agent.spot.tools.scanner import run_fx_scanner
from fx_agent.spot.tools.usd_pressure import FXUSDPressureInput, scan_usd_pressure


def _desired_pressure(view: str) -> int:
    return 1 if view == "long_usd" else -1


def _status_from_score(score: float) -> str:
    if score >= 1.0:
        return "supportive"
    if score <= -1.0:
        return "hostile"
    return "mixed"


def _confidence(score: float, confirmations: int, challenges: int) -> str:
    if abs(score) >= 2.0 and max(confirmations, challenges) >= 3:
        return "high"
    if abs(score) >= 1.0 and max(confirmations, challenges) >= 2:
        return "medium"
    return "low"


def _pressure_metric(value: float | None, view: str) -> FXUSDThesisMetric:
    desired = _desired_pressure(view)
    score = value or 0.0
    if abs(score) < 0.5:
        status = "neutral"
    elif score * desired > 0:
        status = "confirming"
    else:
        status = "challenging"
    return FXUSDThesisMetric(
        name="USD pressure score",
        value=f"{score:+.2f}",
        status=status,
        detail="Positive means broad USD strength; negative means broad USD weakness.",
    )


def _dxy_metric(value: float | None, view: str) -> FXUSDThesisMetric:
    desired = _desired_pressure(view)
    score = value or 0.0
    if value is None or abs(score) < 0.5:
        status = "neutral"
    elif score * desired > 0:
        status = "confirming"
    else:
        status = "challenging"
    return FXUSDThesisMetric(
        name="DXY 1M change",
        value="n/a" if value is None else f"{value:+.2f}%",
        status=status,
        detail="DXY momentum should align with the USD thesis.",
    )


def _expression_label(pair: str, view: str) -> str:
    if view == "long_usd":
        return f"short {pair}" if pair.endswith("USD") else f"long {pair}"
    return f"long {pair}" if pair.endswith("USD") else f"short {pair}"


def _row_rationale(pair: str, pressure: float | None, z_score: float | None, view: str) -> str:
    pressure_text = "n/a" if pressure is None else f"{pressure:+.2f}% USD pressure"
    z_text = "n/a" if z_score is None else f"z-score {z_score:+.2f}"
    return f"{pair}: {pressure_text}, {z_text}; expression aligns with {view.replace('_', ' ')} thesis."


def _rank_expressions(pressure_rows, view: str, top_n: int) -> tuple[list[FXUSDThesisExpression], list[FXUSDThesisExpression]]:
    desired = _desired_pressure(view)
    confirming = []
    counter = []
    for row in pressure_rows:
        pressure = row.usd_pressure_pct
        if pressure is None:
            continue
        target = confirming if pressure * desired > 0 else counter
        target.append(
            FXUSDThesisExpression(
                pair=row.pair,
                expression=_expression_label(row.pair, view),
                rationale=_row_rationale(row.pair, pressure, row.z_score, view),
                usd_pressure_pct=pressure,
                z_score=row.z_score,
                monthly_change_pct=row.monthly_change_pct,
            )
        )

    confirming = sorted(
        confirming,
        key=lambda item: abs(item.usd_pressure_pct or 0.0),
        reverse=True,
    )[:top_n]
    counter = sorted(
        counter,
        key=lambda item: (
            abs(item.z_score or 0.0),
            abs(item.usd_pressure_pct or 0.0),
        ),
        reverse=True,
    )[:top_n]
    return confirming, counter


def get_fx_usd_thesis_monitor(params: FXUSDThesisInput) -> FXUSDThesisOutput:
    anchor_pair = normalize_pair(params.anchor_pair)
    view = params.usd_view
    desired = _desired_pressure(view)

    pressure = scan_usd_pressure(
        FXUSDPressureInput(
            lookback_days=params.lookback_days,
            field_name=params.field_name,
        )
    )
    regime = classify_fx_regime(
        FXRegimeClassifierInput(
            anchor_pair=anchor_pair,
            lookback_days=params.lookback_days,
            field_name=params.field_name,
        )
    )
    scanner = run_fx_scanner(
        FXScannerInput(top_n=max(params.top_n, 10), field_name=params.field_name)
    )
    beta = get_fx_correlation_beta(
        FXCorrelationBetaInput(
            pair=anchor_pair,
            lookback_days=params.lookback_days,
            field_name=params.field_name,
        )
    )

    metrics = [
        _pressure_metric(pressure.usd_pressure_score, view),
        _dxy_metric(pressure.dxy_monthly_change_pct, view),
    ]

    confirmations: list[str] = []
    challenges: list[str] = []
    score = 0.0

    for metric in metrics:
        if metric.status == "confirming":
            score += 1.0
            confirmations.append(f"{metric.name}: {metric.value}. {metric.detail}")
        elif metric.status == "challenging":
            score -= 1.0
            challenges.append(f"{metric.name}: {metric.value}. {metric.detail}")

    if view == "long_usd":
        if regime.usd_regime == "USD strength":
            score += 1.0
            confirmations.append(f"FX regime USD pillar is {regime.usd_regime}.")
        elif regime.usd_regime == "USD weakness":
            score -= 1.0
            challenges.append(f"FX regime USD pillar is {regime.usd_regime}.")
    else:
        if regime.usd_regime == "USD weakness":
            score += 1.0
            confirmations.append(f"FX regime USD pillar is {regime.usd_regime}.")
        elif regime.usd_regime == "USD strength":
            score -= 1.0
            challenges.append(f"FX regime USD pillar is {regime.usd_regime}.")

    risk_text = regime.risk_regime.lower()
    if view == "long_usd":
        if "risk-off" in risk_text:
            score += 0.7
            confirmations.append(f"Risk regime is {regime.risk_regime}, which can support USD safe-haven demand.")
        elif "risk-on" in risk_text:
            score -= 0.7
            challenges.append(f"Risk regime is {regime.risk_regime}, which usually weighs on defensive USD demand.")
    else:
        if "risk-on" in risk_text:
            score += 0.7
            confirmations.append(f"Risk regime is {regime.risk_regime}, which can support short-USD conditions.")
        elif "risk-off" in risk_text:
            score -= 0.7
            challenges.append(f"Risk regime is {regime.risk_regime}, which can support USD safe-haven demand.")

    if regime.vol_regime == "vol rich" and view == "long_usd":
        score += 0.4
        confirmations.append("Vol regime is rich/stressed, which can support USD hedge demand.")
    elif regime.vol_regime == "vol cheap/calm" and view == "long_usd":
        score -= 0.4
        challenges.append("Vol regime is cheap/calm, which reduces USD hedge demand.")
    elif regime.vol_regime == "vol cheap/calm" and view == "short_usd":
        score += 0.4
        confirmations.append("Vol regime is cheap/calm, which supports carry/risk exposure over USD hedge demand.")

    best_expressions, counter_moves = _rank_expressions(pressure.rows, view, params.top_n)
    if best_expressions:
        score += 0.5
        confirmations.append(f"{len(best_expressions)} G10 pair(s) currently express the {view.replace('_', ' ')} thesis.")
    else:
        score -= 0.5
        challenges.append("No scanned G10 pair currently confirms the USD thesis on breadth.")

    stretched_counter_moves = [
        item for item in counter_moves if item.z_score is not None and abs(item.z_score) >= 1.0
    ][: params.top_n]
    for row in scanner.rows:
        if row.z_score is None or abs(row.z_score) < 1.5:
            continue
        # Add scanner-only stretched pairs if they were not in pressure rows.
        if any(item.pair == row.pair for item in stretched_counter_moves):
            continue
        monthly = row.monthly_change_pct or 0.0
        pressure_sign = -monthly if row.pair.endswith("USD") else monthly
        if pressure_sign * desired < 0:
            stretched_counter_moves.append(
                FXUSDThesisExpression(
                    pair=row.pair,
                    expression=_expression_label(row.pair, view),
                    rationale=(
                        f"{row.pair} is stretched against the thesis "
                        f"(z-score {row.z_score:+.2f}, signal {row.signal})."
                    ),
                    usd_pressure_pct=pressure_sign,
                    z_score=row.z_score,
                    monthly_change_pct=row.monthly_change_pct,
                )
            )
        if len(stretched_counter_moves) >= params.top_n:
            break

    if beta.dominant_driver:
        metrics.append(
            FXUSDThesisMetric(
                name=f"{anchor_pair} dominant beta",
                value=f"{beta.dominant_driver} corr {beta.dominant_correlation:+.2f}"
                if beta.dominant_correlation is not None
                else str(beta.dominant_driver),
                status="neutral",
                detail=beta.summary,
            )
        )

    score = round(score, 2)
    status = _status_from_score(score)
    confidence = _confidence(score, len(confirmations), len(challenges))

    invalidation = [
        "USD pressure score flips against the thesis.",
        "DXY 1M momentum flips against the thesis.",
        "At least four core G10 USD pairs move against the thesis.",
    ]
    if view == "long_usd":
        invalidation.extend([
            "Risk regime remains risk-on and vol stays cheap/calm.",
            "Carry regime remains carry-friendly for non-USD exposure.",
        ])
    else:
        invalidation.extend([
            "Risk regime turns risk-off or volatility spikes materially.",
            "DXY breaks higher with broad USD breadth confirmation.",
        ])

    summary = (
        f"{view.replace('_', ' ').title()} thesis is {status} with {confidence} "
        f"confidence (score {score:+.2f})."
    )

    return FXUSDThesisOutput(
        usd_view=view,
        as_of_date=pressure.as_of_date or regime.as_of_date,
        thesis_status=status,
        confidence=confidence,
        confirmation_score=score,
        summary=summary,
        confirmations=confirmations,
        challenges=challenges,
        best_expressions=best_expressions,
        stretched_counter_moves=stretched_counter_moves,
        metrics_to_watch=metrics,
        invalidation_signals=invalidation,
    )

