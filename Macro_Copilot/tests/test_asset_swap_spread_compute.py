"""
test_asset_swap_spread_compute.py — Layer-A offline tests for the per-bond
INGESTED Bloomberg ASW primitive.
=========================================================================

Covers:
  1. Bundled config.yaml structural validity + load (PR7 + PR12).
  2. extra='forbid' on schemas refuses methodology knobs (PR8 + Codex
     finding #6).
  3. min_length=1 on vendor_ticker refused at the Pydantic boundary.
  4. Bit-exact (no-rounding) preservation of the raw ASW value
     (catalog guardrail; Codex finding #4).
  5. Typed AssetSwapSpreadUnavailableError raised on:
       - vendor_ticker not in instrument_master
       - non-cash-bond instrument_type
       - NULL ASW on explicit as_of_date
       - empty fetch result
     (catalog guardrail + Codex finding #3)
  6. Methodology card disclosures: vendor_ingested source tag,
     swap_spread cross-reference, CAD sparsity caveat, security_name
     absence caveat (PR10 + Codex finding #5/#7).
  7. NotImplementedError guards on wire-frozen conventions (PR14).
  8. Explicit-config vs auto-load parity (PR7).

All tests are fully offline (engine mocked, fetcher patched).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from pydantic import ValidationError

from rates_agent.ois.tools.asset_swap_spread import (
    CONFIG_PATH,
    AssetSwapSpreadInput,
    AssetSwapSpreadOutput,
    AssetSwapSpreadUnavailableError,
    get_asset_swap_spread,
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


def _identity_row(instrument_type: str = "sovereign_cash_bond") -> dict:
    """The instrument_master identity row a healthy lookup returns."""
    return {
        "country": "Germany",
        "currency": "EUR",
        "cusip": "DI7485532",
        "isin": "DE000BU22130",
        "maturity_date": date(2028, 6, 14),
        "instrument_type": instrument_type,
    }


def _build_engine_mock(identity_row: object) -> MagicMock:
    """Mock engine whose .connect().execute(...).mappings().first()
    returns the identity row.  ``identity_row=None`` simulates the
    vendor_ticker-not-found branch."""
    mock_engine = MagicMock(name="engine")
    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = identity_row
    mock_conn.execute.return_value = mock_result
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    return mock_engine


def _build_asw_df(values: list, dates: list) -> pd.DataFrame:
    """Build a long-format DataFrame matching the
    fetch_single_bond_series return shape."""
    return pd.DataFrame({"trade_date": dates, "field_value": values})


# ===========================================================================
# 1. Bundled config.yaml — PR7 + PR12
# ===========================================================================


class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "get_asset_swap_spread_tool"
        assert cfg.tool.domain == "ois"
        assert cfg.tool.category == "desk_invariant_primitive"

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
            "default_asw_field_name",
            "data_source_policy",
            "z_score_round_decimals",
            "bps_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_default_asw_field_name_is_bloomberg(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("default_asw_field_name") == "ASSET_SWAP_SPD_MID"

    def test_data_source_policy_is_vendor_ingested(self):
        """The load-bearing P12 commitment.  The convention's source
        tag MUST be ``vendor_ingested`` (Codex finding #5)."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("data_source_policy") == "bloomberg_ingested_asw"
        assert cfg.conventions["data_source_policy"].source == "vendor_ingested"

    def test_convention_sources_are_registered(self):
        """PR12 — every Convention.source value is a registered tag from
        docs_revamped/03_standards/methodology_disclosure.md."""
        cfg = load_tool_config(CONFIG_PATH)
        registered = {
            "industry_standard_1y_window",
            "industry_standard_sample_std",
            "team_judgment_pending_review",
            "derived_from_window",
            "trading_day_convention",
            "bloomberg_field_convention",
            "legacy_default_pre_pilot",
            "vendor_ingested",
        }
        for name, conv in cfg.conventions.items():
            assert conv.source in registered, (
                f"convention {name!r} has unregistered source {conv.source!r}"
            )

    def test_methodology_assumptions_include_pr4_differentiation(self):
        """PR4 differentiation from swap_spread is the load-bearing
        catalog guardrail — its presence in methodology.assumptions is
        the wire-visible disclosure."""
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.assumptions)
        assert "swap_spread" in joined.lower()
        assert "par-par" in joined.lower() or "par_par" in joined.lower()

    def test_methodology_assumptions_include_field_availability_caveat(self):
        """security_name and issuer are catalog-required but absent in
        V1 — must be P5-disclosed (Codex finding #7)."""
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.assumptions)
        assert "security_name" in joined
        assert "issuer" in joined

    def test_methodology_assumptions_include_no_rounding(self):
        """Raw ASW preservation discipline must be wire-visible."""
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.assumptions)
        assert "NOT rounded" in joined or "bit-exact" in joined.lower()

    def test_methodology_assumptions_include_typed_exception(self):
        """The NULL → AssetSwapSpreadUnavailableError discipline must
        be wire-visible (catalog guardrail)."""
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.assumptions)
        assert "AssetSwapSpreadUnavailableError" in joined

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2


# ===========================================================================
# 2. Schema typed-boundary discipline (extra='forbid', min_length)
# ===========================================================================


class TestSchemaTypedBoundary:
    def test_extra_input_rejected(self):
        """Codex finding #6 — methodology knobs cannot smuggle in
        under unknown-field tolerance."""
        with pytest.raises(ValidationError, match="Extra inputs"):
            AssetSwapSpreadInput(
                vendor_ticker="/isin/X",
                methodology_choice="par_par",  # type: ignore[call-arg]
            )

    def test_empty_vendor_ticker_rejected(self):
        with pytest.raises(ValidationError):
            AssetSwapSpreadInput(vendor_ticker="")

    def test_lookback_lower_bound(self):
        with pytest.raises(ValidationError):
            AssetSwapSpreadInput(vendor_ticker="/isin/X", lookback_days=10)

    def test_lookback_upper_bound(self):
        with pytest.raises(ValidationError):
            AssetSwapSpreadInput(vendor_ticker="/isin/X", lookback_days=99999)

    def test_field_name_defaults_to_none_sentinel(self):
        p = AssetSwapSpreadInput(vendor_ticker="/isin/X")
        assert p.field_name is None
        assert p.as_of_date is None

    def test_output_extra_forbid(self):
        """The output model must also enforce extra='forbid' so a
        future refactor cannot silently add a field that downstream
        consumers don't know to handle."""
        assert AssetSwapSpreadOutput.model_config.get("extra") == "forbid"

    def test_input_extra_forbid_config(self):
        assert AssetSwapSpreadInput.model_config.get("extra") == "forbid"


# ===========================================================================
# 3. Happy path — bit-exact preservation of raw ASW (Codex finding #4)
# ===========================================================================


class TestHappyPathBitExact:
    @staticmethod
    def _run(params: AssetSwapSpreadInput, raw_df: pd.DataFrame, identity: object):
        engine = _build_engine_mock(identity)
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=raw_df,
        ):
            return get_asset_swap_spread(engine=engine, params=params)

    def test_raw_value_preserved_bit_exact(self):
        """The DB value -19.47100000 must round-trip to -19.471 in
        Python float — and the snapshot's current_asw_spread_bps must
        equal the raw float bit-exact (no further rounding).  Critical
        for SQL parity within 1e-9 (catalog guardrail / Codex #4)."""
        raw_df = _build_asw_df(
            values=[-19.45100000, -19.46200000, -19.47100000],
            dates=[date(2026, 5, 19), date(2026, 5, 20), date(2026, 5, 21)],
        )
        p = AssetSwapSpreadInput(
            vendor_ticker="/isin/DE000BU22130",
            as_of_date=date(2026, 5, 21),
            lookback_days=30,
        )
        out = self._run(p, raw_df, _identity_row())

        # Bit-exact: float(-19.471) == raw[-1] in the DataFrame.
        assert out["current_metrics"]["current_asw_spread_bps"] == -19.471
        # TimeSeries last row also bit-exact (no rounding).
        assert out["time_series"]["rows"][-1]["value"] == -19.471
        # And matches the snapshot strictly.
        assert (
            out["time_series"]["rows"][-1]["value"]
            == out["current_metrics"]["current_asw_spread_bps"]
        )

    def test_as_of_date_snapshot_matches_supplied_date(self):
        raw_df = _build_asw_df(
            values=[1.1, 2.2, 3.3],
            dates=[date(2026, 5, 19), date(2026, 5, 20), date(2026, 5, 21)],
        )
        p = AssetSwapSpreadInput(
            vendor_ticker="/isin/DE000BU22130",
            as_of_date=date(2026, 5, 20),
            lookback_days=30,
        )
        out = self._run(p, raw_df, _identity_row())
        assert out["current_metrics"]["as_of_date"] == "2026-05-20"
        assert out["current_metrics"]["current_asw_spread_bps"] == 2.2

    def test_latest_mode_resolves_to_most_recent_non_null(self):
        """When as_of_date is omitted, snapshot is the most recent
        non-NULL observation (NOT date.today())."""
        raw_df = _build_asw_df(
            values=[1.1, 2.2, None, 3.3, None],  # NULLs not at the tail
            dates=[
                date(2026, 5, 17),
                date(2026, 5, 18),
                date(2026, 5, 19),
                date(2026, 5, 20),
                date(2026, 5, 21),
            ],
        )
        p = AssetSwapSpreadInput(
            vendor_ticker="/isin/DE000BU22130",
            lookback_days=30,
        )
        out = self._run(p, raw_df, _identity_row())
        # Latest non-NULL is 2026-05-20 with value 3.3 (the tail NULL on
        # 2026-05-21 is skipped per dropna in latest mode).
        assert out["current_metrics"]["as_of_date"] == "2026-05-20"
        assert out["current_metrics"]["current_asw_spread_bps"] == 3.3

    def test_identity_fields_echoed(self):
        """Catalog's required_reference_metrics — country, currency,
        cusip, isin, maturity_date all echoed."""
        raw_df = _build_asw_df([1.0], [date(2026, 5, 21)])
        p = AssetSwapSpreadInput(vendor_ticker="/isin/DE000BU22130")
        out = self._run(p, raw_df, _identity_row())
        cm = out["current_metrics"]
        assert cm["country"] == "Germany"
        assert cm["currency"] == "EUR"
        assert cm["cusip"] == "DI7485532"
        assert cm["isin"] == "DE000BU22130"
        assert cm["maturity_date"] == "2028-06-14"
        assert cm["vendor_ticker"] == "/isin/DE000BU22130"


# ===========================================================================
# 4. Typed P6 refusal — AssetSwapSpreadUnavailableError (Codex #3)
# ===========================================================================


class TestTypedRefusal:
    def test_vendor_ticker_not_found_raises(self):
        engine = _build_engine_mock(identity_row=None)
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=_build_asw_df([], []),
        ):
            with pytest.raises(AssetSwapSpreadUnavailableError) as exc:
                get_asset_swap_spread(
                    engine=engine,
                    params=AssetSwapSpreadInput(vendor_ticker="/isin/MISSING"),
                )
        assert "instrument_master" in exc.value.reason
        assert exc.value.vendor_ticker == "/isin/MISSING"

    def test_non_cash_bond_instrument_type_raises(self):
        """Defence-in-depth — vendor_ticker that exists but with a
        different instrument_type cannot be silently proxied."""
        engine = _build_engine_mock(
            identity_row=_identity_row(instrument_type="ois_swap")
        )
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=_build_asw_df([1.0], [date(2026, 5, 21)]),
        ):
            with pytest.raises(AssetSwapSpreadUnavailableError) as exc:
                get_asset_swap_spread(
                    engine=engine,
                    params=AssetSwapSpreadInput(vendor_ticker="/isin/X"),
                )
        assert "ois_swap" in exc.value.reason
        assert "sovereign_cash_bond" in exc.value.reason

    def test_null_asw_on_explicit_as_of_date_raises(self):
        """No ffill on the explicit-date path (catalog guardrail)."""
        raw_df = _build_asw_df(
            values=[1.0, None],
            dates=[date(2026, 5, 20), date(2026, 5, 21)],
        )
        engine = _build_engine_mock(_identity_row())
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=raw_df,
        ):
            with pytest.raises(AssetSwapSpreadUnavailableError) as exc:
                get_asset_swap_spread(
                    engine=engine,
                    params=AssetSwapSpreadInput(
                        vendor_ticker="/isin/X",
                        as_of_date=date(2026, 5, 21),
                    ),
                )
        assert "NULL" in exc.value.reason
        assert exc.value.as_of_date == date(2026, 5, 21)

    def test_missing_row_on_explicit_as_of_date_raises(self):
        """No fall-through to a sibling date — explicit date with no
        row at all is a typed refusal."""
        raw_df = _build_asw_df(
            values=[1.0, 2.0],
            dates=[date(2026, 5, 19), date(2026, 5, 20)],
        )
        engine = _build_engine_mock(_identity_row())
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=raw_df,
        ):
            with pytest.raises(AssetSwapSpreadUnavailableError) as exc:
                get_asset_swap_spread(
                    engine=engine,
                    params=AssetSwapSpreadInput(
                        vendor_ticker="/isin/X",
                        as_of_date=date(2026, 5, 21),  # not in data
                    ),
                )
        assert "No ASW row" in exc.value.reason or "does not publish" in exc.value.reason

    def test_empty_result_set_raises(self):
        engine = _build_engine_mock(_identity_row())
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=_build_asw_df([], []),
        ):
            with pytest.raises(AssetSwapSpreadUnavailableError) as exc:
                get_asset_swap_spread(
                    engine=engine,
                    params=AssetSwapSpreadInput(vendor_ticker="/isin/X"),
                )
        assert "No ASW observations" in exc.value.reason


# ===========================================================================
# 5. NotImplementedError guards on wire-frozen conventions (PR14)
# ===========================================================================


def _custom_config(**overrides) -> ToolConfig:
    """Build a ToolConfig with overridable conventions."""
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
        "default_asw_field_name": "ASSET_SWAP_SPD_MID",
        "data_source_policy": "bloomberg_ingested_asw",
        "z_score_round_decimals": 4,
        "bps_round_decimals": 2,
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(
            name="t", domain="d", description="x",
            category="desk_invariant_primitive",
        ),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


class TestConventionGuards:
    def test_unsupported_trailing_window_raises(self):
        engine = _build_engine_mock(_identity_row())
        raw_df = _build_asw_df([1.0], [date(2026, 5, 21)])
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=raw_df,
        ):
            with pytest.raises(NotImplementedError, match="trailing_range_window_days"):
                get_asset_swap_spread(
                    engine=engine,
                    params=AssetSwapSpreadInput(vendor_ticker="/isin/X"),
                    config=_custom_config(trailing_range_window_days=180),
                )

    def test_unsupported_data_source_policy_raises(self):
        engine = _build_engine_mock(_identity_row())
        raw_df = _build_asw_df([1.0], [date(2026, 5, 21)])
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=raw_df,
        ):
            with pytest.raises(NotImplementedError, match="data_source_policy"):
                get_asset_swap_spread(
                    engine=engine,
                    params=AssetSwapSpreadInput(vendor_ticker="/isin/X"),
                    config=_custom_config(
                        data_source_policy="warehouse_ingested_asw"
                    ),
                )


# ===========================================================================
# 6. Methodology note + output shape (PR10)
# ===========================================================================


class TestMethodologyNote:
    def test_methodology_note_required_field(self):
        with pytest.raises(ValidationError):
            AssetSwapSpreadOutput(
                current_metrics={
                    "as_of_date": "2026-05-21",
                    "vendor_ticker": "/isin/X",
                    "current_asw_spread_bps": 1.0,
                    "observation_count": 1,
                    "lookback_days": 365,
                },
                time_series={
                    "series_name": "asw_spread_x",
                    "units": "bps",
                    "description": "x",
                    "rows": [],
                },
            )  # type: ignore[call-arg]

    def test_methodology_note_disclosures(self):
        """Output methodology_note must surface the P12 + swap_spread
        + CAD sparsity + security_name disclosures (PR10 / Codex #7)."""
        engine = _build_engine_mock(_identity_row())
        raw_df = _build_asw_df([1.0], [date(2026, 5, 21)])
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=raw_df,
        ):
            out = get_asset_swap_spread(
                engine=engine,
                params=AssetSwapSpreadInput(vendor_ticker="/isin/X"),
            )
        note = out["methodology_note"]
        assert "P12" in note
        assert "swap_spread" in note
        assert "CAD" in note or "Canada" in note.lower()
        assert "security_name" in note
        assert "AssetSwapSpreadUnavailableError" in note


# ===========================================================================
# 7. Explicit-config vs auto-load parity (PR7)
# ===========================================================================


class TestExplicitConfigParity:
    def test_explicit_config_equals_auto_load(self):
        engine = _build_engine_mock(_identity_row())
        raw_df = _build_asw_df(
            values=[1.1, 2.2, 3.3],
            dates=[date(2026, 5, 19), date(2026, 5, 20), date(2026, 5, 21)],
        )
        p = AssetSwapSpreadInput(
            vendor_ticker="/isin/X",
            as_of_date=date(2026, 5, 21),
            lookback_days=30,
        )
        with patch(
            "rates_agent.ois.tools.asset_swap_spread.compute."
            "fetch_single_bond_series",
            return_value=raw_df,
        ):
            out_auto = get_asset_swap_spread(engine=engine, params=p)
            out_explicit = get_asset_swap_spread(
                engine=engine, params=p,
                config=load_tool_config(CONFIG_PATH),
            )
        assert out_auto == out_explicit


# ===========================================================================
# 8. Import paths
# ===========================================================================


class TestImports:
    def test_compute_via_package_init(self):
        from rates_agent.ois.tools.asset_swap_spread import (
            get_asset_swap_spread as via_package,
        )
        from rates_agent.ois.tools.asset_swap_spread.compute import (
            get_asset_swap_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_via_schemas_hub(self):
        from rates_agent.ois.tools.asset_swap_spread import (
            AssetSwapSpreadInput as via_package,
        )
        from rates_agent.ois.tools.asset_swap_spread.schemas import (
            AssetSwapSpreadInput as via_schemas,
        )
        from rates_agent.ois.tools.schemas import (
            AssetSwapSpreadInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_typed_exception_class_via_hub(self):
        from rates_agent.ois.tools.asset_swap_spread import (
            AssetSwapSpreadUnavailableError as via_package,
        )
        from rates_agent.ois.tools.schemas import (
            AssetSwapSpreadUnavailableError as via_hub,
        )
        assert via_package is via_hub


# ===========================================================================
# 9. PR4 symmetric cite in swap_spread (Codex finding #8 — wiring)
# ===========================================================================


class TestSymmetricCite:
    def test_swap_spread_methodology_cites_asset_swap_spread(self):
        """Catalog guardrail — BOTH primitives' methodology cards must
        cite each other.  This pins the swap_spread side."""
        from rates_agent.ois.tools.swap_spread import (
            CONFIG_PATH as SS_CONFIG,
        )
        ss_cfg = load_tool_config(SS_CONFIG)
        joined = " | ".join(ss_cfg.methodology.assumptions)
        assert "asset_swap_spread" in joined
        assert "INGESTED" in joined or "vendor-ingested" in joined.lower()
