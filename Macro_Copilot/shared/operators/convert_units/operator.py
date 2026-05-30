"""convert_units — dimensional unit conversion (the sole conversion site).

ADR 0016 Decision 4: operators never silently convert units; cross-unit
operations refuse, and this single operator is the only place a unit
transition happens.  Finance-blind — a dimensional conversion (1 percent
= 100 basis points), not finance modelling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

from shared.artifacts.lineage import OperatorStep
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.convert_units.schemas import ConvertUnitsParams


_OPERATOR_NAME = "convert_units"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class ConvertUnitsError(ValueError):
    """Raised by ``convert_units`` on a recoverable user-facing failure
    (e.g. an overflow to +/-Inf).  Subclass of ``ValueError`` so the one
    error family holds (OPR13).  An UNSUPPORTED unit pair raises
    ``NotImplementedError`` instead — the sanctioned signal for a
    declared-but-unbuilt conversion (OPR8 honest refusal)."""


# Exact dimensional factors to MULTIPLY the payload by when converting
# (source -> target).  1 percent = 100 basis points.  Identity pairs are
# handled separately (factor 1.0).  This matrix is the closed, finance-
# blind set of conversions v1 supports.
_CONVERSION_FACTORS: Dict[Tuple[TimeSeriesUnits, TimeSeriesUnits], float] = {
    (TimeSeriesUnits.PERCENT, TimeSeriesUnits.BPS): 100.0,
    (TimeSeriesUnits.BPS, TimeSeriesUnits.PERCENT): 0.01,
}


def convert_units(
    series: Series,
    params: Optional[ConvertUnitsParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Convert ``series`` to ``params.target_units``.

    Multiplies the payload by the exact dimensional factor for the
    (source, target) unit pair and re-tags the output with the target
    units.  Identity (source == target) is a no-op re-tag.  An
    unsupported pair raises ``NotImplementedError``; an overflow to
    +/-Inf raises ``ConvertUnitsError`` (OPR14b).  Lineage, frequency,
    and missingness propagate unchanged (a conversion does not touch the
    index or the cleaning regime).
    """
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    # Config identity (OPR12) — name AND version.
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    if not isinstance(series, Series):
        raise ConvertUnitsError(
            f"convert_units: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )
    if params is None:
        raise ConvertUnitsError(
            "convert_units requires explicit params (``target_units``); "
            "none were supplied."
        )

    source = series.units
    target = params.target_units

    if source == target:
        factor = 1.0  # identity conversion — a no-op re-tag.
    else:
        key = (source, target)
        if key not in _CONVERSION_FACTORS:
            raise NotImplementedError(
                f"convert_units: no conversion is defined from "
                f"{source.value!r} to {target.value!r}.  Supported in v1: "
                "PERCENT<->BPS (and identity)."
            )
        factor = _CONVERSION_FACTORS[key]

    new_payload = (series.payload * factor).astype(float)
    # OPR14(b): a conversion that overflows to +/-Inf is a typed refusal,
    # not an inf payload.
    if bool(np.isinf(new_payload.to_numpy()).any()):
        raise ConvertUnitsError(
            f"convert_units: {source.value} -> {target.value} (×{factor}) "
            "overflowed to +/-inf for at least one value."
        )

    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params={
            "source_units": source.value,
            "target_units": target.value,
            "factor": factor,
        },
        input_hashes=(series.lineage.head_hash,),
    )
    return Series(
        series_key=series.series_key,
        payload=new_payload,
        units=target,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=series.lineage.append(step),
    )


__all__ = ["convert_units", "ConvertUnitsError"]
