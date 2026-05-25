"""
test_scan_inflation_swaps_extremes_compute.py — Unit tests for the
                                                 ZCIS universe-wide
                                                 extremes scan

Layer A offline deterministic tests for
``calculate_scan_inflation_swaps_extremes``.  Covers:

  1. Bundled config.yaml is structurally valid + loads cleanly +
     declares the required convention keys.
  2. compute() runs end-to-end against synthetic universe data with
     the bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window,
     min_periods, ddof, ffill_limit, threshold rounding).
  4. Top-N + min_abs_z_score filtering behaves correctly.
  5. Deterministic ordering: ties on |z| broken by curve_family asc
     then tenor asc.
  6. Methodology disclosure: present on EVERY row AND on the
     response; contains the z-score lookback verbatim; contains the
     INDEX-FAMILY + MARKET-STRUCTURE caveats; contains the
     morning-screen / ZCIS-rate-scope label.
  7. Per-row reference columns (maturity_date / underlying_index /
     vendor_ticker) attach to every output row.
  8. Edge cases: empty universe, all stems below min_periods,
     threshold too high, future as_of_date.
  9. Schema-layer behaviour: closed-family whitelist refusal; no
     ``field_name`` / ``lookback_days`` / ``metrics`` inputs.
 10. None-sentinel resolution for ``top_n`` and ``min_abs_z_score``
     falls through to the YAML's defaults.
 11. PR14 wire-freeze guard: changing ``z_score_window_days`` away
     from 252 raises ``NotImplementedError``.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
    CONFIG_PATH,
    ScanInflationSwapsExtremesInput,
    ScanInflationSwapsExtremesOutput,
    calculate_scan_inflation_swaps_extremes,
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
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


_FROZEN_TODAY = date(2026, 4, 8)


class _FrozenDate(date):
    _frozen_value: date = _FROZEN_TODAY

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# Default V1 ZCIS universe — matches the YAML's
# ``inflation_swap_curve_families`` whitelist on a per-tenor basis
# (one representative tenor per curve family is enough for the
# offline tests; the SQL validation runs the live universe).
_DEFAULT_STEMS = [
    ("USD_ZCIS", "2Y"),
    ("USD_ZCIS", "5Y"),
    ("USD_ZCIS", "10Y"),
    ("USD_ZCIS", "30Y"),
    ("EUR_ZCIS", "5Y"),
    ("EUR_ZCIS", "10Y"),
    ("GBP_ZCIS", "10Y"),
]

# Reference rows the (extended) fetcher returns for those stems.
# Mirrors the live DB shape — maturity_date / underlying_index /
# vendor_ticker attached per (curve_family, tenor). ZCIS rows have
# NULL contract_code on the live DB; the helper exposes it for
# universality but compute collapses on (curve_family, tenor).
_DEFAULT_REFERENCE = [
    {
        "curve_family": "USD_ZCIS", "tenor": "2Y",
        "contract_code": None, "maturity_date": date(2028, 3, 15),
        "country": "US",
        "vendor_ticker": "USSWIT2 Curncy",
        "underlying_index": "CPURNSA Index",
    },
    {
        "curve_family": "USD_ZCIS", "tenor": "5Y",
        "contract_code": None, "maturity_date": date(2031, 3, 15),
        "country": "US",
        "vendor_ticker": "USSWIT5 Curncy",
        "underlying_index": "CPURNSA Index",
    },
    {
        "curve_family": "USD_ZCIS", "tenor": "10Y",
        "contract_code": None, "maturity_date": date(2036, 3, 15),
        "country": "US",
        "vendor_ticker": "USSWIT10 Curncy",
        "underlying_index": "CPURNSA Index",
    },
    {
        "curve_family": "USD_ZCIS", "tenor": "30Y",
        "contract_code": None, "maturity_date": date(2056, 3, 15),
        "country": "US",
        "vendor_ticker": "USSWIT30 Curncy",
        "underlying_index": "CPURNSA Index",
    },
    {
        "curve_family": "EUR_ZCIS", "tenor": "5Y",
        "contract_code": None, "maturity_date": date(2031, 3, 15),
        "country": "EU",
        "vendor_ticker": "EUSWI5 Curncy",
        "underlying_index": "CPTFEMU Index",
    },
    {
        "curve_family": "EUR_ZCIS", "tenor": "10Y",
        "contract_code": None, "maturity_date": date(2036, 3, 15),
        "country": "EU",
        "vendor_ticker": "EUSWI10 Curncy",
        "underlying_index": "CPTFEMU Index",
    },
    {
        "curve_family": "GBP_ZCIS", "tenor": "10Y",
        "contract_code": None, "maturity_date": date(2036, 3, 15),
        "country": "UK",
        "vendor_ticker": "BPSWIT10 Curncy",
        "underlying_index": "UKRPI Index",
    },
]


def _build_universe_df(
    *,
    stems: list[tuple[str, str]],
    per_stem_drift: dict[tuple[str, str], float],
    base: float,
    days: int,
    frozen_today: date,
) -> pd.DataFrame:
    """Long-format DataFrame matching ``fetch_scan_universe`` shape
    (columns: trade_date, curve_family, tenor, contract_code,
    field_value)."""
    rows = []
    bdays = pd.bdate_range(
        frozen_today - timedelta(days=days * 2), frozen_today,
    )
    bdays = bdays[-days:]
    n = len(bdays)
    for (cf, tenor) in stems:
        drift = per_stem_drift.get((cf, tenor), 0.0)
        values = np.linspace(base, base + drift, n)
        for ts, value in zip(bdays, values):
            rows.append({
                "trade_date": ts.date(),
                "curve_family": cf,
                "tenor": tenor,
                "contract_code": None,
                "field_value": float(value),
            })
    return pd.DataFrame(rows)


def _default_field_df(
    stems=_DEFAULT_STEMS,
    days: int = 400,
    frozen_today: date = _FROZEN_TODAY,
):
    """Produce a long-format DataFrame with deliberate per-stem drift
    asymmetry so the level z-score ranking is non-trivial."""
    drifts = {
        # 10-year GBP exhibits the largest upward drift → most
        # extreme z.
        ("GBP_ZCIS", "10Y"): 2.5,
        # USD_ZCIS 2Y exhibits a moderate downward drift → 2nd in
        # |z|.
        ("USD_ZCIS", "2Y"): -1.8,
        # USD_ZCIS 30Y exhibits a smaller positive drift.
        ("USD_ZCIS", "30Y"): 1.0,
        # EUR_ZCIS 5Y has a small negative drift.
        ("EUR_ZCIS", "5Y"): -0.5,
        # The rest stay roughly flat.
        ("USD_ZCIS", "5Y"): 0.1,
        ("USD_ZCIS", "10Y"): 0.2,
        ("EUR_ZCIS", "10Y"): -0.1,
    }
    return _build_universe_df(
        stems=stems, per_stem_drift=drifts, base=2.0,
        days=days, frozen_today=frozen_today,
    )


def _run(
    params,
    *,
    field_df=None,
    reference_df=None,
    config=None,
):
    """Patch fetch_scan_universe + fetch_scan_universe_reference +
    date.today() and invoke compute."""
    if field_df is None:
        field_df = _default_field_df()
    if reference_df is None:
        reference_df = pd.DataFrame(_DEFAULT_REFERENCE)

    captured_calls: dict = {}

    def _scan_universe_side_effect(
        *, engine, instrument_type, field_name, start_date,
        curve_families=None,
    ):
        captured_calls["scan_universe"] = {
            "instrument_type": instrument_type,
            "field_name": field_name,
            "start_date": start_date,
            "curve_families": (
                list(curve_families) if curve_families else None
            ),
        }
        return field_df.copy()

    def _scan_reference_side_effect(
        *, engine, instrument_type, curve_families=None,
    ):
        captured_calls["scan_reference"] = {
            "instrument_type": instrument_type,
            "curve_families": (
                list(curve_families) if curve_families else None
            ),
        }
        return reference_df.copy()

    target = (
        "rates_agent.inflation_swaps.tools."
        "scan_inflation_swaps_extremes.compute"
    )
    with patch(
        f"{target}.fetch_scan_universe",
        side_effect=_scan_universe_side_effect,
    ), patch(
        f"{target}.fetch_scan_universe_reference",
        side_effect=_scan_reference_side_effect,
    ), patch(
        f"{target}.date", _FrozenDate,
    ):
        result = calculate_scan_inflation_swaps_extremes(
            engine=None, params=params, config=config,
        )
    return result, captured_calls


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "z_score_window_days": 252,
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "ffill_limit_days": 5,
        "default_zcis_rate_field": "PX_MID",
        "daily_change_offset_rows": 2,
        "monthly_change_offset_rows": 22,
        "inflation_swap_curve_families": (
            "USD_ZCIS,EUR_ZCIS,GBP_ZCIS"
        ),
        "yield_round_decimals": 4,
        "z_score_round_decimals": 4,
        "bps_change_round_decimals": 2,
        "default_top_n": 5,
        "default_min_abs_z_score": 1.5,
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(
            name="scan_inflation_swaps_extremes_tool",
            domain="inflation_swaps",
            description="test",
        ),
        methodology=MethodologyMeta(what_it_does="test"),
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
        assert cfg.tool.name == "scan_inflation_swaps_extremes_tool"
        assert cfg.tool.domain == "inflation_swaps"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "ffill_limit_days",
            "default_zcis_rate_field",
            "daily_change_offset_rows",
            "monthly_change_offset_rows",
            "inflation_swap_curve_families",
            "yield_round_decimals",
            "z_score_round_decimals",
            "bps_change_round_decimals",
            "default_top_n",
            "default_min_abs_z_score",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_zcis_rate_field") == "PX_MID"
        assert cfg.convention_value("daily_change_offset_rows") == 2
        assert cfg.convention_value("monthly_change_offset_rows") == 22
        assert cfg.convention_value("default_top_n") == 5
        assert cfg.convention_value("default_min_abs_z_score") == 1.5
        wl = cfg.convention_value("inflation_swap_curve_families")
        assert isinstance(wl, str)
        for cf in ["USD_ZCIS", "EUR_ZCIS", "GBP_ZCIS"]:
            assert cf in wl
        # Non-ZCIS curve_families must NOT be in the whitelist.
        for cf in ["UST", "DE_BUND", "USD_TIPS", "GBP_LINKER",
                   "USD_SOFR_OIS"]:
            assert cf not in wl

    def test_z_score_window_source_is_registered_tag(self):
        cfg = load_tool_config(CONFIG_PATH)
        src = cfg.conventions["z_score_window_days"].source
        assert src == "industry_standard_1y_window", (
            f"z-window source must be 'industry_standard_1y_window' "
            f"to match the registered tag for a 1Y rolling window; "
            f"got {src!r}"
        )

    def test_methodology_what_it_does_mentions_zcis(self):
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        assert "zcis" in text
        assert "universe" in text

    def test_methodology_assumptions_mention_index_family(self):
        cfg = load_tool_config(CONFIG_PATH)
        joined = " | ".join(cfg.methodology.assumptions).lower()
        assert "index-family" in joined or "index family" in joined
        assert "market-structure" in joined or "market structure" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        assert "error" not in result
        ScanInflationSwapsExtremesOutput.model_validate(result)
        assert isinstance(result["scan_summary"], str)
        assert len(result["results"]) > 0
        assert "methodology_disclosure" in result

    def test_results_ordered_by_abs_z_desc(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        zs = [abs(r["z_score_zcis_rate"]) for r in result["results"]]
        assert zs == sorted(zs, reverse=True), (
            f"results must be ordered by |z| desc; got {zs}"
        )

    def test_rank_starts_at_1_and_is_contiguous(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        ranks = [r["rank"] for r in result["results"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_signal_matches_z_sign(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        for r in result["results"]:
            z = r["z_score_zcis_rate"]
            if z is not None and z > 0:
                assert r["signal"] == "EXTREME_HIGH"
            elif z is not None and z < 0:
                assert r["signal"] == "EXTREME_LOW"

    def test_fetcher_invoked_with_inflation_swap_instrument_type(self):
        """Load-bearing no-proxy guard: the fetcher MUST be invoked
        with instrument_type='inflation_swap' and field_name='PX_MID'."""
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        _result, calls = _run(params)
        assert calls["scan_universe"]["instrument_type"] == "inflation_swap"
        assert calls["scan_universe"]["field_name"] == "PX_MID"
        assert calls["scan_reference"]["instrument_type"] == "inflation_swap"


# ===========================================================================
# 3. Methodology disclosure (catalog guardrail — P5)
# ===========================================================================

class TestMethodologyDisclosure:
    def test_disclosure_on_response(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        md = result["methodology_disclosure"]
        assert isinstance(md, str) and len(md) > 0

    def test_disclosure_on_every_row(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        assert len(result["results"]) > 0
        for row in result["results"]:
            assert "methodology_disclosure" in row
            assert len(row["methodology_disclosure"]) > 0

    def test_disclosure_contains_252_lookback(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        # Catalog methodology guardrail: the lookback window must
        # be disclosed verbatim.
        assert "252" in result["methodology_disclosure"]
        for row in result["results"]:
            assert "252" in row["methodology_disclosure"]

    def test_disclosure_calls_out_zcis_scope(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        md = result["methodology_disclosure"].lower()
        # ZCIS / inflation-swap quoted-rate scope.
        assert "zcis" in md or "inflation swap" in md
        # Explicit "NOT a breakeven scan" disclosure.
        assert "not a breakeven" in md
        # NOT a linker real-yield scan disclosure.
        assert "real-yield" in md or "real yield" in md

    def test_disclosure_index_family_caveat(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        md = result["methodology_disclosure"].lower()
        assert "index-family" in md or "index family" in md
        # Specific index references for cross-currency honesty.
        for ix in ["cpi-u", "hicp", "rpi"]:
            assert ix in md, (
                f"index reference '{ix}' missing from disclosure"
            )

    def test_disclosure_market_structure_caveat(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        md = result["methodology_disclosure"].lower()
        assert "market-structure" in md or "market structure" in md
        # Index-lag wording (the load-bearing ZCIS-specific
        # convention difference).
        assert "lag" in md or "interpolation" in md

    def test_disclosure_morning_screen_scope(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        md = result["methodology_disclosure"].lower()
        # Morning screen, NOT a tactical trade signal.
        assert "morning screen" in md
        assert "not a tactical" in md


# ===========================================================================
# 4. Per-row reference columns
# ===========================================================================

class TestReferenceColumns:
    def test_maturity_underlying_vendor_present(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        for row in result["results"]:
            # The reference fetcher returns these for every stem in
            # the synthetic fixture; the compute layer attaches them
            # by (curve_family, tenor) lookup.
            assert row["maturity_date"] is not None
            assert row["underlying_index"] is not None
            assert row["vendor_ticker"] is not None

    def test_reference_columns_match_lookup(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        expected = {
            (r["curve_family"], r["tenor"]): {
                "underlying_index": r["underlying_index"],
                "vendor_ticker": r["vendor_ticker"],
            }
            for r in _DEFAULT_REFERENCE
        }
        for row in result["results"]:
            key = (row["curve_family"], row["tenor"])
            assert key in expected, key
            assert (
                row["underlying_index"]
                == expected[key]["underlying_index"]
            )
            assert (
                row["vendor_ticker"]
                == expected[key]["vendor_ticker"]
            )

    def test_underlying_index_distinguishes_by_curve_family(self):
        """The ZCIS-specific index-family caveat is observable per
        row: USD_ZCIS rows must surface CPURNSA, EUR_ZCIS rows must
        surface CPTFEMU, GBP_ZCIS rows must surface UKRPI."""
        params = ScanInflationSwapsExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        family_to_index = {
            "USD_ZCIS": "CPURNSA Index",
            "EUR_ZCIS": "CPTFEMU Index",
            "GBP_ZCIS": "UKRPI Index",
        }
        for row in result["results"]:
            cf = row["curve_family"]
            assert row["underlying_index"] == family_to_index[cf], (
                f"{cf} row should reference {family_to_index[cf]!r}, "
                f"got {row['underlying_index']!r}"
            )

    def test_missing_reference_row_leaves_none(self):
        """If the reference fetcher is missing a (curve_family,
        tenor), the corresponding output row has None reference
        columns — defensive; not an error."""
        params = ScanInflationSwapsExtremesInput(
            top_n=10, min_abs_z_score=0.0,
        )
        # Drop GBP_ZCIS 10Y from the reference rows.
        ref = pd.DataFrame([
            r for r in _DEFAULT_REFERENCE
            if (r["curve_family"], r["tenor"]) != ("GBP_ZCIS", "10Y")
        ])
        result, _calls = _run(params, reference_df=ref)
        for row in result["results"]:
            if row["curve_family"] == "GBP_ZCIS" and row["tenor"] == "10Y":
                assert row["maturity_date"] is None
                assert row["underlying_index"] is None
                assert row["vendor_ticker"] is None


# ===========================================================================
# 5. Filtering + ranking
# ===========================================================================

class TestFilteringAndRanking:
    def test_top_n_caps_output(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=2, min_abs_z_score=0.0,
        )
        result, _calls = _run(params)
        assert len(result["results"]) == 2

    def test_min_abs_z_score_filters_below_threshold(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=20, min_abs_z_score=1.0,
        )
        result, _calls = _run(params)
        for row in result["results"]:
            assert abs(row["z_score_zcis_rate"]) >= 1.0

    def test_threshold_too_high_returns_error_envelope(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=999.0,
        )
        result, _calls = _run(params)
        assert "error" in result
        assert (
            "|z|" in result["error"]
            or "min_abs_z_score" in result["error"]
        )

    def test_none_top_n_falls_back_to_yaml_default(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=None, min_abs_z_score=0.0,
        )
        result, _calls = _run(
            params, config=_custom_config(default_top_n=3),
        )
        assert len(result["results"]) == 3

    def test_none_min_abs_z_score_falls_back_to_yaml_default(self):
        params = ScanInflationSwapsExtremesInput(
            top_n=20, min_abs_z_score=None,
        )
        # YAML default 0.0 lets every scored stem through.
        result, _calls = _run(
            params,
            config=_custom_config(default_min_abs_z_score=0.0),
        )
        # With drift dispersion all 7 stems exceed |z| >= 0.0.
        assert len(result["results"]) >= 5


# ===========================================================================
# 6. Deterministic tie-break
# ===========================================================================

class TestDeterministicOrdering:
    def test_tie_break_curve_family_then_tenor_asc(self):
        """Two stems with identical |z| must order by curve_family
        asc then tenor asc (compute's tie-break)."""
        # Build a deliberately tied synthetic universe — two stems
        # with the same drift produce the same |z| at the latest
        # aligned row.
        stems = [
            ("USD_ZCIS", "10Y"),
            ("EUR_ZCIS", "10Y"),
        ]
        # Identical drift → identical level z on the latest row.
        drift = 2.0
        df = _build_universe_df(
            stems=stems, per_stem_drift={
                ("USD_ZCIS", "10Y"): drift,
                ("EUR_ZCIS", "10Y"): drift,
            },
            base=2.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        ref = pd.DataFrame([
            r for r in _DEFAULT_REFERENCE
            if (r["curve_family"], r["tenor"]) in stems
        ])
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params, field_df=df, reference_df=ref)
        cfs = [r["curve_family"] for r in result["results"]]
        # EUR_ZCIS < USD_ZCIS lexicographically — tie-break wins
        # for EUR_ZCIS.
        assert cfs[0] == "EUR_ZCIS", (
            f"tie-break must order by curve_family asc; got {cfs}"
        )


# ===========================================================================
# 7. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_smaller_min_periods_admits_shorter_history(self):
        # Short history (only 50 trading days); default
        # min_periods=60 would skip every stem.
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        short_df = _default_field_df(days=50)
        # YAML default min_periods=60 → error envelope.
        result_default, _ = _run(params, field_df=short_df)
        assert "error" in result_default

        # Override to min_periods=20 → stems become scoreable.
        cfg = _custom_config(z_score_min_periods=20)
        result_low, _ = _run(params, field_df=short_df, config=cfg)
        assert "error" not in result_low
        assert len(result_low["results"]) > 0

    def test_z_window_must_be_252_in_v1(self):
        """PR14 wire-freeze guard."""
        cfg = _custom_config(z_score_window_days=126)
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        with pytest.raises(NotImplementedError):
            _run(params, config=cfg)


# ===========================================================================
# 8. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_nominal_sovereign_curve_family_refused(self):
        with pytest.raises(Exception) as exc_info:
            ScanInflationSwapsExtremesInput(
                # 'UST' is nominal sovereign
                curve_families=["USD_ZCIS", "UST"],
            )
        assert (
            "whitelist" in str(exc_info.value)
            or "UST" in str(exc_info.value)
        )

    def test_linker_curve_family_refused(self):
        with pytest.raises(Exception) as exc_info:
            ScanInflationSwapsExtremesInput(
                # 'USD_TIPS' is a linker, not a ZCIS
                curve_families=["USD_TIPS"],
            )
        assert (
            "whitelist" in str(exc_info.value)
            or "USD_TIPS" in str(exc_info.value)
        )

    def test_ois_curve_family_refused(self):
        with pytest.raises(Exception) as exc_info:
            ScanInflationSwapsExtremesInput(
                # OIS curve family
                curve_families=["USD_SOFR_OIS"],
            )
        assert (
            "whitelist" in str(exc_info.value)
            or "USD_SOFR_OIS" in str(exc_info.value)
        )

    def test_empty_curve_families_list_refused(self):
        with pytest.raises(Exception):
            ScanInflationSwapsExtremesInput(curve_families=[])

    def test_no_field_name_param(self):
        """field_name is YAML-owned — must NOT be a per-query input."""
        with pytest.raises(Exception):
            ScanInflationSwapsExtremesInput(field_name="PX_MID")

    def test_no_lookback_days_param(self):
        with pytest.raises(Exception):
            ScanInflationSwapsExtremesInput(lookback_days=180)

    def test_no_metrics_param(self):
        with pytest.raises(Exception):
            ScanInflationSwapsExtremesInput(metrics=["level"])

    def test_top_n_upper_bound(self):
        with pytest.raises(Exception):
            ScanInflationSwapsExtremesInput(top_n=999)

    def test_min_abs_z_score_negative_refused(self):
        with pytest.raises(Exception):
            ScanInflationSwapsExtremesInput(min_abs_z_score=-1.0)

    def test_frozen_model(self):
        """ConfigDict(frozen=True) — attribute writes must fail."""
        p = ScanInflationSwapsExtremesInput()
        with pytest.raises(Exception):
            p.top_n = 10  # type: ignore[misc]


# ===========================================================================
# 9. Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_empty_universe_returns_error(self):
        empty = pd.DataFrame(
            columns=[
                "trade_date", "curve_family", "tenor", "contract_code",
                "field_value",
            ]
        )
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params, field_df=empty)
        assert "error" in result
        assert "No ZCIS rate data" in result["error"]

    def test_all_stems_below_min_periods_returns_error(self):
        # Only 10 days per stem — far below the default
        # min_periods=60.
        short_df = _default_field_df(days=10)
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
        )
        result, _calls = _run(params, field_df=short_df)
        assert "error" in result
        assert (
            "scoreable" in result["error"]
            or "z-score" in result["error"]
        )

    def test_future_as_of_date_returns_controlled_error(self):
        # as_of_date BEYOND the synthetic universe's last trading
        # day.
        params = ScanInflationSwapsExtremesInput(
            top_n=5, min_abs_z_score=0.0,
            as_of_date=_FROZEN_TODAY + timedelta(days=30),
        )
        result, _calls = _run(params)
        assert "error" in result
        assert "beyond" in result["error"]
        assert "last observed" in result["error"]
        assert "results" not in result
        assert "scan_summary" not in result

    def test_curve_families_scope_passes_to_fetcher(self):
        params = ScanInflationSwapsExtremesInput(
            curve_families=["USD_ZCIS", "EUR_ZCIS"],
            top_n=5, min_abs_z_score=0.0,
        )
        result, calls = _run(params)
        assert calls["scan_universe"]["curve_families"] == (
            ["USD_ZCIS", "EUR_ZCIS"]
        )
        assert calls["scan_reference"]["curve_families"] == (
            ["USD_ZCIS", "EUR_ZCIS"]
        )
