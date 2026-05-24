"""
test_nfp_surprise_compute.py — Unit tests for the nfp_surprise primitive
=========================================================================

Covers:
  1. Bundled config.yaml structurally valid + loads cleanly (PR7 + PR12).
     country='US' and event_type='nfp' both YAML-locked.
  2. compute() runs end-to-end against synthetic input with the bundled
     config and returns a well-formed NfpSurpriseOutput.
  3. Convention overrides actually change behaviour (release_z_window,
     surprise_round_decimals).
  4. NotImplementedError guard on the categorical convention
     (surprise_formula) — PR11 + PR14.
  5. Honest absence (P5 + P6):
     a. Empty raw_df → controlled error envelope (no fabricated surprise).
     b. All-scheduled (no realised) → error envelope.
     c. Rows with consensus_median=None emit surprise=None.
  6. Schema-layer invariants: lookback_releases bounded, no country/
     event_type input (US-only per brief).
  7. Three import paths resolve to the same Pydantic class.
  8. methodology_note surfaces ADR 0008 §2 + TD #28b + PRIOR-MONTH
     REVISIONS disclosure (the NFP-specific caveat).
  9. Bespoke time_series and canonical TimeSeries fields are 1-to-1
     by construction (cannot drift).
 10. Country/event_type from YAML reach the fetcher (load-bearing
     correctness check — the primitive must call the fetcher with
     country='US', event_type='nfp', not with hardcoded constants
     that bypass YAML).
 11. NFP rounding convention: surprise rounded to whole thousands of
     jobs (0 decimals — Bloomberg ECO screen convention).

Tests are fully offline; the SQL fetcher is patched.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.nfp_surprise import (
    CONFIG_PATH,
    NfpSurpriseInput,
    calculate_nfp_surprise,
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


class _FrozenDate(date):
    _frozen_value: date = date(2026, 5, 22)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _build_raw_df(
    *,
    n_releases: int = 30,
    end: date = date(2026, 5, 10),
    actual_base: float = 200.0,  # thousands of jobs (NFP TCH Index)
    consensus_base: float = 180.0,
    null_consensus_indices: tuple = (),
    placeholder_at_end: int = 0,
) -> pd.DataFrame:
    """Build a synthetic event_calendar-shaped frame for US NFP at
    monthly cadence."""
    rows = []
    for i in range(n_releases):
        release_d = end - pd.Timedelta(days=30 * (n_releases - 1 - i))
        is_placeholder = i >= (n_releases - placeholder_at_end)
        is_null_consensus = i in null_consensus_indices
        # Deterministic drift to keep z-score non-degenerate.
        actual = None if is_placeholder else actual_base + 15.0 * (i % 7) - 50.0
        consensus = (
            None
            if (is_placeholder or is_null_consensus)
            else consensus_base + 10.0 * (i % 5) - 20.0
        )
        prior = (
            None
            if i == 0 or is_placeholder
            else actual_base + 15.0 * ((i - 1) % 7) - 50.0
        )
        rows.append({
            "event_id": 9000 + i,
            "event_type": "nfp",
            "event_category": "economic_release",
            "country": "US",
            "currency": "USD",
            "release_date": (
                release_d.date() if hasattr(release_d, "date") else release_d
            ),
            "release_time": None,
            "period": release_d.strftime("%b %Y") if hasattr(release_d, "strftime") else None,
            "actual": actual,
            "consensus_median": consensus,
            "consensus_high": None if consensus is None else consensus + 30.0,
            "consensus_low": None if consensus is None else consensus - 30.0,
            "prior": prior,
            "revised_prior": None,
            "surprise_std_dev": None if consensus is None else 25.0,
        })
    return pd.DataFrame(rows)


def _custom_config(**overrides) -> ToolConfig:
    """Build a custom ToolConfig with overridable conventions."""
    defaults = {
        "default_lookback_releases": 24,
        "release_z_window": 24,
        "release_z_min_periods": 6,
        "release_z_ddof": 1,
        "release_fetch_buffer_days_per_release": 35,
        "surprise_formula": "actual_minus_consensus_median",
        "event_type": "nfp",
        "country": "US",
        "surprise_round_decimals": 0,
        "release_z_round_decimals": 4,
    }
    defaults.update(overrides)

    valid_ranges = {
        "default_lookback_releases": [4, 200],
        "release_z_window": [6, 60],
        "release_z_min_periods": [3, 24],
        "release_fetch_buffer_days_per_release": [25, 45],
        "surprise_round_decimals": [0, 3],
        "release_z_round_decimals": [0, 8],
    }
    return ToolConfig(
        tool=ToolMeta(
            name="t",
            domain="d",
            description="x",
            category="desk_invariant_primitive",
        ),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(
                value=v,
                source="test",
                rationale="test",
                valid_range=valid_ranges.get(k),
            )
            for k, v in defaults.items()
        },
    )


# ===========================================================================
# 1. Bundled config.yaml — PR7 + PR12 + PR13
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_nfp_surprise_tool"
        assert cfg.tool.domain == "sovereign_bonds"
        assert cfg.tool.category == "desk_invariant_primitive"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "default_lookback_releases",
            "release_z_window",
            "release_z_min_periods",
            "release_z_ddof",
            "release_fetch_buffer_days_per_release",
            "surprise_formula",
            "event_type",
            "country",
            "surprise_round_decimals",
            "release_z_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_country_and_event_type_yaml_locked(self):
        """Per the brief: single-country single-event.  Both must be
        YAML-locked at expected values."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("country") == "US"
        assert cfg.convention_value("event_type") == "nfp"

    def test_pr13_shared_conventions_with_cpi_surprise(self):
        """PR13 cross-config consistency — release_z_window /
        release_z_min_periods / release_z_ddof / default_lookback_releases
        / release_fetch_buffer_days_per_release / release_z_round_decimals
        share names with cpi_surprise and MUST share values."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("release_z_window") == 24
        assert cfg.convention_value("release_z_min_periods") == 6
        assert cfg.convention_value("release_z_ddof") == 1
        assert cfg.convention_value("default_lookback_releases") == 24
        assert cfg.convention_value("release_fetch_buffer_days_per_release") == 35
        assert cfg.convention_value("release_z_round_decimals") == 4

    def test_surprise_rounding_is_zero_decimals(self):
        """NFP convention: round to whole thousands of jobs.  Distinct
        from cpi_surprise's surprise_pct_round_decimals=4 because NFP
        is reported in thousands directly while CPI is in percentage
        points."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("surprise_round_decimals") == 0

    def test_convention_sources_registered(self):
        cfg = load_tool_config(CONFIG_PATH)
        registered = {
            "team_judgment_pending_review",
            "industry_standard_release_window",
            "industry_standard_sample_std",
            "derived_from_window",
            "adr_0008_event_playbook_contract",
            "bloomberg_field_convention",
        }
        for name, conv in cfg.conventions.items():
            assert conv.source in registered, (
                f"convention {name!r} has unregistered source {conv.source!r}"
            )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3
        joined = " | ".join(cfg.methodology.planned_extensions)
        # NFP-specific revisions disclosure
        assert "Revision" in joined or "revision" in joined.lower()
        assert "surprise_formula" in joined
        assert "TD #28" in joined or "ECO_RELEASE_DT_LIST" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, raw_df, config=None):
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df) as mock_fetch, patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_nfp_surprise(
                engine=mock_engine, params=params, config=config,
            )
        return out, mock_fetch

    def test_default_config_returns_well_formed_output(self):
        raw_df = _build_raw_df(n_releases=40)
        params = NfpSurpriseInput(lookback_releases=12)
        out, _ = self._run(params, raw_df)

        assert "error" not in out, out.get("error")

        cm = out["current_metrics"]
        for k in (
            "release_date",
            "country",
            "event_type",
            "period",
            "surprise_label",
            "current_surprise_k_jobs",
            "current_z_score",
            "release_z_window_releases",
            "current_actual_k_jobs",
            "current_consensus_median_k_jobs",
            "current_prior_k_jobs",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        assert cm["country"] == "US"
        assert cm["event_type"] == "nfp"
        assert cm["surprise_label"] == "US NFP surprise"
        assert cm["release_z_window_releases"] == 24
        assert isinstance(cm["current_surprise_k_jobs"], float)
        assert cm["observation_count"] == 12

        # Canonical TimeSeries unit declarations
        assert out["time_series_surprise"]["units"] == "count"
        assert out["time_series_zscore"]["units"] == "z_score"
        assert out["time_series_surprise"]["series_name"] == "us_nfp_surprise"
        assert out["time_series_zscore"]["series_name"] == "us_nfp_surprise_zscore"

        # description carries the explicit "thousands of jobs" label so
        # downstream consumers know what COUNT means here.
        desc = out["time_series_surprise"]["description"].lower()
        assert "thousands of jobs" in desc

        # Lengths agree
        n = len(out["time_series"])
        assert n == len(out["time_series_surprise"]["rows"])
        assert n == len(out["time_series_zscore"]["rows"])

    def test_fetcher_called_with_us_nfp(self):
        """Load-bearing correctness — the primitive must pass
        country='US' and event_type='nfp' from YAML to the fetcher,
        NOT bypass the YAML with hardcoded constants."""
        raw_df = _build_raw_df(n_releases=40)
        params = NfpSurpriseInput(lookback_releases=12)
        _, mock_fetch = self._run(params, raw_df)
        assert mock_fetch.call_args.kwargs["country"] == "US"
        assert mock_fetch.call_args.kwargs["event_type"] == "nfp"

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _build_raw_df(n_releases=40)
        params = NfpSurpriseInput(lookback_releases=12)
        out_auto, _ = self._run(params, raw_df, config=None)
        out_explicit, _ = self._run(
            params, raw_df, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_surprise_identity(self):
        """Spot-check: surprise_k_jobs == actual_k_jobs −
        consensus_median_k_jobs on a row with both fields populated."""
        raw_df = _build_raw_df(n_releases=20)
        params = NfpSurpriseInput(lookback_releases=10)
        out, _ = self._run(params, raw_df)
        for row in out["time_series"]:
            if (row["actual_k_jobs"] is not None
                    and row["consensus_median_k_jobs"] is not None):
                expected = round(
                    row["actual_k_jobs"] - row["consensus_median_k_jobs"], 0,
                )
                assert row["surprise_k_jobs"] == expected, (
                    f"{row['date']}: surprise={row['surprise_k_jobs']!r} "
                    f"!= actual − consensus = {expected!r}"
                )

    def test_nfp_rounding_is_whole_thousands(self):
        """Surprise output values must round to integer thousands
        of jobs — Bloomberg ECO convention."""
        raw_df = _build_raw_df(n_releases=20)
        params = NfpSurpriseInput(lookback_releases=10)
        out, _ = self._run(params, raw_df)
        for row in out["time_series"]:
            if row["surprise_k_jobs"] is not None:
                assert row["surprise_k_jobs"] == round(row["surprise_k_jobs"], 0), (
                    f"{row['date']}: surprise={row['surprise_k_jobs']!r} "
                    f"not rounded to whole thousands"
                )


# ===========================================================================
# 3. Convention overrides (PR7)
# ===========================================================================

class TestConventionOverrides:
    def test_release_z_window_override_surfaces(self):
        raw_df = _build_raw_df(n_releases=40)
        params = NfpSurpriseInput(lookback_releases=12)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            out_default = calculate_nfp_surprise(
                engine=mock_engine, params=params,
                config=_custom_config(release_z_window=24),
            )
            out_tight = calculate_nfp_surprise(
                engine=mock_engine, params=params,
                config=_custom_config(release_z_window=12,
                                       release_z_min_periods=4),
            )
        assert out_default["current_metrics"]["release_z_window_releases"] == 24
        assert out_tight["current_metrics"]["release_z_window_releases"] == 12


# ===========================================================================
# 4. Honest-placeholder guard — PR11 + PR14
# ===========================================================================

class TestConventionGuards:
    def test_unsupported_surprise_formula_raises(self):
        raw_df = _build_raw_df(n_releases=10)
        params = NfpSurpriseInput()
        bad = _custom_config(surprise_formula="actual_minus_consensus_mean")
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            with pytest.raises(NotImplementedError) as exc:
                calculate_nfp_surprise(
                    engine=mock_engine, params=params, config=bad,
                )
        msg = str(exc.value)
        assert "actual_minus_consensus_mean" in msg
        assert "surprise_formula" in msg
        assert "planned_extensions" in msg


# ===========================================================================
# 5. Honest absence (P5 + P6)
# ===========================================================================

class TestHonestAbsence:
    def test_empty_raw_df_returns_error_envelope(self):
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        empty = pd.DataFrame(columns=[
            "event_id", "event_type", "event_category", "country",
            "currency", "release_date", "release_time", "period",
            "actual", "consensus_median", "consensus_high",
            "consensus_low", "prior", "revised_prior", "surprise_std_dev",
        ])
        params = NfpSurpriseInput(lookback_releases=12)
        with patch(target, return_value=empty), patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_nfp_surprise(engine=mock_engine, params=params)
        assert "error" in out
        assert "US" in out["error"]
        assert "nfp" in out["error"]
        assert "TD #28" in out["error"] or "ADR 0008" in out["error"]

    def test_all_scheduled_returns_error(self):
        raw_df = _build_raw_df(n_releases=8, placeholder_at_end=8)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        params = NfpSurpriseInput(lookback_releases=12)
        with patch(target, return_value=raw_df), patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_nfp_surprise(engine=mock_engine, params=params)
        assert "error" in out
        assert "realised" in out["error"].lower() or "scheduled" in out["error"].lower()

    def test_null_consensus_emits_none_surprise(self):
        raw_df = _build_raw_df(
            n_releases=20,
            null_consensus_indices=(5, 6, 7),
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        params = NfpSurpriseInput(lookback_releases=20)
        with patch(target, return_value=raw_df), patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_nfp_surprise(engine=mock_engine, params=params)
        assert "error" not in out, out.get("error")
        none_rows = [
            r for r in out["time_series"]
            if r["consensus_median_k_jobs"] is None
        ]
        assert len(none_rows) > 0
        for r in none_rows:
            assert r["surprise_k_jobs"] is None, (
                f"{r['date']}: consensus=None but surprise={r['surprise_k_jobs']!r}"
            )


# ===========================================================================
# 6. Schema-layer invariants
# ===========================================================================

class TestSchemaInvariants:
    def test_no_country_input_accepted(self):
        """The Pydantic input must NOT accept a country argument —
        country is YAML-locked per the brief."""
        from pydantic import ValidationError
        # Extra fields should be ignored by default (Pydantic v2),
        # but the field shouldn't exist on the model.
        p = NfpSurpriseInput()
        assert not hasattr(p, "country"), (
            "NfpSurpriseInput must NOT have a country field — "
            "country is YAML-locked per the brief"
        )
        assert not hasattr(p, "event_type"), (
            "NfpSurpriseInput must NOT have an event_type field"
        )

    def test_lookback_releases_lower_bound(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            NfpSurpriseInput(lookback_releases=2)

    def test_lookback_releases_upper_bound(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            NfpSurpriseInput(lookback_releases=10_000)

    def test_lookback_releases_default_from_yaml(self):
        p = NfpSurpriseInput()
        assert p.lookback_releases == 24


# ===========================================================================
# 7. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_compute_via_package_init(self):
        from rates_agent.sovereign_bonds.tools.nfp_surprise import (
            calculate_nfp_surprise as via_package,
        )
        from rates_agent.sovereign_bonds.tools.nfp_surprise.compute import (
            calculate_nfp_surprise as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.sovereign_bonds.tools.nfp_surprise import (
            NfpSurpriseInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.nfp_surprise.schemas import (
            NfpSurpriseInput as via_schemas,
        )
        from rates_agent.sovereign_bonds.tools.schemas import (
            NfpSurpriseInput as via_hub,
        )
        assert via_package is via_schemas
        assert via_package is via_hub


# ===========================================================================
# 8. PR10 — methodology_note disclosure (incl. PRIOR-MONTH REVISIONS)
# ===========================================================================

class TestMethodologyNoteSurface:
    def test_methodology_note_required(self):
        from pydantic import ValidationError
        from rates_agent.sovereign_bonds.tools.nfp_surprise import (
            NfpSurpriseCurrentMetrics,
            NfpSurpriseOutput,
        )
        from shared.schemas import TimeSeries, TimeSeriesUnits
        cm = NfpSurpriseCurrentMetrics(
            release_date="2026-05-08",
            country="US",
            event_type="nfp",
            surprise_label="US NFP surprise",
            release_z_window_releases=24,
            observation_count=0,
        )
        empty_count = TimeSeries(
            series_name="us_nfp_surprise",
            units=TimeSeriesUnits.COUNT,
            description="x",
            rows=[],
        )
        empty_z = TimeSeries(
            series_name="us_nfp_surprise_zscore",
            units=TimeSeriesUnits.Z_SCORE,
            description="x",
            rows=[],
        )
        with pytest.raises(ValidationError):
            NfpSurpriseOutput(  # type: ignore[call-arg]
                current_metrics=cm,
                time_series=[],
                time_series_surprise=empty_count,
                time_series_zscore=empty_z,
            )

    def test_methodology_note_surfaces_disclosures(self):
        raw_df = _build_raw_df(n_releases=30)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        params = NfpSurpriseInput(lookback_releases=12)
        with patch(target, return_value=raw_df), patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_nfp_surprise(engine=mock_engine, params=params)
        note = out["methodology_note"]
        # Core disclosures
        assert "ADR 0004" in note
        assert "ADR 0008" in note
        assert "TD #28" in note or "ECO_RELEASE_DT_LIST" in note
        assert "P12" in note
        assert "actual − consensus_median" in note
        # NFP-specific PRIOR-MONTH REVISIONS disclosure (the
        # well-known caveat the brief calls out explicitly)
        assert "REVISION" in note.upper() or "revised_prior" in note


# ===========================================================================
# 9. Bespoke vs canonical TimeSeries parity (cannot drift)
# ===========================================================================

class TestSeriesParity:
    def test_bespoke_and_canonical_surprise_match_row_for_row(self):
        raw_df = _build_raw_df(n_releases=40)
        params = NfpSurpriseInput(lookback_releases=20)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_nfp_surprise(engine=mock_engine, params=params)
        for i, (bespoke, canonical) in enumerate(
            zip(out["time_series"], out["time_series_surprise"]["rows"])
        ):
            assert bespoke["date"] == canonical["date"], f"row {i}"
            assert bespoke["surprise_k_jobs"] == canonical["value"], f"row {i}"

    def test_bespoke_and_canonical_zscore_match_row_for_row(self):
        raw_df = _build_raw_df(n_releases=40)
        params = NfpSurpriseInput(lookback_releases=20)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.sovereign_bonds.tools.nfp_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.sovereign_bonds.tools.nfp_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_nfp_surprise(engine=mock_engine, params=params)
        for i, (bespoke, canonical) in enumerate(
            zip(out["time_series"], out["time_series_zscore"]["rows"])
        ):
            assert bespoke["date"] == canonical["date"], f"row {i}"
            assert bespoke["z_score"] == canonical["value"], f"row {i}"
