"""tests/test_bridge_pipeline_smoke.py — end-to-end pipeline integration

Phase 1B Work Item 4.  Exercises the bridge against REAL primitives
and REAL operators end-to-end.  Where the unit-style tests in
``test_adapter_from_time_series.py`` mock individual contracts, this
file proves the full chain:

  primitive → forward bridge → operator(s) → reverse bridge → wire

works without bespoke glue at any stage.

What this file pins
-------------------
- Forward bridge output composes with ``align_series``,
  ``series_arithmetic``, and ``threshold_events`` on the actual
  operator signatures (no operator code changes — the bridge has
  to fit, not the other way around).
- Lineage chain extends correctly through each operator step:
  primitive root + N operator extensions = N+1 step chain.
- Reverse bridge produces a wire ``TimeSeries`` whose description
  matches the actual chain.
- Cross-primitive composition (``rate_level`` + ``curve_spread``)
  passes the structural-metadata compatibility checks correctly —
  same ``ffill_limit_days`` ⇒ identical ``CleanSingleSeriesV1`` ⇒
  ``align_series`` accepts.
- Binary operator's right-operand auxiliary lineage is preserved
  on the artifact AND excluded from the wire summary.
- Lineage hashes are deterministic across re-runs (replay).
- ``NaN`` ↔ ``None`` survives operator round-trips that don't
  silently drop or fill.
- Structural-metadata refusals (frequency / missingness mismatch)
  fire AT the operator boundary as designed; the bridge does not
  paper over them.

Out of scope
------------
- ``event_windows`` and ``conditional_aggregate`` operators —
  they consume EventSet+Series jointly and require synthetic
  multi-input setups beyond the bridge's contract.  The Phase 1B
  bridge plan called for primitive→operator coverage; those two
  operators are downstream of the bridge boundary and are
  exercised by their own test suites.
- Real database fetches.  The DB layer is mocked exactly as the
  per-tool tests mock it; this is a *bridge integration smoke*,
  not a parity / SQL gate.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from shared.artifacts import (
    CleanSingleSeriesV1,
    EventSet,
    Lineage,
    OperatorStep,
    PrimitiveStep,
    Series,
    SeriesSet,
    TimeSeriesUnits,
)
from shared.artifacts.adapters import (
    artifact_series_to_time_series,
    tool_output_to_artifact_series,
)
from shared.config import (
    clear_tool_config_cache,
    load_tool_config,
)
from shared.operators.align_series import align_series
from shared.operators.series_arithmetic import series_arithmetic
from shared.operators.threshold_events import (
    threshold_events,
    ThresholdEventsParams,
)


# ---------------------------------------------------------------------------
# Shared fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


class _FrozenDate(date):
    """Frozen date used to make per-primitive ``date.today()``
    deterministic.  Same pattern every per-tool test uses."""
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _curve_spread_synthetic_df(*, days: int = 600, seed: int = 11) -> pd.DataFrame:
    """Two-tenor curve frame for OIS curve_spread fetcher.  Wide
    enough that the rolling z-score's warmup region fits inside the
    fetched buffer + the displayed window has sub-z-score-warmup
    coverage."""
    bdays = pd.bdate_range(
        _FrozenDate._frozen_value - timedelta(days=days * 2),
        _FrozenDate._frozen_value,
    )[-days:]
    rs = np.random.RandomState(seed)

    def _series(label: str, init: float, drift: float) -> pd.DataFrame:
        n = len(bdays)
        v = np.linspace(init, init + drift, n) + rs.randn(n) * 0.012
        return pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "tenor": label,
            "field_value": v,
        })

    return pd.concat(
        [
            _series("2Y", 4.20, -0.10),
            _series("5Y", 4.35, -0.05),
            _series("10Y", 4.50, +0.05),
        ],
        ignore_index=True,
    ).sort_values(["trade_date", "tenor"]).reset_index(drop=True)


def _rate_level_synthetic_df(*, days: int = 600, seed: int = 7) -> pd.DataFrame:
    """Single-tenor frame for OIS rate_level fetcher (same shape as
    fetch_single_tenor returns)."""
    bdays = pd.bdate_range(
        _FrozenDate._frozen_value - timedelta(days=days * 2),
        _FrozenDate._frozen_value,
    )[-days:]
    rs = np.random.RandomState(seed)
    n = len(bdays)
    rates = np.linspace(4.50, 4.20, n) + rs.randn(n) * 0.01
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": rates,
    })


def _run_curve_spread(
    *, short_tenor: str = "2Y", long_tenor: str = "10Y",
    seed: int = 11,
) -> tuple:
    """Invoke OIS curve_spread against synthetic data; return the
    raw output dict + Pydantic output class + Input + ToolConfig
    so the bridge can lift fields by name."""
    from rates_agent.ois.tools.curve_spread import (
        CONFIG_PATH,
        calculate_ois_curve_spread,
        OISCurveSpreadInput,
    )
    from rates_agent.ois.tools.curve_spread.schemas import (
        OISCurveSpreadOutput,
    )

    raw_df = _curve_spread_synthetic_df(seed=seed)
    params = OISCurveSpreadInput(
        curve_family="USD_SOFR_OIS",
        short_tenor=short_tenor, long_tenor=long_tenor,
        lookback_days=365,
    )
    cfg = load_tool_config(CONFIG_PATH)

    with patch(
        "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
        return_value=raw_df,
    ), patch(
        "rates_agent.ois.tools.curve_spread.compute.date",
        _FrozenDate,
    ):
        tool_output = calculate_ois_curve_spread(
            engine=None, params=params, config=cfg,
        )

    return tool_output, OISCurveSpreadOutput, params, cfg


def _run_rate_level(*, tenor: str = "2Y", seed: int = 7) -> tuple:
    """Invoke OIS rate_level against synthetic data."""
    from rates_agent.ois.tools.rate_level import (
        CONFIG_PATH,
        get_ois_rate_level,
        OISRateLevelInput,
    )
    from rates_agent.ois.tools.rate_level.schemas import (
        OISRateLevelOutput,
    )

    raw_df = _rate_level_synthetic_df(seed=seed)
    params = OISRateLevelInput(
        curve_family="USD_SOFR_OIS", tenor=tenor, lookback_days=365,
    )
    cfg = load_tool_config(CONFIG_PATH)

    with patch(
        "rates_agent.ois.tools.rate_level.compute.fetch_single_tenor",
        return_value=raw_df,
    ), patch(
        "rates_agent.ois.tools.rate_level.compute.date",
        _FrozenDate,
    ):
        tool_output = get_ois_rate_level(
            engine=None, params=params, config=cfg,
        )

    return tool_output, OISRateLevelOutput, params, cfg


def _bridge_curve_spread(*, output_field: str, **run_kwargs) -> Series:
    """End-to-end: invoke curve_spread → forward bridge → Series."""
    tool_output, OutClass, params, cfg = _run_curve_spread(**run_kwargs)
    return tool_output_to_artifact_series(
        tool_output,
        output_class=OutClass,
        output_field=output_field,
        # MCP wrapper name (per the bridge plan + PR #69 follow-up
        # that pinned this contract).
        tool_name="calculate_ois_curve_spread_tool",
        tool_config=cfg,
        params=params,
    )


def _bridge_rate_level(**run_kwargs) -> Series:
    tool_output, OutClass, params, cfg = _run_rate_level(**run_kwargs)
    return tool_output_to_artifact_series(
        tool_output,
        output_class=OutClass,
        output_field="time_series",
        # MCP wrapper name (NOT the YAML tool.name — see PR #69).
        tool_name="calculate_ois_rate_level_tool",
        tool_config=cfg,
        params=params,
    )


# ===========================================================================
# 1. Canonical 3-stage pipeline: primitive → operator → reverse bridge
# ===========================================================================

class TestPipeline_PrimitiveOperatorReverse:
    """The end-state Phase 1B contract: a primitive output flows
    through the bridge, an operator extends the chain, and the
    reverse bridge serializes the result with a multi-step lineage
    summary on the wire.
    """

    def test_curve_spread_then_diff_then_reverse(self):
        # Step 1: primitive → bridge → Series A (BPS spread).
        s_spread = _bridge_curve_spread(output_field="time_series_spread")
        assert s_spread.units == TimeSeriesUnits.BPS
        assert len(s_spread.lineage.steps) == 1
        assert s_spread.lineage.steps[0].kind == "primitive"

        # Step 2: series_arithmetic(A, "diff") → Series B (BPS, unary).
        s_diff = series_arithmetic(s_spread, "diff")
        assert s_diff.units == TimeSeriesUnits.BPS
        # Lineage now has primitive + operator (chain length 2).
        assert len(s_diff.lineage.steps) == 2
        assert [step.kind for step in s_diff.lineage.steps] == [
            "primitive", "operator",
        ]
        assert s_diff.lineage.steps[1].name == "series_arithmetic"

        # Step 3: reverse bridge → wire TimeSeries.
        wire = artifact_series_to_time_series(s_diff)
        # Description summary captures the full chain.
        assert wire.description == (
            "derived: calculate_ois_curve_spread_tool → series_arithmetic"
        )
        # Units survive the wire format.
        assert wire.units == TimeSeriesUnits.BPS
        # First row of `diff` output is NaN (no previous value to diff
        # against); reverse bridge converts NaN → None.
        assert wire.rows[0].value is None
        # Subsequent rows are real numeric diffs.
        non_none_rows = [r for r in wire.rows if r.value is not None]
        assert len(non_none_rows) == len(wire.rows) - 1

    def test_rate_level_then_diff_then_reverse_preserves_PERCENT_units(self):
        s_level = _bridge_rate_level()
        assert s_level.units == TimeSeriesUnits.PERCENT
        s_diff = series_arithmetic(s_level, "diff")
        assert s_diff.units == TimeSeriesUnits.PERCENT  # unary preserves
        wire = artifact_series_to_time_series(s_diff)
        assert wire.units == TimeSeriesUnits.PERCENT
        assert wire.description == (
            "derived: calculate_ois_rate_level_tool → series_arithmetic"
        )

    def test_chain_length_grows_by_one_per_operator(self):
        s = _bridge_curve_spread(output_field="time_series_spread")
        assert len(s.lineage.steps) == 1
        s = series_arithmetic(s, "diff")
        assert len(s.lineage.steps) == 2
        s = series_arithmetic(s, "diff")  # diff a diff
        assert len(s.lineage.steps) == 3
        # All operator steps in the tail.
        kinds = [step.kind for step in s.lineage.steps]
        assert kinds == ["primitive", "operator", "operator"]


# ===========================================================================
# 2. align_series consumes bridge output, returns a SeriesSet
# ===========================================================================

class TestPipeline_AlignSeriesAcceptsBridgeOutput:
    """The bridge produces ``Series`` artifacts whose
    ``frequency=None`` and ``missingness_policy=CleanSingleSeriesV1``
    are the structural-metadata signature ``align_series`` is built
    to operate on.  This pins the integration."""

    def test_align_two_curve_spread_fields(self):
        # BPS spread + Z_SCORE — same primitive, different output_fields.
        # Both have identical missingness regime
        # (CleanSingleSeriesV1(ffill_limit=5)) and frequency=None.
        s_spread = _bridge_curve_spread(output_field="time_series_spread")
        s_zscore = _bridge_curve_spread(output_field="time_series_zscore")

        ss = align_series([s_spread, s_zscore])
        assert isinstance(ss, SeriesSet)
        # Both series are keyed in the set under their series_keys
        # (which the bridge populated from TimeSeries.series_name).
        assert set(ss.series_by_key.keys()) == {
            s_spread.series_key, s_zscore.series_key,
        }
        # SeriesSet has its own lineage step (the alignment).
        assert ss.lineage.steps[-1].kind == "operator"
        assert ss.lineage.steps[-1].name == "align_series"

    def test_align_then_get_per_series_lineage(self):
        """SeriesSet.upstream_lineage_by_key carries each input's
        original lineage chain, with the alignment step appended.
        Pin the contract end-to-end."""
        s_spread = _bridge_curve_spread(output_field="time_series_spread")
        s_zscore = _bridge_curve_spread(output_field="time_series_zscore")

        ss = align_series([s_spread, s_zscore])

        # Each per-series upstream lineage is the original (1 step =
        # primitive); the alignment step lives on the SeriesSet's own
        # lineage, not duplicated per series.
        for key, ln in ss.upstream_lineage_by_key.items():
            assert len(ln.steps) == 1
            assert ln.steps[0].kind == "primitive"


# ===========================================================================
# 3. threshold_events consumes bridge output, returns an EventSet
# ===========================================================================

class TestPipeline_ThresholdEventsConsumesBridgeOutput:
    """``threshold_events`` is a Series → EventSet conversion.  The
    output's lineage extends the input's chain with an OperatorStep,
    proving the bridge's Series contract works for this operator
    family (different output type than series_arithmetic but same
    lineage discipline)."""

    def test_threshold_zscore_with_raw_value_basis(self):
        s_zscore = _bridge_curve_spread(output_field="time_series_zscore")

        # raw_value basis: |z-score| ≥ 1.5 → event.  No rolling args
        # because basis is raw_value (the schema validator forbids
        # them for raw_value).
        events = threshold_events(
            s_zscore,
            ThresholdEventsParams(
                rule="abs_above",
                threshold=1.5,
                threshold_basis="raw_value",
            ),
        )
        assert isinstance(events, EventSet)
        # Lineage extends the bridge's primitive step with an
        # operator step.
        assert len(events.lineage.steps) == 2
        assert [step.kind for step in events.lineage.steps] == [
            "primitive", "operator",
        ]
        assert events.lineage.steps[1].name == "threshold_events"
        # source_series_key echoes the bridge's series_key.
        assert events.source_series_key == s_zscore.series_key


# ===========================================================================
# 4. Binary operator: auxiliary lineage preserved on artifact + excluded
#    from wire summary
# ===========================================================================

class TestPipeline_BinaryOperatorAuxiliaryLineage:
    """When ``series_arithmetic`` is binary (e.g. subtract two
    Series), the right operand's lineage chain is preserved on the
    output's structured ``OperatorStep.auxiliary_lineages``, but the
    reverse bridge correctly EXCLUDES it from the linear wire
    description summary.

    Pinned in unit tests already (PR #70, on synthetic
    PrimitiveStep data); here we exercise the same property end-to-
    end against two REAL primitive invocations.
    """

    def test_subtract_two_curve_spreads_summary_is_linear(self):
        # Two real curve_spread invocations on different tenor pairs
        # (2s10s vs 2s5s) — same primitive, same units (BPS), same
        # missingness regime.  Critically, we do NOT pre-align them
        # via ``align_series`` here:
        #
        #   - ``align_series`` returns a ``SeriesSet`` whose member
        #     Series SHARE the set's lineage (by design), so
        #     post-alignment the left and right operands have the
        #     SAME ``lineage.head_hash``.  That makes the
        #     auxiliary-vs-left negative control vacuous.
        #
        #   - Both bridge outputs already share a date index because
        #     the synthetic frame uses the same date range and
        #     ``date.today()`` is frozen — so ``series_arithmetic``
        #     accepts them directly.
        #
        # Skipping the alignment lets us prove auxiliary IDENTITY
        # (right-operand's chain, NOT left's) on operands whose
        # lineage hashes genuinely differ.  The "pipeline ends on
        # SeriesSet members" shape is already pinned in
        # ``TestPipeline_AlignSeriesAcceptsBridgeOutput``.
        s_2s10s = _bridge_curve_spread(
            output_field="time_series_spread",
            short_tenor="2Y", long_tenor="10Y", seed=11,
        )
        s_2s5s = _bridge_curve_spread(
            output_field="time_series_spread",
            short_tenor="2Y", long_tenor="5Y", seed=11,
        )
        # Pre-condition: the two operands have DIFFERENT lineage
        # head_hashes — if they didn't, the auxiliary-vs-left check
        # below would be vacuously true.  This fails loudly if a
        # future fixture change accidentally aligns them.
        assert s_2s10s.lineage.head_hash != s_2s5s.lineage.head_hash, (
            "test fixture is degenerate: the two pre-binary operands "
            "share a lineage head_hash, so the auxiliary-identity "
            "check would be vacuous.  Make the two primitive "
            "invocations differ in at least one identity bit."
        )
        # Pre-condition: indices match (so series_arithmetic accepts
        # without needing an alignment step).
        assert s_2s10s.payload.index.equals(s_2s5s.payload.index), (
            "test fixture has misaligned indices; either align "
            "first or fix the synthetic frame so date ranges match."
        )
        assert s_2s10s.units == s_2s5s.units == TimeSeriesUnits.BPS

        # Binary subtract.
        diff = series_arithmetic(s_2s10s, "subtract", s_2s5s)
        assert diff.units == TimeSeriesUnits.BPS  # BPS - BPS = BPS

        # The OperatorStep should carry the RIGHT operand's lineage
        # in ``auxiliary_lineages`` — this is the structured
        # provenance.  Codex P2 follow-up: previously this assertion
        # only checked PRESENCE (``len >= 1``), which would still
        # pass if the operator silently attached the WRONG chain
        # (e.g. the left operand's, or an empty Lineage stub).  We
        # now assert chain IDENTITY by comparing the full head_hash
        # AND every per-step hash.
        last_step = diff.lineage.steps[-1]
        assert last_step.kind == "operator"
        assert last_step.name == "series_arithmetic"
        assert len(last_step.auxiliary_lineages) == 1, (
            "binary series_arithmetic must record EXACTLY ONE "
            "auxiliary lineage (the right operand's chain)"
        )

        # IDENTITY check, not presence.  The auxiliary chain must
        # be the right operand's full lineage exactly.
        recorded_aux = last_step.auxiliary_lineages[0]
        assert recorded_aux.head_hash == s_2s5s.lineage.head_hash, (
            f"auxiliary_lineages[0].head_hash="
            f"{recorded_aux.head_hash[:16]}... does not match "
            f"s_2s5s.lineage.head_hash="
            f"{s_2s5s.lineage.head_hash[:16]}... — operator may "
            "have attached the wrong chain (e.g. the left operand's "
            "chain)."
        )
        assert len(recorded_aux.steps) == len(s_2s5s.lineage.steps)
        for i, (rec_step, exp_step) in enumerate(
            zip(recorded_aux.steps, s_2s5s.lineage.steps),
        ):
            assert rec_step.kind == exp_step.kind, (
                f"aux chain step {i} kind mismatch: "
                f"recorded={rec_step.kind!r} expected={exp_step.kind!r}"
            )
            assert rec_step.hash == exp_step.hash, (
                f"aux chain step {i} hash mismatch — auxiliary "
                "lineage drift would have flowed silently into "
                "downstream provenance summaries without this check."
            )

        # Negative-control: the aux chain must NOT match the LEFT
        # operand's chain.  Now meaningful because the two operands
        # have genuinely different head_hashes (asserted above).
        assert recorded_aux.head_hash != s_2s10s.lineage.head_hash, (
            "auxiliary_lineages[0] matches the LEFT operand's "
            "chain — operator likely attached the wrong side."
        )

        # Reverse bridge.  The wire description must summarise only
        # the LINEAR primary chain — the right-operand's chain stays
        # on the structured artifact.
        wire = artifact_series_to_time_series(diff)
        # Summary contains the primary chain steps in order.
        assert wire.description.startswith("derived: ")
        # The last step name appears.
        assert "series_arithmetic" in wire.description
        # Linear: the description has exactly the primary chain
        # length minus the structural prefix (no nested/branched
        # representation of the auxiliary chain).
        steps_in_summary = wire.description.replace(
            "derived: ", "",
        ).split(" → ")
        assert len(steps_in_summary) == len(diff.lineage.steps)
        # And critically: the right-operand's primitive name does
        # NOT bleed into the description — the auxiliary chain
        # stays on the structured artifact, not on the wire.
        # (Both operands are calculate_ois_curve_spread_tool here,
        # so the name itself appears on the wire — but only ONCE,
        # for the primary chain.  Verify by counting.)
        assert wire.description.count(
            "calculate_ois_curve_spread_tool"
        ) == 1


# ===========================================================================
# 5. Cross-primitive composition: rate_level + curve_spread
# ===========================================================================

class TestPipeline_CrossPrimitiveComposition:
    """Bridge outputs from two DIFFERENT primitives (rate_level +
    curve_spread) compose at align_series without bespoke glue
    because both auto-derived missingness policies match
    (``CleanSingleSeriesV1(ffill_limit=5)``).  This is the load-
    bearing claim from the bridge plan: the structural-metadata
    contract is honoured by the bridge → operator handoff."""

    def test_rate_level_plus_curve_spread_align(self):
        s_rate = _bridge_rate_level(tenor="2Y")
        s_spread = _bridge_curve_spread(output_field="time_series_spread")

        # Different units.
        assert s_rate.units == TimeSeriesUnits.PERCENT
        assert s_spread.units == TimeSeriesUnits.BPS
        # Same missingness regime — that's the structural metadata
        # align_series checks.
        assert s_rate.missingness_policy == s_spread.missingness_policy
        # Step 6 (Decision 3): the bridge now derives frequency from the
        # index; both synthetic series share a cadence, so they agree and
        # strict-mode align passes.
        assert s_rate.frequency is not None
        assert s_rate.frequency == s_spread.frequency

        ss = align_series([s_rate, s_spread])
        assert isinstance(ss, SeriesSet)
        assert s_rate.series_key in ss.series_by_key
        assert s_spread.series_key in ss.series_by_key
        # Per-series units survive the alignment.
        assert ss.units_by_key[s_rate.series_key] == TimeSeriesUnits.PERCENT
        assert ss.units_by_key[s_spread.series_key] == TimeSeriesUnits.BPS


# ===========================================================================
# 6. Lineage hash determinism across re-runs (replay)
# ===========================================================================

class TestPipeline_ReplayDeterminism:
    """Two independent pipeline runs with identical inputs must
    produce the same lineage head_hash.  This is the load-bearing
    cache-correctness invariant: a content-addressed cache can
    reuse a previously-computed artifact only if the hash is
    bit-stable across runs.
    """

    def test_two_runs_same_lineage_hash(self):
        s1 = _bridge_curve_spread(output_field="time_series_spread")
        s2 = _bridge_curve_spread(output_field="time_series_spread")
        # Same hash AT THE BRIDGE — both runs build the same
        # PrimitiveStep with the same identity bits (same params,
        # same tool_config_hash, same output_field, same as_of_date).
        assert s1.lineage.head_hash == s2.lineage.head_hash

        # And same hash AFTER an operator step too — operator
        # extension is deterministic given identical inputs.
        d1 = series_arithmetic(s1, "diff")
        d2 = series_arithmetic(s2, "diff")
        assert d1.lineage.head_hash == d2.lineage.head_hash

    def test_different_output_field_changes_hash(self):
        s_spread = _bridge_curve_spread(output_field="time_series_spread")
        s_zscore = _bridge_curve_spread(output_field="time_series_zscore")
        # Different output_field (an identity bit on PrimitiveStep) →
        # different head_hash.  The cache will not conflate them.
        assert s_spread.lineage.head_hash != s_zscore.lineage.head_hash


# ===========================================================================
# 7. NaN ↔ None survives operator round-trips
# ===========================================================================

class TestPipeline_NaNSurvivesOperators:
    """The forward bridge maps wire ``None`` → ``NaN``; the reverse
    bridge maps ``NaN`` → ``None``.  The operators in between
    propagate ``NaN`` semantically (they don't silently fill or
    drop), so the wire round-trip preserves missing positions."""

    def test_diff_introduces_nan_that_round_trips_to_none(self):
        """The most reliable NaN-source in this pipeline is
        ``series_arithmetic(s, "diff")``: row 0 is undefined and
        becomes ``NaN`` regardless of upstream NaN content.  That
        NaN must reach the wire as ``None``."""
        s_spread = _bridge_curve_spread(output_field="time_series_spread")
        # Pre-condition: the BPS spread series has no NaNs in its
        # display window (production-shaped data).
        assert int(s_spread.payload.isna().sum()) == 0

        s_diff = series_arithmetic(s_spread, "diff")
        # diff(period=1) introduces exactly 1 NaN at row 0.
        assert int(s_diff.payload.isna().sum()) == 1
        assert math.isnan(float(s_diff.payload.iloc[0]))

        wire = artifact_series_to_time_series(s_diff)
        # No silent dropping: row count preserved.
        assert len(wire.rows) == len(s_diff.payload)
        # Number of None rows on the wire equals number of NaN cells
        # in the artifact (the load-bearing round-trip invariant).
        none_count = sum(1 for r in wire.rows if r.value is None)
        assert none_count == int(s_diff.payload.isna().sum())
        # And specifically: row 0 is None on the wire.
        assert wire.rows[0].value is None
        assert all(r.value is not None for r in wire.rows[1:])

    def test_diff_with_period_2_introduces_two_nans_that_round_trip(self):
        """``period=2`` introduces 2 leading NaNs.  Row count + the
        round-trip both preserve them."""
        from shared.operators.series_arithmetic import (
            SeriesArithmeticParams,
        )
        s_spread = _bridge_curve_spread(output_field="time_series_spread")
        s_diff_2 = series_arithmetic(
            s_spread, "diff",
            params=SeriesArithmeticParams(op="diff", period=2),
        )
        assert int(s_diff_2.payload.isna().sum()) == 2
        wire = artifact_series_to_time_series(s_diff_2)
        none_count = sum(1 for r in wire.rows if r.value is None)
        assert none_count == 2
        assert wire.rows[0].value is None
        assert wire.rows[1].value is None
        assert all(r.value is not None for r in wire.rows[2:])


# ===========================================================================
# 8. Structural-metadata refusals fire AT the operator boundary
# ===========================================================================

class TestPipeline_StructuralMetadataRefusalAtOperator:
    """The bridge produces honest structural metadata; operators
    refuse incompatible compositions loudly.  These tests pin the
    refusal locus — if a future bridge change accidentally sets the
    wrong policy, an existing bridge-unit test would catch the
    bridge side, but THIS test confirms the operator-side refusal
    still fires when bridge metadata genuinely diverges.
    """

    def test_align_refuses_mismatched_frequencies_when_strict(self):
        """Forge frequency drift between two bridge outputs by
        rebuilding one Series with a different frequency tag.  The
        operator's strict-mode default refuses."""
        from shared.operators.align_series.operator import (
            AlignSeriesError,
        )

        s_a = _bridge_curve_spread(output_field="time_series_spread")
        s_b_orig = _bridge_curve_spread(output_field="time_series_zscore")
        # Deliberately drift s_b to a frequency that differs from s_a's
        # (now bridge-derived) tag, to test the operator's refusal.
        drift_freq = "M" if s_a.frequency != "M" else "Q"
        s_b_drift = Series(
            series_key=s_b_orig.series_key,
            payload=s_b_orig.payload,
            units=s_b_orig.units,
            frequency=drift_freq,  # drift!
            missingness_policy=s_b_orig.missingness_policy,
            lineage=s_b_orig.lineage,
        )
        with pytest.raises(AlignSeriesError, match="frequency"):
            align_series([s_a, s_b_drift])

    def test_series_arithmetic_refuses_unaligned_indices(self):
        """``series_arithmetic`` refuses unaligned binary inputs and
        instructs the caller to align first.  We construct a
        deliberately misaligned pair by truncating one bridge
        output by a few rows — same primitive, same units, but
        different indices."""
        from shared.operators.series_arithmetic.operator import (
            SeriesArithmeticError,
        )

        s_a = _bridge_curve_spread(output_field="time_series_spread")
        s_b_full = _bridge_curve_spread(
            output_field="time_series_spread", seed=11,
        )
        # Truncate s_b by one row at the head to force index drift
        # while preserving units / missingness / lineage shape.  We
        # rebuild via the Series constructor to keep the artifact
        # frozen-and-valid; any index mismatch alone is enough for
        # the refusal.
        truncated_payload = s_b_full.payload.iloc[1:]
        s_b = Series(
            series_key=s_b_full.series_key + "_truncated",
            payload=truncated_payload,
            units=s_b_full.units,
            frequency=s_b_full.frequency,
            missingness_policy=s_b_full.missingness_policy,
            lineage=s_b_full.lineage,
        )
        # Sanity: indices genuinely differ.
        assert not s_a.payload.index.equals(s_b.payload.index)

        with pytest.raises(SeriesArithmeticError, match="(?i)index"):
            series_arithmetic(s_a, "subtract", s_b)


# ===========================================================================
# 8b. Cross-unit refusals on REAL bridge outputs
# ===========================================================================

class TestPipeline_CrossUnitRefusalAtOperator:
    """The bridge plan + ``docs/architecture/bridge.md`` explicitly say
    the bridge is NOT a unit converter — a primitive returning BPS and
    an operator wanting PERCENT must NOT be papered over.  The bridge
    hands the operator a Series with closed-enum ``units`` attached;
    the operator's strict unit-algebra refuses cross-unit arithmetic.

    This test pins the refusal locus on REAL bridge outputs (Codex P2
    follow-up: prior smoke coverage proved structural-metadata
    refusals fire at the operator boundary for FREQUENCY and INDEX
    mismatches but never for UNIT mismatches, leaving the bridge
    plan's "not a unit converter" claim under-proved at the
    integration-smoke level).
    """

    def test_subtract_refuses_PERCENT_minus_BPS(self):
        """rate_level (PERCENT) vs curve_spread (BPS) on aligned
        indices — series_arithmetic.subtract refuses cross-unit
        arithmetic AT the operator boundary."""
        from shared.operators.series_arithmetic.operator import (
            SeriesArithmeticError,
        )

        s_pct = _bridge_rate_level()  # PERCENT
        s_bps = _bridge_curve_spread(  # BPS
            output_field="time_series_spread",
        )
        # Sanity: the two operands have different units.
        assert s_pct.units == TimeSeriesUnits.PERCENT
        assert s_bps.units == TimeSeriesUnits.BPS

        # Pre-condition: confirm the indices are compatible enough that
        # ``series_arithmetic`` would otherwise accept (i.e. the
        # refusal is genuinely on UNITS, not on shape drift).  If the
        # two synthetic frames don't share an index here, align them
        # explicitly.
        if not s_pct.payload.index.equals(s_bps.payload.index):
            ss = align_series([s_pct, s_bps])
            s_pct = ss.get_series(s_pct.series_key)
            s_bps = ss.get_series(s_bps.series_key)
        assert s_pct.payload.index.equals(s_bps.payload.index)

        # The refusal fires AT the operator with a clear
        # "incompatible units" error pointing the caller toward an
        # explicit unit conversion.
        with pytest.raises(
            SeriesArithmeticError, match="(?i)incompatible units",
        ):
            series_arithmetic(s_pct, "subtract", s_bps)

    def test_add_refuses_PERCENT_plus_BPS(self):
        """Symmetric to the subtract test: ``add`` is the other half of
        the strict unit-algebra requirement that left.units ==
        right.units for additive ops."""
        from shared.operators.series_arithmetic.operator import (
            SeriesArithmeticError,
        )

        s_pct = _bridge_rate_level()
        s_bps = _bridge_curve_spread(output_field="time_series_spread")
        if not s_pct.payload.index.equals(s_bps.payload.index):
            ss = align_series([s_pct, s_bps])
            s_pct = ss.get_series(s_pct.series_key)
            s_bps = ss.get_series(s_bps.series_key)

        with pytest.raises(
            SeriesArithmeticError, match="(?i)incompatible units",
        ):
            series_arithmetic(s_pct, "add", s_bps)

    def test_divide_refuses_cross_unit_series_div_series(self):
        """``divide`` with two Series demands matching units (the
        result type is ``RATIO``, but the two operands' units must
        agree).  ``BPS / PERCENT`` is meaningless and refused."""
        from shared.operators.series_arithmetic.operator import (
            SeriesArithmeticError,
        )

        s_pct = _bridge_rate_level()
        s_bps = _bridge_curve_spread(output_field="time_series_spread")
        if not s_pct.payload.index.equals(s_bps.payload.index):
            ss = align_series([s_pct, s_bps])
            s_pct = ss.get_series(s_pct.series_key)
            s_bps = ss.get_series(s_bps.series_key)

        with pytest.raises(
            SeriesArithmeticError, match="(?i)matching units",
        ):
            series_arithmetic(s_bps, "divide", s_pct)

    def test_subtract_accepts_matching_units_baseline(self):
        """Negative-control sanity: same primitive, same units →
        no refusal.  Confirms the cross-unit refusals above are
        genuinely about UNIT mismatch, not some other coincidence
        (e.g. lineage hash collision, missingness drift).

        The failure mode this guards against: if the unit refusal
        check above passed for the wrong reason, this test would
        ALSO fail — making the cross-unit tests vacuous.
        """
        s_a = _bridge_curve_spread(output_field="time_series_spread")
        s_b = _bridge_curve_spread(
            output_field="time_series_spread",
            short_tenor="2Y", long_tenor="5Y",
        )
        # Same units (both BPS), same fixture-driven indices.
        assert s_a.units == s_b.units == TimeSeriesUnits.BPS
        assert s_a.payload.index.equals(s_b.payload.index)
        # No refusal — the operator accepts.
        diff = series_arithmetic(s_a, "subtract", s_b)
        assert diff.units == TimeSeriesUnits.BPS  # BPS - BPS = BPS


# ===========================================================================
# 9. Per-primitive lineage carries the YAML-aware tool_config_hash
# ===========================================================================

class TestPipeline_ToolConfigHashSurvivesPipeline:
    """The PrimitiveStep's ``tool_config_hash`` is the cache-
    invalidation invariant.  It must be:
      - populated correctly by the bridge,
      - survive operator extensions (they don't overwrite it),
      - inspectable from the final artifact's lineage.
    """

    def test_tool_config_hash_visible_at_pipeline_end(self):
        s_rate = _bridge_rate_level()
        # Pipeline: bridge output → operator → operator → reverse.
        s = series_arithmetic(s_rate, "diff")
        # The PrimitiveStep at the head of the chain still has the
        # tool_config_hash the bridge populated.
        prim = s.lineage.steps[0]
        assert prim.kind == "primitive"
        assert prim.tool_config_hash  # non-empty
        assert len(prim.tool_config_hash) == 64  # sha256 hex

        # And the hash matches what we'd recompute from the loaded
        # ToolConfig — proves end-to-end that the bridge didn't
        # silently use a stale or different config.
        from rates_agent.ois.tools.rate_level import (
            CONFIG_PATH as RATE_LEVEL_CONFIG_PATH,
        )
        cfg = load_tool_config(RATE_LEVEL_CONFIG_PATH)
        assert prim.tool_config_hash == cfg.conventions_hash()
