from __future__ import annotations

from fx_agent.macro.tools.currency_thesis_monitor.schemas import (
    FXCurrencyThesisExpression,
    FXCurrencyThesisInput,
    FXCurrencyThesisMetric,
    FXCurrencyThesisOutput,
)
from fx_agent.reference.conventions import G10_CURRENCIES, split_pair
from fx_agent.spot.tools.currency_pressure import (
    FXCurrencyPressureInput,
    scan_currency_pressure,
)
from fx_agent.spot.tools.schemas import FXScannerInput
from fx_agent.spot.tools.scanner import run_fx_scanner


def _desired(view: str) -> int:
    return 1 if view == "long" else -1


def _status(score: float) -> str:
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


def _expression(pair: str, currency: str, view: str) -> str:
    base, quote = split_pair(pair)
    if currency == base:
        return f"{view} {pair}"
    if currency == quote:
        return f"{'short' if view == 'long' else 'long'} {pair}"
    return f"{view} {currency} via {pair}"


def _to_expression(item, currency: str, view: str) -> FXCurrencyThesisExpression:
    return FXCurrencyThesisExpression(
        pair=item.pair,
        expression=_expression(item.pair, currency, view),
        rationale=(
            f"{item.pair} contributes "
            f"{'n/a' if item.contribution_pct is None else f'{item.contribution_pct:+.2f}%'} "
            f"to {currency} pressure."
        ),
        currency_contribution_pct=item.contribution_pct,
        z_score=item.z_score,
        monthly_change_pct=item.monthly_change_pct,
    )


def _metric(currency: str, score: float, view: str) -> FXCurrencyThesisMetric:
    desired = _desired(view)
    if abs(score) < 0.5:
        status = "neutral"
    elif score * desired > 0:
        status = "confirming"
    else:
        status = "challenging"
    return FXCurrencyThesisMetric(
        name=f"{currency} pressure score",
        value=f"{score:+.2f}%",
        status=status,
        detail="Positive means broad currency strength across available FX pairs.",
    )


def get_fx_currency_thesis_monitor(
    params: FXCurrencyThesisInput,
) -> FXCurrencyThesisOutput:
    currency = params.currency.upper().strip()
    if currency not in G10_CURRENCIES:
        raise ValueError(f"Unsupported currency={currency}. Supported: {', '.join(G10_CURRENCIES)}")

    view = params.view
    desired = _desired(view)
    pressure = scan_currency_pressure(
        FXCurrencyPressureInput(
            lookback_days=params.lookback_days,
            field_name=params.field_name,
        )
    )
    scanner = run_fx_scanner(
        FXScannerInput(top_n=max(params.top_n, 10), field_name=params.field_name)
    )

    rows_by_currency = {row.currency: row for row in pressure.rows}
    row = rows_by_currency.get(currency)
    if row is None:
        raise ValueError(f"No currency pressure row available for {currency}.")

    ranked = [item.currency for item in pressure.rows]
    rank = ranked.index(currency) + 1 if currency in ranked else None
    metric = _metric(currency, row.pressure_score_pct, view)

    confirmations: list[str] = []
    challenges: list[str] = []
    score = 0.0
    if metric.status == "confirming":
        score += 1.2
        confirmations.append(f"{metric.name}: {metric.value}.")
    elif metric.status == "challenging":
        score -= 1.2
        challenges.append(f"{metric.name}: {metric.value}.")

    if rank is not None:
        if view == "long" and rank <= 2:
            score += 0.8
            confirmations.append(f"{currency} ranks #{rank} by pressure across the covered universe.")
        elif view == "short" and rank >= max(len(ranked) - 1, 1):
            score += 0.8
            confirmations.append(f"{currency} ranks #{rank} by pressure, near the bottom of the universe.")
        elif view == "long" and rank >= max(len(ranked) - 1, 1):
            score -= 0.8
            challenges.append(f"{currency} ranks #{rank}, near the weakest currencies.")
        elif view == "short" and rank <= 2:
            score -= 0.8
            challenges.append(f"{currency} ranks #{rank}, near the strongest currencies.")

    confirming_items = [
        item
        for item in row.contributions
        if item.contribution_pct is not None and item.contribution_pct * desired > 0.5
    ]
    challenging_items = [
        item
        for item in row.contributions
        if item.contribution_pct is not None and item.contribution_pct * desired < -0.5
    ]

    if confirming_items:
        score += 0.5
        confirmations.append(f"{len(confirming_items)} pair(s) confirm the {view} {currency} thesis.")
    else:
        score -= 0.5
        challenges.append(f"No pair contribution currently confirms the {view} {currency} thesis.")

    if challenging_items:
        score -= 0.4
        challenges.append(f"{len(challenging_items)} pair(s) challenge the {view} {currency} thesis.")

    best_expressions = [
        _to_expression(item, currency, view)
        for item in sorted(
            confirming_items,
            key=lambda item: abs(item.contribution_pct or 0.0),
            reverse=True,
        )[: params.top_n]
    ]
    stretched_counter = [
        _to_expression(item, currency, view)
        for item in sorted(
            challenging_items,
            key=lambda item: (
                abs(item.z_score or 0.0),
                abs(item.contribution_pct or 0.0),
            ),
            reverse=True,
        )
        if item.z_score is not None and abs(item.z_score) >= 1.0
    ][: params.top_n]

    for scan_row in scanner.rows:
        if scan_row.z_score is None or abs(scan_row.z_score) < 1.5:
            continue
        if currency not in scan_row.pair or any(item.pair == scan_row.pair for item in stretched_counter):
            continue
        monthly = scan_row.monthly_change_pct or 0.0
        base, quote = split_pair(scan_row.pair)
        contribution = monthly if currency == base else -monthly
        if contribution * desired < 0:
            stretched_counter.append(
                FXCurrencyThesisExpression(
                    pair=scan_row.pair,
                    expression=_expression(scan_row.pair, currency, view),
                    rationale=(
                        f"{scan_row.pair} is stretched against the thesis "
                        f"(z-score {scan_row.z_score:+.2f}, signal {scan_row.signal})."
                    ),
                    currency_contribution_pct=contribution,
                    z_score=scan_row.z_score,
                    monthly_change_pct=scan_row.monthly_change_pct,
                )
            )
        if len(stretched_counter) >= params.top_n:
            break

    score = round(score, 2)
    thesis_status = _status(score)
    confidence = _confidence(score, len(confirmations), len(challenges))

    invalidation = [
        f"{currency} pressure score flips against the thesis.",
        f"{currency} falls out of the {'top' if view == 'long' else 'bottom'} half of the currency pressure ranking.",
        "More pair contributions challenge than confirm the thesis.",
    ]

    return FXCurrencyThesisOutput(
        currency=currency,
        view=view,
        as_of_date=pressure.as_of_date,
        thesis_status=thesis_status,
        confidence=confidence,
        confirmation_score=score,
        summary=(
            f"{view.title()} {currency} thesis is {thesis_status} with "
            f"{confidence} confidence (score {score:+.2f})."
        ),
        currency_pressure_score_pct=row.pressure_score_pct,
        currency_rank=rank,
        strongest_currency=pressure.strongest_currency,
        weakest_currency=pressure.weakest_currency,
        confirmations=confirmations,
        challenges=challenges,
        best_expressions=best_expressions,
        stretched_counter_moves=stretched_counter,
        metrics_to_watch=[metric],
        invalidation_signals=invalidation,
    )

