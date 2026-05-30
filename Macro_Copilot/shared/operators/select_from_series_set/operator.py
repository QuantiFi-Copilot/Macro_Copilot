"""select_from_series_set — finance-blind Series extraction from a SeriesSet.

Closes the SeriesSet → Series gap surfaced by Codex on PR #84:
``align_series`` emits a SeriesSet, but downstream Series-consuming
operators (``threshold_events``, ``event_windows``, ``apply_mask``,
``series_arithmetic``, ...) need an individually-named Series back
out.  ``SeriesSet.get_series(key)`` is the in-memory accessor; this
module is its workflow-graph surface — registered in the operator
registry so templates can reference it.

Lineage contract
----------------
The returned Series's lineage chain is::

    [..., upstream_step_n_for_key, align_step, select_step]

where:
  - ``upstream_step_*`` are the steps that produced the original
    Series before alignment (e.g. the primitive ``PrimitiveStep``,
    any preceding Series transforms);
  - ``align_step`` is the SeriesSet-producing step (typically
    ``align_series``, but could in principle be any operator that
    emits a SeriesSet);
  - ``select_step`` is this operator's own ``OperatorStep``,
    parameterised by ``series_key``.

This way, a downstream operator's runtime hash includes the full
provenance of the selected Series, not just the alignment step.

Errors
------
Raises ``SelectFromSeriesSetError`` (a ``ValueError`` subclass) when
``series_key`` is not present in the input set.  Mirrors the
operator-family error pattern from ``align_series``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from shared.artifacts.lineage import OperatorStep
from shared.artifacts.types import Series, SeriesSet
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.select_from_series_set.schemas import (
    SelectFromSeriesSetParams,
)


_OPERATOR_NAME = "select_from_series_set"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class SelectFromSeriesSetError(ValueError):
    """Raised by ``select_from_series_set`` on a recoverable
    user-facing failure (e.g. unknown series_key).  Subclass of
    ``ValueError`` so existing ``except ValueError`` blocks in
    shared/ continue to work."""


def select_from_series_set(
    series_set: SeriesSet,
    params: Optional[SelectFromSeriesSetParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Extract one named ``Series`` from a ``SeriesSet`` by key.

    Parameters
    ----------
    series_set :
        The input ``SeriesSet`` to extract from.
    params :
        Required ``SelectFromSeriesSetParams`` with the target
        ``series_key``.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (and process-cached).  The
        select operator has no consequential conventions today,
        but the config is still loaded + name-checked for
        substrate-discipline parity with the rest of the operator
        family.

    Returns
    -------
    Series
        The selected member as a typed ``Series`` artifact.  Lineage
        chain is the upstream chain for THAT key (recovered via
        ``SeriesSet.get_series``) with this select step appended.

    Raises
    ------
    SelectFromSeriesSetError
        When ``series_key`` is not present in the input set.  The
        error message lists the available keys for diagnostic
        clarity.
    """
    if config is None:
        config = load_operator_config(_CONFIG_PATH)

    # Config identity (OPR12) — name AND version.
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    if params is None:
        # select_from_series_set has a required per-call field
        # (``series_key``) with no default — refuse cleanly (OPR8/OPR13).
        raise SelectFromSeriesSetError(
            "select_from_series_set requires explicit params "
            "(``series_key``); none were supplied."
        )

    if params.series_key not in series_set.series_by_key:
        available = sorted(series_set.series_by_key.keys())
        raise SelectFromSeriesSetError(
            f"select_from_series_set: series_key="
            f"{params.series_key!r} is not in the input SeriesSet.  "
            f"Available keys: {available}."
        )

    # SeriesSet.get_series propagates the per-key upstream lineage +
    # the SeriesSet-producing step as a properly chained Lineage.
    extracted = series_set.get_series(params.series_key)

    # Append this select step so the workflow graph surface records
    # that an explicit selection happened (and the runtime hash
    # captures it).
    select_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params={"series_key": params.series_key},
        input_hashes=(extracted.lineage.head_hash,),
    )

    return Series(
        series_key=extracted.series_key,
        payload=extracted.payload,
        units=extracted.units,
        frequency=extracted.frequency,
        missingness_policy=extracted.missingness_policy,
        lineage=extracted.lineage.append(select_step),
    )


__all__ = [
    "select_from_series_set",
    "SelectFromSeriesSetError",
]
