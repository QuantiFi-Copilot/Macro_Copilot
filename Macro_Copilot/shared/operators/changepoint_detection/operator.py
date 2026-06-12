"""changepoint_detection — structural mean-shift breaks of a Series.

Finance-blind ``statistical_relationship`` operator and the SECOND A4
model-fit engine (after fit_ou).  Detects where the LEVEL of a Series
structurally shifts via binary segmentation with an L2 mean-shift cost
(the math lives in ``shared/quant/changepoint.py``, the fourth module
there — no library) and emits the break dates as an ``EventSet``.

This answers "WHEN did the series break / regime-shift" for ANY series
(a spread, a residual, a ratio) — finance-blind: the code would not
change for FX or equity inputs.

EMPTY IS HONEST (unlike fit_ou): an ``EventSet`` has an empty channel,
so a series with FEWER real breaks than requested emits FEWER events
(down to an empty EventSet) — never a refusal or a garbage break.  Each
event's gain (the SSE reduction) rides in metadata so a weak break is
auditable.

DESIGN LOCKS (OPR7, lineage-stamped): the L2 mean-shift cost
(``cost_model='l2_mean_shift'``) and the greedy binary-segmentation
algorithm (``algorithm='binary_segmentation'``).  Variance-shift /
kernel costs, a penalty/BIC-driven count, and exact PELT /
dynamic-programming are declared planned extensions, never silent
knobs.

n_changepoints IS REQUIRED (no default — the hp_filter ``lamb``
precedent): the number of breaks is the central methodology choice and
has no honest default.  ``params=None`` refuses naming the field.

LOOK-AHEAD DISCLOSURE (P5, load-bearing): binary segmentation places
EVERY break using the WHOLE sample (full-sample detection).  Disclosed
here, in the YAML, the card, the registry text, and in lineage
(``detection_scope='full_sample'``).  Never feed the output into
point-in-time compositions.

NaN POLICY (two-tier, the hp_filter precedent): contiguous
LEADING/TRAILING NaN is TOLERATED (segment the interior finite block;
the edges are never eligible breaks).  INTERIOR gaps are REFUSED: the
SSE cost is over CONTIGUOUS segments, so dropping interior NaNs would
splice non-adjacent observations into one segment and corrupt its
mean.  The refusal names the actionable remedy (``align_series`` ffill).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (segmentation);
    zero finance vocabulary.
  - OPR2      : ONE output artifact type — always an ``EventSet``.
  - OPR8      : n_changepoints required (no honest default); min_size
    config-resolved; the cost/algorithm locks documented + stamped.
  - OPR9      : one ``Series`` in, one ``EventSet`` out.
  - OPR10     : one ``OperatorStep``; the requested/found counts, the
    locks, the scope and the edge counts ride in lineage.
  - OPR11     : an EventSet has no value units; input units recorded
    in lineage; per-event segment means ride in the input's units.
  - OPR13     : typed ``ChangepointDetectionError`` refusals:
    non-Series input; missing params (n_changepoints); fewer than
    max(12, (n_changepoints+1)*min_size) finite interior observations;
    zero variance; INTERIOR NaN.
  - OPR14     : pure + rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import EventSet, Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.changepoint_detection.schemas import (
    ChangepointDetectionParams,
)
from shared.quant.changepoint import binary_segmentation


_OPERATOR_NAME = "changepoint_detection"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# A4 family floor (mirrors fit_ou / variance_ratio); the feasibility
# floor (n_changepoints+1)*min_size is OR'd on top.
_FAMILY_FLOOR = 12


class ChangepointDetectionError(ValueError):
    """Raised by ``changepoint_detection`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def changepoint_detection(
    series: Series,
    *,
    params: Optional[ChangepointDetectionParams] = None,
    config: Optional[OperatorConfig] = None,
) -> EventSet:
    """Detect mean-shift breaks in ``series`` and emit them as events.

    Parameters
    ----------
    series :
        The input ``Series`` — interior-gap-free (contiguous
        leading/trailing warmup NaN is tolerated; interior NaN must be
        cleaned upstream).
    params :
        ``ChangepointDetectionParams`` — REQUIRED (n_changepoints has
        no honest default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    EventSet
        The break dates (the mask is True at each break) on the input
        index; per-event metadata carries the gain and the segment
        means before/after; lineage records the requested/found counts,
        the locked cost/algorithm, the full-sample scope and the
        edge-NaN counts.  FEWER events than requested (down to empty)
        is the honest answer when breaks are exhausted.

    Raises
    ------
    ChangepointDetectionError
        Missing params (n_changepoints required); a non-Series input;
        fewer than max(12, (n_changepoints+1)*min_size) finite interior
        observations; zero variance; or an INTERIOR NaN.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. n_changepoints is methodology — params=None refuses naming it
    #    (the hp_filter lamb precedent; OPR8 honest refusal).
    # ------------------------------------------------------------------
    if params is None:
        raise ChangepointDetectionError(
            "changepoint_detection requires explicit params with "
            "n_changepoints (the number of mean-shift breaks to find) "
            "— it is the central methodology choice with no honest "
            "default."
        )
    n_changepoints = int(params.n_changepoints)
    if params.min_size is not None:
        min_size = int(params.min_size)
    else:
        min_size = int(config.default_value("min_size"))

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; two-tier NaN).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise ChangepointDetectionError(
            "changepoint_detection: input must be a Series artifact; "
            f"got {type(series).__name__}."
        )

    payload = series.payload
    values_all = payload.to_numpy(dtype=float)
    n_total = int(len(values_all))
    finite_mask = np.isfinite(values_all)
    n_finite = int(finite_mask.sum())
    if n_finite == 0:
        raise ChangepointDetectionError(
            "changepoint_detection: the input has no finite "
            "observations."
        )

    # Two-tier NaN: contiguous leading/trailing tolerated; the filter
    # runs on the interior finite block.  INTERIOR gaps refused.
    first = int(np.argmax(finite_mask))
    last = n_total - int(np.argmax(finite_mask[::-1]))  # exclusive
    core = values_all[first:last]
    n_interior_nan = int(np.isnan(core).sum())
    if n_interior_nan > 0:
        raise ChangepointDetectionError(
            f"changepoint_detection: the input has {n_interior_nan} "
            "INTERIOR NaN value(s).  The L2 segmentation cost runs over "
            "CONTIGUOUS rows; dropping interior gaps would silently "
            "splice non-adjacent observations into one segment.  Fill "
            "the gaps upstream (align_series ffill) and re-run.  "
            "(Contiguous leading/trailing warmup NaN is tolerated.)"
        )

    n_core = int(len(core))
    floor = max(_FAMILY_FLOOR, (n_changepoints + 1) * min_size)
    if n_core < floor:
        raise ChangepointDetectionError(
            f"changepoint_detection: needs at least max(12, "
            f"(n_changepoints+1)*min_size) = {floor} finite "
            f"observations for {n_changepoints} break(s) of min_size "
            f"{min_size} (got {n_core}).  Lower n_changepoints or "
            "widen the input lookback."
        )
    if float(np.var(core)) == 0.0:
        raise ChangepointDetectionError(
            "changepoint_detection: the input has zero variance — a "
            "constant series has no definable break."
        )

    # ------------------------------------------------------------------
    # 4. The detection (shared/quant; design-locked L2 binary seg).
    # ------------------------------------------------------------------
    result = binary_segmentation(
        core, n_changepoints=n_changepoints, min_size=min_size,
    )

    # ------------------------------------------------------------------
    # 5. Build the EventSet — mask True at each break date (mapped from
    #    the core back to the full index by the leading offset).
    # ------------------------------------------------------------------
    global_bps = [first + bp for bp in result.breakpoints]
    mask = pd.Series(False, index=payload.index)
    if global_bps:
        mask.iloc[global_bps] = True
    event_dates: List[pd.Timestamp] = list(payload.index[mask.to_numpy()])

    per_event_metadata: List[Dict[str, Any]] = []
    for k, gbp in enumerate(global_bps):
        per_event_metadata.append({
            "gain": float(result.gains[k]),
            "segment_mean_before": float(result.segment_means[k]),
            "segment_mean_after": float(result.segment_means[k + 1]),
            "break_index": int(gbp),
        })

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep (OPR10); locks + scope disclosed.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "n_changepoints_requested": n_changepoints,
        "n_changepoints_found": int(result.n_changepoints),
        "min_size": min_size,
        "cost_model": "l2_mean_shift",         # design-locked (OPR7)
        "algorithm": "binary_segmentation",    # design-locked (OPR7)
        "detection_scope": "full_sample",      # LOOK-AHEAD (P5)
        "n_events": len(event_dates),
        "n_obs": n_total,
        "n_dropped": n_total - n_finite,
        "n_leading_nan": first,
        "n_trailing_nan": n_total - last,
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )

    return EventSet(
        mask=mask,
        event_dates=event_dates,
        per_event_metadata=per_event_metadata,
        source_series_key=series.series_key,
        frequency=series.frequency,
        lineage=series.lineage.append(step),
    )


__all__ = ["changepoint_detection", "ChangepointDetectionError"]
