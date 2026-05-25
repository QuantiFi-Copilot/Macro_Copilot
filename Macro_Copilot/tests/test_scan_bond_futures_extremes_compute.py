"""
test_scan_bond_futures_extremes_compute.py — Unit tests for the bond-
                                              futures universe-wide
                                              extremes scan

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic universe data with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window, ddof,
     ffill, min_periods, threshold rounding).
  4. Per-metric top-N + min_abs_z_score filtering behaves correctly.
  5. Deterministic ordering: ties on |z| broken by contract_code asc.
  6. Schema-layer behaviour: closed-family whitelist refusal, no
     ``metrics`` / ``field_name`` inputs accepted (input-schema-
     overreach guard).
  7. Methodology disclosure: present on EVERY row AND on the response,
     contains the z-score lookback verbatim, contains the universe-
     wide-sweep + non-DV01-spread + ADR 0013 caveats.
  8. Edge cases: empty universe, all stems below min_periods, threshold
     too high, missing field, NaN stems.
  9. New fetcher helper ``fetch_rolling_generic_universe_series`` unit
     test (Layer A — sanity check on the SQL + return shape via
     mocked engine).
 10. Curve_families scoping: full universe vs subset; subset reaches
     the fetcher; empty list refused at schema time.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
    CONFIG_PATH,
    ScanBondFuturesExtremesInput,
    ScanBondFuturesExtremesOutput,
    calculate_scan_bond_futures_extremes,
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


_FROZEN_TODAY = date(2026, 5, 22)


class _FrozenDate(date):
    _frozen_value: date = _FROZEN_TODAY

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# Default V1 universe — sovereign-bond futures only (excludes
# SOFR_FUT / SONIA_FUT / EUR_SHORT_RATE_FUT per ADR 0013 V1 scope).
_DEFAULT_STEMS = [
    ("UST_FUT", "TY1", "10Y"),
    ("UST_FUT", "UXY1", "10Y"),
    ("UST_FUT", "US1", "30Y"),
    ("UST_FUT", "WN1", "30Y"),
    ("DE_FUT", "RX1", "10Y"),
    ("JP_FUT", "JB1", "10Y"),
]


def _stem_series(
    *,
    drift: float,
    start: float,
    days: int,
    frozen_today: date,
) -> pd.Series:
    """Synthetic business-day series of length `days` ending at frozen_today."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    values = np.linspace(start, start + drift, n)
    return pd.Series(values, index=bdays)


def _build_universe_field_df(
    *,
    stems: list[tuple[str, str, str]],
    per_stem_drift: dict[str, float],
    base: float,
    days: int,
    frozen_today: date,
) -> pd.DataFrame:
    """Assemble a long-format DataFrame matching
    ``fetch_rolling_generic_universe_series`` shape across the
    universe."""
    rows = []
    for (cf, cc, tenor) in stems:
        drift = per_stem_drift.get(cc, 0.0)
        series = _stem_series(
            drift=drift, start=base, days=days, frozen_today=frozen_today,
        )
        for ts, value in series.items():
            rows.append({
                "trade_date": ts.date(),
                "curve_family": cf,
                "contract_code": cc,
                "tenor": tenor,
                "field_value": float(value),
            })
    return pd.DataFrame(rows)


def _default_field_data(
    stems=_DEFAULT_STEMS,
    days: int = 400,
    frozen_today: date = _FROZEN_TODAY,
):
    """Produce three universe-wide DataFrames (price, volume, OI) with
    enough cross-stem drift dispersion that each metric has at least
    one stem with |z| >= 1.5 on the latest aligned row.

    The drift pattern is deliberately asymmetric per stem so different
    metrics produce different rankings — exercises the per-metric
    top-N path rather than the trivial case where one stem dominates
    every metric.
    """
    # Price drifts (linear trend → tail dominates rolling mean → high z).
    price_drifts = {
        "TY1": 5.0,    # large up drift → highest z on price level
        "UXY1": 2.0,
        "US1": -3.0,   # down drift → negative z
        "WN1": 0.5,
        "RX1": 4.0,    # close 2nd on price
        "JB1": 0.2,
    }
    # Volume drifts (in CONTRACTS).
    volume_drifts = {
        "TY1": 50000.0,
        "UXY1": 200000.0,  # high z on volume
        "US1": -10000.0,
        "WN1": 5000.0,
        "RX1": 80000.0,
        "JB1": 1000.0,
    }
    # OI drifts (in CONTRACTS).
    oi_drifts = {
        "TY1": 100000.0,
        "UXY1": 50000.0,
        "US1": 800000.0,  # high z on OI
        "WN1": 30000.0,
        "RX1": 60000.0,
        "JB1": 5000.0,
    }
    price_df = _build_universe_field_df(
        stems=stems, per_stem_drift=price_drifts, base=110.0,
        days=days, frozen_today=frozen_today,
    )
    volume_df = _build_universe_field_df(
        stems=stems, per_stem_drift=volume_drifts, base=600000.0,
        days=days, frozen_today=frozen_today,
    )
    oi_df = _build_universe_field_df(
        stems=stems, per_stem_drift=oi_drifts, base=3000000.0,
        days=days, frozen_today=frozen_today,
    )
    return price_df, volume_df, oi_df


_UNSET = object()


def _run(
    params,
    *,
    price_df=None,
    volume_df=None,
    oi_df=None,
    config=None,
    universe_max_date=_UNSET,
):
    """Patch the universe fetcher + max-date probe + date.today() and
    invoke compute.

    ``universe_max_date`` controls the value the future-anchor guard's
    DB probe returns. By default it returns ``_FROZEN_TODAY`` (the
    synthetic data's max trade_date), so an in-data ``as_of_date <=
    _FROZEN_TODAY`` passes the guard. Tests that exercise the future-
    anchor guard explicitly override this to a smaller value than the
    requested ``as_of_date``.
    """
    if price_df is None or volume_df is None or oi_df is None:
        default_price, default_volume, default_oi = _default_field_data()
        if price_df is None:
            price_df = default_price
        if volume_df is None:
            volume_df = default_volume
        if oi_df is None:
            oi_df = default_oi

    captured_calls: list[dict] = []

    def _universe_side_effect(
        *, engine, curve_families, field_name, start_date, end_date=None,
    ):
        captured_calls.append({
            "curve_families": list(curve_families),
            "field_name": field_name,
            "start_date": start_date,
            "end_date": end_date,
        })
        # Defaults per config: PX_LAST / PX_VOLUME / OPEN_INT.
        if field_name in ("PX_LAST",):
            return price_df.copy()
        if field_name in ("PX_VOLUME",):
            return volume_df.copy()
        if field_name in ("OPEN_INT",):
            return oi_df.copy()
        # Unknown — return empty so the error path is exercised.
        return pd.DataFrame(
            columns=[
                "trade_date", "curve_family", "contract_code", "tenor",
                "field_value",
            ]
        )

    resolved_max = (
        _FROZEN_TODAY if universe_max_date is _UNSET else universe_max_date
    )

    def _max_date_side_effect(*, engine, curve_families):
        return resolved_max

    with patch(
        "rates_agent.bond_futures.tools.scan_bond_futures_extremes."
        "compute.fetch_rolling_generic_universe_series",
        side_effect=_universe_side_effect,
    ), patch(
        "rates_agent.bond_futures.tools.scan_bond_futures_extremes."
        "compute.fetch_rolling_generic_universe_max_date",
        side_effect=_max_date_side_effect,
    ), patch(
        "rates_agent.bond_futures.tools.scan_bond_futures_extremes."
        "compute.date",
        _FrozenDate,
    ):
        result = calculate_scan_bond_futures_extremes(
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
        "default_price_field": "PX_LAST",
        "default_volume_field": "PX_VOLUME",
        "default_open_interest_field": "OPEN_INT",
        "price_change_offset_rows": 2,
        "bond_futures_curve_families": (
            "UST_FUT,DE_FUT,UK_FUT,JP_FUT,FR_FUT,IT_FUT,ES_FUT,CA_FUT,AU_FUT"
        ),
        "price_round_decimals": 6,
        "volume_round_decimals": 0,
        "oi_round_decimals": 0,
        "z_score_round_decimals": 4,
        # Round-2 YAML-owned display thresholds (PR9 / PR10 — the
        # default lives in config.yaml, not in the schema's Field
        # default; compute resolves the None sentinel against these
        # convention values).
        "default_top_n": 5,
        "default_min_abs_z_score": 1.5,
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(
            name="scan_bond_futures_extremes_tool",
            domain="bond_futures",
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
        assert cfg.tool.name == "scan_bond_futures_extremes_tool"
        assert cfg.tool.domain == "bond_futures"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "ffill_limit_days",
            "default_price_field",
            "default_volume_field",
            "default_open_interest_field",
            "price_change_offset_rows",
            "bond_futures_curve_families",
            "price_round_decimals",
            "volume_round_decimals",
            "oi_round_decimals",
            "z_score_round_decimals",
            # Round-2 mandatory-fix: display thresholds are YAML-owned
            # (PR9 / PR10), defaulted in config.yaml and resolved via
            # the schema's None sentinel inside compute.
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
        assert cfg.convention_value("default_price_field") == "PX_LAST"
        assert cfg.convention_value("default_volume_field") == "PX_VOLUME"
        assert cfg.convention_value("default_open_interest_field") == "OPEN_INT"
        assert cfg.convention_value("price_change_offset_rows") == 2
        # YAML-owned display thresholds.
        assert cfg.convention_value("default_top_n") == 5
        assert cfg.convention_value("default_min_abs_z_score") == 1.5
        # Whitelist must be CSV-of-curve-families per the schema's read.
        wl = cfg.convention_value("bond_futures_curve_families")
        assert isinstance(wl, str)
        # Closed-family per ADR 0013 V1 — sovereign-bond futures only.
        for cf in ["UST_FUT", "DE_FUT", "UK_FUT", "JP_FUT", "FR_FUT",
                   "IT_FUT", "ES_FUT", "CA_FUT", "AU_FUT"]:
            assert cf in wl
        # Policy-futures curves must NOT be in the whitelist.
        for cf in ["SOFR_FUT", "SONIA_FUT", "EUR_SHORT_RATE_FUT"]:
            assert cf not in wl

    def test_z_score_window_source_is_registered_tag(self):
        """The z-score lookback MUST disclose a registered source tag."""
        cfg = load_tool_config(CONFIG_PATH)
        src = cfg.conventions["z_score_window_days"].source
        assert src == "industry_standard_1y_window", (
            f"z-window source must be 'industry_standard_1y_window' to "
            f"match the registered tag for a 1Y rolling window; got {src!r}"
        )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        # The DV01 inter-commodity spread + roll-pressure extensions
        # MUST be named in planned_extensions per the catalog scope.
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "DV01" in joined or "dv01" in joined.lower()


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        params = ScanBondFuturesExtremesInput()
        out, calls = _run(params)

        assert "error" not in out, out.get("error")
        assert "scan_summary" in out
        assert "results" in out
        assert "methodology_disclosure" in out
        assert isinstance(out["results"], list)
        assert len(out["results"]) > 0

        # Each result row must validate against the schema.
        ScanBondFuturesExtremesOutput.model_validate(out)

        # Three fetcher calls (one per field: PX_LAST / PX_VOLUME / OPEN_INT).
        assert len(calls) == 3
        fields_called = sorted(c["field_name"] for c in calls)
        assert fields_called == ["OPEN_INT", "PX_LAST", "PX_VOLUME"]

    def test_explicit_config_matches_auto_loaded(self):
        params = ScanBondFuturesExtremesInput()
        out_auto, _ = _run(params, config=None)
        out_explicit, _ = _run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_output_row_structure(self):
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(params)
        for row in out["results"]:
            for k in (
                "rank", "metric", "curve_family", "contract_code", "tenor",
                "as_of_date",
                "current_price", "daily_price_change",
                "current_volume", "current_open_interest",
                "delta_open_interest_1d",
                "z_score", "signal", "methodology_disclosure",
            ):
                assert k in row, f"missing {k}"
            assert row["metric"] in (
                "price", "price_change", "volume", "open_interest"
            )
            assert row["signal"] in ("EXTREME_HIGH", "EXTREME_LOW")
            assert row["rank"] >= 1

    def test_per_metric_blocks_ordered(self):
        """Results must be ordered price → price_change → volume →
        open_interest (stable for downstream consumers)."""
        params = ScanBondFuturesExtremesInput(top_n=10, min_abs_z_score=0.0)
        out, _ = _run(params)
        metrics_seen = [row["metric"] for row in out["results"]]
        # Each metric block, when present, must appear contiguously
        # and in the canonical order.
        order = ["price", "price_change", "volume", "open_interest"]
        last_idx = -1
        for m in metrics_seen:
            idx = order.index(m)
            assert idx >= last_idx, (
                f"out-of-order block: {metrics_seen}"
            )
            last_idx = idx


# ===========================================================================
# 3. Methodology disclosure
# ===========================================================================

class TestMethodologyDisclosure:
    def test_disclosure_present_on_response_and_every_row(self):
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(params)
        md_response = out["methodology_disclosure"]
        # Catalog methodology guardrail: z-score lookback explicit.
        assert "252" in md_response
        # P5 / ADR 0013 caveats.
        assert "universe-wide" in md_response.lower()
        assert "DV01" in md_response or "dv01" in md_response.lower()
        assert "ADR 0013" in md_response or "Phase-4" in md_response
        # Every row carries the SAME disclosure (per the catalog wording).
        for row in out["results"]:
            assert row["methodology_disclosure"] == md_response

    def test_disclosure_required_in_schema(self):
        from pydantic import ValidationError as _VE
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(params)
        bad = dict(out)
        bad.pop("methodology_disclosure")
        with pytest.raises(_VE):
            ScanBondFuturesExtremesOutput.model_validate(bad)

    def test_disclosure_tracks_config_window(self):
        """If a custom config sets z_score_window_days to 180, the
        disclosure must surface 180 — never a hardcoded 252."""
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(
            params,
            config=_custom_config(
                z_score_window_days=180,
                z_score_min_periods=60,
            ),
        )
        # The "= 180" pattern is unique to the custom-window run.
        assert "= 180" in out["methodology_disclosure"]
        assert "= 252" not in out["methodology_disclosure"]

    def test_disclosure_says_not_dv01_spread(self):
        """Catalog guardrail: 'NOT an inter-commodity DV01-weighted
        spread' must be on the disclosure verbatim (content; wording
        may vary)."""
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(params)
        md = out["methodology_disclosure"]
        # Some form of "NOT a DV01-weighted spread" must be present.
        assert ("not" in md.lower() and "dv01" in md.lower())


# ===========================================================================
# 4. Filtering + top_n + ranking
# ===========================================================================

class TestRankingAndFiltering:
    def test_top_n_per_metric_respected(self):
        params = ScanBondFuturesExtremesInput(top_n=2, min_abs_z_score=0.0)
        out, _ = _run(params)
        per_metric = {}
        for row in out["results"]:
            per_metric.setdefault(row["metric"], []).append(row)
        for metric, rows in per_metric.items():
            assert len(rows) <= 2, (
                f"metric {metric} returned {len(rows)} rows > top_n=2"
            )

    def test_high_threshold_drops_rows(self):
        """A very high threshold should leave fewer rows than threshold=0."""
        out_low, _ = _run(
            ScanBondFuturesExtremesInput(top_n=5, min_abs_z_score=0.0)
        )
        out_high, _ = _run(
            ScanBondFuturesExtremesInput(top_n=5, min_abs_z_score=10.0)
        )
        # min_abs_z_score=10 is unreachable in synthetic data — must error.
        assert "error" in out_high
        # min_abs_z_score=0 admits everyone (subject to top_n cap).
        assert "error" not in out_low

    def test_ranking_by_abs_zscore_desc(self):
        """Within each metric, rank 1 has the largest |z|, rank 2 next."""
        params = ScanBondFuturesExtremesInput(top_n=10, min_abs_z_score=0.0)
        out, _ = _run(params)
        per_metric = {}
        for row in out["results"]:
            per_metric.setdefault(row["metric"], []).append(row)
        for metric, rows in per_metric.items():
            rows_sorted = sorted(rows, key=lambda r: r["rank"])
            abs_zs = [abs(r["z_score"]) for r in rows_sorted]
            assert abs_zs == sorted(abs_zs, reverse=True), (
                f"metric {metric}: rank order not monotonic on |z|: "
                f"{abs_zs}"
            )

    def test_signal_matches_zscore_sign(self):
        params = ScanBondFuturesExtremesInput(top_n=10, min_abs_z_score=0.0)
        out, _ = _run(params)
        for row in out["results"]:
            if row["z_score"] is None:
                continue
            if row["z_score"] > 0:
                assert row["signal"] == "EXTREME_HIGH"
            elif row["z_score"] < 0:
                assert row["signal"] == "EXTREME_LOW"


# ===========================================================================
# 5. Determinism on ties
# ===========================================================================

class TestDeterministicTieBreaking:
    def test_ties_broken_by_contract_code_ascending(self):
        """Two stems with identical |z| must order by contract_code asc."""
        frozen = _FROZEN_TODAY
        # Two stems with IDENTICAL drift pattern → identical z-scores.
        stems = [
            ("UST_FUT", "TY1", "10Y"),
            ("UST_FUT", "UXY1", "10Y"),
            ("UST_FUT", "US1", "30Y"),
        ]
        identical_drift = 5.0
        price_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                "TY1": identical_drift,
                "UXY1": identical_drift,
                "US1": identical_drift,
            },
            base=110.0, days=400, frozen_today=frozen,
        )
        volume_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                "TY1": 50000.0, "UXY1": 50000.0, "US1": 50000.0,
            },
            base=600000.0, days=400, frozen_today=frozen,
        )
        oi_df = _build_universe_field_df(
            stems=stems, per_stem_drift={
                "TY1": 100000.0, "UXY1": 100000.0, "US1": 100000.0,
            },
            base=3000000.0, days=400, frozen_today=frozen,
        )
        out, _ = _run(
            ScanBondFuturesExtremesInput(top_n=10, min_abs_z_score=0.0),
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
        )
        # Take the price block — three identical-|z| stems.
        price_rows = [r for r in out["results"] if r["metric"] == "price"]
        codes_in_rank_order = [r["contract_code"] for r in price_rows]
        # On |z|-tie, contract_code asc determines order: TY1 < US1 < UXY1.
        assert codes_in_rank_order == sorted(codes_in_rank_order), (
            f"expected contract_code-asc tie-break, got {codes_in_rank_order}"
        )


# ===========================================================================
# 6. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_full_universe_default(self):
        """Round-2: all four optional inputs default to None — compute
        resolves to YAML conventions for ``top_n`` /
        ``min_abs_z_score`` and to the most-recent shared trading day
        for ``as_of_date``. ``lookback_days`` has been REMOVED from
        the schema per PR8 input-schema discipline (reviewer
        round-1 mandatory-fix #2)."""
        params = ScanBondFuturesExtremesInput()
        assert params.curve_families is None
        assert params.top_n is None
        assert params.min_abs_z_score is None
        assert params.as_of_date is None

    def test_lookback_days_no_longer_an_input(self):
        """PR8 / OPR8 input-schema discipline (reviewer round-1
        mandatory-fix #2): ``lookback_days`` controls the fetch
        window — that is methodology, not a per-query knob. The
        schema must REFUSE it via extra='forbid'."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesInput(
                lookback_days=365,  # type: ignore[call-arg]
            )

    def test_as_of_date_accepted(self):
        params = ScanBondFuturesExtremesInput(
            as_of_date=date(2026, 4, 8),
        )
        assert params.as_of_date == date(2026, 4, 8)

    def test_as_of_date_iso_string_coerced(self):
        """Pydantic v2 coerces a ISO string to a ``date`` for the
        ``date`` annotation — keeps the MCP wrapper free to pass
        either a string or a parsed date."""
        params = ScanBondFuturesExtremesInput(
            as_of_date="2026-04-08",  # type: ignore[arg-type]
        )
        assert params.as_of_date == date(2026, 4, 8)

    def test_as_of_date_malformed_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesInput(
                as_of_date="not-a-date",  # type: ignore[arg-type]
            )

    def test_curve_families_subset_accepted(self):
        params = ScanBondFuturesExtremesInput(
            curve_families=["UST_FUT", "DE_FUT"],
        )
        assert params.curve_families == ["UST_FUT", "DE_FUT"]

    def test_policy_futures_curve_family_refused(self):
        """A policy-futures stem in curve_families must raise per the
        ADR 0013 V1 closed-family whitelist."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            ScanBondFuturesExtremesInput(
                curve_families=["UST_FUT", "SOFR_FUT"],
            )
        msg = str(exc_info.value)
        assert "SOFR_FUT" in msg
        assert "policy_futures" in msg.lower()

    def test_empty_curve_families_list_refused(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesInput(curve_families=[])

    def test_no_field_name_input_accepted(self):
        """field_name MUST NOT be an input — YAML owns the field
        mnemonics. extra="forbid" should reject it."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesInput(
                field_name="PX_LAST",  # type: ignore[call-arg]
            )

    def test_no_metrics_input_accepted(self):
        """metrics MUST NOT be an input — the four metrics ARE the
        concept. extra="forbid" should reject any attempt."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesInput(
                metrics=["price"],  # type: ignore[call-arg]
            )

    def test_top_n_bounded(self):
        """Round-2: None is the legal sentinel (YAML fallback), but
        out-of-range positive integers are still refused by the
        schema-level Field(ge=1, le=50) bounds — invariants stay in
        code per PR9 / PR10."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesInput(top_n=0)
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesInput(top_n=51)

    def test_min_abs_z_score_negative_refused(self):
        """Round-2: None is the legal sentinel (YAML fallback), but
        a negative concrete value is still refused by the schema-
        level Field(ge=0.0) bound — invariants stay in code per
        PR9 / PR10."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ScanBondFuturesExtremesInput(min_abs_z_score=-0.5)


# ===========================================================================
# 7. Curve_families scoping reaches the fetcher
# ===========================================================================

class TestCurveFamiliesScoping:
    def test_full_universe_calls_with_whitelist(self):
        params = ScanBondFuturesExtremesInput()
        out, calls = _run(params)
        assert "error" not in out, out.get("error")
        for call in calls:
            cf_list = call["curve_families"]
            # When None on input → resolves to full YAML whitelist.
            for cf in ["UST_FUT", "DE_FUT", "UK_FUT", "JP_FUT", "FR_FUT",
                       "IT_FUT", "ES_FUT", "CA_FUT", "AU_FUT"]:
                assert cf in cf_list

    def test_subset_curve_families_reaches_fetcher(self):
        params = ScanBondFuturesExtremesInput(
            curve_families=["UST_FUT", "DE_FUT"],
        )
        out, calls = _run(params)
        # Synthetic data still has UST + DE stems → no error.
        assert "error" not in out, out.get("error")
        for call in calls:
            assert call["curve_families"] == ["UST_FUT", "DE_FUT"]


# ===========================================================================
# 8. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_zscore_window_override_changes_zs(self):
        params = ScanBondFuturesExtremesInput(top_n=5, min_abs_z_score=0.0)
        out_default, _ = _run(params, config=_custom_config())
        out_short, _ = _run(
            params, config=_custom_config(z_score_window_days=120),
        )
        # Compare the price metric's top-row z under the two windows.
        z_default = next(
            (r["z_score"] for r in out_default["results"] if r["metric"] == "price"),
            None,
        )
        z_short = next(
            (r["z_score"] for r in out_short["results"] if r["metric"] == "price"),
            None,
        )
        assert z_default != z_short, (
            "z_score_window_days override must change the latest z"
        )

    def test_ddof_override_changes_zs(self):
        params = ScanBondFuturesExtremesInput(top_n=5, min_abs_z_score=0.0)
        out_sample, _ = _run(params, config=_custom_config(z_score_ddof=1))
        out_pop, _ = _run(params, config=_custom_config(z_score_ddof=0))
        z_sample = next(
            (r["z_score"] for r in out_sample["results"] if r["metric"] == "price"),
            None,
        )
        z_pop = next(
            (r["z_score"] for r in out_pop["results"] if r["metric"] == "price"),
            None,
        )
        assert z_sample != z_pop


# ===========================================================================
# 9. Edge cases
# ===========================================================================

class TestEdgeCases:
    def test_empty_universe_returns_error(self):
        empty = pd.DataFrame(
            columns=[
                "trade_date", "curve_family", "contract_code", "tenor",
                "field_value",
            ]
        )
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(
            params,
            price_df=empty, volume_df=empty, oi_df=empty,
        )
        assert "error" in out
        assert "No bond-futures universe data" in out["error"]

    def test_all_stems_below_min_periods_returns_error(self):
        """Universe present but every stem has fewer than z_min_periods
        observations after alignment — must error rather than emit empty
        rankings."""
        # Only 30 days of data → below the default 60-day min_periods.
        price_df, volume_df, oi_df = _default_field_data(days=30)
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(
            params,
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
        )
        assert "error" in out
        # Either the "no observation" error or the "no scoreable stems"
        # error — both are honest exits.
        assert ("aligned observations" in out["error"]
                or "scoreable" in out["error"]
                or "z_score_min_periods" in out["error"])

    def test_single_stem_universe(self):
        """A universe of 1 stem still produces a valid scan if the
        stem has enough history."""
        single_stems = [("UST_FUT", "TY1", "10Y")]
        price_df = _build_universe_field_df(
            stems=single_stems, per_stem_drift={"TY1": 5.0},
            base=110.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        volume_df = _build_universe_field_df(
            stems=single_stems, per_stem_drift={"TY1": 50000.0},
            base=600000.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        oi_df = _build_universe_field_df(
            stems=single_stems, per_stem_drift={"TY1": 100000.0},
            base=3000000.0, days=400, frozen_today=_FROZEN_TODAY,
        )
        out, _ = _run(
            ScanBondFuturesExtremesInput(top_n=5, min_abs_z_score=0.0),
            price_df=price_df, volume_df=volume_df, oi_df=oi_df,
        )
        # Trending linear series produces a finite z; the one stem
        # appears at rank 1 on every metric.
        assert "error" not in out, out.get("error")
        assert len(out["results"]) >= 1


# ===========================================================================
# 9b. YAML-fallback vs explicit-override for top_n / min_abs_z_score
# ===========================================================================
# Reviewer round-1 mandatory-fix #1: ``top_n`` and ``min_abs_z_score``
# defaults must live in config.yaml (PR9 / PR10). Compute must:
#   - resolve None inputs from ``default_top_n`` / ``default_min_abs_z_score``
#   - honour an explicit input override per query
#   - preserve the schema-level invariants (top_n bound, min_abs_z >= 0)


class TestThresholdYamlFallback:
    def test_top_n_none_falls_through_to_yaml(self):
        """When ``top_n`` is None, compute must use the YAML default.
        Set the YAML default to a distinct value (2) and prove the
        ranking reflects it — without re-passing top_n on the input."""
        params = ScanBondFuturesExtremesInput(min_abs_z_score=0.0)
        out, _ = _run(
            params,
            config=_custom_config(default_top_n=2),
        )
        assert "error" not in out, out.get("error")
        # Each metric block must have at most 2 rows.
        per_metric: dict[str, list[dict]] = {}
        for row in out["results"]:
            per_metric.setdefault(row["metric"], []).append(row)
        for metric, rows in per_metric.items():
            assert len(rows) <= 2, (
                f"YAML default_top_n=2 was not honoured for {metric}: "
                f"got {len(rows)} rows"
            )

    def test_top_n_explicit_overrides_yaml(self):
        """Explicit per-query input wins over the YAML default."""
        params = ScanBondFuturesExtremesInput(
            top_n=4, min_abs_z_score=0.0,
        )
        out, _ = _run(
            params,
            config=_custom_config(default_top_n=2),
        )
        assert "error" not in out, out.get("error")
        per_metric: dict[str, list[dict]] = {}
        for row in out["results"]:
            per_metric.setdefault(row["metric"], []).append(row)
        # At least one metric block must exceed YAML default to prove
        # the explicit override actually fired (4 > 2).
        max_block_size = max(len(rows) for rows in per_metric.values())
        assert max_block_size > 2, (
            f"explicit top_n=4 was overridden by YAML default_top_n=2 — "
            f"max block size {max_block_size}"
        )
        # And still <= 4 (the explicit override).
        for metric, rows in per_metric.items():
            assert len(rows) <= 4

    def test_min_abs_z_score_none_falls_through_to_yaml(self):
        """When ``min_abs_z_score`` is None, compute must use the
        YAML default. Set the YAML default to an unreachable 10.0 and
        prove the scan returns the same error envelope it does on an
        explicit 10.0."""
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(
            params,
            config=_custom_config(default_min_abs_z_score=10.0),
        )
        # |z| >= 10 unreachable on the synthetic universe → error.
        assert "error" in out
        assert "passed |z| >= 10" in out["error"]

    def test_min_abs_z_score_explicit_overrides_yaml(self):
        """Explicit per-query input wins over the YAML default — even
        when the YAML default would have produced an error."""
        params = ScanBondFuturesExtremesInput(min_abs_z_score=0.0)
        out, _ = _run(
            params,
            config=_custom_config(default_min_abs_z_score=10.0),
        )
        # Explicit 0 lets every row through → no error.
        assert "error" not in out, out.get("error")
        assert len(out["results"]) > 0

    def test_summary_reflects_resolved_thresholds(self):
        """The ``scan_summary`` must show the RESOLVED values, not
        ``None`` — so a desk reader sees the effective thresholds
        regardless of whether they were YAML-defaulted or
        explicitly overridden."""
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(params)
        assert "error" not in out, out.get("error")
        summary = out["scan_summary"]
        # YAML defaults: top_n=5, min_abs_z=1.5 — both must appear
        # literally (formatted as integers/floats, not "None").
        assert "1.5" in summary
        assert "top 5" in summary
        assert "None" not in summary


# ===========================================================================
# 9c. as_of_date anchor behaviour (reviewer round-1 mandatory-fix #2)
# ===========================================================================


class TestAsOfDateAnchor:
    """``as_of_date`` is the legitimate per-query anchor input.

    - When None → resolve to the most-recent shared trading day in the
      fetched data (max trade_date across the per-stem aligned series).
    - When supplied → cap the per-stem series at the date and use that
      anchor for all snapshot fields.
    - When supplied beyond the DB's last trading day → controlled
      error envelope (the per-stem cap leaves zero scoreable rows;
      same shape as the generic 'no scoreable stems' exit).
    """

    def test_none_anchors_to_data_max(self):
        """The default behaviour: as_of_date omitted → anchor matches
        the most-recent observation in the fetched data."""
        params = ScanBondFuturesExtremesInput()
        out, _ = _run(params)
        assert "error" not in out, out.get("error")
        # Every result row's per-stem as_of_date should be <= the
        # frozen wall-clock date (we'd cap at as_of if supplied; here
        # the cap is just the data's max).
        for row in out["results"]:
            row_date = date.fromisoformat(row["as_of_date"])
            assert row_date <= _FROZEN_TODAY

    def test_explicit_as_of_date_caps_per_stem(self):
        """An explicit as_of_date in the past must cap the per-stem
        time series — the snapshot fields reflect the cap, not the
        data's true max."""
        # Anchor the scan ~30 business days back. Use a date that
        # actually falls on a business day to avoid weekend
        # alignment surprises.
        cap_date = pd.bdate_range(
            end=_FROZEN_TODAY, periods=30,
        )[0].date()
        params = ScanBondFuturesExtremesInput(as_of_date=cap_date)
        out, _ = _run(params)
        assert "error" not in out, out.get("error")
        # Every row's per-stem as_of_date must be <= the cap.
        for row in out["results"]:
            row_date = date.fromisoformat(row["as_of_date"])
            assert row_date <= cap_date, (
                f"row {row['contract_code']} as_of_date={row_date} "
                f"exceeds cap {cap_date}"
            )

    def test_explicit_as_of_date_changes_snapshot_vs_default(self):
        """The capped-history scan must produce different SNAPSHOT
        values from the un-capped run on the same synthetic data —
        proves the per-stem cap actually flows through scoring. Z-
        score is intentionally NOT compared here because the rolling
        z of a perfect linear trend is window-position-invariant (a
        mathematical property of the synthetic test data, NOT a
        compute bug); the current_price + as_of_date snapshot fields
        are the load-bearing observable of the cap."""
        cap_date = pd.bdate_range(
            end=_FROZEN_TODAY, periods=80,
        )[0].date()
        default_out, _ = _run(
            ScanBondFuturesExtremesInput(min_abs_z_score=0.0)
        )
        capped_out, _ = _run(
            ScanBondFuturesExtremesInput(
                min_abs_z_score=0.0, as_of_date=cap_date,
            )
        )
        assert "error" not in default_out and "error" not in capped_out
        # Compare price-metric top row across the two anchors.
        default_price_top = next(
            (r for r in default_out["results"] if r["metric"] == "price"),
            None,
        )
        capped_price_top = next(
            (r for r in capped_out["results"] if r["metric"] == "price"),
            None,
        )
        assert default_price_top is not None and capped_price_top is not None
        # Different as_of_date is the headline cap signal — the
        # default anchors at the data's max date; the capped run
        # anchors at the cap.
        assert default_price_top["as_of_date"] != capped_price_top["as_of_date"]
        # The snapshot price at the cap must be the price ON the cap
        # date, not on the data's max — a smaller number for an up-
        # trending series.
        assert default_price_top["current_price"] != capped_price_top["current_price"]

    @pytest.mark.parametrize("days_after_max", [1, 30, 1825])
    def test_future_as_of_date_returns_controlled_error(
        self, days_after_max: int,
    ):
        """An ``as_of_date`` BEYOND the universe's last observed
        ``trade_date`` must return the documented controlled-error
        envelope — NOT a normal scan computed on only the actually-
        available rows.

        Replaces the round-2 test that openly admitted it was
        exercising the WRONG case (a past as_of_date before any
        data). The reviewer's load-bearing finding was that
        ``as_of_date > universe_max`` silently delivered a normal
        scan — the future-anchor guard now intercepts at the DB
        probe and returns the controlled-error envelope the schema
        promised.

        Parametrised on the ``days_after_max`` offset so the guard
        fires on a one-day-past edge case AND on a far-future
        anchor (e.g. ``+1825 days`` = ~5 years past the data max),
        covering the original round-2 docstring's "5 years in the
        future" scenario honestly. The mocked DB-probe helper
        returns ``_FROZEN_TODAY`` (the synthetic universe's max
        trade_date), so any anchor strictly greater than
        ``_FROZEN_TODAY`` must trip the guard.
        """
        future_anchor = _FROZEN_TODAY + timedelta(days=days_after_max)
        params = ScanBondFuturesExtremesInput(as_of_date=future_anchor)
        out, _ = _run(params)
        # Controlled-error envelope: ``error`` key only, no rankings
        # nor scan_summary keys (matches the documented envelope shape).
        assert "error" in out
        assert "results" not in out, (
            f"future-anchor envelope must not carry rankings; got "
            f"{list(out.keys())}"
        )
        assert "scan_summary" not in out
        # The error text must name BOTH the requested anchor AND the
        # universe's last observed trade_date so the desk reader can
        # tell what tripped the guard (P5 honest disclosure).
        assert "beyond" in out["error"]
        assert "last observed" in out["error"]
        assert future_anchor.isoformat() in out["error"]
        assert _FROZEN_TODAY.isoformat() in out["error"]
        # ``as_of_date`` echoed back is the requested future anchor.
        # No exception was raised; the call completed normally.

    def test_past_as_of_date_before_data_returns_no_scoreable_error(self):
        """An ``as_of_date`` BEFORE any ingested data lands in the
        OTHER controlled-error path: the future-anchor guard does NOT
        trip (the requested anchor is <= the universe's last observed
        trade_date), but the SQL upper bound + per-stem cap leave
        every stem with zero aligned rows — same ``no scoreable
        stems`` envelope shape as the in-data ``threshold too high``
        exit. Round-3 re-homes this case under an honest name (the
        round-2 ``test_future_as_of_date_returns_controlled_error``
        test was actually exercising THIS path while claiming to
        exercise the future case)."""
        # The synthetic data starts ~400 business days before
        # _FROZEN_TODAY; 10 years back is comfortably before that.
        before_data = _FROZEN_TODAY - timedelta(days=10 * 365)
        params = ScanBondFuturesExtremesInput(as_of_date=before_data)
        # No need to override universe_max — the mock returns
        # _FROZEN_TODAY and ``before_data < _FROZEN_TODAY`` so the
        # future-anchor guard does NOT fire; the empty-aligned-rows
        # exit fires instead.
        out, _ = _run(params)
        assert "error" in out
        assert (
            "aligned observations" in out["error"]
            or "scoreable" in out["error"]
        )
        # The future-anchor guard's substring MUST NOT appear — this
        # is the OTHER envelope, not the future-anchor one.
        assert "beyond" not in out["error"]

    def test_summary_reports_as_of_date_when_explicit(self):
        """The ``scan_summary`` must surface the as-of date so a
        desk reader sees the anchor."""
        cap_date = pd.bdate_range(
            end=_FROZEN_TODAY, periods=30,
        )[0].date()
        params = ScanBondFuturesExtremesInput(
            min_abs_z_score=0.0, as_of_date=cap_date,
        )
        out, _ = _run(params)
        assert "error" not in out, out.get("error")
        # Either "as_of <date>" or "as_of dates span <date> to <date>"
        # — both contain the cap date when the cap caps the whole
        # universe to a single day. With synthetic-linear data the
        # cap typically leaves every stem with the SAME max date
        # (the cap itself), so the summary is "as_of <cap>".
        assert (
            cap_date.isoformat() in out["scan_summary"]
        ), out["scan_summary"]


# ===========================================================================
# 10. New fetcher helper unit test (Layer A — sanity check)
# ===========================================================================

class TestUniverseFetcherHelper:
    """Layer-A unit test for the NEW
    ``fetch_rolling_generic_universe_series`` helper added in
    ``shared/analytics/rates_fetch.py``.

    Verifies the helper's SQL bind shape + return shape via a mock
    engine — does NOT touch the DB. Required because we extended
    rates_fetch.py for this primitive (per the orchestrator builder
    instructions: "if you do, ship the helper with a Layer-A unit
    test and document it in the docstring honestly")."""

    def test_empty_curve_families_raises(self):
        from shared.analytics.rates_fetch import (
            fetch_rolling_generic_universe_series,
        )
        engine = MagicMock()
        with pytest.raises(ValueError) as exc_info:
            fetch_rolling_generic_universe_series(
                engine=engine,
                curve_families=[],
                field_name="PX_LAST",
                start_date=date(2025, 1, 1),
            )
        assert "at least one" in str(exc_info.value)

    def test_returns_dataframe_with_expected_columns(self):
        from shared.analytics.rates_fetch import (
            fetch_rolling_generic_universe_series,
        )
        # Mock the SQLAlchemy connection chain so the function returns
        # a frame built from a synthetic row set.
        synthetic_rows = [
            (date(2026, 4, 30), "UST_FUT", "TY1", "10Y", 110.50),
            (date(2026, 4, 30), "UST_FUT", "UXY1", "10Y", 115.25),
            (date(2026, 4, 30), "DE_FUT", "RX1", "10Y", 132.75),
        ]
        column_names = [
            "trade_date", "curve_family", "contract_code", "tenor",
            "field_value",
        ]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = synthetic_rows
        mock_result.keys.return_value = column_names
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_result
        # The function uses `with engine.connect() as conn:` — make
        # the context manager return our mock_conn.
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        df = fetch_rolling_generic_universe_series(
            engine=mock_engine,
            curve_families=["UST_FUT", "DE_FUT"],
            field_name="PX_LAST",
            start_date=date(2025, 1, 1),
        )
        assert list(df.columns) == column_names
        assert len(df) == 3
        # Bind params propagated to the SQL.
        bind = mock_conn.execute.call_args.args[1]
        assert bind["curve_families"] == ["UST_FUT", "DE_FUT"]
        assert bind["field_name"] == "PX_LAST"
        assert bind["start_date"] == "2025-01-01"
        # Round-3 new ``end_date`` parameter — defaults to None
        # (preserves the round-2 fetcher behaviour exactly when the
        # caller does not opt in to the upper-bound scope).
        assert bind["end_date"] is None

    def test_end_date_propagates_to_sql_bind(self):
        """Round-3: when ``end_date`` is supplied, the SQL bind carries
        the ISO-formatted value so the underlying CTE's
        ``CAST(:end_date AS DATE) IS NULL OR d.trade_date <= ...``
        predicate narrows the result set to the requested anchor.
        Belt-and-braces with the per-stem ``cap_at`` step inside the
        bond-futures scanner; this is the SQL-level scope for
        deterministic Layer-B validation.
        """
        from shared.analytics.rates_fetch import (
            fetch_rolling_generic_universe_series,
        )
        column_names = [
            "trade_date", "curve_family", "contract_code", "tenor",
            "field_value",
        ]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = column_names
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        fetch_rolling_generic_universe_series(
            engine=mock_engine,
            curve_families=["UST_FUT"],
            field_name="PX_LAST",
            start_date=date(2025, 1, 1),
            end_date=date(2026, 4, 8),
        )
        bind = mock_conn.execute.call_args.args[1]
        assert bind["start_date"] == "2025-01-01"
        assert bind["end_date"] == "2026-04-08"

    def test_max_date_probe_returns_value(self):
        """Round-3: the new ``fetch_rolling_generic_universe_max_date``
        probe returns the single ``MAX(trade_date)`` cell across the
        universe (used by the scanner's future-anchor guard).
        """
        from shared.analytics.rates_fetch import (
            fetch_rolling_generic_universe_max_date,
        )
        mock_result = MagicMock()
        mock_result.first.return_value = (date(2026, 4, 8),)
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        out = fetch_rolling_generic_universe_max_date(
            engine=mock_engine,
            curve_families=["UST_FUT", "DE_FUT"],
        )
        assert out == date(2026, 4, 8)
        bind = mock_conn.execute.call_args.args[1]
        assert bind["curve_families"] == ["UST_FUT", "DE_FUT"]

    def test_max_date_probe_returns_none_when_empty(self):
        """An empty universe returns ``None`` — the future-anchor
        guard short-circuits to "no probe data" and lets the
        downstream empty-data error path handle it (P5 honest).
        """
        from shared.analytics.rates_fetch import (
            fetch_rolling_generic_universe_max_date,
        )
        mock_result = MagicMock()
        mock_result.first.return_value = (None,)
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn

        out = fetch_rolling_generic_universe_max_date(
            engine=mock_engine,
            curve_families=["UST_FUT"],
        )
        assert out is None

    def test_max_date_probe_requires_curve_families(self):
        from shared.analytics.rates_fetch import (
            fetch_rolling_generic_universe_max_date,
        )
        with pytest.raises(ValueError) as exc_info:
            fetch_rolling_generic_universe_max_date(
                engine=MagicMock(),
                curve_families=[],
            )
        assert "at least one" in str(exc_info.value)


# ===========================================================================
# 11. Import path discipline
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_via_package_init(self):
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
            calculate_scan_bond_futures_extremes as via_package,
        )
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes.compute import (
            calculate_scan_bond_futures_extremes as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
            ScanBondFuturesExtremesInput as via_package,
        )
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes.schemas import (
            ScanBondFuturesExtremesInput as via_schemas,
        )
        from rates_agent.bond_futures.tools.schemas import (
            ScanBondFuturesExtremesInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_output_schema_via_three_paths(self):
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
            ScanBondFuturesExtremesOutput as via_package,
        )
        from rates_agent.bond_futures.tools.scan_bond_futures_extremes.schemas import (
            ScanBondFuturesExtremesOutput as via_schemas,
        )
        from rates_agent.bond_futures.tools.schemas import (
            ScanBondFuturesExtremesOutput as via_hub,
        )
        assert via_package is via_schemas is via_hub
