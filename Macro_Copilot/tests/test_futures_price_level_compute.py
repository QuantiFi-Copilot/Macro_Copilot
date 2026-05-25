"""
test_futures_price_level_compute.py — Unit tests for the bond-futures
                                       front-month price-level monitor

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window, ddof,
     ffill, default_price_field, price_round_decimals).
  4. The honest placeholder for trailing_range_window_days: setting
     it to anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML's
     default_price_field.
  6. Price-space deltas: daily/weekly/monthly change_price fields are
     RAW subtraction (no *100 multiplication that would lie about
     the unit).
  7. P5 disclosure: every response carries methodology_disclosure
     verbatim, the schema makes it required, and the snapshot is
     unit-honest (current_price not current_yield_pct, change_price
     not change_bps).
  8. Reference metadata flows through to the snapshot.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.bond_futures.tools.futures_price_level import (
    CONFIG_PATH,
    METHODOLOGY_DISCLOSURE,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    calculate_futures_price_level,
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


def _synthetic_price_df(
    *,
    drift: float = -1.5,
    start_price: float = 110.0,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Build a single-instrument long-format DataFrame matching the
    shape ``fetch_rolling_generic_series`` returns. Linspace from
    ``start_price`` to ``start_price + drift`` over ``days`` business
    days ending at ``frozen_today``."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(start_price, start_price + drift, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
    })


def _synthetic_reference(
    *,
    curve_family: str = "UST_FUT",
    contract_code: str = "TY1",
    tenor: str = "10Y",
    quote_units: str = "points",
    contract_size: float = 100000.0,
    expiry: date = date(2026, 12, 21),
    security_name: str = "TYZ6 COMB",
) -> dict:
    return {
        "contract_code": contract_code,
        "curve_family": curve_family,
        "tenor": tenor,
        "expiry_date": expiry,
        "security_name": security_name,
        "quote_units": quote_units,
        "contract_size": contract_size,
    }


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


_UNSET = object()


def _run(
    params,
    *,
    price_df=None,
    reference=_UNSET,
    config=None,
):
    if price_df is None:
        price_df = _synthetic_price_df()
    ref_value = _synthetic_reference() if reference is _UNSET else reference
    with patch(
        "rates_agent.bond_futures.tools.futures_price_level.compute.fetch_rolling_generic_series",
        return_value=price_df,
    ), patch(
        "rates_agent.bond_futures.tools.futures_price_level.compute.fetch_rolling_generic_reference",
        return_value=ref_value,
    ), patch(
        "rates_agent.bond_futures.tools.futures_price_level.compute.date",
        _FrozenDate,
    ):
        return calculate_futures_price_level(engine=None, params=params, config=config)


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "z_score_window_days": 252,
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "daily_change_offset_rows": 2,
        "weekly_change_offset_rows": 6,
        "monthly_change_offset_rows": 22,
        "trailing_range_window_days": 252,
        "ffill_limit_days": 5,
        "default_price_field": "PX_LAST",
        "price_round_decimals": 6,
        "z_score_round_decimals": 4,
        "price_high_low_round_decimals": 6,
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


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "get_futures_price_level_tool"
        assert cfg.tool.domain == "bond_futures"

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
            "default_price_field",
            "price_round_decimals",
            "z_score_round_decimals",
            "price_high_low_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
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
        assert cfg.convention_value("default_price_field") == "PX_LAST"
        assert cfg.convention_value("price_round_decimals") == 6
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("price_high_low_round_decimals") == 6

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        # CTD path is documented-deferred.
        assert "CTD" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date", "curve_family", "contract_code", "tenor",
            "quote_units", "contract_size", "expiry_date", "security_name",
            "current_price", "daily_change_price", "weekly_change_price",
            "monthly_change_price", "z_score",
            "high_252d_price", "low_252d_price", "percentile_252d",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # Unit-honest naming — guard against silent renames back to
        # yield-space conventions.
        assert "current_yield_pct" not in cm
        assert "daily_change_bps" not in cm
        assert "weekly_change_bps" not in cm
        assert "monthly_change_bps" not in cm
        assert "high_252d_pct" not in cm
        assert "low_252d_pct" not in cm

        assert isinstance(cm["current_price"], float)

        # Bespoke time_series shape: list of {date, price}.
        ts = out["time_series"]
        assert isinstance(ts, list)
        assert len(ts) > 0
        assert "date" in ts[0]
        assert "price" in ts[0]
        # NOT the canonical TimeSeries shape — should not carry units
        # / series_name / description keys.
        assert "value" not in ts[0]

    def test_explicit_config_matches_auto_loaded(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_reference_metadata_flows_to_snapshot(self):
        params = FuturesPriceLevelInput(
            curve_family="DE_FUT", contract_code="RX1", lookback_days=365,
        )
        ref = _synthetic_reference(
            curve_family="DE_FUT",
            contract_code="RX1",
            tenor="10Y",
            quote_units="% of par value",
            contract_size=100000.0,
            security_name="RXZ6 Comdty",
        )
        out = _run(params, reference=ref)
        cm = out["current_metrics"]
        assert cm["curve_family"] == "DE_FUT"
        assert cm["contract_code"] == "RX1"
        assert cm["tenor"] == "10Y"
        assert cm["quote_units"] == "% of par value"
        assert cm["contract_size"] == 100000.0
        assert cm["security_name"] == "RXZ6 Comdty"

    def test_snapshot_current_price_equals_time_series_last_row_strictly(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params)
        last_row_price = out["time_series"][-1]["price"]
        snapshot_price = out["current_metrics"]["current_price"]
        # Strict equality — both go through the SAME price_round_decimals
        # convention.
        assert last_row_price == snapshot_price

    def test_methodology_disclosure_present_and_required(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params)
        assert out["methodology_disclosure"] == METHODOLOGY_DISCLOSURE
        # Schema must require it — pop and re-validate.
        from pydantic import ValidationError as _VE
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            FuturesPriceLevelOutput.model_validate(bad)

    def test_methodology_disclosure_mentions_ctd_caveat(self):
        # The P5 / ADR 0013 caveat MUST mention CTD-implied yield so a
        # casual reader cannot miss the scope-limited reading.
        assert "CTD" in METHODOLOGY_DISCLOSURE
        assert "rolling-generic" in METHODOLOGY_DISCLOSURE


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_short = _run(params, config=_custom_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["z_score"]
            != out_short["current_metrics"]["z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out_sample = _run(params, config=_custom_config(z_score_ddof=1))
        out_pop = _run(params, config=_custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score"]
            != out_pop["current_metrics"]["z_score"]
        )

    def test_period_offsets_override_changes_change_values(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_wider = _run(params, config=_custom_config(daily_change_offset_rows=5))
        assert (
            out_default["current_metrics"]["daily_change_price"]
            != out_wider["current_metrics"]["daily_change_price"]
        )

    def test_price_round_decimals_propagates_to_time_series(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params, config=_custom_config(price_round_decimals=2))
        for row in out["time_series"]:
            assert row["price"] == round(row["price"], 2)
        # Snapshot must agree at the latest row.
        assert (
            out["current_metrics"]["current_price"]
            == out["time_series"][-1]["price"]
        )

    def test_ffill_limit_passed_to_clean(self):
        from shared.analytics.levels import clean_single_series as real_clean
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        with patch(
            "rates_agent.bond_futures.tools.futures_price_level.compute.fetch_rolling_generic_series",
            return_value=_synthetic_price_df(),
        ), patch(
            "rates_agent.bond_futures.tools.futures_price_level.compute.fetch_rolling_generic_reference",
            return_value=_synthetic_reference(),
        ), patch(
            "rates_agent.bond_futures.tools.futures_price_level.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.bond_futures.tools.futures_price_level.compute.clean_single_series",
            wraps=real_clean,
        ) as spy:
            calculate_futures_price_level(
                engine=None, params=params,
                config=_custom_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(params, config=_custom_config(trailing_range_window_days=180))
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_price" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params, config=_custom_config(trailing_range_window_days=252))
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer field_name behaviour + YAML fall-through
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1",
        )
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_price_field"
        )

    def test_explicit_field_name_passes_through(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", field_name="PX_BID",
        )
        assert params.field_name == "PX_BID"


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_price_field reaches fetch."""

    def _capture_field_name(self, params, config):
        with patch(
            "rates_agent.bond_futures.tools.futures_price_level.compute.fetch_rolling_generic_series",
            return_value=_synthetic_price_df(),
        ) as spy, patch(
            "rates_agent.bond_futures.tools.futures_price_level.compute.fetch_rolling_generic_reference",
            return_value=_synthetic_reference(),
        ), patch(
            "rates_agent.bond_futures.tools.futures_price_level.compute.date",
            _FrozenDate,
        ):
            calculate_futures_price_level(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", field_name="PX_ASK",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_price_field="PX_LAST"),
        )
        assert passed == "PX_ASK"


# ===========================================================================
# 6. Price-space deltas (NOT multiplied by 100)
# ===========================================================================

class TestPriceSpaceDeltas:
    """The bond-futures price level is in price units (NOT yield bps).
    The period-change fields are RAW subtraction — multiplying by 100
    would silently lie about the unit."""

    def test_daily_change_is_raw_subtraction(self):
        # Synthetic linear drift of -1.5 over `days` business days,
        # starting at 110. Daily step is then exactly -1.5/(days-1).
        days = 400
        drift = -1.5
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(
            params,
            price_df=_synthetic_price_df(drift=drift, days=days),
        )
        expected_step = drift / (days - 1)
        observed = out["current_metrics"]["daily_change_price"]
        # observed is rounded to price_round_decimals (6); compare with
        # ample tolerance.
        assert abs(observed - expected_step) < 1e-5

        # If we had multiplied by 100 (bps-style), the value would be
        # ~100x larger. Guard the lie explicitly.
        assert abs(observed) < 0.1  # price-step magnitude on -1.5 over 400 days
        assert abs(observed * 100 - expected_step) > 0.01

    def test_weekly_change_is_raw_subtraction(self):
        days = 400
        drift = -1.5
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(
            params,
            price_df=_synthetic_price_df(drift=drift, days=days),
        )
        # weekly_change_offset_rows = 6 → iloc[-1] - iloc[-6] = 5 steps.
        step = drift / (days - 1)
        expected = 5 * step
        observed = out["current_metrics"]["weekly_change_price"]
        assert abs(observed - expected) < 1e-5


# ===========================================================================
# 7. Reference miss → error envelope
# ===========================================================================

class TestReferenceMiss:
    def test_missing_reference_returns_error(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="UNKNOWN1", lookback_days=365,
        )
        out = _run(params, reference=None)
        assert "error" in out
        assert "UNKNOWN1" in out["error"]

    def test_policy_futures_stem_returns_routing_error(self):
        """A reference row with no tenor (the policy_futures shape —
        SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT are strip-position-
        keyed and have NULL tenor on instrument_master) must produce
        the routing-error envelope rather than crash in Pydantic."""
        params = FuturesPriceLevelInput(
            curve_family="EUR_SHORT_RATE_FUT", contract_code="ER1",
            lookback_days=365,
        )
        ref = _synthetic_reference(
            curve_family="EUR_SHORT_RATE_FUT",
            contract_code="ER1",
            tenor=None,  # policy-futures shape
        )
        out = _run(params, reference=ref)
        assert "error" in out
        assert "policy_futures" in out["error"]
        assert "ADR 0013" in out["error"]

    def test_empty_price_history_returns_error(self):
        params = FuturesPriceLevelInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        empty_df = pd.DataFrame({"trade_date": [], "field_value": []})
        out = _run(params, price_df=empty_df)
        assert "error" in out
        assert "TY1" in out["error"]


# ===========================================================================
# 8. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.bond_futures.tools.futures_price_level import (
            calculate_futures_price_level as via_package,
        )
        from rates_agent.bond_futures.tools.futures_price_level.compute import (
            calculate_futures_price_level as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.bond_futures.tools.futures_price_level import (
            FuturesPriceLevelInput as via_package,
        )
        from rates_agent.bond_futures.tools.futures_price_level.schemas import (
            FuturesPriceLevelInput as via_schemas,
        )
        from rates_agent.bond_futures.tools.schemas import (
            FuturesPriceLevelInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.bond_futures.tools.futures_price_level import (
            FuturesPriceLevelOutput as via_package,
        )
        from rates_agent.bond_futures.tools.futures_price_level.schemas import (
            FuturesPriceLevelOutput as via_schemas,
        )
        from rates_agent.bond_futures.tools.schemas import (
            FuturesPriceLevelOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
