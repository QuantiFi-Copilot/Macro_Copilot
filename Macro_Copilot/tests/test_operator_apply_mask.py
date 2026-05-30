"""tests/test_operator_apply_mask.py — Phase 2A operator.

Covers the "split sample by mask" load-bearing primitive of the
``regime_conditioned_relationship`` archetype:

  1. Bundled config.yaml loads + name-checks.
  2. Happy path: subsample a Series by a True/False mask.
     Default index_policy=intersect, preserve_full_index=false.
  3. preserve_full_index=true: full index, mask=False → NaN.
  4. index_policy=strict_match: raises on differing indexes.
  5. index_policy=intersect: clean across slightly-different indexes.
  6. Empty intersection raises with diagnostic.
  7. All-False mask raises (empty subsample).
  8. Lineage chain extends through both inputs (Series chain primary,
     EventSet chain in auxiliary_lineages).
  9. Units / frequency / missingness propagate 1:1 from input Series.
 10. Registry entry registered with correct slot types.
 11. OPR11 structural-metadata algebra: require_matching_frequency is a
     REAL check vs the EventSet's frequency tag (strict raises on
     mismatch, lenient opt-out accepts); require_matching_missingness
     is exposed + recorded but vacuous (an EventSet carries no
     missingness regime).  Both flags recorded in the lineage step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, OperatorStep, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import EventSet, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.apply_mask import (
    CONFIG_PATH,
    apply_mask,
    ApplyMaskError,
    ApplyMaskParams,
)
from shared.operators.threshold_events import (
    threshold_events,
    ThresholdEventsParams,
)
from shared.workflow.registry import OPERATOR_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _primitive_lineage(series_key: str) -> Lineage:
    step = PrimitiveStep.build(
        name="synthetic_primitive",
        version="1.0.0",
        params={"series_key": series_key},
        tool_config_hash="test_config_hash",
        output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _make_series(
    *,
    series_key: str = "target",
    n: int = 50,
    start: str = "2025-01-01",
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    frequency: Optional[str] = "B",
) -> Series:
    idx = pd.bdate_range(start, periods=n)
    payload = pd.Series(
        np.linspace(4.0, 5.0, n), index=idx, name=series_key,
    )
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=frequency,
        missingness_policy=RawNoCleaning(),
        lineage=_primitive_lineage(series_key),
    )


def _make_mask_via_threshold(series: Series, *, threshold: float) -> EventSet:
    """Build an EventSet by thresholding the input series above
    ``threshold`` — exercises the canonical mask-producer path."""
    return threshold_events(
        series, params=ThresholdEventsParams(
            rule="above",
            threshold=threshold,
            threshold_basis="raw_value",
            look_ahead_safe=True,
        ),
    )


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================


class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads_with_correct_name(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "apply_mask"
        assert cfg.operator.method_family == "masking"

    def test_required_defaults_present(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.default_value("index_policy") == "intersect"
        assert cfg.default_value("preserve_full_index") is False
        # OPR11 structural-metadata controls — strict by default.
        assert cfg.default_value("require_matching_frequency") is True
        assert cfg.default_value("require_matching_missingness") is True


# ===========================================================================
# 2. Happy path: sparse subsample
# ===========================================================================


class TestHappyPathSparse:
    def test_default_returns_only_true_dates(self):
        s = _make_series(n=50)  # values 4.0..5.0
        # Threshold above 4.5 → about half the dates (later half).
        mask = _make_mask_via_threshold(s, threshold=4.5)

        out = apply_mask(s, mask)

        # Default preserve_full_index=False ⇒ payload size == True count.
        n_true = int(mask.mask.sum())
        assert len(out.payload) == n_true
        # Every date in the output had mask True.
        assert all(mask.mask.loc[d] for d in out.payload.index)

    def test_values_match_inputs_at_true_dates(self):
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        for d, v in out.payload.items():
            assert v == s.payload.loc[d], f"value mismatch at {d}"


# ===========================================================================
# 3. preserve_full_index=True
# ===========================================================================


class TestPreserveFullIndex:
    def test_full_index_returned_with_NaNs_at_false_dates(self):
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(
            s, mask,
            params=ApplyMaskParams(
                index_policy="intersect",
                preserve_full_index=True,
            ),
        )
        # Same length as input.
        assert len(out.payload) == len(s.payload)
        # mask=False cells are NaN.
        false_dates = mask.mask[~mask.mask].index
        for d in false_dates:
            assert pd.isna(out.payload.loc[d])
        # mask=True cells are preserved.
        true_dates = mask.mask[mask.mask].index
        for d in true_dates:
            assert out.payload.loc[d] == s.payload.loc[d]


# ===========================================================================
# 4. index_policy contracts
# ===========================================================================


class TestIndexPolicy:
    def test_strict_match_with_identical_index_passes(self):
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        # Mask is built from the same series → indexes match exactly.
        out = apply_mask(
            s, mask,
            params=ApplyMaskParams(index_policy="strict_match"),
        )
        assert isinstance(out, Series)

    def test_strict_match_with_differing_indexes_raises(self):
        """Build a mask on one index, apply to a Series on a DIFFERENT
        index → strict_match must surface the mismatch."""
        s = _make_series(n=50, start="2025-01-01")
        # Build a mask from a DIFFERENT series (different start date)
        # so the mask's index does not match s's index.
        s_for_mask = _make_series(
            series_key="mask_source", n=50, start="2025-02-15",
        )
        mask = _make_mask_via_threshold(s_for_mask, threshold=4.5)

        with pytest.raises(ApplyMaskError, match="strict_match"):
            apply_mask(
                s, mask,
                params=ApplyMaskParams(index_policy="strict_match"),
            )

    def test_intersect_with_partial_overlap_works(self):
        """Mask built on a slightly different calendar → intersect
        should compute the common subset and apply the mask there."""
        s = _make_series(n=50, start="2025-01-01")
        s_for_mask = _make_series(
            series_key="mask_source", n=50, start="2025-01-15",
        )
        mask = _make_mask_via_threshold(s_for_mask, threshold=4.5)

        out = apply_mask(s, mask, params=ApplyMaskParams(
            index_policy="intersect", preserve_full_index=False,
        ))
        # Output dates must lie inside BOTH s's and the mask's index.
        for d in out.payload.index:
            assert d in s.payload.index
            assert d in mask.mask.index
            assert mask.mask.loc[d]

    def test_intersect_with_zero_overlap_raises(self):
        s = _make_series(n=50, start="2025-01-01")
        # Mask built on a totally non-overlapping window.
        s_for_mask = _make_series(
            series_key="mask_source", n=50, start="2030-01-01",
        )
        mask = _make_mask_via_threshold(s_for_mask, threshold=4.5)

        with pytest.raises(ApplyMaskError, match="NO dates in common"):
            apply_mask(s, mask)


# ===========================================================================
# 5. Empty subsample refusals
# ===========================================================================


class TestEmptySubsampleRefusal:
    def test_all_false_mask_raises(self):
        s = _make_series(n=50)
        # Threshold above the maximum value → mask is all False.
        mask = _make_mask_via_threshold(s, threshold=999.0)
        with pytest.raises(ApplyMaskError, match="empty"):
            apply_mask(s, mask)


# ===========================================================================
# 6. Lineage propagation
# ===========================================================================


class TestLineagePropagation:
    def test_apply_mask_step_appended_to_input_series_chain(self):
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        # Last step of output is apply_mask.
        head = out.lineage.steps[-1]
        assert isinstance(head, OperatorStep)
        assert head.name == "apply_mask"

    def test_lineage_records_realized_mask_cardinality(self):
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        head = out.lineage.steps[-1]
        # Sanity: the operator step records what the mask actually fired
        # so a lineage walker can confirm the subsample is non-degenerate.
        assert "n_true" in head.params
        assert "n_total" in head.params
        assert head.params["n_total"] == len(mask.mask)

    def test_mask_lineage_recorded_as_auxiliary(self):
        """The EventSet's lineage chain MUST be captured as an
        auxiliary_lineage on the apply_mask step (mirrors the
        series_arithmetic auxiliary discipline)."""
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        head = out.lineage.steps[-1]
        assert len(head.auxiliary_lineages) == 1
        # The auxiliary lineage's head must equal the mask's head hash.
        aux = head.auxiliary_lineages[0]
        assert aux.head_hash == mask.lineage.head_hash

    def test_input_series_chain_preserved(self):
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        names = [step.name for step in out.lineage.steps]
        # The synthetic primitive step that produced ``s`` must
        # survive into the post-apply lineage.
        assert "synthetic_primitive" in names

    def test_lineage_records_opr11_flags(self):
        """Both OPR11 structural-metadata flags are recorded on the
        apply_mask step (lineage honesty — a reviewer sees the
        strict/relaxed choice)."""
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        head = out.lineage.steps[-1]
        assert head.params["require_matching_frequency"] is True
        assert head.params["require_matching_missingness"] is True

    def test_lineage_records_relaxed_frequency_flag(self):
        """A frequency opt-out is visible to a lineage walker."""
        s = _make_series(n=50)
        s_w = _make_series(series_key="weekly_src", n=50, frequency="W")
        mask_w = _make_mask_via_threshold(s_w, threshold=4.5)
        out = apply_mask(
            s, mask_w,
            params=ApplyMaskParams(require_matching_frequency=False),
        )
        head = out.lineage.steps[-1]
        assert head.params["require_matching_frequency"] is False


# ===========================================================================
# 7. Metadata propagation
# ===========================================================================


class TestMetadataPropagation:
    def test_units_propagate_from_input(self):
        s = _make_series(n=50, units=TimeSeriesUnits.BPS)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        assert out.units == TimeSeriesUnits.BPS

    def test_frequency_propagates_from_input(self):
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        assert out.frequency == s.frequency

    def test_missingness_propagates_from_input(self):
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        out = apply_mask(s, mask)
        assert out.missingness_policy == s.missingness_policy

    def test_missingness_flag_is_vacuous_output_inherits_series_policy(self):
        """``require_matching_missingness`` is a uniformity flag: an
        EventSet has no missingness regime, so the output inherits the
        input Series's policy REGARDLESS of the flag's value (there is
        no second policy to combine or refuse)."""
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        for flag in (True, False):
            out = apply_mask(
                s, mask,
                params=ApplyMaskParams(require_matching_missingness=flag),
            )
            assert out.missingness_policy == s.missingness_policy


# ===========================================================================
# 7b. Frequency compatibility (OPR11 — real check vs EventSet.frequency)
# ===========================================================================


class TestFrequencyCompatibility:
    """``require_matching_frequency`` is an ENFORCED check: the EventSet
    carries a real ``frequency`` tag (propagated from its source Series
    by ``threshold_events``), so apply_mask compares it against the
    target Series and refuses a mismatch in strict mode (default)."""

    def test_matching_frequency_passes_strict_default(self):
        # Mask built from the same series → identical "B" frequency tag.
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        assert s.frequency == mask.frequency == "B"
        out = apply_mask(s, mask)  # strict default → no raise
        assert isinstance(out, Series)

    def test_both_none_frequency_passes_strict(self):
        # Two untagged inputs agree (None == None) → strict pass.
        s = _make_series(n=50, frequency=None)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        assert s.frequency is None and mask.frequency is None
        out = apply_mask(s, mask)
        assert isinstance(out, Series)

    def test_mismatched_frequency_raises_strict_default(self):
        # Mask source shares the index but is tagged "W" → the EventSet
        # inherits "W" while the target is "B".  Strict default refuses.
        s = _make_series(n=50)
        s_w = _make_series(series_key="weekly_src", n=50, frequency="W")
        mask_w = _make_mask_via_threshold(s_w, threshold=4.5)
        assert s.frequency == "B" and mask_w.frequency == "W"
        with pytest.raises(ApplyMaskError, match="incompatible frequencies"):
            apply_mask(s, mask_w)

    def test_partial_tagging_one_none_raises_strict(self):
        # One side tagged, the other None → strict refuses (the same
        # partial-metadata discipline as event_windows / align_series).
        s = _make_series(n=50)
        s_none = _make_series(series_key="untagged", n=50, frequency=None)
        mask_none = _make_mask_via_threshold(s_none, threshold=4.5)
        assert s.frequency == "B" and mask_none.frequency is None
        with pytest.raises(ApplyMaskError, match="incompatible frequencies"):
            apply_mask(s, mask_none)

    def test_mismatched_frequency_lenient_opt_out_accepts(self):
        # require_matching_frequency=False opts into mixed-frequency
        # masking; the output is the subsampled input, so its frequency
        # stays the input Series's "B".
        s = _make_series(n=50)
        s_w = _make_series(series_key="weekly_src", n=50, frequency="W")
        mask_w = _make_mask_via_threshold(s_w, threshold=4.5)
        out = apply_mask(
            s, mask_w,
            params=ApplyMaskParams(require_matching_frequency=False),
        )
        assert isinstance(out, Series)
        assert out.frequency == "B"


# ===========================================================================
# 8. Config-discipline parity
# ===========================================================================


class TestConfigDiscipline:
    def test_wrong_config_name_raises(self):
        from shared.operators.align_series import (
            CONFIG_PATH as OTHER_CONFIG_PATH,
        )
        align_cfg = load_operator_config(OTHER_CONFIG_PATH)
        s = _make_series(n=50)
        mask = _make_mask_via_threshold(s, threshold=4.5)
        with pytest.raises(OperatorConfigError, match="config name mismatch"):
            apply_mask(s, mask, config=align_cfg)


# ===========================================================================
# 9. Registry entry
# ===========================================================================


class TestRegistryEntry:
    def test_registered(self):
        assert "apply_mask" in OPERATOR_REGISTRY

    def test_input_slots(self):
        # PART B refactor: input_slots values are SlotDescriptor instances
        # (ArtifactTypeName is a str-Enum, so == "Series" still compares
        # equal to the string).
        spec = OPERATOR_REGISTRY["apply_mask"]
        assert set(spec.input_slots) == {"series", "mask"}
        assert spec.input_slots["series"].artifact_type == "Series"
        assert spec.input_slots["mask"].artifact_type == "EventSet"

    def test_output_type(self):
        # PART B refactor: output is an OutputDescriptor.
        spec = OPERATOR_REGISTRY["apply_mask"]
        assert spec.output.artifact_type == "Series"

    def test_callable_resolves_to_operator(self):
        spec = OPERATOR_REGISTRY["apply_mask"]
        assert spec.callable is apply_mask

    def test_params_class_resolves(self):
        spec = OPERATOR_REGISTRY["apply_mask"]
        assert spec.params_class is ApplyMaskParams
