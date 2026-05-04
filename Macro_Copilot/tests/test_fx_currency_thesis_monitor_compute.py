from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fx_agent.macro.tools.currency_thesis_monitor import (
    FXCurrencyThesisInput,
    get_fx_currency_thesis_monitor,
)


def _contribution(pair: str, contribution: float, z_score: float = 0.5):
    base, quote = pair[:3], pair[3:]
    return SimpleNamespace(
        pair=pair,
        base_currency=base,
        quote_currency=quote,
        monthly_change_pct=contribution if base == "EUR" else -contribution,
        contribution_pct=contribution,
        z_score=z_score,
        as_of_date="2026-04-30",
    )


def test_currency_thesis_monitor_supports_long_strong_currency():
    pressure = SimpleNamespace(
        as_of_date="2026-04-30",
        strongest_currency="EUR",
        weakest_currency="USD",
        rows=[
            SimpleNamespace(
                currency="EUR",
                pressure_score_pct=1.6,
                signal="currency strength",
                pair_count=2,
                confirming_pairs=["EURUSD", "EURGBP"],
                challenging_pairs=[],
                contributions=[
                    _contribution("EURUSD", 2.0),
                    _contribution("EURGBP", 1.2),
                ],
            ),
            SimpleNamespace(currency="USD", pressure_score_pct=-1.0, contributions=[]),
        ],
    )
    with patch(
        "fx_agent.macro.tools.currency_thesis_monitor.compute.scan_currency_pressure",
        return_value=pressure,
    ), patch(
        "fx_agent.macro.tools.currency_thesis_monitor.compute.run_fx_scanner",
        return_value=SimpleNamespace(rows=[]),
    ):
        out = get_fx_currency_thesis_monitor(
            FXCurrencyThesisInput(currency="EUR", view="long")
        )

    assert out.thesis_status == "supportive"
    assert out.best_expressions[0].expression == "long EURUSD"
    assert out.currency_rank == 1


def test_currency_thesis_monitor_flags_hostile_long_weak_currency():
    pressure = SimpleNamespace(
        as_of_date="2026-04-30",
        strongest_currency="AUD",
        weakest_currency="USD",
        rows=[
            SimpleNamespace(currency="AUD", pressure_score_pct=2.0, contributions=[]),
            SimpleNamespace(
                currency="USD",
                pressure_score_pct=-2.1,
                signal="currency weakness",
                pair_count=2,
                confirming_pairs=[],
                challenging_pairs=["EURUSD", "AUDUSD"],
                contributions=[
                    _contribution("EURUSD", -1.2, 0.6),
                    _contribution("AUDUSD", -3.9, 2.1),
                ],
            ),
        ],
    )
    scanner = SimpleNamespace(
        rows=[
            SimpleNamespace(
                pair="AUDUSD",
                z_score=2.1,
                monthly_change_pct=3.9,
                signal="Bullish breakout",
            )
        ]
    )
    with patch(
        "fx_agent.macro.tools.currency_thesis_monitor.compute.scan_currency_pressure",
        return_value=pressure,
    ), patch(
        "fx_agent.macro.tools.currency_thesis_monitor.compute.run_fx_scanner",
        return_value=scanner,
    ):
        out = get_fx_currency_thesis_monitor(
            FXCurrencyThesisInput(currency="USD", view="long")
        )

    assert out.thesis_status == "hostile"
    assert out.challenges
    assert out.stretched_counter_moves[0].pair == "AUDUSD"

