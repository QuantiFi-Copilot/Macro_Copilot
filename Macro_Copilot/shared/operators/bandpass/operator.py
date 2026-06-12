"""bandpass — Christiano–Fitzgerald band-pass cycle of a Series.

Finance-blind ``single_series_transform`` operator (the hp_filter
sibling).  Extracts the component of the input whose period lies in a
chosen ``[low, high]`` band via the standard Christiano–Fitzgerald
asymmetric full-sample band-pass filter
(``statsmodels.tsa.filters.cf_filter.cffilter`` — the math is
deliberately NOT re-implemented), emitting the BAND-PASS CYCLE as a
Series in the input's units.

DESIGN LOCKS (OPR7, lineage-stamped): the Christiano–Fitzgerald filter
(full-length, no endpoint truncation — the Baxter–King symmetric filter
truncates K observations at each end and is a declared planned
extension, never a silent knob) and ``drift=True`` (removes a
unit-root / linear-drift component — the right default for near-unit-
root series; a no-drift variant is a planned extension).

low/high ARE REQUIRED (no default — the hp_filter ``lamb`` precedent):
the band is frequency-dependent METHODOLOGY (6–32 is the business cycle
for QUARTERLY data; the same cycle is a different band at another
sampling frequency) and a fixed default applied at the wrong frequency
silently extracts a garbage band.  ``params=None`` refuses cleanly
naming the fields.

LOOK-AHEAD DISCLOSURE (P5, load-bearing): the CF filter is ASYMMETRIC
and uses the WHOLE sample — the cycle at every position is fitted
against the entire series (genuine full-sample look-ahead).  Disclosed
here, in the YAML, the card, the registry text, and in lineage
(``filter_scope='full_sample_two_sided'``).  Never feed the output into
point-in-time compositions.

NaN POLICY (two-tier, the hp_filter precedent): contiguous
LEADING/TRAILING NaN — the warmup head every rolling/ewm/lag upstream
legitimately emits — is TOLERATED: the filter runs on the interior
finite block and the edges pass through as NaN (trimming a contiguous
edge leaves all remaining rows genuinely adjacent).  INTERIOR gaps are
REFUSED: the filter is a moving average over ADJACENT rows, so dropping
interior NaNs would collapse gaps and pretend non-adjacent observations
are adjacent — quiet methodology distortion.  The refusal names the
actionable remedy (``align_series`` ffill).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (frequency-band
    extraction); zero finance vocabulary; row adjacency, no calendar
    math.
  - OPR2      : ONE output artifact type — always a ``Series`` (the
    band-pass cycle; never a residual/trend alternate).
  - OPR8      : low/high required (the band has no honest default); the
    refusal names the fields; ``high > low`` enforced in the schema.
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the band, drift, scope and edge
    counts ride in lineage.
  - OPR11     : units passthrough (the filter is linear; a band-pass
    cycle of BPS is BPS).
  - OPR13     : typed ``BandpassError`` refusals: non-Series input;
    missing params (low/high); INTERIOR NaN (gap distortion — edge
    warmup NaN passes through); fewer than ``2 * high`` finite
    observations (the band's longest period is otherwise
    unidentified); a non-finite output.
  - OPR12/14  : config name+version identity checked; pure +
    rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from statsmodels.tsa.filters.cf_filter import cffilter

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.bandpass.schemas import BandpassParams


_OPERATOR_NAME = "bandpass"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# DESIGN-LOCKED filter option (OPR7), recorded in lineage: remove a
# unit-root / linear-drift component (the right default for near-unit-
# root series).  A no-drift variant is a declared planned extension.
_DRIFT = True


class BandpassError(ValueError):
    """Raised by ``bandpass`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def bandpass(
    series: Series,
    *,
    params: Optional[BandpassParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Christiano–Fitzgerald band-pass cycle of ``series``.

    Parameters
    ----------
    series :
        The input ``Series`` — interior-gap-free (contiguous
        leading/trailing warmup NaN is tolerated and passes through;
        interior NaN must be cleaned upstream).
    params :
        ``BandpassParams`` — REQUIRED (the band has no honest default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The band-pass cycle on the input index — contiguous edge NaN
        passes through; the filter covers the interior finite block —
        in the input's units; frequency and missingness policy pass
        through; lineage records the band, drift, the two-sided
        full-sample scope and the edge-NaN counts.

    Raises
    ------
    BandpassError
        Missing params (low/high are required); a non-Series input; an
        INTERIOR NaN; fewer than ``2 * high`` finite observations; or a
        non-finite output.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. The band is methodology — params=None refuses naming the fields
    #    (the hp_filter lamb precedent; OPR8 honest refusal).
    # ------------------------------------------------------------------
    if params is None:
        raise BandpassError(
            "bandpass requires explicit params with low and high (the "
            "period band, in observations) — the band is "
            "frequency-dependent methodology with no honest default "
            "(6-32 is the business cycle for QUARTERLY data; the same "
            "cycle is a different band at another sampling frequency)."
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; two-tier NaN).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise BandpassError(
            "bandpass: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    low = int(params.low)
    high = int(params.high)

    values = series.payload.to_numpy(dtype=float)
    n_obs = int(len(values))
    finite_mask = np.isfinite(values)
    n_finite = int(finite_mask.sum())

    # Two-tier NaN policy: contiguous LEADING/TRAILING NaN tolerated
    # (the warmup head every rolling/ewm/lag upstream emits) — the
    # filter runs on the interior finite block and the edges pass
    # through.  INTERIOR gaps refused: a band-pass MA over non-adjacent
    # rows would silently pretend gaps away.
    if n_finite == 0:
        raise BandpassError(
            "bandpass: the input has no finite observations."
        )
    first = int(np.argmax(finite_mask))
    last = n_obs - int(np.argmax(finite_mask[::-1]))  # exclusive
    core = values[first:last]
    n_interior_nan = int(np.isnan(core).sum())
    if n_interior_nan > 0:
        raise BandpassError(
            f"bandpass: the input has {n_interior_nan} INTERIOR NaN "
            "value(s).  The band-pass filter is a moving average over "
            "ADJACENT rows; dropping interior gaps would silently "
            "pretend non-adjacent observations are adjacent.  Fill the "
            "gaps upstream (align_series ffill) and re-run.  "
            "(Contiguous leading/trailing warmup NaN is tolerated and "
            "passes through.)"
        )

    n_core = int(len(core))
    if n_core < 2 * high:
        raise BandpassError(
            f"bandpass: needs at least 2 * high = {2 * high} finite "
            f"observations to resolve a period-{high} band edge (got "
            f"{n_core}).  Lower high or widen the input lookback."
        )

    # ------------------------------------------------------------------
    # 4. The filter (statsmodels CF; design-locked drift) on the
    #    interior finite block; edge NaN passes through.
    # ------------------------------------------------------------------
    core_payload = series.payload.iloc[first:last]
    cycle_core, _trend_core = cffilter(
        core_payload, low=low, high=high, drift=_DRIFT,
    )
    cycle_core = np.asarray(cycle_core, dtype=float)
    if not np.isfinite(cycle_core).all():
        raise BandpassError(
            "bandpass: the filter produced a non-finite output "
            "(numerical failure)."
        )

    result = pd.Series(np.nan, index=series.payload.index, dtype=float)
    result.iloc[first:last] = cycle_core
    result.name = f"bandpass_cycle__{series.series_key}"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); band + scope disclosed.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "low": low,
        "high": high,
        "drift": _DRIFT,                          # design-locked (OPR7)
        "filter": "christiano_fitzgerald",        # design-locked (OPR7)
        "filter_scope": "full_sample_two_sided",  # LOOK-AHEAD (P5)
        "n_obs": n_obs,
        "n_finite": n_finite,
        "n_leading_nan": first,
        "n_trailing_nan": n_obs - last,
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
        series_key=f"bandpass_cycle__{series.series_key}",
        payload=result,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["bandpass", "BandpassError"]
