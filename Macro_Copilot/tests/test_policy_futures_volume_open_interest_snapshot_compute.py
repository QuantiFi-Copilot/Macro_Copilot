"""
test_policy_futures_volume_open_interest_snapshot_compute.py — Unit
                                                                tests for
                                                                the
                                                                policy-
                                                                futures
                                                                strip-
                                                                position
                                                                volume +
                                                                OI
                                                                snapshot
                                                                monitor

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (oi z-window,
     ddof, ffill, delta_oi_offset, default volume / OI field, rounding).
  4. The honest-placeholder guards: setting either
     oi_trailing_range_window_days or volume_avg_window_days to
     anything other than the wire-frozen value raises
     NotImplementedError.
  5. Schema-layer behaviour: no field_name input is accepted (input-
     schema-overreach guard); curve_family / strip_position are
     required.
  6. Contract-count delta: delta_open_interest_1d is RAW subtraction
     (no *100 multiplication that would lie about the unit).
  7. P5 / catalog-guardrail disclosure: every response carries
     methodology_disclosure naming the OI z-score lookback verbatim,
     the schema makes it required, and the snapshot is unit-honest
     (current_volume / current_open_interest not ..._notional;
     delta_open_interest_1d not ..._bps).
  8. Reference metadata flows through to the snapshot.
  9. The snapshot's current_volume / current_open_interest equal
     time_series[-1].volume / open_interest STRICTLY (same rounding).
 10. Intersection alignment: when volume + OI series have different
     date supports, the snapshot anchors to the latest SHARED day.
 11. Future-anchor guard: as_of_date beyond the universe max returns
     the controlled-error envelope; the probe is against the OI
     field (the headline series).

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
    CONFIG_PATH,
    VolumeOpenInterestSnapshotInput,
    VolumeOpenInterestSnapshotOutput,
    calculate_volume_open_interest_snapshot,
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


def _synthetic_series_df(
    *,
    drift: float,
    start: float,
    days: int,
    frozen_today: date,
) -> pd.DataFrame:
    """Build a single long-format DataFrame matching the shape
    ``fetch_strip_position`` returns. Linspace from ``start`` to
    ``start + drift`` over ``days`` business days ending at
    ``frozen_today``."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(start, start + drift, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
    })


def _synthetic_volume_df(
    *, days: int = 400, frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    return _synthetic_series_df(
        drift=20000.0, start=600000.0, days=days, frozen_today=frozen_today,
    )


def _synthetic_oi_df(
    *, days: int = 400, frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    return _synthetic_series_df(
        drift=50000.0, start=1000000.0, days=days, frozen_today=frozen_today,
    )


def _synthetic_reference(
    *,
    curve_family: str = "SOFR_FUT",
    contract_code: str = "SFR1",
    strip_position: int = 1,
    underlying_contract_code: str = "SFRM26",
    expiry: date = date(2026, 6, 16),
    security_name: str = "SFRM26 COMB",
    contract_size: float = 2500.0,
    inverse_pricing: bool = True,
) -> dict:
    # inverse_pricing is carried on the reference dict (lives on
    # instrument_master.attributes for the policy_futures domain) but
    # is unused by this primitive — included here so the synthetic
    # reference dict matches the real fetch_strip_position_reference
    # return shape.
    return {
        "curve_family": curve_family,
        "contract_code": contract_code,
        "strip_position": strip_position,
        "inverse_pricing": inverse_pricing,
        "underlying_contract_code": underlying_contract_code,
        "security_name": security_name,
        "expiry_date": expiry,
        "tick_size": 0.005,
        "tick_value": 12.5,
        "contract_size": contract_size,
    }


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


_UNSET = object()


_TARGET = (
    "rates_agent.policy_futures.tools.volume_open_interest_snapshot.compute"
)


def _run(
    params,
    *,
    volume_df=None,
    oi_df=None,
    reference=_UNSET,
    config=None,
    universe_max_date=None,
):
    if volume_df is None:
        volume_df = _synthetic_volume_df()
    if oi_df is None:
        oi_df = _synthetic_oi_df()
    ref_value = _synthetic_reference() if reference is _UNSET else reference

    def _series_side_effect(
        *, engine, curve_family, strip_position, field_name,
        start_date, end_date=None,
    ):
        # Default-config field names: PX_VOLUME / OPEN_INT.
        if field_name in ("PX_VOLUME", "VOLUME"):
            return volume_df
        if field_name in ("OPEN_INT", "OI"):
            return oi_df
        # Unknown — return empty so the error path is exercised.
        return pd.DataFrame({"trade_date": [], "field_value": []})

    with patch(
        f"{_TARGET}.fetch_strip_position",
        side_effect=_series_side_effect,
    ), patch(
        f"{_TARGET}.fetch_strip_position_reference",
        return_value=ref_value,
    ), patch(
        f"{_TARGET}.fetch_strip_position_max_date",
        return_value=universe_max_date,
    ), patch(
        f"{_TARGET}.date",
        _FrozenDate,
    ):
        return calculate_volume_open_interest_snapshot(
            engine=None, params=params, config=config,
        )


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "oi_z_score_window_days": 252,
        "oi_z_score_min_periods": 60,
        "oi_z_score_ddof": 1,
        "oi_z_score_buffer_multiplier": 1.5,
        "delta_oi_offset_rows": 2,
        "oi_trailing_range_window_days": 252,
        "volume_avg_window_days": 22,
        "ffill_limit_days": 5,
        "default_volume_field": "PX_VOLUME",
        "default_open_interest_field": "OPEN_INT",
        "short_rate_regime_map": (
            "SOFR_FUT=RFR,SONIA_FUT=RFR,EUR_SHORT_RATE_FUT=IBOR"
        ),
        "volume_round_decimals": 0,
        "oi_round_decimals": 0,
        "oi_z_score_round_decimals": 4,
        "oi_high_low_round_decimals": 0,
        "volume_avg_round_decimals": 0,
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
        assert cfg.tool.name == (
            "policy_futures_get_volume_open_interest_snapshot_tool"
        )
        assert cfg.tool.domain == "policy_futures"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "oi_z_score_window_days",
            "oi_z_score_min_periods",
            "oi_z_score_ddof",
            "oi_z_score_buffer_multiplier",
            "delta_oi_offset_rows",
            "oi_trailing_range_window_days",
            "volume_avg_window_days",
            "ffill_limit_days",
            "default_volume_field",
            "default_open_interest_field",
            "volume_round_decimals",
            "oi_round_decimals",
            "oi_z_score_round_decimals",
            "oi_high_low_round_decimals",
            "volume_avg_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("oi_z_score_window_days") == 252
        assert cfg.convention_value("oi_z_score_min_periods") == 60
        assert cfg.convention_value("oi_z_score_ddof") == 1
        assert cfg.convention_value("oi_z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("delta_oi_offset_rows") == 2
        assert cfg.convention_value("oi_trailing_range_window_days") == 252
        assert cfg.convention_value("volume_avg_window_days") == 22
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_volume_field") == "PX_VOLUME"
        assert (
            cfg.convention_value("default_open_interest_field") == "OPEN_INT"
        )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        # The two wire-frozen windows must both be named.
        assert "oi_trailing_range_window_days" in joined
        assert "volume_avg_window_days" in joined

    def test_oi_zscore_source_is_registered_tag(self):
        """The OI z-score lookback MUST disclose a registered source tag
        from docs_revamped/03_standards/methodology_disclosure.md §2."""
        cfg = load_tool_config(CONFIG_PATH)
        src = cfg.conventions["oi_z_score_window_days"].source
        assert src == "industry_standard_1y_window", (
            f"OI z-window source must be 'industry_standard_1y_window' "
            f"to match the registered tag; got {src!r}"
        )


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date", "curve_family", "strip_position",
            "contract_code", "underlying_contract_code", "security_name",
            "expiry_date", "contract_size",
            "current_volume", "current_open_interest",
            "delta_open_interest_1d", "oi_z_score",
            "oi_high_252d", "oi_low_252d", "oi_percentile_252d",
            "volume_rolling_mean_22d", "volume_rolling_max_22d",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # Unit-honest naming guards.
        assert "current_yield_pct" not in cm
        assert "current_volume_notional" not in cm
        assert "current_open_interest_notional" not in cm
        assert "delta_open_interest_bps" not in cm
        assert "oi_high_252d_pct" not in cm
        # price-side fields don't belong on a volume/OI snapshot.
        assert "raw_price" not in cm
        assert "implied_rate_pct" not in cm
        assert "quote_units" not in cm
        assert "tick_size" not in cm
        assert "tick_value" not in cm
        # inverse_priced flag is only relevant on the price-axis sibling.
        assert "inverse_priced" not in cm

        assert isinstance(cm["current_volume"], float)
        assert isinstance(cm["current_open_interest"], float)

        # Bespoke time_series shape: list of {date, volume, open_interest}.
        ts = out["time_series"]
        assert isinstance(ts, list)
        assert len(ts) > 0
        assert "date" in ts[0]
        assert "volume" in ts[0]
        assert "open_interest" in ts[0]
        # Not canonical TimeSeries.
        assert "value" not in ts[0]

    def test_explicit_config_matches_auto_loaded(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_reference_metadata_flows_to_snapshot(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="EUR_SHORT_RATE_FUT", strip_position=2,
            lookback_days=365,
        )
        ref = _synthetic_reference(
            curve_family="EUR_SHORT_RATE_FUT",
            contract_code="ER2",
            strip_position=2,
            underlying_contract_code="ERM26",
            security_name="ERM26 Comdty",
            expiry=date(2026, 9, 15),
            contract_size=2500.0,
        )
        out = _run(params, reference=ref)
        cm = out["current_metrics"]
        assert cm["curve_family"] == "EUR_SHORT_RATE_FUT"
        assert cm["contract_code"] == "ER2"
        assert cm["strip_position"] == 2
        assert cm["underlying_contract_code"] == "ERM26"
        assert cm["security_name"] == "ERM26 Comdty"
        assert cm["expiry_date"] == "2026-09-15"
        assert cm["contract_size"] == 2500.0

    def test_snapshot_equals_time_series_last_row_strictly(self):
        """current_volume / current_open_interest must equal
        time_series[-1].volume / open_interest byte-for-byte (same
        rounding pipeline)."""
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params)
        last_row = out["time_series"][-1]
        assert out["current_metrics"]["current_volume"] == last_row["volume"]
        assert (
            out["current_metrics"]["current_open_interest"]
            == last_row["open_interest"]
        )

    def test_methodology_disclosure_present_and_required(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params)
        md = out["methodology_disclosure"]
        # Catalog standardness guardrail: the OI z-score lookback window
        # MUST be stated explicitly in the methodology card.
        assert "252" in md, (
            "methodology_disclosure must name the OI z-score lookback "
            "verbatim (catalog standardness guardrail)"
        )
        assert "OI z-score lookback" in md
        # P5 / ADR 0013 caveats.
        assert "rolling-generic" in md.lower()
        assert "ADR 0013" in md
        assert "strip" in md.lower()
        # Schema must require it — pop and re-validate.
        from pydantic import ValidationError as _VE
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            VolumeOpenInterestSnapshotOutput.model_validate(bad)

    def test_methodology_disclosure_tracks_config_window(self):
        """If a custom config sets oi_z_score_window_days to 120, the
        methodology disclosure must surface 120 — never a hardcoded
        252 that the YAML can't reach."""
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, config=_custom_config(oi_z_score_window_days=120))
        assert "120" in out["methodology_disclosure"]
        # And the default 252 must NOT leak through.
        assert "= 252" not in out["methodology_disclosure"]


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_oi_zscore_window_override_changes_z(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_short = _run(
            params, config=_custom_config(oi_z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["oi_z_score"]
            != out_short["current_metrics"]["oi_z_score"]
        )

    def test_oi_zscore_ddof_override_changes_z(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_sample = _run(params, config=_custom_config(oi_z_score_ddof=1))
        out_pop = _run(params, config=_custom_config(oi_z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["oi_z_score"]
            != out_pop["current_metrics"]["oi_z_score"]
        )

    def test_delta_oi_offset_override_changes_delta(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_wider = _run(
            params, config=_custom_config(delta_oi_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["delta_open_interest_1d"]
            != out_wider["current_metrics"]["delta_open_interest_1d"]
        )

    def test_oi_round_decimals_propagates_to_time_series(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, config=_custom_config(oi_round_decimals=2))
        for row in out["time_series"]:
            assert row["open_interest"] == round(row["open_interest"], 2)
        # Snapshot must agree at the latest row.
        assert (
            out["current_metrics"]["current_open_interest"]
            == out["time_series"][-1]["open_interest"]
        )

    def test_default_field_names_threaded_to_fetch(self):
        """End-to-end proof that the YAML field-name conventions reach
        ``fetch_strip_position``."""
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
            compute as compute_mod,
        )

        captured_fields: list[str] = []

        def _spy_side_effect(
            *, engine, curve_family, strip_position, field_name,
            start_date, end_date=None,
        ):
            captured_fields.append(field_name)
            if field_name == "FUT_VOLUME_X":
                return _synthetic_volume_df()
            if field_name == "FUT_OI_X":
                return _synthetic_oi_df()
            return pd.DataFrame({"trade_date": [], "field_value": []})

        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        cfg = _custom_config(
            default_volume_field="FUT_VOLUME_X",
            default_open_interest_field="FUT_OI_X",
        )
        with patch.object(
            compute_mod, "fetch_strip_position",
            side_effect=_spy_side_effect,
        ), patch.object(
            compute_mod, "fetch_strip_position_reference",
            return_value=_synthetic_reference(),
        ), patch.object(
            compute_mod, "fetch_strip_position_max_date",
            return_value=None,
        ), patch.object(
            compute_mod, "date", _FrozenDate,
        ):
            out = calculate_volume_open_interest_snapshot(
                engine=None, params=params, config=cfg,
            )
        assert "error" not in out, out.get("error")
        assert captured_fields == ["FUT_VOLUME_X", "FUT_OI_X"], (
            f"YAML field-name conventions did not reach the fetch "
            f"helper. Got: {captured_fields}"
        )

    def test_ffill_limit_passed_to_clean(self):
        from shared.analytics.levels import clean_single_series as real_clean
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        with patch(
            f"{_TARGET}.fetch_strip_position",
            side_effect=lambda **kw: (
                _synthetic_volume_df() if kw["field_name"] == "PX_VOLUME"
                else _synthetic_oi_df()
            ),
        ), patch(
            f"{_TARGET}.fetch_strip_position_reference",
            return_value=_synthetic_reference(),
        ), patch(
            f"{_TARGET}.fetch_strip_position_max_date",
            return_value=None,
        ), patch(
            f"{_TARGET}.date", _FrozenDate,
        ), patch(
            f"{_TARGET}.clean_single_series",
            wraps=real_clean,
        ) as spy:
            calculate_volume_open_interest_snapshot(
                engine=None, params=params,
                config=_custom_config(ffill_limit_days=2),
            )
        # clean_single_series is called twice — once for volume, once
        # for OI. Both must use the override.
        assert spy.call_count == 2
        for call in spy.call_args_list:
            assert call.kwargs["ffill_limit"] == 2


# ===========================================================================
# 4. Honest-placeholder guards on wire-frozen windows
# ===========================================================================

class TestWireFrozenWindowGuards:
    def test_unsupported_oi_trailing_window_raises(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                config=_custom_config(oi_trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "oi_high_252d" in msg
        assert "planned_extensions" in msg

    def test_unsupported_volume_window_raises(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(params, config=_custom_config(volume_avg_window_days=10))
        msg = str(exc_info.value)
        assert "10" in msg
        assert "volume_rolling_mean_22d" in msg
        assert "planned_extensions" in msg

    def test_default_windows_do_not_raise(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, config=_custom_config(
            oi_trailing_range_window_days=252,
            volume_avg_window_days=22,
        ))
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_no_field_name_input_accepted(self):
        """field_name MUST NOT be an input — YAML owns the volume / OI
        field mnemonics. extra="forbid" should reject it."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            VolumeOpenInterestSnapshotInput(
                curve_family="SOFR_FUT",
                strip_position=1,
                field_name="PX_VOLUME",  # type: ignore[call-arg]
            )

    def test_curve_family_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            VolumeOpenInterestSnapshotInput(
                strip_position=1,  # type: ignore[call-arg]
            )

    def test_strip_position_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            VolumeOpenInterestSnapshotInput(
                curve_family="SOFR_FUT",  # type: ignore[call-arg]
            )

    def test_lookback_days_default(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1,
        )
        assert params.lookback_days == 365

    def test_as_of_date_default_is_none(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1,
        )
        assert params.as_of_date is None, (
            "schema default must be None so compute() anchors at the "
            "post-fetch data-max date"
        )

    def test_strip_position_lower_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            VolumeOpenInterestSnapshotInput(
                curve_family="SOFR_FUT", strip_position=0,
            )

    def test_strip_position_upper_bound_enforced(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            VolumeOpenInterestSnapshotInput(
                curve_family="SOFR_FUT", strip_position=13,
            )

    def test_input_extra_forbidden(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            VolumeOpenInterestSnapshotInput(
                curve_family="SOFR_FUT",
                strip_position=1,
                random_attr="x",  # type: ignore[call-arg]
            )


# ===========================================================================
# 6. Contract-count delta (NOT bps)
# ===========================================================================

class TestContractCountDelta:
    """delta_open_interest_1d is in CONTRACTS — raw subtraction.
    Multiplying by 100 would lie about the unit."""

    def test_delta_oi_is_raw_subtraction(self):
        days = 400
        drift = 50000.0
        frozen = date(2026, 4, 30)
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(
            params,
            oi_df=_synthetic_series_df(
                drift=drift, start=1000000.0, days=days, frozen_today=frozen,
            ),
        )
        # iloc[-1] - iloc[-2] over a linspace of `days` steps and `drift`
        # delta = drift / (days - 1).
        expected_step = drift / (days - 1)
        observed = out["current_metrics"]["delta_open_interest_1d"]
        # observed is rounded to oi_round_decimals (0); compare with
        # ample tolerance.
        assert abs(observed - expected_step) < 1.0

        # If we had multiplied by 100 (bps-style), the value would be
        # ~100x larger. Guard the lie explicitly.
        assert abs(observed) < expected_step * 5
        assert abs(observed - expected_step * 100) > 100


# ===========================================================================
# 7. Future-anchor guard (PR8 + PR16)
# ===========================================================================

class TestFutureAnchorGuard:
    def test_as_of_beyond_universe_max_returns_error(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
            as_of_date=date(2030, 1, 1),
        )
        out = _run(
            params,
            universe_max_date=date(2026, 4, 8),
        )
        assert "error" in out
        assert "no scoreable strip" in out["error"]
        assert "2030-01-01" in out["error"]
        assert "2026-04-08" in out["error"]
        # No snapshot should be returned when the guard fires.
        assert "current_metrics" not in out
        assert "time_series" not in out

    def test_as_of_within_universe_proceeds(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
            as_of_date=date(2026, 4, 8),
        )
        out = _run(
            params,
            universe_max_date=date(2026, 4, 30),
        )
        assert "error" not in out, out.get("error")
        assert "current_metrics" in out

    def test_as_of_none_skips_guard(self):
        """When as_of_date is None, the future-anchor probe is skipped
        — the post-fetch data-max anchor is honest by construction."""
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, universe_max_date=date(1900, 1, 1))
        # Should NOT return the future-anchor error even though
        # universe_max is in 1900 (the guard is skipped when as_of
        # is None).
        assert "error" not in out, out.get("error")

    def test_future_anchor_probe_uses_oi_field(self):
        """The future-anchor probe must be against the OI field (the
        headline series), NOT the volume field — the OI series is the
        positioning anchor."""
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
            compute as compute_mod,
        )

        captured_probe_field: list[str] = []

        def _max_date_spy(
            *, engine, curve_family, strip_position, field_name,
        ):
            captured_probe_field.append(field_name)
            return date(2026, 4, 8)

        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
            as_of_date=date(2026, 4, 8),
        )
        with patch.object(
            compute_mod, "fetch_strip_position",
            side_effect=lambda **kw: (
                _synthetic_volume_df() if kw["field_name"] == "PX_VOLUME"
                else _synthetic_oi_df()
            ),
        ), patch.object(
            compute_mod, "fetch_strip_position_reference",
            return_value=_synthetic_reference(),
        ), patch.object(
            compute_mod, "fetch_strip_position_max_date",
            side_effect=_max_date_spy,
        ), patch.object(
            compute_mod, "date", _FrozenDate,
        ):
            calculate_volume_open_interest_snapshot(
                engine=None, params=params,
            )
        assert captured_probe_field == ["OPEN_INT"], (
            "future-anchor probe must target the OI field (the headline "
            f"series); probed field(s): {captured_probe_field}"
        )


# ===========================================================================
# 8. Error envelopes
# ===========================================================================

class TestErrorEnvelopes:
    def test_missing_reference_returns_error(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=12, lookback_days=365,
        )
        out = _run(params, reference=None)
        assert "error" in out
        assert "strip_position=12" in out["error"]

    def test_empty_volume_history_returns_error(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        empty_df = pd.DataFrame({"trade_date": [], "field_value": []})
        out = _run(params, volume_df=empty_df)
        assert "error" in out
        assert "volume" in out["error"].lower()

    def test_empty_oi_history_returns_error(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        empty_df = pd.DataFrame({"trade_date": [], "field_value": []})
        out = _run(params, oi_df=empty_df)
        assert "error" in out
        assert "open" in out["error"].lower()


# ===========================================================================
# 9. Intersection alignment between volume + OI
# ===========================================================================

class TestIntersectionAlignment:
    """If volume and OI have different date supports, the snapshot must
    anchor to the latest day that appears in BOTH — never to a vol-only
    day with a stale OI reading or vice versa."""

    def test_snapshot_uses_latest_shared_date(self):
        frozen = date(2026, 4, 30)
        # Volume has 400 days ending at the frozen date.
        vol_df = _synthetic_series_df(
            drift=20000.0, start=600000.0, days=400, frozen_today=frozen,
        )
        # OI ends 3 BUSINESS DAYS earlier.
        oi_end = frozen - timedelta(days=5)  # 5 cal days ≈ 3 bdays
        oi_df = _synthetic_series_df(
            drift=50000.0, start=1000000.0, days=400, frozen_today=oi_end,
        )

        params = VolumeOpenInterestSnapshotInput(
            curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(params, volume_df=vol_df, oi_df=oi_df)
        # as_of_date should be no later than the OI series' end.
        as_of = date.fromisoformat(out["current_metrics"]["as_of_date"])
        assert as_of <= oi_end


# ===========================================================================
# 10. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
            calculate_volume_open_interest_snapshot as via_package,
        )
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot.compute import (
            calculate_volume_open_interest_snapshot as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
            VolumeOpenInterestSnapshotInput as via_package,
        )
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot.schemas import (
            VolumeOpenInterestSnapshotInput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            VolumeOpenInterestSnapshotInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
            VolumeOpenInterestSnapshotOutput as via_package,
        )
        from rates_agent.policy_futures.tools.volume_open_interest_snapshot.schemas import (
            VolumeOpenInterestSnapshotOutput as via_schemas,
        )
        from rates_agent.policy_futures.tools.schemas import (
            VolumeOpenInterestSnapshotOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 11. Per-curve_family RFR / IBOR regime disclosure (P5)
# ===========================================================================

class TestMethodologyDisclosureRegimeLabel:
    """The policy_futures domain spans both RFR (SOFR / SONIA) and IBOR
    (Euribor) regimes. The methodology disclosure must surface the
    per-curve_family regime label so a consumer relaying SFR1 OI vs ER1
    OI does not lose the fact that a Euribor position is structurally
    different from a SOFR position (P5 — honest disclosure)."""

    def test_methodology_disclosure_regime_label_per_curve_family(self):
        # SOFR_FUT — RFR.
        out_sofr = _run(
            VolumeOpenInterestSnapshotInput(
                curve_family="SOFR_FUT", strip_position=1, lookback_days=365,
            ),
            reference=_synthetic_reference(
                curve_family="SOFR_FUT", contract_code="SFR1",
                strip_position=1,
            ),
        )
        md_sofr = out_sofr["methodology_disclosure"]
        assert "RFR" in md_sofr
        assert "SOFR_FUT" in md_sofr

        # SONIA_FUT — RFR.
        out_sonia = _run(
            VolumeOpenInterestSnapshotInput(
                curve_family="SONIA_FUT", strip_position=1, lookback_days=365,
            ),
            reference=_synthetic_reference(
                curve_family="SONIA_FUT", contract_code="SFI1",
                strip_position=1,
            ),
        )
        md_sonia = out_sonia["methodology_disclosure"]
        assert "RFR" in md_sonia
        assert "SONIA_FUT" in md_sonia

        # EUR_SHORT_RATE_FUT — IBOR.
        out_eur = _run(
            VolumeOpenInterestSnapshotInput(
                curve_family="EUR_SHORT_RATE_FUT", strip_position=1,
                lookback_days=365,
            ),
            reference=_synthetic_reference(
                curve_family="EUR_SHORT_RATE_FUT", contract_code="ER1",
                strip_position=1,
            ),
        )
        md_eur = out_eur["methodology_disclosure"]
        assert "IBOR" in md_eur
        assert "EUR_SHORT_RATE_FUT" in md_eur

    def test_unknown_curve_family_returns_controlled_error(self):
        params = VolumeOpenInterestSnapshotInput(
            curve_family="MADE_UP_FUT", strip_position=1, lookback_days=365,
        )
        out = _run(
            params,
            reference=_synthetic_reference(
                curve_family="MADE_UP_FUT", contract_code="MU1",
                strip_position=1,
            ),
        )
        assert set(out.keys()) == {"error"}
        assert "short_rate_regime_map" in out["error"]
