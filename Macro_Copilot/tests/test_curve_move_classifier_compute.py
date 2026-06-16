"""
test_curve_move_classifier_compute.py — Unit tests for the
config-driven curve-move classifier (formerly known as curve_regime).

Mirrors the structure of ``test_curve_spread_compute.py``:

  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output, with the new
     ``classification`` / ``description`` field names.
  3. Convention overrides actually change behaviour:
       - parallel_threshold_bps:  threshold edge cases flip
       - move_threshold_bps:      flat-flat detection toggles
       - ffill_limit_days:        wired through to pivot_and_align
       - allowed_lookback_periods: gates which lookback labels work
  4. The honest placeholder for ``avg_change_method``: setting it to
     anything other than ``arithmetic_mean`` raises NotImplementedError
     with a pointer to ``methodology.planned_extensions``.
  5. The shared primitive ``classify_curve_move`` agrees with the
     6-quadrant taxonomy on hand-computed cases.

Tests are fully offline — DB fetcher mocked, no live data needed.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.curve_move_classifier import (
    CONFIG_PATH,
    classify_curve_move_compute,
)
from rates_agent.sovereign_bonds.tools.curve_move_classifier.schemas import (
    CurveMoveInput,
)
from shared.analytics.curve_move import (
    BEAR_FLATTENER,
    BEAR_STEEPENER,
    BULL_FLATTENER,
    BULL_STEEPENER,
    CURVE_MOVE_TAGS,
    PARALLEL_SHIFT,
    TWIST,
    classify_curve_move,
)
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _synthetic_raw_df(
    *,
    front_change_pct: float,
    back_change_pct: float,
    front_init: float = 4.0,
    back_init: float = 4.5,
    days: int = 80,
    frozen_today: date = date(2026, 4, 30),
    front_tenor: str = "2Y",
    back_tenor: str = "10Y",
) -> pd.DataFrame:
    """Build a 2-tenor long-format DataFrame where the FIRST and LAST
    observations differ by exactly the requested per-tenor amount.

    Linear interpolation between init and final means a 1-day lookback
    sees ``change/days`` per day, while a 22-day lookback sees
    ``change*22/days``.  Tests pick params + lookback to land on
    specific classification boundaries.
    """
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    front_series = np.linspace(front_init, front_init + front_change_pct, n)
    back_series = np.linspace(back_init, back_init + back_change_pct, n)

    front_rows = pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "tenor": front_tenor,
        "field_value": front_series,
    })
    back_rows = pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "tenor": back_tenor,
        "field_value": back_series,
    })
    return pd.concat([front_rows, back_rows], ignore_index=True)


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# ===========================================================================
# 1. Bundled config.yaml structurally valid
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "classify_curve_move_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "parallel_threshold_bps",
            "move_threshold_bps",
            "ffill_limit_days",
            "default_field_name",
            "avg_change_method",
            "allowed_lookback_periods",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing conventions: {sorted(missing)}"

    def test_convention_defaults_match_legacy_constants(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("parallel_threshold_bps") == 1.0
        assert cfg.convention_value("move_threshold_bps") == 0.5
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("avg_change_method") == "arithmetic_mean"

    def test_methodology_planned_extensions_populated(self):
        """The honest-placeholder convention pattern requires the
        methodology block to enumerate the future-supported alternatives."""
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3, (
            "planned_extensions should list at least the three "
            "avg_change_method alternatives"
        )
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "duration_weighted" in joined
        assert "dv01_weighted" in joined
        assert "back_leg_only" in joined


# ===========================================================================
# 2. End-to-end happy path with the bundled config
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, raw_df, config=None):
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.date",
            _FrozenDate,
        ):
            return classify_curve_move_compute(
                engine=None, params=params, config=config,
            )

    def test_default_config_returns_well_formed_output(self):
        # front_change_pct=-0.10 → -10bps over 80d → -2.66bps over 22d
        # back_change_pct =-0.05 →  -5bps over 80d → -1.33bps over 22d
        # spread_change_bps = back - front = -1.33 - (-2.66) = +1.33  → steepening
        # avg_change_bps    = (-2.66 + -1.33)/2 ≈ -1.99               → bull
        # |1.33| > 1.0 → BULL_STEEPENER.
        raw_df = _synthetic_raw_df(front_change_pct=-0.10, back_change_pct=-0.05)
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        out = self._run(params, raw_df)
        assert "error" not in out, f"unexpected error: {out.get('error')!r}"

        cm = out["current_metrics"]
        # Output uses the new field names — no regime_tag / regime_description.
        assert "classification" in cm
        assert "description" in cm
        assert "regime_tag" not in cm
        assert "regime_description" not in cm

        assert cm["classification"] in CURVE_MOVE_TAGS
        assert cm["spread_label"] == "2s10s"
        # Front fell more than back → spread (back-front) widened → BULL_STEEPENER.
        assert cm["classification"] == "BULL_STEEPENER"

    def test_explicit_default_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df(front_change_pct=-0.10, back_change_pct=-0.05)
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        out_auto = self._run(params, raw_df, config=None)
        out_explicit = self._run(params, raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides actually change behaviour
# ===========================================================================

class TestConventionOverrides:
    def _custom_config(self, **overrides) -> ToolConfig:
        defaults = {
            "parallel_threshold_bps": 1.0,
            "move_threshold_bps": 0.5,
            "ffill_limit_days": 5,
            "default_field_name": "YLD_YTM_MID",
            "avg_change_method": "arithmetic_mean",
            "allowed_lookback_periods": "1d,5d,22d,63d",
        }
        defaults.update(overrides)
        return ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                k: Convention(value=v, source="test", rationale="test")
                for k, v in defaults.items()
            },
        )

    def _run(self, params, raw_df, config):
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.date",
            _FrozenDate,
        ):
            return classify_curve_move_compute(
                engine=None, params=params, config=config,
            )

    def test_parallel_threshold_flips_classification_at_boundary(self):
        """A move where the 22d spread change lands BETWEEN two
        threshold values: the lower threshold sees it as a
        flattener, the higher threshold sees it as PARALLEL_SHIFT.

        Verified synthetic-data math (n=80 trading days, 22d
        lookback):

          front_change_pct = -0.20 → front_change_bps ≈ -5.32
          back_change_pct  = -0.30 → back_change_bps  ≈ -7.97
          spread_change_bps ≈ -2.66   (back fell more → spread narrowed)
          avg_change_bps    ≈ -6.65   (yields fell → bull)

        So the move is a BULL_FLATTENER under default thresholds.
        With a strict parallel_threshold of 5.0, |-2.66| < 5.0 →
        PARALLEL_SHIFT.  Together this exercises the threshold seam.
        """
        raw_df = _synthetic_raw_df(front_change_pct=-0.20, back_change_pct=-0.30)
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )

        # Default 1.0:  |-2.66| > 1.0  → bull flattener.
        out_default = self._run(
            params, raw_df, self._custom_config(parallel_threshold_bps=1.0),
        )
        # Strict 5.0:   |-2.66| < 5.0  → parallel.
        out_strict = self._run(
            params, raw_df, self._custom_config(parallel_threshold_bps=5.0),
        )

        assert out_default["current_metrics"]["classification"] == "BULL_FLATTENER"
        assert out_strict["current_metrics"]["classification"] == "PARALLEL_SHIFT"

    def test_move_threshold_promotes_tiny_moves_to_parallel(self):
        """A move where both legs drop 0.4 bps over 22d:
        with default move_threshold=0.5, both abs(0.4) < 0.5 → PARALLEL.
        With move_threshold=0.1, both abs(0.4) > 0.1 → not flat-flat,
        and since spread didn't change either, still PARALLEL via the
        spread-threshold branch.
        Use spread that DOES change to make the difference observable."""
        # Front drops 0.4bps, back drops 0.41bps over 22d.
        # abs(front)=0.4, abs(back)=0.41.
        # Spread change ≈ -0.01bps (well below parallel_threshold=1.0).
        raw_df = _synthetic_raw_df(front_change_pct=-0.004, back_change_pct=-0.0041)
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        out_default = self._run(
            params, raw_df, self._custom_config(move_threshold_bps=0.5),
        )
        # Default: both legs below 0.5 → PARALLEL via the move-threshold branch.
        assert out_default["current_metrics"]["classification"] == "PARALLEL_SHIFT"

        # With move_threshold=0.1: both legs cleared the threshold,
        # but spread change of 0.01 is still below parallel_threshold=1.0,
        # so it falls to PARALLEL via the spread-threshold branch.
        out_low = self._run(
            params, raw_df, self._custom_config(move_threshold_bps=0.1),
        )
        assert out_low["current_metrics"]["classification"] == "PARALLEL_SHIFT"

    def test_ffill_limit_passed_to_pivot(self):
        """ffill_limit_days flows from config → pivot_and_align_tenors."""
        from shared.analytics.spreads import pivot_and_align_tenors as real_pivot

        raw_df = _synthetic_raw_df(front_change_pct=-0.10, back_change_pct=-0.05)
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            classify_curve_move_compute(
                engine=None, params=params,
                config=self._custom_config(ffill_limit_days=2),
            )
        assert spy.call_count >= 1
        assert spy.call_args.kwargs["ffill_limit"] == 2

    def test_allowed_lookback_periods_gates_invalid_labels(self):
        """A lookback label NOT in allowed_lookback_periods returns
        a controlled error rather than silently classifying the move."""
        raw_df = _synthetic_raw_df(front_change_pct=-0.10, back_change_pct=-0.05)
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="63d",  # bundled config allows this
        )
        # Override to remove '63d' from allowed set:
        out = self._run(
            params, raw_df,
            self._custom_config(allowed_lookback_periods="1d,5d,22d"),
        )
        assert "error" in out
        assert "Invalid lookback_period '63d'" in out["error"]


# ===========================================================================
# 4. Honest-placeholder guard on avg_change_method
# ===========================================================================

class TestPlannedExtensionGuard:
    def _custom_config(self, **overrides) -> ToolConfig:
        defaults = {
            "parallel_threshold_bps": 1.0,
            "move_threshold_bps": 0.5,
            "ffill_limit_days": 5,
            "default_field_name": "YLD_YTM_MID",
            "avg_change_method": "arithmetic_mean",
            "allowed_lookback_periods": "1d,5d,22d,63d",
        }
        defaults.update(overrides)
        return ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                k: Convention(value=v, source="test", rationale="test")
                for k, v in defaults.items()
            },
        )

    def test_unsupported_avg_change_method_raises(self):
        """Setting ``avg_change_method`` to a documented-but-not-yet-
        implemented value raises NotImplementedError with a pointer
        to ``methodology.planned_extensions``.  This is the
        commit-6-pilot honest-placeholder pattern: the YAML field is
        real, the supported value works, and unsupported values fail
        loudly rather than silently falling back to the default."""
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        config = self._custom_config(avg_change_method="duration_weighted")

        # No need to mock the fetcher — the guard fires before fetch.
        with pytest.raises(NotImplementedError) as exc_info:
            classify_curve_move_compute(engine=None, params=params, config=config)

        msg = str(exc_info.value)
        assert "duration_weighted" in msg
        assert "planned_extensions" in msg
        assert "arithmetic_mean" in msg

    def test_supported_avg_change_method_does_not_raise(self):
        """The default (and only currently-supported) value works."""
        raw_df = _synthetic_raw_df(front_change_pct=-0.10, back_change_pct=-0.05)
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        config = self._custom_config(avg_change_method="arithmetic_mean")
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.date",
            _FrozenDate,
        ):
            out = classify_curve_move_compute(
                engine=None, params=params, config=config,
            )
        assert "error" not in out


# ===========================================================================
# 5. Shared primitive — hand-computable taxonomy
# ===========================================================================

class TestClassifyCurveMovePrimitive:
    """The 6-quadrant rule.  These tests pin the math regardless of
    which calling tool consumes the primitive."""

    def _classify(self, fc, bc, **overrides):
        kwargs = {
            "parallel_threshold_bps": 1.0,
            "move_threshold_bps": 0.5,
        }
        kwargs.update(overrides)
        return classify_curve_move(
            front_change_bps=fc,
            back_change_bps=bc,
            spread_change_bps=bc - fc,
            avg_change_bps=(fc + bc) / 2,
            **kwargs,
        )

    def test_bull_steepener(self):
        # Yields fell, front fell more → spread widens → BULL_STEEPENER
        assert self._classify(-5.0, -2.0) == BULL_STEEPENER

    def test_bear_steepener(self):
        # Yields rose, back rose more → spread widens → BEAR_STEEPENER
        assert self._classify(2.0, 5.0) == BEAR_STEEPENER

    def test_bull_flattener(self):
        # Yields fell, back fell more → spread narrows → BULL_FLATTENER
        assert self._classify(-2.0, -5.0) == BULL_FLATTENER

    def test_bear_flattener(self):
        # Yields rose, front rose more → spread narrows → BEAR_FLATTENER
        assert self._classify(5.0, 2.0) == BEAR_FLATTENER

    def test_twist(self):
        # Front up, back down → opposite directions → TWIST
        assert self._classify(3.0, -3.0) == TWIST

    def test_parallel_via_move_threshold(self):
        # Both legs flat → PARALLEL_SHIFT (move_threshold branch)
        assert self._classify(0.2, 0.3) == PARALLEL_SHIFT

    def test_parallel_via_spread_threshold(self):
        # Both legs moved enough, but spread barely changed → PARALLEL
        assert self._classify(5.0, 5.5) == PARALLEL_SHIFT  # spread_change = 0.5 < 1.0

    def test_twist_takes_precedence_over_parallel(self):
        # Opposite-direction legs even when both small → TWIST, not PARALLEL.
        # front=+0.6, back=-0.6 → both above move_threshold=0.5, opposite signs.
        assert self._classify(0.6, -0.6) == TWIST

    def test_threshold_kwargs_required(self):
        # The primitive no longer has module-level defaults; both
        # threshold kwargs must be supplied explicitly by the caller.
        with pytest.raises(TypeError):
            classify_curve_move(  # type: ignore[call-arg]
                front_change_bps=1.0,
                back_change_bps=2.0,
                spread_change_bps=1.0,
                avg_change_bps=1.5,
            )


# ===========================================================================
# 6. Schema-layer validation (P2 fix from Codex review)
# ===========================================================================

class TestSchemaLayerValidation:
    """Locks in the validation discipline for the input schema:

      - ``front_tenor != back_tenor`` is an invariant.
      - ``lookback_period`` must be in the bundled config's
        ``allowed_lookback_periods`` set.  Direct API callers should
        get a clean Pydantic ValidationError (which FastAPI turns
        into a 422) rather than a deferred error envelope from
        compute().
    """

    def test_invalid_lookback_period_rejected_at_construction(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match=r"lookback_period '999d'"):
            CurveMoveInput(
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period="999d",
            )

    def test_valid_lookback_periods_accepted(self):
        # All four labels in the bundled config's
        # allowed_lookback_periods convention construct cleanly.
        for label in ("1d", "5d", "22d", "63d"):
            CurveMoveInput(
                curve_family="UST",
                front_tenor="2Y",
                back_tenor="10Y",
                lookback_period=label,
            )

    def test_same_tenor_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match=r"must be different"):
            CurveMoveInput(
                curve_family="UST",
                front_tenor="5Y",
                back_tenor="5Y",
                lookback_period="1d",
            )


# ===========================================================================
# 7. default_field_name actually flows from YAML (P1 fix)
# ===========================================================================

class TestDefaultFieldNameFromYaml:
    """When ``field_name`` is omitted (or explicitly None), the tool
    must pull ``default_field_name`` from the active config and pass
    it to ``fetch_tenor_group``.  When ``field_name`` is set
    explicitly, the explicit value wins regardless of YAML.

    This locks in the P1 fix from the Codex review: previously the
    Pydantic schema's hardcoded ``default="YLD_YTM_MID"`` shadowed
    the YAML convention, making ``default_field_name`` a dead config
    field.  Now the schema defaults to None and ``compute()``
    resolves the sentinel against the active config.
    """

    def _custom_config(self, **overrides) -> ToolConfig:
        defaults = {
            "parallel_threshold_bps": 1.0,
            "move_threshold_bps": 0.5,
            "ffill_limit_days": 5,
            "default_field_name": "YLD_YTM_MID",
            "avg_change_method": "arithmetic_mean",
            "allowed_lookback_periods": "1d,5d,22d,63d",
        }
        defaults.update(overrides)
        return ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                k: Convention(value=v, source="test", rationale="test")
                for k, v in defaults.items()
            },
        )

    def _run_capture_field_name(
        self, params: CurveMoveInput, config: ToolConfig,
    ) -> str:
        """Run compute() with mocked fetch + frozen date and return
        the ``field_name`` that fetch_tenor_group received."""
        raw_df = _synthetic_raw_df(front_change_pct=-0.10, back_change_pct=-0.05)
        with patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.fetch_tenor_group",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.date",
            _FrozenDate,
        ):
            classify_curve_move_compute(
                engine=None, params=params, config=config,
            )
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_field_name_default_is_none_at_schema_layer(self):
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML default; a hardcoded string here makes "
            "default_field_name a dead convention"
        )

    def test_omitted_field_name_uses_yaml_default(self):
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",  # no field_name passed
        )
        # YAML default is YLD_YTM_MID.  Round 3 A4 introduced per-
        # playbook field auto-discovery: for a discoverable
        # curve_family (UST → sovereign_bonds.yml), the discovered
        # field wins over the YAML fallback.  In this UST case both
        # values are YLD_YTM_MID (sovereign_bonds.yml's
        # target_metrics[0] AND the YAML default), so the resolved
        # value is identical to the pre-A4 path — backward compat
        # invariant.
        passed = self._run_capture_field_name(params, self._custom_config())
        assert passed == "YLD_YTM_MID"

    def test_known_curve_family_uses_per_playbook_field_not_yaml_override(self):
        """Round 3 A4 — contract change.  For any curve_family
        declared in a tenor-keyed playbook under
        rates_agent/playbooks/, the resolved field comes from the
        playbook's ``target_metrics[0].bloomberg_field`` rather than
        the YAML's ``default_field_name``.  Editing the YAML default
        no longer affects callers whose curve_family is discoverable
        — they get the playbook-canonical field instead.  The YAML
        default is only a final fallback for curve_families NOT
        declared in any tenor-keyed playbook (a rare path that today
        produces an error envelope at fetch time anyway, because
        the playbook discovery is the same source of truth the
        fetcher relies on).

        This replaces the pre-A4 test
        ``test_yaml_override_changes_resolved_field_name`` which
        asserted the inverse contract (YAML override dominating).
        That contract was strictly less honest for non-sovereign
        callers — sovereign + linker callers happen to share the
        YAML's YLD_YTM_MID, but OIS callers need PX_LAST and ZCIS
        callers need PX_MID, so per-playbook discovery is the
        right primary path.
        """
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        # Override the YAML default to PX_LAST.  Per-playbook
        # discovery wins: UST → sovereign_bonds.yml → YLD_YTM_MID.
        passed = self._run_capture_field_name(
            params, self._custom_config(default_field_name="PX_LAST"),
        )
        assert passed == "YLD_YTM_MID", (
            "UST resolves to sovereign_bonds.yml's YLD_YTM_MID via "
            "per-playbook discovery; YAML default_field_name override "
            "is now the FINAL fallback (only reachable for "
            "curve_families NOT in any tenor-keyed playbook)."
        )

    def test_explicit_field_name_overrides_both_discovery_and_yaml(self):
        """Explicit params.field_name remains the highest-priority
        signal — beats both per-playbook discovery AND the YAML
        fallback.  Backward compat: callers that already pass an
        explicit field see no change in behaviour."""
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
            field_name="YLD_BID",
        )
        # Caller's explicit value wins, regardless of the YAML default
        # OR the per-playbook discovered value.
        passed = self._run_capture_field_name(
            params, self._custom_config(default_field_name="PX_LAST"),
        )
        assert passed == "YLD_BID"


# ===========================================================================
# 9. Curve-family-agnostic scope (Round 3 Stage 2, work item A4 — PR5
#    coverage extension)
# ===========================================================================
#
# The pre-A4 implementation hardcoded a sovereign-specific field
# default (``YLD_YTM_MID``).  After A4, the primitive accepts ANY
# tenor-keyed rates curve_family declared in any playbook under
# rates_agent/playbooks/ — sovereign + OIS + ZCIS + sovereign-linker
# curves are all classifiable through the same code path, and the
# field name is auto-discovered from the owning playbook's
# ``target_metrics[0].bloomberg_field``.
#
# Tests below pin (a) end-to-end runs on >=3 non-sovereign curve_
# families with the right per-playbook field auto-discovery, and
# (b) the sovereign-callers-unchanged invariant (backward compat).


class TestCurveFamilyAgnosticScope:
    """Round 3 A4: classify_curve_move accepts any tenor-keyed rates
    curve_family declared in any playbook under
    rates_agent/playbooks/.

    The fetcher (shared.analytics.rates_fetch.fetch_tenor_group) is
    already instrument-type agnostic; the change here is in
    compute()'s field-name resolution chain (params.field_name >
    per-playbook auto-discovered field > YAML default)."""

    @staticmethod
    def _run_capture(
        params: CurveMoveInput,
        raw_df,
        captured: dict,
    ):
        """Variant of TestComputeHappyPath._run that captures the
        field_name passed to fetch_tenor_group."""
        def fake_fetch(*, engine, curve_family, tenors, field_name,
                       start_date, end_date=None):
            captured["field_name"] = field_name
            captured["curve_family"] = curve_family
            captured["tenors"] = list(tenors)
            return raw_df

        with patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.fetch_tenor_group",
            side_effect=fake_fetch,
        ), patch(
            "rates_agent.sovereign_bonds.tools.curve_move_classifier.compute.date",
            _FrozenDate,
        ):
            return classify_curve_move_compute(
                engine=None, params=params, config=None,
            )

    # -----------------------------------------------------------------
    # End-to-end runs on >=3 non-sovereign curve_families
    # -----------------------------------------------------------------

    def test_runs_on_usd_sofr_ois(self):
        """OIS curve_family — discovered from ois.yml; default field
        PX_LAST."""
        raw_df = _synthetic_raw_df(
            front_change_pct=-0.10, back_change_pct=-0.05,
        )
        params = CurveMoveInput(
            curve_family="USD_SOFR_OIS", front_tenor="2Y",
            back_tenor="10Y", lookback_period="22d",
        )
        captured: dict = {}
        out = self._run_capture(params, raw_df, captured)
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["curve_family"] == "USD_SOFR_OIS"
        assert cm["classification"] in CURVE_MOVE_TAGS
        # Per-playbook field auto-discovery: ois.yml's
        # target_metrics[0] is PX_LAST.
        assert captured["field_name"] == "PX_LAST", (
            f"USD_SOFR_OIS should auto-discover PX_LAST from "
            f"ois.yml; got {captured['field_name']!r}"
        )

    def test_runs_on_usd_zcis(self):
        """ZCIS curve_family — discovered from inflation_swaps.yml;
        default field PX_MID."""
        raw_df = _synthetic_raw_df(
            front_change_pct=-0.10, back_change_pct=-0.05,
        )
        params = CurveMoveInput(
            curve_family="USD_ZCIS", front_tenor="2Y",
            back_tenor="10Y", lookback_period="22d",
        )
        captured: dict = {}
        out = self._run_capture(params, raw_df, captured)
        assert "error" not in out, out.get("error")
        assert captured["field_name"] == "PX_MID", (
            f"USD_ZCIS should auto-discover PX_MID from "
            f"inflation_swaps.yml; got {captured['field_name']!r}"
        )

    def test_runs_on_usd_tips(self):
        """Sovereign-linker curve_family — discovered from
        inflation_indexed_bonds.yml; default field YLD_YTM_MID."""
        raw_df = _synthetic_raw_df(
            front_change_pct=-0.10, back_change_pct=-0.05,
            front_tenor="5Y", back_tenor="10Y",
        )
        params = CurveMoveInput(
            curve_family="USD_TIPS", front_tenor="5Y",
            back_tenor="10Y", lookback_period="22d",
        )
        captured: dict = {}
        out = self._run_capture(params, raw_df, captured)
        assert "error" not in out, out.get("error")
        # Per-playbook field auto-discovery: inflation_indexed_bonds
        # .yml's target_metrics[0] is YLD_YTM_MID.
        assert captured["field_name"] == "YLD_YTM_MID", (
            f"USD_TIPS should auto-discover YLD_YTM_MID from "
            f"inflation_indexed_bonds.yml; got "
            f"{captured['field_name']!r}"
        )

    # -----------------------------------------------------------------
    # Backward-compat invariant for sovereign callers
    # -----------------------------------------------------------------

    def test_sovereign_ust_field_default_unchanged(self):
        """Backward-compat: a UST call with field_name=None still
        resolves to YLD_YTM_MID (the same value as pre-A4 — both the
        YAML default AND the sovereign playbook's
        target_metrics[0] are YLD_YTM_MID; the per-playbook
        discovery picks the playbook value, identical to the legacy
        YAML fallback)."""
        raw_df = _synthetic_raw_df(
            front_change_pct=-0.10, back_change_pct=-0.05,
        )
        params = CurveMoveInput(
            curve_family="UST", front_tenor="2Y", back_tenor="10Y",
            lookback_period="22d",
        )
        captured: dict = {}
        out = self._run_capture(params, raw_df, captured)
        assert "error" not in out, out.get("error")
        assert captured["field_name"] == "YLD_YTM_MID"

    # -----------------------------------------------------------------
    # Discovery boundary — cash-bond playbook excluded
    # -----------------------------------------------------------------

    def test_ust_resolves_to_sovereign_benchmark_playbook(self):
        """UST appears in BOTH sovereign_bonds.yml (benchmark
        generics) AND sovereign_cash_bonds.yml (specific cusips).
        The discovery rule excludes cash-bond playbooks
        (instrument_type='sovereign_cash_bond'), so UST resolves to
        sovereign_bonds.yml — preserving the pre-A4 lookup
        behaviour.  Regression guard for the cash-bond
        bleed-through case."""
        from shared.analytics.playbook_discovery import (
            playbook_curve_family_index,
        )
        idx = playbook_curve_family_index()
        ust_entry = idx.get("UST")
        assert ust_entry is not None, (
            "UST must be discoverable for backward compat"
        )
        assert ust_entry["playbook"] == "sovereign_bonds.yml", (
            f"UST must resolve to sovereign_bonds.yml (benchmark "
            f"playbook), got {ust_entry['playbook']!r} — cash-bond "
            f"playbook bleed-through is a regression."
        )

    def test_at_least_three_non_sovereign_curve_families_discoverable(self):
        """The work order's A4 acceptance requires
        classify_curve_move to work on >=3 non-sovereign
        curve_families.  Pins discoverability of one OIS + one ZCIS
        + one linker curve_family — three independent playbook
        sources."""
        from shared.analytics.playbook_discovery import (
            playbook_curve_family_index,
        )
        idx = playbook_curve_family_index()
        for cf in ("USD_SOFR_OIS", "USD_ZCIS", "USD_TIPS"):
            assert cf in idx, f"{cf} must be discoverable post-A4"
            assert idx[cf]["default_field"]
            assert idx[cf]["playbook"].endswith(".yml")
