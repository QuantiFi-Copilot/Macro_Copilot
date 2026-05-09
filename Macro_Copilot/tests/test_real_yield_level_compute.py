"""
test_real_yield_level_compute.py — Unit tests for the linker
real_yield_level primitive.

Mirrors ``test_ois_rate_level_compute.py`` and
``test_yield_levels_compute.py`` (the structurally-equivalent OIS and
sovereign analogs) so the three level surfaces evolve together.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window, ddof,
     period offsets, ffill, default_field_name).
  4. Honest placeholder for trailing_range_window_days: setting it to
     anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML.
  6. Two import paths still resolve to the same Pydantic class.
  7. Canonical TimeSeries output (units = PERCENT, snapshot ==
     time_series.rows[-1].value strictly, ``_real_yield`` series-name
     suffix to distinguish from nominal sovereign yields).

Tests are fully offline (DB-backed grounding lives in the SQL-validation
runner — see ``tests/test_real_yield_level_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
    CONFIG_PATH,
    get_real_yield_level,
    RealYieldLevelInput,
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
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _synthetic_raw_df(
    *,
    drift_pct: float = -0.40,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
    base_pct: float = 1.85,
) -> pd.DataFrame:
    """Build a single-tenor long-format DataFrame matching the shape
    fetch_single_tenor returns.  Linspace from base_pct to
    base_pct+drift_pct over `days` business days ending at frozen_today.

    Real yields can be negative across parts of the post-2008 / 2020-21
    history; the math is unchanged — base_pct is parameterised so the
    fixture can probe both positive and negative regimes.

    The tool anchors observation_count to the data's latest date (NOT
    to date.today()), so this fixture's last business day is the
    effective ``as_of_date`` regardless of frozen-today patching.
    """
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(base_pct, base_pct + drift_pct, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
    })


class _FrozenDate(date):
    """Patches ``compute.date`` so the fetch ``start_date`` is
    deterministic across machines.  The observation-count cutoff inside
    compute() is anchored to the data's last index date
    (``real_yields.index[-1]``), not to ``date.today()``, so the frozen
    today value only matters for the fetch-window computation (which is
    mocked away in these tests anyway).
    """
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# Default-convention dict shared across the override-style tests below.
# Mirrors the bundled config.yaml exactly except per-test overrides
# applied via ``_build_config(**overrides)``.
_BUNDLED_DEFAULTS = {
    "z_score_window_days": 252,
    "z_score_min_periods": 60,
    "z_score_ddof": 1,
    "z_score_buffer_multiplier": 1.5,
    "daily_change_offset_rows": 2,
    "weekly_change_offset_rows": 6,
    "monthly_change_offset_rows": 22,
    "trailing_range_window_days": 252,
    "ffill_limit_days": 5,
    "default_field_name": "YLD_YTM_MID",
    "yield_round_decimals": 4,
    "z_score_round_decimals": 4,
    "high_low_round_decimals": 4,
}


def _build_config(**overrides) -> ToolConfig:
    defaults = dict(_BUNDLED_DEFAULTS)
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(name="t", domain="d", description="x"),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "get_real_yield_level_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "weekly_change_offset_rows",
            "monthly_change_offset_rows",
            "trailing_range_window_days",
            "ffill_limit_days",
            "default_field_name",
            "yield_round_decimals",
            "z_score_round_decimals",
            "high_low_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_align_with_sovereign_level_tools(self):
        """Cross-tool convention values MUST match sovereign yield_levels
        and OIS rate_level — the cross-config lint enforces this, and
        the test pins the values explicitly so a YAML edit drifting one
        of these silently breaks here too."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("daily_change_offset_rows") == 2
        assert cfg.convention_value("weekly_change_offset_rows") == 6
        assert cfg.convention_value("monthly_change_offset_rows") == 22
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("high_low_round_decimals") == 4

    def test_uses_default_field_name_not_swap_rate_field(self):
        """Linkers publish their real yield-to-maturity under the same
        ``YLD_YTM_MID`` Bloomberg mnemonic the nominal sovereigns use
        — see the linker playbook's ``target_metrics``.  The tool MUST
        therefore reuse the ``default_field_name`` convention name (not
        OIS's ``default_swap_rate_field``); otherwise the cross-config
        lint would not catch a future drift between linker and
        sovereign defaults that should track each other."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "default_field_name" in cfg.conventions
        assert "default_swap_rate_field" not in cfg.conventions, (
            "linker real_yield_level must reuse the sovereign "
            "convention name ``default_field_name`` (not the OIS-only "
            "``default_swap_rate_field``); the linker mnemonic is "
            "YLD_YTM_MID, same as the sovereign side."
        )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        assert "observation_count" in joined  # the sovereign-vs-linker anchoring follow-up


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, raw_df, config=None):
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            return get_real_yield_level(engine=None, params=params, config=config)

    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        # Required snapshot fields present.
        for k in (
            "as_of_date", "curve_family", "tenor", "real_yield_pct",
            "daily_change_bps", "weekly_change_bps", "monthly_change_bps",
            "z_score", "high_252d_pct", "low_252d_pct", "percentile_252d",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_pct" not in cm
        assert "trailing_window_days" not in cm

        # The wire field MUST be the linker-specific name, not the
        # nominal-yield name.  Operator panels rely on this distinction.
        assert "real_yield_pct" in cm
        assert "current_yield_pct" not in cm
        assert "current_rate_pct" not in cm

        assert isinstance(cm["real_yield_pct"], float)

    def test_handles_negative_real_yields(self):
        """Linker real yields were negative across parts of the
        post-2020 history; the level math is unchanged but a regression
        guard here pins that the wire types tolerate negative values."""
        raw_df = _synthetic_raw_df(base_pct=-0.50, drift_pct=0.20)
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        assert "error" not in out
        assert out["current_metrics"]["real_yield_pct"] < 0

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out_auto = self._run(params, raw_df, config=None)
        out_explicit = self._run(params, raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def _run(self, params, raw_df, config):
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            return get_real_yield_level(engine=None, params=params, config=config)

    def test_z_score_window_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out_default = self._run(params, raw_df, _build_config())
        out_short = self._run(params, raw_df, _build_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["z_score"]
            != out_short["current_metrics"]["z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out_sample = self._run(params, raw_df, _build_config(z_score_ddof=1))
        out_pop = self._run(params, raw_df, _build_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score"]
            != out_pop["current_metrics"]["z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out_default = self._run(params, raw_df, _build_config())
        out_wider = self._run(params, raw_df, _build_config(daily_change_offset_rows=5))
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_clean(self):
        from shared.analytics.levels import clean_single_series as real_clean

        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.clean_single_series",
            wraps=real_clean,
        ) as spy:
            get_real_yield_level(
                engine=None, params=params,
                config=_build_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            get_real_yield_level(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            out = get_real_yield_level(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=252),
            )
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer field_name behaviour
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = RealYieldLevelInput(curve_family="USD_TIPS", tenor="10Y")
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_field_name"
        )

    def test_explicit_field_name_passes_through(self):
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", field_name="YLD_YTM_BID",
        )
        assert params.field_name == "YLD_YTM_BID"


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches fetch."""

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            get_real_yield_level(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y",
        )  # no field_name
        passed = self._capture_field_name(
            params, _build_config(default_field_name="YLD_YTM_MID"),
        )
        assert passed == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = RealYieldLevelInput(curve_family="USD_TIPS", tenor="10Y")
        passed = self._capture_field_name(
            params, _build_config(default_field_name="YLD_YTM_BID"),
        )
        assert passed == "YLD_YTM_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", field_name="YLD_YTM_ASK",
        )
        passed = self._capture_field_name(
            params, _build_config(default_field_name="YLD_YTM_MID"),
        )
        assert passed == "YLD_YTM_ASK"


# ===========================================================================
# 6. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_get_real_yield_level_via_package_init(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
            get_real_yield_level as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute import (
            get_real_yield_level as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
            RealYieldLevelInput as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level.schemas import (
            RealYieldLevelInput as via_schemas,
        )
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            RealYieldLevelInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 7. Canonical TimeSeries output
# ===========================================================================

class TestCanonicalTimeSeries:
    """Pin the canonical TimeSeries contract on the linker
    real_yield_level surface.  Field name is ``time_series`` (singular
    ``TimeSeries`` value), matching the v6 sovereign primitive
    convention used by ``yield_levels`` / ``zscore_custom`` / OIS
    ``rate_level``.  Each row is rounded with the YAML-controlled
    ``yield_round_decimals`` convention so the snapshot's
    ``real_yield_pct`` equals ``time_series.rows[-1].value`` STRICTLY
    (not just within tolerance).
    """

    def _run(self, params, raw_df, *, config=None):
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            return get_real_yield_level(engine=None, params=params, config=config)

    def test_time_series_field_present(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        assert "time_series" in out
        # Singular TimeSeries object, not a list — matches v6 pattern.
        assert isinstance(out["time_series"], dict)

    def test_time_series_uses_closed_enum_units(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert ts["units"] == "percent"

    def test_time_series_name_carries_real_yield_suffix(self):
        """``_real_yield`` suffix distinguishes from nominal sovereign
        yield series (``_yield`` suffix) when both end up in the same
        operator panel downstream — the load-bearing reason the linker
        tool exists as a separate primitive rather than a yield_levels
        universe expansion."""
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert ts["series_name"] == "usd_tips_10y_real_yield"

    def test_time_series_length_matches_observation_count(self):
        """The canonical series covers the same display window the
        snapshot's observation_count was computed from — length must
        match exactly."""
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert len(ts["rows"]) == out["current_metrics"]["observation_count"]

    def test_time_series_last_value_matches_snapshot_STRICTLY(self):
        """Latest row in the canonical series MUST equal
        ``real_yield_pct`` STRICTLY (not just within tolerance) — both
        go through the same ``yield_round_decimals`` convention applied
        via ``compute_level_metrics`` and the canonical builder."""
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        last_row_value = ts["rows"][-1]["value"]
        snapshot_value = out["current_metrics"]["real_yield_pct"]
        assert last_row_value == snapshot_value

    def test_yaml_yield_round_decimals_change_propagates_to_time_series(self):
        """Tweaking the YAML's ``yield_round_decimals`` MUST change the
        precision of the canonical series.  Regression guard against
        the canonical builder hardcoding a default instead of reading
        the YAML."""
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df, config=_build_config(yield_round_decimals=2))
        ts = out["time_series"]
        for row in ts["rows"]:
            v = row["value"]
            if v is not None:
                assert v == round(v, 2)

    def test_time_series_dates_chronological(self):
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        ts = out["time_series"]
        dates = [r["date"] for r in ts["rows"]]
        assert dates == sorted(dates)

    def test_time_series_validates_against_TimeSeries_schema(self):
        """The output dict must round-trip cleanly through the
        canonical ``shared.schemas.TimeSeries`` model — guards against
        the bespoke shape silently leaking back in."""
        from shared.schemas import TimeSeries
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        out = self._run(params, raw_df)
        TimeSeries.model_validate(out["time_series"])


# ===========================================================================
# 8. Linker instrument_type guard
# ===========================================================================

class TestLinkerInstrumentTypeGuard:
    """The linker real_yield_level primitive MUST require
    ``instrument_type='inflation_linker'`` at the fetch boundary.
    Without that filter, calling it with a nominal curve_family
    (``UST``, ``DE_BUND``) silently returns nominal sovereign rows
    presented under a ``real_yield_pct`` label — a proxy violation
    forbidden by DESIGN_PRINCIPLES.md §1, §3 and
    STANDARD_TOOL_AND_YAML_RULES.md §J.

    These tests pin the guarantee at three layers: the parameter
    flow into ``fetch_single_tenor``, the controlled-error envelope
    when zero linker rows match, and the absence of any silent
    fallback to nominal data.
    """

    def test_fetch_called_with_inflation_linker_instrument_type(self):
        """The compute path MUST forward
        ``instrument_type='inflation_linker'`` to fetch_single_tenor.
        Regression guard against a future edit that loses the filter
        argument."""
        raw_df = _synthetic_raw_df()
        params = RealYieldLevelInput(
            curve_family="USD_TIPS", tenor="10Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            get_real_yield_level(engine=None, params=params)
        assert spy.call_count == 1
        kwargs = spy.call_args.kwargs
        assert kwargs.get("instrument_type") == "inflation_linker", (
            "compute() must pass instrument_type='inflation_linker' to "
            "fetch_single_tenor — without it, nominal sovereign rows "
            f"would silently flow through; got {kwargs.get('instrument_type')!r}"
        )

    def test_nominal_curve_family_returns_controlled_error_envelope(self):
        """Adversarial probe: calling the linker tool with a nominal
        curve_family must NOT silently return nominal yields under a
        real_yield_pct label.  When zero linker rows match, the tool
        must return ``{"error": "..."}`` — same shape every other
        primitive uses for recoverable failures.

        Simulated by returning an empty DataFrame from
        fetch_single_tenor (which is what the SQL filter does when
        instrument_type='inflation_linker' is applied to a nominal
        curve)."""
        empty_df = pd.DataFrame(columns=["trade_date", "field_value"])
        params = RealYieldLevelInput(
            curve_family="UST", tenor="10Y", lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor",
            return_value=empty_df,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.date",
            _FrozenDate,
        ):
            out = get_real_yield_level(engine=None, params=params)
        assert "error" in out, (
            "linker tool must return a controlled error envelope when "
            "fed a nominal curve_family — got a successful payload "
            "instead, which would surface nominal yields under a "
            f"real_yield_pct label.  Output: {out!r}"
        )
        # No real-yield value should be computed; the error envelope
        # is the ONLY shape allowed here.
        assert "current_metrics" not in out
        assert "real_yield_pct" not in out
        # The error message must mention the instrument_type guard so
        # operators can tell the difference between "no linker rows
        # for this curve" and an unrelated failure.
        assert "inflation_linker" in out["error"]
