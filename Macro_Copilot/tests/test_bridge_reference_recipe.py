"""tests/test_bridge_reference_recipe.py — bridge worked example.

Phase 1B Work Item 5.  The canonical end-to-end recipe new bridge
authors and orchestrator authors should read first.

Why a runnable test, not a markdown snippet
-------------------------------------------
The repo's "tests are docs" pattern: a runnable example is
self-verifying — if the bridge contract drifts, this file's
assertions fail in CI, surfacing the drift exactly where the
recipe lives.  A markdown snippet would silently rot.

What this recipe shows
----------------------
A 3-stage pipeline that exercises every load-bearing bridge
contract once:

  1. **Forward bridge** — invoke a primitive
     (``calculate_ois_curve_spread`` against synthetic data with a
     mocked DB fetcher), then lift the canonical
     ``time_series_spread`` field into a typed ``Series`` artifact.

  2. **Operator step** — run ``series_arithmetic(series, "diff")``.
     Lineage chain extends with an ``OperatorStep``.

  3. **Reverse bridge** — demote the operator output back to wire
     ``TimeSeries`` for serialization.  Description is
     auto-populated with a linear lineage summary.

Walking through the assertions in order is the fastest way to
internalise:

  - which arguments ``tool_output_to_artifact_series`` requires,
  - what the lineage chain looks like at each stage,
  - how ``NaN`` ↔ ``None`` works through the operator boundary,
  - what the wire description summary looks like,
  - how to round-trip back to wire format.

For the broader pipeline-shape coverage (multi-step chains, cross-
primitive composition, replay determinism, structural-metadata
refusals, binary-operator auxiliary-lineage identity), see
``tests/test_bridge_pipeline_smoke.py``.

For the bridge's design rationale (the why behind each contract),
see ``docs/architecture/bridge.md``.
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
    Series,
    TimeSeriesUnits,
)
from shared.artifacts.adapters import (
    artifact_series_to_time_series,
    tool_output_to_artifact_series,
)
from shared.config import clear_tool_config_cache, load_tool_config
from shared.operators.series_arithmetic import series_arithmetic


# ---------------------------------------------------------------------------
# Recipe fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    """Process-wide ToolConfig cache is reset between tests so each
    invocation pays the YAML load cost once and identity hashes stay
    deterministic across the file's tests."""
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


class _FrozenDate(date):
    """Frozen ``date.today()`` for the primitive's fetch-window
    computation.  Same pattern every per-tool test uses."""
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _synthetic_curve_spread_df() -> pd.DataFrame:
    """A 600-business-day frame with two tenors (2Y + 10Y).  Returned
    in the long-format shape ``fetch_tenor_pair`` produces, so we can
    drop it in via patch and the primitive's pipeline runs unchanged."""
    bdays = pd.bdate_range(
        _FrozenDate._frozen_value - timedelta(days=600 * 2),
        _FrozenDate._frozen_value,
    )[-600:]
    rs = np.random.RandomState(11)

    def _series(tenor_label: str, init: float, drift: float) -> pd.DataFrame:
        n = len(bdays)
        v = np.linspace(init, init + drift, n) + rs.randn(n) * 0.012
        return pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "tenor": tenor_label,
            "field_value": v,
        })

    return pd.concat(
        [_series("2Y", 4.20, -0.10), _series("10Y", 4.50, +0.05)],
        ignore_index=True,
    ).sort_values(["trade_date", "tenor"]).reset_index(drop=True)


def _invoke_curve_spread() -> tuple:
    """Step 0: invoke OIS curve_spread against synthetic data.

    Returns the raw output dict + Pydantic output class + Input +
    ToolConfig — exactly the four pieces the bridge's high-level
    wrapper needs to lift the output into a ``Series``.
    """
    from rates_agent.ois.tools.curve_spread import (
        CONFIG_PATH,
        calculate_ois_curve_spread,
        OISCurveSpreadInput,
    )
    from rates_agent.ois.tools.curve_spread.schemas import (
        OISCurveSpreadOutput,
    )

    raw_df = _synthetic_curve_spread_df()
    params = OISCurveSpreadInput(
        curve_family="USD_SOFR_OIS",
        short_tenor="2Y",
        long_tenor="10Y",
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


# ===========================================================================
# Reference recipe — single test that walks through the full pipeline
# ===========================================================================

class TestBridgeReferenceRecipe:
    """The canonical end-to-end recipe.  One method per pipeline
    stage; assertions document the bridge contract at each step."""

    def test_stage_1_forward_bridge_lifts_primitive_output_to_series(self):
        # Step 0: invoke the primitive.  In production this is a real
        # DB-backed call; here we mock the fetcher with synthetic data
        # so the recipe is offline-runnable.
        tool_output, OutClass, params, cfg = _invoke_curve_spread()

        # Step 1: forward bridge.  The high-level wrapper takes:
        #   - tool_output       : raw dict the primitive returned
        #   - output_class      : the primitive's *Output schema (catches
        #                          malformed dicts as a clean Pydantic
        #                          ValidationError instead of a downstream
        #                          KeyError)
        #   - output_field      : which TimeSeries-typed field to extract
        #                          (the primitive may emit several)
        #   - tool_name         : the primitive's MCP wrapper name (NOT
        #                          the YAML's ``tool.name``; see PR #69)
        #   - tool_config       : already-loaded ToolConfig — bridge
        #                          computes ``conventions_hash()`` for the
        #                          PrimitiveStep's identity bit
        #   - params            : the primitive's *Input model
        series = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )

        # The bridge produced a typed Series artifact.
        assert isinstance(series, Series)

        # Identity: series_key carries through verbatim from
        # TimeSeries.series_name.  The wire convention says
        # series_name is a stable lower-snake-case identifier, so it
        # slots into the artifact 1:1 without re-derivation.
        assert series.series_key.endswith("_ois_spread")

        # Closed-enum units preserved from the wire.
        assert series.units == TimeSeriesUnits.BPS

        # Auto-derived missingness policy: CleanSingleSeriesV1 with
        # ffill_limit from the YAML, drop_nan=True and dedup_keep="last"
        # pinned explicitly to match clean_single_series invariants.
        assert isinstance(series.missingness_policy, CleanSingleSeriesV1)
        assert series.missingness_policy.ffill_limit == 5
        assert series.missingness_policy.drop_nan is True
        assert series.missingness_policy.dedup_keep == "last"

        # Lineage chain has exactly one step — the PrimitiveStep that
        # records the primitive's identity bits.
        assert len(series.lineage.steps) == 1
        prim = series.lineage.steps[0]
        assert prim.kind == "primitive"
        assert prim.name == "calculate_ois_curve_spread_tool"
        # All four identity bits captured on the step:
        assert prim.params == params.model_dump(mode="json")
        assert prim.tool_config_hash == cfg.conventions_hash()
        assert prim.output_field == "time_series_spread"
        assert prim.as_of_date == "2026-04-30"

    def test_stage_2_operator_extends_lineage_chain(self):
        # Stage 1 setup: forward bridge.
        tool_output, OutClass, params, cfg = _invoke_curve_spread()
        series = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )

        # Stage 2: run an operator.  ``diff`` is unary (no right
        # operand); units pass through (BPS in → BPS out).
        diffed = series_arithmetic(series, "diff")

        # Lineage chain grew by exactly one — the operator's
        # OperatorStep was appended.
        assert len(diffed.lineage.steps) == 2
        assert [step.kind for step in diffed.lineage.steps] == [
            "primitive", "operator",
        ]
        assert diffed.lineage.steps[1].name == "series_arithmetic"

        # Operator preserved units (diff is unary; BPS - BPS = BPS).
        assert diffed.units == TimeSeriesUnits.BPS

        # ``diff(period=1)`` introduces exactly 1 NaN at row 0 (no
        # previous value to diff against).  The bridge's NaN ↔ None
        # contract means this NaN will round-trip to None on the wire
        # — verified in stage 3 below.
        assert int(diffed.payload.isna().sum()) == 1
        assert math.isnan(float(diffed.payload.iloc[0]))

    def test_stage_3_reverse_bridge_serializes_to_wire(self):
        # Stage 1 + 2 setup: forward bridge → operator.
        tool_output, OutClass, params, cfg = _invoke_curve_spread()
        series = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )
        diffed = series_arithmetic(series, "diff")

        # Stage 3: reverse bridge.  Demotes the artifact back to a
        # wire-compatible TimeSeries for serialization to the LLM /
        # frontend / future REST.
        wire = artifact_series_to_time_series(diffed)

        # Identity preserved: series_key ↔ series_name byte-identical.
        assert wire.series_name == diffed.series_key

        # Units preserved.
        assert wire.units == TimeSeriesUnits.BPS

        # Description is the lineage summary by default — linear
        # primary chain, oldest-to-newest, ``→`` separator.
        assert wire.description == (
            "derived: calculate_ois_curve_spread_tool → series_arithmetic"
        )

        # Row count preserved (no silent dropping).
        assert len(wire.rows) == len(diffed.payload)

        # NaN ↔ None mapping applied at the wire boundary.
        # Row 0 (the diff's leading NaN) is None on the wire.
        assert wire.rows[0].value is None
        # Subsequent rows are real numeric diffs.
        assert all(r.value is not None for r in wire.rows[1:])

    def test_recipe_runs_end_to_end(self):
        """Same as the three stage tests above, but in one
        sequential walk so a reader can see the full happy path
        without flipping between methods."""
        # ---- Stage 0: invoke primitive -------------------------------
        tool_output, OutClass, params, cfg = _invoke_curve_spread()

        # ---- Stage 1: forward bridge ---------------------------------
        series = tool_output_to_artifact_series(
            tool_output,
            output_class=OutClass,
            output_field="time_series_spread",
            tool_name="calculate_ois_curve_spread_tool",
            tool_config=cfg,
            params=params,
        )

        # ---- Stage 2: operator ---------------------------------------
        diffed = series_arithmetic(series, "diff")

        # ---- Stage 3: reverse bridge ---------------------------------
        wire = artifact_series_to_time_series(diffed)

        # End-state: a wire TimeSeries that carries the full primary
        # lineage in its description AND the BPS units that survived
        # both operator passes.
        assert wire.units == TimeSeriesUnits.BPS
        assert wire.description.startswith("derived: ")
        assert "series_arithmetic" in wire.description
        assert len(wire.rows) > 0
