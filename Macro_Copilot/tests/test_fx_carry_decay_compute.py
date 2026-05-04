from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fx_agent.forwards.tools.carry_decay import FXCarryDecayInput, get_fx_carry_decay


def _row(tenor: str, days: int, carry: float) -> SimpleNamespace:
    return SimpleNamespace(
        pair="EURUSD",
        spot_date="2026-04-30",
        forward_date="2026-04-30",
        spot=1.17,
        tenor=tenor,
        tenor_days=days,
        forward_points=10.0,
        forward_points_spot_units=0.001,
        outright_forward=1.171,
        carry_bps_spot=8.5,
        carry_annualized_pct=carry,
    )


def test_fx_carry_decay_labels_front_loaded_curve():
    curve = SimpleNamespace(
        rows=[
            _row("1W", 5, 2.4),
            _row("1M", 21, 1.7),
            _row("3M", 63, 1.1),
            _row("6M", 126, 0.8),
        ]
    )
    with patch(
        "fx_agent.forwards.tools.carry_decay.compute.get_fx_forward_curve",
        return_value=curve,
    ):
        out = get_fx_carry_decay(FXCarryDecayInput(pair="eur/usd"))

    assert out.pair == "EURUSD"
    assert out.decay_label == "front-loaded carry"
    assert out.best_tenor == "1W"
    assert out.slope_front_to_back_pct == 1.5999999999999999
    assert out.curve_span == "1W to 6M"
    assert len(out.rows) == 4


def test_fx_carry_decay_labels_persistent_curve():
    curve = SimpleNamespace(
        rows=[
            _row("1W", 5, 1.3),
            _row("1M", 21, 1.25),
            _row("3M", 63, 1.15),
            _row("6M", 126, 1.05),
        ]
    )
    with patch(
        "fx_agent.forwards.tools.carry_decay.compute.get_fx_forward_curve",
        return_value=curve,
    ):
        out = get_fx_carry_decay(FXCarryDecayInput(pair="EURUSD"))

    assert out.decay_label == "persistent carry"
    assert out.best_tenor == "1W"


def test_fx_carry_decay_handles_no_curve_data():
    with patch(
        "fx_agent.forwards.tools.carry_decay.compute.get_fx_forward_curve",
        return_value=SimpleNamespace(rows=[]),
    ):
        out = get_fx_carry_decay(FXCarryDecayInput(pair="EURUSD"))

    assert out.decay_label == "no data"
    assert out.rows == []
    assert "Missing FX forward curve data." in out.risks

