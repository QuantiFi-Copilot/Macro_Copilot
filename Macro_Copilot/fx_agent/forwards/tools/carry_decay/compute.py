from __future__ import annotations

from fx_agent.forwards.tools.carry_decay.schemas import (
    FXCarryDecayInput,
    FXCarryDecayOutput,
    FXCarryDecayTenorRow,
)
from fx_agent.forwards.tools.forward_curve import FXForwardCurveInput, get_fx_forward_curve
from fx_agent.reference.conventions import normalize_pair


def _label_curve(front: float, back: float, best: float, slope: float) -> str:
    if best < 0:
        return "negative carry"
    if front > 0 and back > 0 and abs(slope) < 0.35:
        return "persistent carry"
    if front > 0 and slope > 0.75:
        return "front-loaded carry"
    if front > 0 and slope > 0.35:
        return "carry fading"
    if front < 0 and back > front:
        return "back-end carry improvement"
    return "mixed curve"


def _summary(pair: str, label: str, best_tenor: str | None, best: float | None, slope: float | None) -> str:
    best_text = "n/a" if best is None or best_tenor is None else f"{best_tenor} at {best:.2f}%"
    slope_text = "n/a" if slope is None else f"{slope:.2f}pp front-to-back"
    return f"{pair} carry curve is {label}; best tenor is {best_text}, slope is {slope_text}."


def get_fx_carry_decay(params: FXCarryDecayInput) -> FXCarryDecayOutput:
    pair = normalize_pair(params.pair)
    curve = get_fx_forward_curve(FXForwardCurveInput(pair=pair))

    if not curve.rows:
        return FXCarryDecayOutput(
            pair=pair,
            decay_label="no data",
            rows=[],
            summary=f"No forward curve data available for {pair}.",
            risks=["Missing FX forward curve data."],
        )

    curve_rows = sorted(curve.rows, key=lambda row: row.tenor_days)
    rows = [
        FXCarryDecayTenorRow(
            tenor=row.tenor,
            tenor_days=row.tenor_days,
            carry_annualized_pct=row.carry_annualized_pct,
            carry_bps_spot=row.carry_bps_spot,
            outright_forward=row.outright_forward,
        )
        for row in curve_rows
    ]

    front = rows[0]
    back = rows[-1]
    best_row = max(rows, key=lambda row: row.carry_annualized_pct)
    slope = front.carry_annualized_pct - back.carry_annualized_pct
    label = _label_curve(
        front=front.carry_annualized_pct,
        back=back.carry_annualized_pct,
        best=best_row.carry_annualized_pct,
        slope=slope,
    )

    risks: list[str] = []
    if len(rows) < 3:
        risks.append("Carry decay is based on fewer than three available tenors.")
    if front.carry_annualized_pct > 0 and back.carry_annualized_pct < 0:
        risks.append("Positive front-end carry turns negative further out the curve.")
    if abs(slope) > 1.5:
        risks.append("Carry curve is steep; roll-down assumptions matter.")

    return FXCarryDecayOutput(
        pair=pair,
        spot=curve_rows[0].spot,
        as_of_date=curve_rows[0].forward_date,
        decay_label=label,
        best_tenor=best_row.tenor,
        best_carry_annualized_pct=best_row.carry_annualized_pct,
        front_carry_annualized_pct=front.carry_annualized_pct,
        back_carry_annualized_pct=back.carry_annualized_pct,
        slope_front_to_back_pct=slope,
        curve_span=f"{front.tenor} to {back.tenor}",
        rows=rows,
        summary=_summary(pair, label, best_row.tenor, best_row.carry_annualized_pct, slope),
        risks=risks,
    )

