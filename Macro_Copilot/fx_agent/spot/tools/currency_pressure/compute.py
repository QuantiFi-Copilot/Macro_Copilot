from __future__ import annotations

from collections import defaultdict

from fx_agent.reference.conventions import (
    G10_ALL_PAIRS,
    G10_CURRENCIES,
    normalize_pair,
    split_pair,
)
from fx_agent.spot.tools.currency_pressure.schemas import (
    FXCurrencyPairContribution,
    FXCurrencyPressureInput,
    FXCurrencyPressureOutput,
    FXCurrencyPressureRow,
)
from fx_agent.spot.tools.spot_levels import FXSpotLevelInput, get_fx_spot_level


def _signal(score: float) -> str:
    if score > 0.5:
        return "currency strength"
    if score < -0.5:
        return "currency weakness"
    return "mixed"


def _target_currencies(params: FXCurrencyPressureInput) -> list[str]:
    if not params.currencies:
        return list(G10_CURRENCIES)
    return [currency.upper().strip() for currency in params.currencies if currency.strip()]


def scan_currency_pressure(
    params: FXCurrencyPressureInput,
) -> FXCurrencyPressureOutput:
    currencies = _target_currencies(params)
    field_name = params.field_name.upper().strip()
    contributions_by_currency: dict[str, list[FXCurrencyPairContribution]] = defaultdict(list)
    as_of_dates: list[str] = []

    for pair in G10_ALL_PAIRS:
        try:
            normalized = normalize_pair(pair)
            base, quote = split_pair(normalized)
            metrics = get_fx_spot_level(
                FXSpotLevelInput(
                    pair=normalized,
                    lookback_days=params.lookback_days,
                    field_name=field_name,
                )
            ).current_metrics
        except Exception:
            continue

        monthly = metrics.monthly_change_pct
        as_of_dates.append(metrics.as_of_date)
        for currency, sign in ((base, 1.0), (quote, -1.0)):
            if currency not in currencies:
                continue
            contribution = None if monthly is None else monthly * sign
            contributions_by_currency[currency].append(
                FXCurrencyPairContribution(
                    pair=normalized,
                    base_currency=base,
                    quote_currency=quote,
                    monthly_change_pct=monthly,
                    contribution_pct=contribution,
                    z_score=metrics.z_score,
                    as_of_date=metrics.as_of_date,
                )
            )

    rows: list[FXCurrencyPressureRow] = []
    for currency in currencies:
        contributions = contributions_by_currency.get(currency, [])
        values = [
            item.contribution_pct
            for item in contributions
            if item.contribution_pct is not None
        ]
        score = float(sum(values) / len(values)) if values else 0.0
        confirming = [
            item.pair
            for item in contributions
            if item.contribution_pct is not None and item.contribution_pct > 0.5
        ]
        challenging = [
            item.pair
            for item in contributions
            if item.contribution_pct is not None and item.contribution_pct < -0.5
        ]
        rows.append(
            FXCurrencyPressureRow(
                currency=currency,
                pressure_score_pct=round(score, 2),
                signal=_signal(score),
                pair_count=len(values),
                confirming_pairs=confirming,
                challenging_pairs=challenging,
                contributions=sorted(
                    contributions,
                    key=lambda item: abs(item.contribution_pct or 0.0),
                    reverse=True,
                ),
            )
        )

    rows = sorted(rows, key=lambda row: row.pressure_score_pct, reverse=True)
    strongest = rows[0].currency if rows else None
    weakest = rows[-1].currency if rows else None

    return FXCurrencyPressureOutput(
        as_of_date=max(as_of_dates) if as_of_dates else None,
        strongest_currency=strongest,
        weakest_currency=weakest,
        rows=rows,
    )

