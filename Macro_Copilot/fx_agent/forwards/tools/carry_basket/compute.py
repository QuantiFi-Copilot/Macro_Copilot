from __future__ import annotations

from fx_agent.forwards.tools.carry_basket.schemas import (
    FXCarryBasketInput,
    FXCarryBasketLeg,
    FXCarryBasketOutput,
)
from fx_agent.forwards.tools.fx_carry import FXCarryInput, get_fx_carry
from fx_agent.reference.conventions import normalize_tenor
from fx_agent.spot.tools.spot_levels import FXSpotLevelInput, get_fx_spot_level
from fx_agent.vol.tools.realized_vol import FXRealizedVolInput, get_fx_realized_vol


def _pair_risk(pair: str, lookback_days: int) -> tuple[float | None, float | None]:
    spot_z = None
    realized = None
    try:
        spot_z = get_fx_spot_level(
            FXSpotLevelInput(pair=pair, lookback_days=lookback_days)
        ).current_metrics.z_score
    except Exception:
        pass
    try:
        realized = get_fx_realized_vol(
            FXRealizedVolInput(pair=pair, lookback_days=lookback_days)
        ).current_metrics.realized_vol_annualized_pct
    except Exception:
        pass
    return spot_z, realized


def build_fx_carry_basket(params: FXCarryBasketInput) -> FXCarryBasketOutput:
    tenor = normalize_tenor(params.tenor)
    carry = get_fx_carry(FXCarryInput(tenor=tenor))

    eligible: list[FXCarryBasketLeg] = []
    excluded: list[str] = []

    for row in carry.rows:
        spot_z, realized = _pair_risk(row.pair, params.lookback_days)
        if realized is not None and realized > params.max_realized_vol_pct:
            excluded.append(f"{row.pair}: realized vol {realized:.2f}%")
            continue
        if spot_z is not None and abs(spot_z) > params.max_abs_spot_z_score:
            excluded.append(f"{row.pair}: spot z-score {spot_z:.2f}")
            continue

        side = "long carry" if row.carry_annualized_pct >= 0 else "short low-carry"
        eligible.append(
            FXCarryBasketLeg(
                pair=row.pair,
                side=side,
                carry_annualized_pct=row.carry_annualized_pct,
                realized_vol_annualized_pct=realized,
                spot_z_score=spot_z,
                rationale=(
                    f"{row.pair} {tenor} carry {row.carry_annualized_pct:.2f}% "
                    f"with realized vol "
                    f"{'n/a' if realized is None else f'{realized:.2f}%'}."
                ),
            )
        )

    longs = sorted(eligible, key=lambda leg: leg.carry_annualized_pct, reverse=True)[
        : params.basket_size
    ]
    shorts = sorted(eligible, key=lambda leg: leg.carry_annualized_pct)[
        : params.basket_size
    ]

    return FXCarryBasketOutput(
        tenor=tenor,
        basket_label=f"G10 FX carry basket · {tenor}",
        long_legs=longs,
        short_legs=shorts,
        excluded_pairs=excluded,
    )
