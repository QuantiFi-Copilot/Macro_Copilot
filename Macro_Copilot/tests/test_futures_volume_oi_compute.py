"""
test_futures_volume_oi_compute.py — Unit tests for the bond-futures
                                     volume + open-interest monitor

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (oi z-window,
     ddof, ffill, delta_oi_offset, rounding).
  4. The honest-placeholder guards: setting either
     oi_trailing_range_window_days or volume_avg_window_days to
     anything other than the wire-frozen value raises
     NotImplementedError.
  5. Schema-layer behaviour: no field_name input is accepted (input-
     schema-overreach guard); curve_family / contract_code are
     required.
  6. Contract-count deltas: delta_open_interest_1d is RAW subtraction
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

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.bond_futures.tools.futures_volume_oi import (
    CONFIG_PATH,
    FuturesVolumeOIInput,
    FuturesVolumeOIOutput,
    calculate_futures_volume_oi,
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
    field_value_name: str = "field_value",
) -> pd.DataFrame:
    """Build a single long-format DataFrame matching the shape
    ``fetch_rolling_generic_series`` returns. Linspace from ``start``
    to ``start + drift`` over ``days`` business days ending at
    ``frozen_today``."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(start, start + drift, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        field_value_name: series,
    })


def _synthetic_volume_df(*, days: int = 400, frozen_today: date = date(2026, 4, 30)) -> pd.DataFrame:
    return _synthetic_series_df(
        drift=20000.0, start=600000.0, days=days, frozen_today=frozen_today,
    )


def _synthetic_oi_df(*, days: int = 400, frozen_today: date = date(2026, 4, 30)) -> pd.DataFrame:
    return _synthetic_series_df(
        drift=50000.0, start=3000000.0, days=days, frozen_today=frozen_today,
    )


def _synthetic_reference(
    *,
    curve_family: str = "UST_FUT",
    contract_code: str = "TY1",
    tenor: str = "10Y",
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
        "quote_units": "points",
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
    volume_df=None,
    oi_df=None,
    reference=_UNSET,
    config=None,
):
    if volume_df is None:
        volume_df = _synthetic_volume_df()
    if oi_df is None:
        oi_df = _synthetic_oi_df()
    ref_value = _synthetic_reference() if reference is _UNSET else reference

    # The compute fetches volume first then open_interest. Return the
    # right frame for each call via a closure-captured iterator.
    fetch_calls: list[str] = []

    def _series_side_effect(*, engine, curve_family, contract_code, field_name, start_date):
        fetch_calls.append(field_name)
        # Default-config field names: PX_VOLUME / OPEN_INT.
        if field_name in ("PX_VOLUME", "VOLUME"):
            return volume_df
        if field_name in ("OPEN_INT", "OI"):
            return oi_df
        # Unknown — return empty so the error path is exercised.
        return pd.DataFrame({"trade_date": [], "field_value": []})

    with patch(
        "rates_agent.bond_futures.tools.futures_volume_oi.compute.fetch_rolling_generic_series",
        side_effect=_series_side_effect,
    ), patch(
        "rates_agent.bond_futures.tools.futures_volume_oi.compute.fetch_rolling_generic_reference",
        return_value=ref_value,
    ), patch(
        "rates_agent.bond_futures.tools.futures_volume_oi.compute.date",
        _FrozenDate,
    ):
        return calculate_futures_volume_oi(engine=None, params=params, config=config)


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
        assert cfg.tool.name == "get_futures_volume_oi_tool"
        assert cfg.tool.domain == "bond_futures"

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
        assert cfg.convention_value("default_open_interest_field") == "OPEN_INT"

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
            f"OI z-window source must be 'industry_standard_1y_window' to "
            f"match the registered tag for a 1Y rolling window; got {src!r}"
        )


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date", "curve_family", "contract_code", "tenor",
            "contract_size", "expiry_date", "security_name",
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
        assert "quote_units" not in cm, (
            "quote_units belongs on price-side primitives, not volume/OI"
        )

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
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out_auto = _run(params, config=None)
        out_explicit = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_reference_metadata_flows_to_snapshot(self):
        params = FuturesVolumeOIInput(
            curve_family="DE_FUT", contract_code="RX1", lookback_days=365,
        )
        ref = _synthetic_reference(
            curve_family="DE_FUT",
            contract_code="RX1",
            tenor="10Y",
            contract_size=100000.0,
            security_name="RXZ6 Comdty",
        )
        out = _run(params, reference=ref)
        cm = out["current_metrics"]
        assert cm["curve_family"] == "DE_FUT"
        assert cm["contract_code"] == "RX1"
        assert cm["tenor"] == "10Y"
        assert cm["contract_size"] == 100000.0
        assert cm["security_name"] == "RXZ6 Comdty"

    def test_snapshot_equals_time_series_last_row_strictly(self):
        """current_volume / current_open_interest must equal
        time_series[-1].volume / open_interest byte-for-byte (same
        rounding pipeline)."""
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params)
        last_row = out["time_series"][-1]
        assert out["current_metrics"]["current_volume"] == last_row["volume"]
        assert (
            out["current_metrics"]["current_open_interest"]
            == last_row["open_interest"]
        )

    def test_methodology_disclosure_present_and_required(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params)
        md = out["methodology_disclosure"]
        # Catalog methodology guardrail: the OI z-score lookback window
        # MUST be stated explicitly in the methodology card.
        assert "252" in md, (
            "methodology_disclosure must name the OI z-score lookback "
            "verbatim (catalog guardrail)"
        )
        assert "OI z-score lookback" in md
        # P5 / ADR 0013 caveats.
        assert "rolling-generic" in md.lower()
        assert "ADR 0013" in md or "Phase-4" in md
        # Schema must require it — pop and re-validate.
        from pydantic import ValidationError as _VE
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            FuturesVolumeOIOutput.model_validate(bad)

    def test_methodology_disclosure_tracks_config_window(self):
        """If a custom config sets oi_z_score_window_days to 120, the
        methodology disclosure must surface 120 — never a hardcoded
        252 that the YAML can't reach."""
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
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
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_short = _run(params, config=_custom_config(oi_z_score_window_days=120))
        assert (
            out_default["current_metrics"]["oi_z_score"]
            != out_short["current_metrics"]["oi_z_score"]
        )

    def test_oi_zscore_ddof_override_changes_z(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out_sample = _run(params, config=_custom_config(oi_z_score_ddof=1))
        out_pop = _run(params, config=_custom_config(oi_z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["oi_z_score"]
            != out_pop["current_metrics"]["oi_z_score"]
        )

    def test_delta_oi_offset_override_changes_delta(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out_default = _run(params, config=_custom_config())
        out_wider = _run(params, config=_custom_config(delta_oi_offset_rows=5))
        assert (
            out_default["current_metrics"]["delta_open_interest_1d"]
            != out_wider["current_metrics"]["delta_open_interest_1d"]
        )

    def test_oi_round_decimals_propagates_to_time_series(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
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
        ``fetch_rolling_generic_series``."""
        from rates_agent.bond_futures.tools.futures_volume_oi import compute as compute_mod

        captured_fields: list[str] = []

        def _spy_side_effect(*, engine, curve_family, contract_code, field_name, start_date):
            captured_fields.append(field_name)
            if field_name == "FUT_VOLUME_X":
                return _synthetic_volume_df()
            if field_name == "FUT_OI_X":
                return _synthetic_oi_df()
            return pd.DataFrame({"trade_date": [], "field_value": []})

        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        cfg = _custom_config(
            default_volume_field="FUT_VOLUME_X",
            default_open_interest_field="FUT_OI_X",
        )
        with patch.object(
            compute_mod, "fetch_rolling_generic_series",
            side_effect=_spy_side_effect,
        ), patch.object(
            compute_mod, "fetch_rolling_generic_reference",
            return_value=_synthetic_reference(),
        ), patch.object(
            compute_mod, "date", _FrozenDate,
        ):
            out = calculate_futures_volume_oi(engine=None, params=params, config=cfg)
        assert "error" not in out, out.get("error")
        assert captured_fields == ["FUT_VOLUME_X", "FUT_OI_X"], (
            f"YAML field-name conventions did not reach the fetch helper. "
            f"Got: {captured_fields}"
        )


# ===========================================================================
# 4. Honest-placeholder guards on wire-frozen windows
# ===========================================================================

class TestWireFrozenWindowGuards:
    def test_unsupported_oi_trailing_window_raises(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(params, config=_custom_config(oi_trailing_range_window_days=180))
        msg = str(exc_info.value)
        assert "180" in msg
        assert "oi_high_252d" in msg
        assert "planned_extensions" in msg

    def test_unsupported_volume_window_raises(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(params, config=_custom_config(volume_avg_window_days=10))
        msg = str(exc_info.value)
        assert "10" in msg
        assert "volume_rolling_mean_22d" in msg
        assert "planned_extensions" in msg

    def test_default_windows_do_not_raise(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
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
            FuturesVolumeOIInput(
                curve_family="UST_FUT",
                contract_code="TY1",
                field_name="PX_VOLUME",  # type: ignore[call-arg]
            )

    def test_curve_family_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesVolumeOIInput(contract_code="TY1")  # type: ignore[call-arg]

    def test_contract_code_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesVolumeOIInput(curve_family="UST_FUT")  # type: ignore[call-arg]

    def test_lookback_days_default(self):
        params = FuturesVolumeOIInput(curve_family="UST_FUT", contract_code="TY1")
        assert params.lookback_days == 365

    def test_input_extra_forbidden(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            FuturesVolumeOIInput(
                curve_family="UST_FUT",
                contract_code="TY1",
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
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(
            params,
            oi_df=_synthetic_series_df(
                drift=drift, start=3000000.0, days=days, frozen_today=frozen,
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
# 7. Reference miss / empty data → error envelope
# ===========================================================================

class TestErrorEnvelopes:
    def test_missing_reference_returns_error(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="UNKNOWN1", lookback_days=365,
        )
        out = _run(params, reference=None)
        assert "error" in out
        assert "UNKNOWN1" in out["error"]

    def test_policy_futures_stem_returns_routing_error(self):
        params = FuturesVolumeOIInput(
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

    def test_empty_volume_history_returns_error(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        empty_df = pd.DataFrame({"trade_date": [], "field_value": []})
        out = _run(params, volume_df=empty_df)
        assert "error" in out
        assert "volume" in out["error"].lower()

    def test_empty_oi_history_returns_error(self):
        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        empty_df = pd.DataFrame({"trade_date": [], "field_value": []})
        out = _run(params, oi_df=empty_df)
        assert "error" in out
        # "open-interest" appears in the error message
        assert "open" in out["error"].lower()


# ===========================================================================
# 8. Intersection alignment between volume + OI
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
            drift=50000.0, start=3000000.0, days=400, frozen_today=oi_end,
        )

        params = FuturesVolumeOIInput(
            curve_family="UST_FUT", contract_code="TY1", lookback_days=365,
        )
        out = _run(params, volume_df=vol_df, oi_df=oi_df)
        # as_of_date should be no later than the OI series' end.
        as_of = date.fromisoformat(out["current_metrics"]["as_of_date"])
        assert as_of <= oi_end


# ===========================================================================
# 9. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.bond_futures.tools.futures_volume_oi import (
            calculate_futures_volume_oi as via_package,
        )
        from rates_agent.bond_futures.tools.futures_volume_oi.compute import (
            calculate_futures_volume_oi as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.bond_futures.tools.futures_volume_oi import (
            FuturesVolumeOIInput as via_package,
        )
        from rates_agent.bond_futures.tools.futures_volume_oi.schemas import (
            FuturesVolumeOIInput as via_schemas,
        )
        from rates_agent.bond_futures.tools.schemas import (
            FuturesVolumeOIInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.bond_futures.tools.futures_volume_oi import (
            FuturesVolumeOIOutput as via_package,
        )
        from rates_agent.bond_futures.tools.futures_volume_oi.schemas import (
            FuturesVolumeOIOutput as via_schemas,
        )
        from rates_agent.bond_futures.tools.schemas import (
            FuturesVolumeOIOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
