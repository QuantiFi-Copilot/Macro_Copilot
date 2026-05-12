"""tests/state/test_hash_stability.py — cross-version determinism gate.

This file pins specific hash values for two canonical inputs:

  1. The lineage step hash (``shared.artifacts.lineage._compute_step_hash``)
     for two representative shapes — a ``FetchStep``-style params dict and
     an ``OperatorStep``-style params dict.
  2. The ingestion content hash (``ingestion.hashing.compute_normalized_data_hash``)
     for a small canonical DataFrame.

These tests run in CI on a matrix of Python 3.11 + 3.12.  If the recipe
ever drifts between Python versions (e.g. via numpy / pandas / json
representation changes), the pinned values stop matching on the affected
version and CI fails on that job.

Pinning the hash values is the *whole point*: any change to the
canonicalization recipe is a breaking change to every persisted lineage
hash in the system, so it must be a deliberate, reviewed update to these
test vectors, not a silent drift.

Closes ``docs/technical_debt.md`` item #20.
"""

from __future__ import annotations

import datetime
import json as _json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest


# Make sibling packages importable.  ``conftest.py`` at
# ``Macro_Copilot/tests/`` already inserts ``Macro_Copilot/`` into
# ``sys.path``; this is a belt-and-braces add-on in case the test is
# run via a different entry point.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# LINEAGE LAYER — _compute_step_hash + _canonical_json + _canonicalize_for_hash
# ============================================================================

from shared.artifacts.lineage import (  # noqa: E402
    _canonical_json,
    _canonicalize_for_hash,
    _compute_step_hash,
)


class TestLineageHashStability:
    """Pinned hash values for the lineage step hash.

    If any of these pinned values changes, the lineage hash recipe has
    drifted and every persisted lineage object in the system would
    produce a different hash.  That is a breaking change requiring a
    deliberate update to these vectors and a follow-up audit of any
    cached lineage state.
    """

    # ----- Test vector 1: FetchStep-style params ------------------------
    EXPECTED_FETCH_HASH = (
        "9421006aa846374cb4cc0db796077e609459356c35605567ee52ec1e9945f821"
    )

    def test_fetch_step_hash_pinned(self) -> None:
        """A representative ``FetchStep`` params dict hashes to a known value."""
        h = _compute_step_hash(
            kind="fetch",
            name="fetch_single_tenor",
            version="1.0.0",
            params={
                "curve_family": "UST",
                "tenor": "10Y",
                "field_name": "YLD_YTM_MID",
                "start_date": datetime.date(2020, 1, 1),
            },
            input_hashes=(),
        )
        assert h == self.EXPECTED_FETCH_HASH, (
            "Lineage hash recipe has drifted.  This is a breaking change "
            "to every persisted lineage hash.  If intentional, update the "
            "pinned value here AND audit any cached lineage state."
        )

    # ----- Test vector 2: OperatorStep-style params ---------------------
    EXPECTED_OPERATOR_HASH = (
        "f31ac6790facb58c6992a5898b306688d235fa3cd7fb1b05863c9aec7a4ee437"
    )

    def test_operator_step_hash_pinned(self) -> None:
        """A representative ``OperatorStep`` params dict with two upstream
        input hashes hashes to a known value."""
        h = _compute_step_hash(
            kind="operator",
            name="align_series",
            version="1.0.0",
            params={"how": "inner", "min_overlap_days": 252},
            input_hashes=("aaaa", "bbbb"),
        )
        assert h == self.EXPECTED_OPERATOR_HASH

    # ----- Key-order invariance -----------------------------------------
    def test_hash_invariant_to_param_key_order(self) -> None:
        """Two dicts with the same keys in different insertion order
        produce the same hash."""
        h1 = _compute_step_hash(
            kind="operator",
            name="x",
            version="1.0.0",
            params={"a": 1, "b": 2, "c": 3},
            input_hashes=(),
        )
        h2 = _compute_step_hash(
            kind="operator",
            name="x",
            version="1.0.0",
            params={"c": 3, "a": 1, "b": 2},
            input_hashes=(),
        )
        assert h1 == h2

    # ----- Input-hash order invariance ----------------------------------
    def test_hash_invariant_to_input_hash_order(self) -> None:
        """``input_hashes`` is sorted internally; caller ordering does not
        affect the hash."""
        h1 = _compute_step_hash(
            kind="operator",
            name="x",
            version="1.0.0",
            params={},
            input_hashes=("ccc", "aaa", "bbb"),
        )
        h2 = _compute_step_hash(
            kind="operator",
            name="x",
            version="1.0.0",
            params={},
            input_hashes=("aaa", "bbb", "ccc"),
        )
        assert h1 == h2

    # ----- Param value sensitivity --------------------------------------
    def test_hash_changes_when_value_changes(self) -> None:
        """Changing a single param value flips the hash."""
        h1 = _compute_step_hash(
            kind="fetch", name="x", version="1.0.0",
            params={"tenor": "10Y"}, input_hashes=(),
        )
        h2 = _compute_step_hash(
            kind="fetch", name="x", version="1.0.0",
            params={"tenor": "5Y"}, input_hashes=(),
        )
        assert h1 != h2


class TestPhase1ClosedFamilyHashStability:
    """Phase 1 PR 12 — pinned vectors for the new TradeSet closed-
    family member and the ``construct_trades`` operator step.

    Adding a new artifact type to the closed family does NOT change
    any existing hash recipe — these pinned vectors run alongside
    the original PR 2 vectors and the CI matrix exercises both on
    Python 3.11 + 3.12.  If either set of vectors drifts, the
    affected PR landed an unintended canonicalization change.
    """

    # ----- construct_trades operator step pinned hash -----
    # Recorded against the canonical step shape the operator emits:
    # fixed-horizon V1 with a two-leg long-short composition and
    # five trades.  If this value moves, the construct_trades step
    # params recipe has drifted — investigate before bumping.
    EXPECTED_CONSTRUCT_TRADES_HASH = (
        "a2b5c59ae93893afac701e5ffecc463cdb348dc040d22ede1f8614b8a424b460"
    )

    def test_construct_trades_step_hash_pinned(self) -> None:
        h = _compute_step_hash(
            kind="operator",
            name="construct_trades",
            version="1.0.0",
            params={
                "holding_rule": "fixed_horizon",
                "holding_window_days": 20,
                "leg_construction_rule": "equal_weight_signed",
                "methodology_policy": "fixed_horizon_v1",
                "n_trades": 5,
                "legs": [
                    {
                        "instrument_key": "UST.10Y.yield_mid",
                        "weight": 1.0,
                        "side": "long",
                        "units": "bps",
                    },
                    {
                        "instrument_key": "UST.2Y.yield_mid",
                        "weight": -1.0,
                        "side": "short",
                        "units": "bps",
                    },
                ],
                "source_event_key": "UST.10Y.zscore",
            },
            input_hashes=("a" * 64,),
        )
        assert h == self.EXPECTED_CONSTRUCT_TRADES_HASH, (
            "construct_trades operator step hash has drifted.  This "
            "is a closed-family-extension contract break: every "
            "TradeSet artifact produced by V1 would re-hash to a "
            "different value.  Investigate the step-param recipe "
            "before bumping the pinned vector."
        )

    def test_tradeset_round_trip_preserves_lineage_head(self) -> None:
        """A TradeSet built from records and back round-trips with
        an unchanged lineage head hash.  The records form is
        load-bearing for storage; if it ever stops round-tripping
        cleanly, the artifact-store would silently corrupt
        persisted TradeSets."""
        import pandas as pd

        from shared.artifacts.lineage import FetchStep, Lineage
        from shared.artifacts.trades import LegSpec, Trade, TradeSet

        step = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={
                "curve_family": "UST", "tenor": "10Y",
                "test": "pinned_hash_tradeset",
            },
        )
        leg_a = LegSpec(
            instrument_key="UST.10Y.yield_mid", weight=1.0,
            side="long", units="bps",
        )
        leg_b = LegSpec(
            instrument_key="UST.2Y.yield_mid", weight=-1.0,
            side="short", units="bps",
        )
        trade = Trade(
            entry_date=pd.Timestamp("2024-01-08"),
            exit_date=pd.Timestamp("2024-01-29"),
            leg_specs=(leg_a, leg_b),
        )
        original = TradeSet(
            trades=(trade,),
            source_event_key="UST.10Y.zscore",
            methodology_policy="fixed_horizon_v1",
            lineage=Lineage.from_steps([step]),
        )
        records = original.to_records()
        recovered = TradeSet.from_records(
            records,
            source_event_key=original.source_event_key,
            methodology_policy=original.methodology_policy,
            lineage=original.lineage,
        )
        assert recovered.lineage.head_hash == original.lineage.head_hash


class TestPhase1PR19PinnedHashes:
    """Phase 1 PR 19/20 — pinned lineage-step hashes for the 3 new
    primitives + the backtest archetype's terminal operator step.

    These vectors guard against silent drift in:
      (a) the bridge's ``PrimitiveStep`` shape for Panel-emitting
          primitives (PR 20's parallel-bridge addition),
      (b) the bridge's ``PrimitiveStep`` shape for TimeSeries-emitting
          primitives that came via the standard
          ``tool_output_to_artifact_series`` path (breakeven_inflation),
      (c) the operator-step shape for the BacktestReport terminal
          (summarize_trades) — anchors the backtest workflow's
          replay-determinism contract.

    Closed-family-extension discipline: every new primitive +
    operator gets a pinned vector here.  If any value drifts, the
    PR that caused it touched a hash recipe — fix the recipe drift
    OR deliberately update the pinned value with rationale.

    Hash anchoring
    --------------
    Each vector uses a canonical, hand-picked parameter shape that
    matches what the production code path emits.  The
    ``tool_config_hash`` value is a fixed placeholder (NOT the live
    YAML's conventions_hash) because:

      - The point of this test is to detect ``_compute_step_hash``
        recipe drift, not to couple the test to specific YAML
        content.  If YAML defaults change, the test still passes
        as long as the canonicalization recipe is stable.
      - The live YAML's conventions_hash is itself an output of
        ``ToolConfig.conventions_hash()`` — pinning that here
        would create a brittle two-layer coupling.
    """

    # ----- PR 19 primitive: build_sovereign_yield_panel_tool -----
    # Bridge's PrimitiveStep for a canonical two-leg sovereign Panel
    # call (UST 2Y + USD_TIPS 10Y over a 6-month window).
    EXPECTED_SOVEREIGN_YIELD_PANEL_HASH = (
        "f5af6837fc8c4080680b779a9dd0460dd67e814d2234c75d6ef83f22e221c26a"
    )

    def test_sovereign_yield_panel_bridge_step_hash_pinned(self) -> None:
        h = _compute_step_hash(
            kind="primitive",
            name="build_sovereign_yield_panel_tool",
            version="1.0.0",
            params={
                "input_params": {
                    "legs": [
                        {
                            "curve_family": "UST",
                            "tenor": "2Y",
                            "field_name": None,
                        },
                        {
                            "curve_family": "USD_TIPS",
                            "tenor": "10Y",
                            "field_name": None,
                        },
                    ],
                    "start_date": "2024-01-01",
                    "end_date": "2024-06-30",
                    "missing_data_policy": None,
                },
                "tool_config_hash": "PINNED_PLACEHOLDER_HASH_PR20",
                "output_field": "panel",
                "as_of_date": "2024-06-28",
            },
            input_hashes=(),
        )
        assert h == self.EXPECTED_SOVEREIGN_YIELD_PANEL_HASH, (
            "build_sovereign_yield_panel_tool primitive-step hash has "
            "drifted.  This is a closed-family-extension contract "
            "break: every Panel produced by this primitive would re-"
            "hash to a different value.  Investigate the bridge's "
            "PrimitiveStep recipe before bumping the pinned vector."
        )

    # ----- PR 19 primitive: compute_financing_rate_tool -----
    # Bridge's PrimitiveStep for a canonical overnight_index_proxy call.
    EXPECTED_FINANCING_RATE_HASH = (
        "a1b3b5751af549070ef8a132092ca1ebe0d49b15b8cd87d303bdc401e8539e00"
    )

    def test_financing_rate_bridge_step_hash_pinned(self) -> None:
        h = _compute_step_hash(
            kind="primitive",
            name="compute_financing_rate_tool",
            version="1.0.0",
            params={
                "input_params": {
                    "method": "overnight_index_proxy",
                    "start_date": "2024-01-01",
                    "end_date": "2024-06-30",
                    "constant_rate_pct": None,
                    "proxy_curve": "USD_SOFR_OIS",
                    "day_count_basis": None,
                    "calendar": None,
                },
                "tool_config_hash": "PINNED_PLACEHOLDER_HASH_PR20",
                "output_field": "panel",
                "as_of_date": "2024-06-28",
            },
            input_hashes=(),
        )
        assert h == self.EXPECTED_FINANCING_RATE_HASH, (
            "compute_financing_rate_tool primitive-step hash has "
            "drifted.  Investigate before bumping."
        )

    # ----- PR 19 primitive: calculate_breakeven_inflation_tool -----
    # Bridge's PrimitiveStep for a canonical UST nominal / USD_TIPS
    # breakeven call at the 10Y matched tenor.
    EXPECTED_BREAKEVEN_INFLATION_HASH = (
        "8bfc81cbf1e81f79bab9b777aebab30f1218925cb2ccea75110c6c218aefd7a8"
    )

    def test_breakeven_inflation_bridge_step_hash_pinned(self) -> None:
        h = _compute_step_hash(
            kind="primitive",
            name="calculate_breakeven_inflation_tool",
            version="1.0.0",
            params={
                "input_params": {
                    "nominal_curve_family": "UST",
                    "real_curve_family": "USD_TIPS",
                    "tenor": "10Y",
                    "convention": None,
                    "lookback_days": 365,
                    "nominal_field_name": "YLD_YTM_MID",
                    "real_field_name": "YLD_YTM_MID",
                },
                "tool_config_hash": "PINNED_PLACEHOLDER_HASH_PR20",
                "output_field": "time_series_breakeven",
                "as_of_date": "2024-06-28",
            },
            input_hashes=(),
        )
        assert h == self.EXPECTED_BREAKEVEN_INFLATION_HASH, (
            "calculate_breakeven_inflation_tool primitive-step hash "
            "has drifted.  Investigate before bumping."
        )

    # ----- Backtest archetype terminal: summarize_trades -----
    # OperatorStep for the terminal node of the backtest workflow.
    # The summary Panel (hit_rate, mean_pnl, Sharpe, drawdown, p10/50/90)
    # is what evaluate_trades' P&L panel reduces to via summarize_trades.
    # The pinned vector uses a synthetic upstream input_hash (the
    # canonical convention from PR 12's construct_trades vector).
    EXPECTED_SUMMARIZE_TRADES_TERMINAL_HASH = (
        "9dd7ed2f6c58455039387859d05cff908939395cfd4448105dbfaba101325948"
    )

    def test_summarize_trades_terminal_step_hash_pinned(self) -> None:
        h = _compute_step_hash(
            kind="operator",
            name="summarize_trades",
            version="1.0.0",
            params={
                "aggregation": "final_pnl",
                "trading_days_per_year": 252,
                "holding_window_days": 20,
                "n_trades": 5,
                "metric_columns": [
                    "hit_rate", "mean_pnl", "sharpe_annualized",
                    "max_drawdown", "p10_pnl", "p50_pnl", "p90_pnl",
                ],
                "notes": [],
            },
            input_hashes=("b" * 64,),
        )
        assert h == self.EXPECTED_SUMMARIZE_TRADES_TERMINAL_HASH, (
            "summarize_trades terminal-step hash has drifted.  This "
            "is the BacktestReport terminal's anchor — every backtest "
            "workspace's replay-determinism guarantee depends on "
            "this hash being stable.  Investigate before bumping."
        )


class TestCanonicalJsonShape:
    """The intermediate JSON form has the exact shape we promise."""

    def test_nested_dict_canonical_form(self) -> None:
        """Sorted keys at every level, compact separators, no whitespace."""
        out = _canonical_json({"b": 2.5, "a": [1, 2, 3], "c": {"nested": True}})
        assert out == '{"a":[1,2,3],"b":2.5,"c":{"nested":true}}'

    def test_date_isoformat_serialization(self) -> None:
        """``datetime.date`` round-trips via ``.isoformat()``."""
        out = _canonical_json({"d": datetime.date(2024, 1, 15)})
        assert out == '{"d":"2024-01-15"}'

    def test_datetime_isoformat_serialization(self) -> None:
        """``datetime.datetime`` round-trips via ``.isoformat()``."""
        out = _canonical_json({"d": datetime.datetime(2024, 1, 15, 14, 30, 0)})
        assert out == '{"d":"2024-01-15T14:30:00"}'

    def test_unicode_passes_through_as_utf8(self) -> None:
        """``ensure_ascii=False`` keeps non-ASCII characters as their
        natural UTF-8 representation rather than ``\\uXXXX`` escapes."""
        out = _canonical_json({"label": "BTP-Bund spread € → bps"})
        assert "€" in out
        assert "→" in out
        # Round-trip stable: the JSON parses back to the same dict.
        parsed = _json.loads(out)
        assert parsed == {"label": "BTP-Bund spread € → bps"}


class TestCanonicalizeRejection:
    """Inputs that have no canonical form are rejected loudly."""

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValueError, match="NaN"):
            _canonical_json({"x": float("nan")})

    def test_positive_infinity_rejected(self) -> None:
        with pytest.raises(ValueError, match="Infinity"):
            _canonical_json({"x": float("inf")})

    def test_negative_infinity_rejected(self) -> None:
        with pytest.raises(ValueError, match="Infinity"):
            _canonical_json({"x": float("-inf")})

    def test_non_string_dict_key_rejected(self) -> None:
        with pytest.raises(TypeError, match="string"):
            _canonical_json({1: "value"})

    def test_set_rejected(self) -> None:
        with pytest.raises(TypeError):
            _canonical_json({"x": {1, 2, 3}})

    def test_object_without_canonical_form_rejected(self) -> None:
        class Opaque:
            pass

        with pytest.raises(TypeError, match="Cannot canonicalize"):
            _canonical_json({"x": Opaque()})


class TestNumpyAndPandasCanonicalization:
    """NumPy scalars and Pandas timestamps unwrap to stable Python natives.

    These are the values most likely to drift across NumPy / Pandas
    versions if naively serialized via ``str()``.  Explicit unwrap via
    ``.item()`` and ``.isoformat()`` pins them to the Python-native
    representation which IS stable across 3.x.
    """

    def test_numpy_int_scalar_unwraps(self) -> None:
        np = pytest.importorskip("numpy")
        out = _canonical_json({"n": np.int64(42)})
        assert out == '{"n":42}'

    def test_numpy_float_scalar_unwraps(self) -> None:
        np = pytest.importorskip("numpy")
        out = _canonical_json({"x": np.float64(2.5)})
        assert out == '{"x":2.5}'

    def test_numpy_bool_scalar_unwraps(self) -> None:
        np = pytest.importorskip("numpy")
        out = _canonical_json({"flag": np.bool_(True)})
        assert out == '{"flag":true}'

    def test_pandas_timestamp_unwraps(self) -> None:
        ts = pd.Timestamp("2024-01-15T14:30:00")
        out = _canonical_json({"t": ts})
        # pandas Timestamps use isoformat-compatible serialization; the
        # exact suffix may include ``T`` and minute/second padding.
        parsed = _json.loads(out)
        assert parsed["t"].startswith("2024-01-15")


# ============================================================================
# INGESTION LAYER — compute_normalized_data_hash
# ============================================================================

from ingestion.hashing import compute_normalized_data_hash  # noqa: E402


class TestIngestionHashStability:
    """Pinned values for the ingestion-side content hash."""

    EXPECTED_TWO_ROW_HASH = (
        "43bb4be8c156011ac45e8374d94aa10fdb91b94466853396d6422dd33f4c852e"
    )
    EXPECTED_EMPTY_DF_HASH = (
        "862fde8d130e81926af8336ab6556aa005a95e1ce9deb7f6c467de9a5600660f"
    )

    @staticmethod
    def _canonical_df() -> pd.DataFrame:
        """Two-row canonical DataFrame used by the pinned-hash test."""
        return pd.DataFrame({
            "ticker": ["USGG10Y Index", "GTBPS10Y Govt"],
            "trade_date": [
                pd.Timestamp("2024-01-15"),
                pd.Timestamp("2024-01-16"),
            ],
            "field_name": ["YLD_YTM_MID", "YLD_YTM_MID"],
            "field_value": [4.1234, 4.0567],
            "extracted_at": [
                pd.Timestamp("2024-01-17 12:00:00"),
                pd.Timestamp("2024-01-17 12:00:00"),
            ],
        })

    def test_canonical_two_row_hash_pinned(self) -> None:
        """The canonical two-row DataFrame hashes to a known value."""
        h = compute_normalized_data_hash(self._canonical_df())
        assert h == self.EXPECTED_TWO_ROW_HASH, (
            "Ingestion hash recipe has drifted.  Every dedup decision in "
            "load_audit is now meaningless against historical hashes.  If "
            "intentional, update the pinned value here AND audit "
            "load_audit.source_file_hash continuity."
        )

    def test_empty_dataframe_hash_pinned(self) -> None:
        """An empty DataFrame with the four core columns hashes to a
        known value (i.e. the hash function does not raise on empty)."""
        df = pd.DataFrame({
            "ticker": [],
            "trade_date": [],
            "field_name": [],
            "field_value": [],
        })
        h = compute_normalized_data_hash(df)
        assert h == self.EXPECTED_EMPTY_DF_HASH

    def test_lineage_columns_excluded(self) -> None:
        """Run-variant lineage columns (``extracted_at``, ``git_commit_hash``,
        ``playbook_hash``, ...) do NOT affect the hash."""
        df1 = self._canonical_df()
        df2 = df1.copy()
        df2["extracted_at"] = pd.Timestamp("2024-12-31 23:59:59")
        df2["git_commit_hash"] = "abc123"
        df2["playbook_hash"] = "deadbeef"
        df2["extractor_version"] = "v9.9.9"

        h1 = compute_normalized_data_hash(df1)
        h2 = compute_normalized_data_hash(df2)
        assert h1 == h2

    def test_row_order_invariance(self) -> None:
        """Reordering rows in the input does not change the hash
        (``_build_normalized_hash_dataframe`` sorts rows internally)."""
        df = self._canonical_df()
        reversed_df = df.iloc[::-1].reset_index(drop=True)
        assert compute_normalized_data_hash(df) == compute_normalized_data_hash(
            reversed_df
        )

    def test_column_order_invariance(self) -> None:
        """Reordering columns in the input does not change the hash."""
        df = self._canonical_df()
        cols = list(df.columns)
        reordered = df[list(reversed(cols))]
        assert compute_normalized_data_hash(df) == compute_normalized_data_hash(
            reordered
        )

    def test_value_sensitivity(self) -> None:
        """Changing a single field_value flips the hash."""
        df1 = self._canonical_df()
        df2 = df1.copy()
        df2.loc[0, "field_value"] = 4.1235  # 1-bp shift
        assert compute_normalized_data_hash(df1) != compute_normalized_data_hash(
            df2
        )


# ============================================================================
# CROSS-CUTTING — both layers stable in the same process
# ============================================================================


class TestPythonVersionInvariance:
    """A sanity check that both hashes are stable within the CURRENT
    Python process.  Cross-version stability is enforced by running this
    test on the Python 3.11 + 3.12 matrix in CI; the pinned values above
    are what tie the two CI legs to identical output.
    """

    def test_lineage_hash_idempotent_in_process(self) -> None:
        params = {"tenor": "10Y", "field_name": "YLD_YTM_MID", "lookback_days": 252}
        h1 = _compute_step_hash(
            kind="operator", name="zscore_custom", version="1.0.0",
            params=params, input_hashes=("upstream-hash",),
        )
        h2 = _compute_step_hash(
            kind="operator", name="zscore_custom", version="1.0.0",
            params=params, input_hashes=("upstream-hash",),
        )
        assert h1 == h2

    def test_ingestion_hash_idempotent_in_process(self) -> None:
        df = TestIngestionHashStability._canonical_df()
        h1 = compute_normalized_data_hash(df)
        h2 = compute_normalized_data_hash(df.copy())
        assert h1 == h2

    def test_python_version_reported_for_diagnostics(self, capsys) -> None:
        """Emit the running Python version so a failure log on one matrix
        leg makes the version comparison obvious in CI output.  The test
        does NOT assert a minimum version — that is the project's
        responsibility (``pyproject.toml requires-python = ">=3.11"``);
        this test runs under whatever Python invoked pytest."""
        major, minor = sys.version_info[:2]
        print(f"running on Python {major}.{minor}")
        # Sanity: version_info is well-formed.
        assert major >= 3


# Defensive: ensure no test inadvertently calls the math module just to
# verify the import landed.
assert math is not None
