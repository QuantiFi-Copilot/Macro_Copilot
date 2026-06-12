"""resample — downsample a Series to a lower frequency.

Finance-blind ``single_series_transform`` operator and THE FREQUENCY-
TRANSITION SITE of the toolbox: the only operator licensed to change a
Series' ``frequency`` tag (the ``convert_units`` analogue for the time
axis).  Buckets the input into weekly / monthly / quarterly / yearly
periods and reduces each bucket with the chosen method, emitting a
Series whose index is the period-end dates and whose ``frequency`` is
stamped to the target.

DESIGN LOCKS (OPR7, recorded in lineage): weekly buckets anchor to
Friday (``W-FRI``); monthly/quarterly/yearly anchor to period END
(``ME``/``QE``/``YE``); ``label='right'`` and ``closed='right'`` (each
bucket is labelled by its period-end date).  Variants would silently
re-date observations — a P5 hazard with no analytical gain.

UPSAMPLING IS REFUSED (fabrication): the target set is W/M/Q/Y only,
a known input frequency must be strictly higher than the target, and a
row-count guard (more output rows than input rows) refuses any
residual fabrication case when the input's frequency tag is unknown.

PARTIAL-PERIOD HONESTY: the final bucket may be incomplete (a series
ending mid-week still emits that week's bucket, reduced over the rows
that exist).  It is INCLUDED — dropping it would silently discard the
freshest data — and disclosed via ``final_period_complete`` in lineage,
computed FREQUENCY-AWARE where possible (B inputs: against the last
expected business day on or before the calendar label, so a complete
month ending on a weekend calendar-end still flags True; D inputs: the
label itself; unknown frequencies: one-sided — True proves complete,
False does not imply missing; the basis rides in lineage).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (time-bucketing);
    period-end anchoring is calendar STRUCTURE, not finance math.
  - OPR2      : ONE output artifact type — always a ``Series``; units
    passthrough for every method.
  - OPR8      : ``params: Optional[ResampleParams] = None``; defaults
    resolve from ``config.yaml``.
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the locks, the resolved pandas
    rule and the partial-period flag are recorded.
  - OPR11     : units passthrough; ``frequency`` INTENTIONALLY changes
    to the target — the licensed exception to metadata passthrough.
  - OPR13     : typed ``ResampleError`` refusals: non-Series input;
    a known input frequency not strictly higher than the target
    (upsampling / identity); an output with more rows than the input
    (fabrication guard for unknown input frequencies); an all-NaN
    output.  Empty interior buckets emit NaN — legitimate missingness.
  - OPR14     : pure + rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.resample.schemas import ResampleParams


_OPERATOR_NAME = "resample"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# DESIGN-LOCKED pandas rules + labelling (OPR7): period-end anchors,
# right label, right closed.  Recorded in lineage params.
_RULES: Dict[str, str] = {"W": "W-FRI", "M": "ME", "Q": "QE", "Y": "YE"}
_LABEL = "right"
_CLOSED = "right"

# Frequency ordering for the downsample-only check (higher value =
# higher frequency).  ``irregular``/None cannot be ordered — the
# row-count fabrication guard covers those inputs instead.
_FREQ_RANK: Dict[str, int] = {"B": 6, "D": 5, "W": 4, "M": 3, "Q": 2, "Y": 1}


class ResampleError(ValueError):
    """Raised by ``resample`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def resample(
    series: Series,
    *,
    params: Optional[ResampleParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Downsample ``series`` to ``params.target_frequency``.

    Parameters
    ----------
    series :
        The input ``Series`` (canonically B/D data).
    params :
        Optional ``ResampleParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        One value per target period, indexed by the period-end dates
        (Friday / month-end / quarter-end / year-end), in the input's
        units; the ``frequency`` tag is STAMPED to the target (the
        licensed frequency transition); missingness policy passes
        through; lineage records the locks and the partial-period
        disclosure.

    Raises
    ------
    ResampleError
        The input is not a ``Series``; its known frequency is not
        strictly higher than the target; the output would have more
        rows than the input (fabrication guard); or the output is
        all-NaN.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params from config when omitted — OPR8.
    # ------------------------------------------------------------------
    if params is None:
        params = ResampleParams(
            target_frequency=config.default_value("target_frequency"),
            method=config.default_value("method"),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; downsample-only).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise ResampleError(
            "resample: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    target = params.target_frequency
    in_freq = series.frequency
    if in_freq in _FREQ_RANK:
        if _FREQ_RANK[in_freq] <= _FREQ_RANK[target]:
            raise ResampleError(
                f"resample: input frequency {in_freq!r} is not "
                f"strictly higher than the target {target!r} — "
                "resample only DOWNSAMPLES (upsampling fabricates "
                "rows; an identity resample re-dates observations)."
            )

    # ------------------------------------------------------------------
    # 4. The bucketing (design-locked rule/label/closed).
    # ------------------------------------------------------------------
    rule = _RULES[target]
    roller = series.payload.resample(rule, label=_LABEL, closed=_CLOSED)
    if params.method == "last":
        result = roller.last()
    elif params.method == "mean":
        result = roller.mean()
    elif params.method == "first":
        result = roller.first()
    elif params.method == "max":
        result = roller.max()
    elif params.method == "min":
        result = roller.min()
    else:
        # Defensive (the schema's Literal closes the set) — OPR8.
        raise NotImplementedError(
            f"resample: method={params.method!r} is declared but not "
            "implemented; see config.yaml methodology.planned_extensions."
        )

    result = result.astype(float)

    # Fabrication guard: with an unknown input frequency the ordering
    # check above cannot run; a denser output than input means buckets
    # were CREATED, not reduced — upsampling by the back door.
    if len(result) > len(series.payload):
        raise ResampleError(
            f"resample: the output has more rows ({len(result)}) than "
            f"the input ({len(series.payload)}) — the input is coarser "
            f"than the {target!r} target (upsampling fabricates rows)."
        )

    n_finite = int(np.isfinite(result.to_numpy(dtype=float)).sum())
    if n_finite == 0:
        raise ResampleError(
            f"resample: produced an all-NaN output ({len(result)} "
            f"{target!r} buckets from {len(series.payload)} input "
            "rows)."
        )

    # Partial-period disclosure (frequency-aware where possible): with
    # closed/label 'right' the bucket label is the CALENDAR period-end,
    # which for B-frequency inputs may fall on a weekend — the honest
    # completeness reference is the last EXPECTED observation <= label,
    # not the label itself (critic finding: the naive comparison flags
    # complete months False whenever the calendar month-end is a
    # weekend).
    last_label = result.index[-1]
    last_input = series.payload.index[-1]
    if in_freq == "B":
        # Last business day on or before the calendar label.
        expected_last = pd.bdate_range(end=last_label, periods=1)[0]
        completeness_basis = "expected_b_observation"
    elif in_freq == "D":
        expected_last = last_label
        completeness_basis = "calendar_label"
    else:
        # Unknown/irregular/coarser input frequency: only the
        # one-sided calendar-label comparison is available.  True
        # PROVES completeness; False does NOT imply data is missing
        # (disclosed in YAML/card/registry).
        expected_last = last_label
        completeness_basis = "calendar_label_one_sided"
    final_period_complete = bool(last_input >= expected_last)

    result.name = f"resampled_{target}__{series.series_key}"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); locks + disclosure ride in
    #    params.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "target_frequency": target,
        "method": params.method,
        "pandas_rule": rule,        # design-locked (OPR7)
        "label": _LABEL,            # design-locked (OPR7)
        "closed": _CLOSED,          # design-locked (OPR7)
        "input_frequency": in_freq,
        "n_input_rows": int(len(series.payload)),
        "n_output_periods": int(len(result)),
        "final_period_complete": final_period_complete,
        "completeness_basis": completeness_basis,
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )
    out_lineage = series.lineage.append(step)

    return Series(
        series_key=f"resampled_{target}__{series.series_key}",
        payload=result,
        units=series.units,
        frequency=target,  # the licensed frequency transition (OPR11)
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["resample", "ResampleError"]
