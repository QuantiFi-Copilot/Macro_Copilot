"""align_series — finance-blind index alignment of N typed Series.

Per the operator architecture doc, this module implements the
structural-method-family-of-one operator that combines N ``Series``
artifacts onto a common ``DatetimeIndex`` and emits a keyed
``SeriesSet``.

Errors
------
Operators in ``shared.operators.*`` follow the ``shared.analytics.*``
pattern: they **raise** typed exceptions on user-facing failures.  The
orchestration / template / public-tool layer is responsible for
converting those exceptions into ``{"error": "..."}`` envelopes for
end users.  See build plan v5 / fix #3.

Per build plan v5 / R2: ``SeriesSet.get_series(key)`` returns a
``Series`` whose lineage carries forward the original upstream lineage
of THAT input series with the alignment step appended.  Implementing
that contract requires storing per-key upstream lineage on the
``SeriesSet`` here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Counter, List, Optional

import pandas as pd

from shared.artifacts.lineage import Lineage, OperatorStep
from shared.artifacts.missingness import AlignSeriesFFillV1, MissingnessPolicy
from shared.artifacts.types import Series, SeriesSet
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.align_series.schemas import AlignSeriesParams


_OPERATOR_NAME = "align_series"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class AlignSeriesError(ValueError):
    """Raised by ``align_series`` on a recoverable user-facing failure.

    Subclass of ``ValueError`` so existing ``except ValueError`` blocks
    in the rest of ``shared/`` continue to work.  Templates and public
    wrappers convert this into the controlled error envelope at the
    user-facing boundary.
    """


def align_series(
    series_list: List[Series],
    params: Optional[AlignSeriesParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Align N typed Series onto a common DatetimeIndex.

    Parameters
    ----------
    series_list :
        List of input ``Series`` artifacts.  Must contain ≥1 series.
        Series ``series_key`` values must be unique across the list.
    params :
        Optional ``AlignSeriesParams``.  When omitted, defaults are
        loaded from the bundled ``config.yaml``.
    config :
        Optional ``OperatorConfig``.  When omitted, the bundled
        ``config.yaml`` is loaded (and process-cached).

    Returns
    -------
    SeriesSet
        Keyed by each input's ``series_key``.  ``common_index`` is the
        combined index per the chosen ``join_policy``.  Lineage's head
        step is the alignment ``OperatorStep`` whose input hashes are
        the per-input lineage heads.
    """
    # ------------------------------------------------------------------
    # 1. Load config + resolve params from defaults when not supplied.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)

    if params is None:
        # Pull each scalar default out of the operator config.  The
        # config's valid_values check already gates them at load time.
        params = AlignSeriesParams(
            join_policy=config.default_value("join_policy"),
            fill_policy=config.default_value("fill_policy"),
            fill_limit=config.default_value("fill_limit"),
            require_matching_frequency=config.default_value(
                "require_matching_frequency"
            ),
            require_matching_missingness=config.default_value(
                "require_matching_missingness"
            ),
        )

    # Defensive: detect drift between config and operator schema.
    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"align_series: 'config' must be an OperatorConfig instance; "
            f"got {type(config).__name__}."
        )
    if config.operator.name != _OPERATOR_NAME:
        raise OperatorConfigError(
            f"align_series: config name mismatch — expected "
            f"{_OPERATOR_NAME!r}, got {config.operator.name!r}."
        )

    # ------------------------------------------------------------------
    # 2. Structural-metadata compatibility checks (per operator
    #    architecture doc — finance-blind, but structurally informed).
    # ------------------------------------------------------------------
    if not series_list:
        raise AlignSeriesError(
            "align_series requires at least 1 input series; got 0."
        )

    keys = [s.series_key for s in series_list]
    counts = Counter(keys)
    duplicates = sorted(k for k, n in counts.items() if n > 1)
    if duplicates:
        raise AlignSeriesError(
            f"align_series: duplicate series_key(s) {duplicates!r}; "
            "every input must have a unique series_key."
        )

    # Index-type compatibility.  ``Series.__init__`` already enforces
    # DatetimeIndex per artifact, but a defensive recheck protects
    # against bypass code paths that might construct via private
    # construction in the future.
    for s in series_list:
        if not isinstance(s.payload.index, pd.DatetimeIndex):
            raise AlignSeriesError(
                f"align_series: series '{s.series_key}' payload index is "
                f"{type(s.payload.index).__name__}; DatetimeIndex required."
            )

    # Frequency compatibility (build plan v5: structural metadata is
    # load-bearing).  Defaults to strict — silently aligning a daily
    # series with a weekly one is the canonical hazard the structural-
    # metadata contract is meant to surface.  Pass
    # ``require_matching_frequency=False`` to opt into mixed
    # frequencies; the choice is recorded in lineage either way.
    declared_frequencies = {s.frequency for s in series_list}
    non_none_frequencies = declared_frequencies - {None}
    if params.require_matching_frequency and len(non_none_frequencies) > 1:
        per_series = sorted(
            f"{s.series_key}={s.frequency!r}" for s in series_list
        )
        raise AlignSeriesError(
            f"align_series: incompatible frequencies across inputs "
            f"({per_series}).  Pass require_matching_frequency=False to "
            "opt into mixed-frequency alignment explicitly."
        )
    if (
        params.require_matching_frequency
        and len(non_none_frequencies) == 1
        and None in declared_frequencies
    ):
        # Some inputs declare a frequency, others don't.  In strict
        # mode this is a partial-metadata case that almost certainly
        # means the caller forgot to tag one — surface it.
        per_series = sorted(
            f"{s.series_key}={s.frequency!r}" for s in series_list
        )
        raise AlignSeriesError(
            f"align_series: some inputs declare a frequency, others do "
            f"not ({per_series}).  Tag every input or pass "
            "require_matching_frequency=False."
        )
    # Resolved output frequency: the single non-None value if one was
    # agreed on; None when all inputs were untagged or when lenient
    # mode allowed mixed values (we cannot honestly emit a single tag
    # in that case).
    if len(non_none_frequencies) == 1 and None not in declared_frequencies:
        common_frequency = next(iter(non_none_frequencies))
    else:
        common_frequency = None

    # Missingness compatibility (same rationale).  We compare on the
    # canonical JSON dump of each Pydantic policy model — same kind +
    # same params = same dump.  Mixing CleanSingleSeriesV1 with
    # RawNoCleaning is exactly what the structured ``MissingnessPolicy``
    # family is supposed to catch.
    missingness_signatures = {
        s.series_key: s.missingness_policy.model_dump(mode="json")
        for s in series_list
    }
    distinct_signatures = {
        tuple(sorted(sig.items())) for sig in missingness_signatures.values()
    }
    if params.require_matching_missingness and len(distinct_signatures) > 1:
        per_series = sorted(
            f"{k}={v}" for k, v in missingness_signatures.items()
        )
        raise AlignSeriesError(
            f"align_series: incompatible missingness policies across "
            f"inputs ({per_series}).  Pass "
            "require_matching_missingness=False to opt into mixed "
            "policies explicitly."
        )

    # ------------------------------------------------------------------
    # 3. Combine indexes per join_policy.
    # ------------------------------------------------------------------
    if params.join_policy == "inner":
        # Intersection of indexes.  pandas Index.intersection is
        # commutative + associative + preserves sort order when both
        # are sorted (which we enforce on Series construction).
        common_index = series_list[0].payload.index
        for s in series_list[1:]:
            common_index = common_index.intersection(s.payload.index)
    elif params.join_policy == "outer":
        common_index = series_list[0].payload.index
        for s in series_list[1:]:
            common_index = common_index.union(s.payload.index)
    else:
        # Pydantic Literal already enforces, but defensive on unknown
        # values arriving via misuse:
        raise AlignSeriesError(
            f"align_series: unsupported join_policy={params.join_policy!r}."
        )

    common_index = pd.DatetimeIndex(common_index).sort_values()

    if len(common_index) == 0:
        raise AlignSeriesError(
            f"align_series: after {params.join_policy} join over "
            f"{len(series_list)} series, the combined index is empty.  "
            "Consider join_policy='outer' or check that the inputs "
            "cover overlapping date ranges."
        )

    # ------------------------------------------------------------------
    # 4. Reindex + apply fill_policy.  When ffill is applied, wrap the
    #    upstream missingness policy in AlignSeriesFFillV1 so the
    #    output's metadata honestly reflects that the operator imputed
    #    cells (build plan v5 / Codex P1 follow-up: "ffill changes
    #    payload but missingness_policy was stale").
    # ------------------------------------------------------------------
    series_by_key = {}
    missingness_by_key: dict[str, MissingnessPolicy] = {}
    for s in series_list:
        reindexed = s.payload.reindex(common_index)
        if params.fill_policy == "ffill":
            reindexed = reindexed.ffill(limit=params.fill_limit)
            missingness_by_key[s.series_key] = AlignSeriesFFillV1(
                upstream=s.missingness_policy,
                fill_limit=params.fill_limit,
            )
        else:
            # fill_policy == "raw": payload is reindex-only; the
            # upstream policy still describes it accurately.
            missingness_by_key[s.series_key] = s.missingness_policy
        series_by_key[s.series_key] = reindexed

    units_by_key = {s.series_key: s.units for s in series_list}
    upstream_lineage_by_key = {s.series_key: s.lineage for s in series_list}

    # ------------------------------------------------------------------
    # 5. Build the alignment lineage step.
    # ------------------------------------------------------------------
    input_hashes = tuple(s.lineage.head_hash for s in series_list)
    align_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params={
            "join_policy": params.join_policy,
            "fill_policy": params.fill_policy,
            "fill_limit": params.fill_limit,
            "require_matching_frequency": params.require_matching_frequency,
            "require_matching_missingness": params.require_matching_missingness,
            # Stable, sorted to keep the hash invariant under input
            # reordering; per-key upstream lineage is captured via
            # input_hashes already.
            "input_series_keys": sorted(keys),
            # Record the resolved compatibility outcomes so consumers
            # can recover what was actually checked vs accepted.
            "resolved_frequency": common_frequency,
        },
        input_hashes=input_hashes,
    )
    set_lineage = Lineage.from_steps([align_step])

    return SeriesSet(
        series_by_key=series_by_key,
        units_by_key=units_by_key,
        missingness_by_key=missingness_by_key,
        upstream_lineage_by_key=upstream_lineage_by_key,
        common_index=common_index,
        # Preserve the agreed-on frequency tag.  Drops to None only
        # when (a) no input declared a frequency or (b) lenient mode
        # was used and inputs disagreed — in that case we cannot
        # honestly emit a single tag.
        frequency=common_frequency,
        lineage=set_lineage,
    )


__all__ = [
    "align_series",
    "AlignSeriesError",
]
