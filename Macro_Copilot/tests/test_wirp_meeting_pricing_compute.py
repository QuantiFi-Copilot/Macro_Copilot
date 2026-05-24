"""
test_wirp_meeting_pricing_compute.py — Unit tests for the WIRP primitive
=========================================================================

Covers:
  1. Bundled config.yaml structurally valid + loads cleanly (PR7 + PR12).
     The four ``*_field`` conventions wire-locked to ADR 0009 §1's
     metric → field mapping; ``supported_central_banks`` wire-locked
     to the four ADR 0009 §2 region-prefix banks.
  2. compute() runs end-to-end against synthetic input for both
     selection modes (next_n_meetings + specific_meeting_date) and
     returns a well-formed WirpMeetingPricingOutput.
  3. NotImplementedError guard on every wire-frozen ``*_field``
     convention — PR11 + PR14.
  4. Honest absence (P5 + P6):
     a. Empty raw_df + next_n_meetings → controlled error envelope.
     b. Empty raw_df + specific_meeting_date → error envelope naming
        the missing date.
     c. Unsupported central_bank → error envelope listing supported set.
     d. Meeting missing one of the four metrics → None on that
        meeting's column (NOT a fabricated zero).
  5. Schema-layer invariants:
     a. ``extra='forbid'`` rejects unknown fields (Codex P2 lesson
        from PR #188).
     b. ``@model_validator`` enforces mode-specific required fields:
        next_n_meetings rejects meeting_date; specific_meeting_date
        requires meeting_date and rejects n_meetings.
     c. central_bank canonicalisation (lowercase / whitespace).
  6. Three import paths resolve to the same Pydantic class.
  7. Hike/cut/hold IDENTITY derivation from the signed move
     probability — math correctness + None propagation when
     signed_prob is None.
  8. methodology_note surfaces the MANDATORY P5 disclosure (the
     brief calls this out explicitly).
  9. Output shape is list-shaped — primitive registers in
     WORKFLOW_INCOMPATIBLE_TOOLS (verified by wiring tests).

Tests are fully offline; the SQL fetcher is patched.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.ois.tools.wirp_meeting_pricing import (
    CONFIG_PATH,
    WirpMeetingPricingInput,
    calculate_wirp_meeting_pricing,
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
    meetings: list[tuple[date, float, float, float, float]],
    central_bank: str = "FOMC",
    as_of_date: date = date(2026, 5, 22),
    drop_field: str | None = None,
) -> pd.DataFrame:
    """Build a synthetic event-calendar-shaped frame for WIRP.

    ``meetings`` is a list of (meeting_date, implied_rate, move_prob,
    num_moves, rate_change) tuples.  Each tuple becomes 4 rows in
    the raw frame (one per WIRP field).  ``drop_field`` (e.g.
    'WIRP_NUM_MOVES') omits that field from every meeting — exercises
    the missing-metric honest-absence path.
    """
    region_prefix = {
        "FOMC": "US0B", "ECB": "EZ0B", "BOE": "GB0B", "BOJ": "JP0B",
    }.get(central_bank, "US0B")
    country = {
        "FOMC": "US", "ECB": "EU", "BOE": "GB", "BOJ": "JP",
    }.get(central_bank, "US")
    field_value_map = {
        "WIRP_IMPLIED_RATE": lambda m: m[1],
        "WIRP_MOVE_PROB": lambda m: m[2],
        "WIRP_NUM_MOVES": lambda m: m[3],
        "WIRP_RATE_CHANGE": lambda m: m[4],
    }
    rows = []
    for idx, m in enumerate(meetings, start=1000):
        meeting_d = m[0]
        token = meeting_d.strftime("%b%Y").upper()  # e.g. JUN2026
        common = {
            "instrument_id": idx,
            "vendor_ticker": f"WIRP:{central_bank}:{meeting_d.isoformat()}",
            "meeting_date": meeting_d,
            "central_bank": central_bank,
            "meeting_token": token,
            "bloomberg_ticker_fr": f"{region_prefix}FR {token} Index",
            "bloomberg_ticker_pr": f"{region_prefix}PR {token} Index",
            "bloomberg_ticker_nm": f"{region_prefix}NM {token} Index",
            "bloomberg_ticker_ch": f"{region_prefix}CH {token} Index",
            "as_of_date": as_of_date,
        }
        for field, extractor in field_value_map.items():
            if field == drop_field:
                continue
            rows.append({
                **common,
                "field_name": field,
                "field_value": extractor(m),
            })
    return pd.DataFrame(rows)


def _custom_config(**overrides) -> ToolConfig:
    """Build a custom ToolConfig with overridable conventions."""
    defaults = {
        "default_n_meetings": 6,
        "implied_rate_field": "WIRP_IMPLIED_RATE",
        "move_prob_field": "WIRP_MOVE_PROB",
        "num_moves_field": "WIRP_NUM_MOVES",
        "rate_change_field": "WIRP_RATE_CHANGE",
        "supported_central_banks": "FOMC,ECB,BOE,BOJ",
        "rate_round_decimals": 4,
        "prob_round_decimals": 2,
        "num_moves_round_decimals": 3,
        "forward_horizon_days": 365,
        "past_horizon_days": 540,
    }
    defaults.update(overrides)
    valid_ranges = {
        "default_n_meetings": [1, 24],
        "rate_round_decimals": [2, 8],
        "prob_round_decimals": [0, 4],
        "num_moves_round_decimals": [0, 6],
        "forward_horizon_days": [30, 1095],
        "past_horizon_days": [30, 1825],
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
        assert cfg.tool.name == "calculate_wirp_meeting_pricing_tool"
        assert cfg.tool.domain == "ois"
        assert cfg.tool.category == "desk_invariant_primitive"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "default_n_meetings",
            "implied_rate_field",
            "move_prob_field",
            "num_moves_field",
            "rate_change_field",
            "supported_central_banks",
            "rate_round_decimals",
            "prob_round_decimals",
            "num_moves_round_decimals",
            "forward_horizon_days",
            "past_horizon_days",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_horizons_sourced_from_wirp_yml(self):
        """Codex P3 fix — the 1500-day hard-coded horizon was
        replaced with YAML conventions sourced from wirp.yml's
        Stage-B-verified band."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("forward_horizon_days") == 365
        assert cfg.convention_value("past_horizon_days") == 540

    def test_field_mappings_pinned_to_adr_0009(self):
        """The four ``*_field`` conventions must match ADR 0009 §1's
        metric → field mapping.  Drifting any silently misses every
        row of the corresponding metric."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("implied_rate_field") == "WIRP_IMPLIED_RATE"
        assert cfg.convention_value("move_prob_field") == "WIRP_MOVE_PROB"
        assert cfg.convention_value("num_moves_field") == "WIRP_NUM_MOVES"
        assert cfg.convention_value("rate_change_field") == "WIRP_RATE_CHANGE"

    def test_supported_central_banks_pinned_to_adr_0009(self):
        """ADR 0009 §2 lists exactly four region-prefix mappings:
        FOMC ↔ US0B / ECB ↔ EZ0B / BOE ↔ GB0B / BOJ ↔ JP0B.  The
        supported_central_banks convention is wire-locked to these
        four; any addition requires extending wirp.yml AND this
        convention in the same PR."""
        cfg = load_tool_config(CONFIG_PATH)
        raw = cfg.convention_value("supported_central_banks")
        parsed = set(s.strip() for s in str(raw).split(","))
        assert parsed == {"FOMC", "ECB", "BOE", "BOJ"}

    def test_default_n_meetings_matches_brief(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("default_n_meetings") == 6

    def test_convention_sources_registered(self):
        """PR12 — every Convention.source value is a registered tag."""
        cfg = load_tool_config(CONFIG_PATH)
        registered = {
            "team_judgment_pending_review",
            "adr_0009_wirp_time_series",
        }
        for name, conv in cfg.conventions.items():
            assert conv.source in registered, (
                f"convention {name!r} has unregistered source {conv.source!r}"
            )


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, raw_df, config=None):
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=raw_df) as mock_fetch, patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            out = calculate_wirp_meeting_pricing(
                engine=mock_engine, params=params, config=config,
            )
        return out, mock_fetch

    def test_next_n_meetings_happy_path(self):
        raw_df = _build_raw_df(meetings=[
            (date(2026, 6, 17), 3.637, 8.10, 0.081, 0.020),
            (date(2026, 7, 29), 3.676, 15.60, 0.237, 0.059),
            (date(2026, 9, 16), 3.745, 27.40, 0.450, 0.128),
        ], central_bank="FOMC")
        params = WirpMeetingPricingInput(
            central_bank="FOMC",
            selection_mode="next_n_meetings",
            n_meetings=3,
        )
        out, mock_fetch = self._run(params, raw_df)
        assert "error" not in out, out.get("error")
        assert mock_fetch.call_args.kwargs["central_bank"] == "FOMC"

        cm = out["current_metrics"]
        assert cm["central_bank"] == "FOMC"
        assert cm["selection_mode"] == "next_n_meetings"
        assert cm["n_meetings_returned"] == 3
        assert cm["n_meetings_requested"] == 3
        assert cm["requested_meeting_date"] is None
        assert cm["next_meeting_date"] == "2026-06-17"
        assert cm["next_implied_policy_rate_pct"] == 3.637
        # Post-Codex-P0 schema: raw cumulative move prob surfaced
        # verbatim; no derived hike/hold/cut fields.
        assert cm["next_cumulative_move_prob_pct"] == 8.10
        assert cm["next_num_25bp_moves_priced"] == 0.081
        assert cm["next_rate_change_native"] == 0.020
        # The removed fields MUST NOT be present (pins the
        # post-Codex-P0 schema change).
        assert "next_hike_prob_pct" not in cm
        assert "next_cut_prob_pct" not in cm
        assert "next_hold_prob_pct" not in cm
        assert "next_signed_move_prob_pct" not in cm

        assert len(out["meetings"]) == 3
        meeting = out["meetings"][0]
        # Per-meeting shape: removed derived fields too.
        assert "hike_prob_pct" not in meeting
        assert "cut_prob_pct" not in meeting
        assert "hold_prob_pct" not in meeting
        assert "signed_move_prob_pct" not in meeting
        # Renamed field is present
        assert "cumulative_move_prob_pct" in meeting
        # Provenance: synthetic vendor_ticker + 4 Bloomberg tickers
        assert meeting["vendor_ticker"] == "WIRP:FOMC:2026-06-17"
        assert meeting["bloomberg_ticker_implied_rate"] == "US0BFR JUN2026 Index"
        assert meeting["bloomberg_ticker_move_prob"] == "US0BPR JUN2026 Index"
        assert meeting["bloomberg_ticker_num_moves"] == "US0BNM JUN2026 Index"
        assert meeting["bloomberg_ticker_rate_change"] == "US0BCH JUN2026 Index"

    def test_specific_meeting_date_happy_path(self):
        raw_df = _build_raw_df(meetings=[
            (date(2026, 6, 17), 3.637, 8.10, 0.081, 0.020),
        ], central_bank="FOMC")
        params = WirpMeetingPricingInput(
            central_bank="FOMC",
            selection_mode="specific_meeting_date",
            meeting_date=date(2026, 6, 17),
        )
        out, _ = self._run(params, raw_df)
        assert "error" not in out
        cm = out["current_metrics"]
        assert cm["selection_mode"] == "specific_meeting_date"
        assert cm["n_meetings_requested"] is None
        assert cm["requested_meeting_date"] == "2026-06-17"
        assert cm["n_meetings_returned"] == 1
        assert len(out["meetings"]) == 1

    def test_cumulative_move_prob_negative_is_cut_leaning(self):
        """Negative cumulative_move_prob_pct surfaces verbatim as
        cut-leaning pricing.  No derivation."""
        raw_df = _build_raw_df(meetings=[
            (date(2026, 6, 17), 3.50, -22.0, -0.30, -0.055),
        ], central_bank="FOMC")
        params = WirpMeetingPricingInput(
            central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=1,
        )
        out, _ = self._run(params, raw_df)
        assert "error" not in out
        cm = out["current_metrics"]
        assert cm["next_cumulative_move_prob_pct"] == -22.0

    def test_cumulative_move_prob_zero_surfaces_verbatim(self):
        raw_df = _build_raw_df(meetings=[
            (date(2026, 6, 17), 3.50, 0.0, 0.0, 0.0),
        ], central_bank="ECB")
        params = WirpMeetingPricingInput(
            central_bank="ECB", selection_mode="next_n_meetings", n_meetings=1,
        )
        out, _ = self._run(params, raw_df)
        cm = out["current_metrics"]
        assert cm["next_cumulative_move_prob_pct"] == 0.0

    def test_default_n_meetings_used_when_input_omits_it(self):
        """When selection_mode=next_n_meetings and n_meetings is None,
        the YAML default (6) is applied by compute."""
        raw_df = _build_raw_df(meetings=[
            (date(2026, 6, 17), 3.637, 8.10, 0.081, 0.020),
            (date(2026, 7, 29), 3.676, 15.60, 0.237, 0.059),
            (date(2026, 9, 16), 3.745, 27.40, 0.450, 0.128),
            (date(2026, 11, 4), 3.812, 33.0, 0.66, 0.165),
            (date(2026, 12, 16), 3.881, 38.0, 0.76, 0.190),
            (date(2027, 1, 27), 3.950, 43.0, 0.86, 0.215),
            (date(2027, 3, 17), 4.000, 48.0, 0.96, 0.240),  # 7th — not returned
        ], central_bank="FOMC")
        params = WirpMeetingPricingInput(
            central_bank="FOMC", selection_mode="next_n_meetings",
        )  # n_meetings omitted
        out, _ = self._run(params, raw_df)
        assert "error" not in out
        assert out["current_metrics"]["n_meetings_returned"] == 6
        assert out["current_metrics"]["n_meetings_requested"] == 6
        assert len(out["meetings"]) == 6


# ===========================================================================
# 3. Convention guards — PR11 + PR14
# ===========================================================================

class TestConventionGuards:
    @pytest.mark.parametrize("convention_key,bad_value", [
        ("implied_rate_field", "WIRP_IMPLIED_RATE_v2"),
        ("move_prob_field", "WIRP_MOVE_PROB_X"),
        ("num_moves_field", "WIRP_NUM_MOVES_X"),
        ("rate_change_field", "WIRP_BP_CHANGE"),  # the OLD name before ADR 0009 v2
    ])
    def test_unsupported_field_convention_raises(self, convention_key, bad_value):
        raw_df = _build_raw_df(meetings=[
            (date(2026, 6, 17), 3.637, 8.10, 0.081, 0.020),
        ])
        params = WirpMeetingPricingInput(
            central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=1,
        )
        bad = _custom_config(**{convention_key: bad_value})
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            with pytest.raises(NotImplementedError) as exc:
                calculate_wirp_meeting_pricing(
                    engine=mock_engine, params=params, config=bad,
                )
        msg = str(exc.value)
        assert convention_key in msg
        assert bad_value in msg
        assert "ADR 0009" in msg


# ===========================================================================
# 4. Honest absence (P5 + P6)
# ===========================================================================

class TestHonestAbsence:
    def test_empty_raw_df_next_n_returns_error_envelope(self):
        empty = pd.DataFrame(columns=[
            "instrument_id", "vendor_ticker", "meeting_date", "central_bank",
            "meeting_token", "bloomberg_ticker_fr", "bloomberg_ticker_pr",
            "bloomberg_ticker_nm", "bloomberg_ticker_ch", "field_name",
            "field_value", "as_of_date",
        ])
        params = WirpMeetingPricingInput(
            central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=3,
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=empty), patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            out = calculate_wirp_meeting_pricing(engine=mock_engine, params=params)
        assert "error" in out
        assert "FOMC" in out["error"]
        assert "WIRP horizon" in out["error"] or "ADR 0009" in out["error"]

    def test_empty_raw_df_specific_date_returns_error_envelope(self):
        empty = pd.DataFrame(columns=[
            "instrument_id", "vendor_ticker", "meeting_date", "central_bank",
            "meeting_token", "bloomberg_ticker_fr", "bloomberg_ticker_pr",
            "bloomberg_ticker_nm", "bloomberg_ticker_ch", "field_name",
            "field_value", "as_of_date",
        ])
        params = WirpMeetingPricingInput(
            central_bank="FOMC",
            selection_mode="specific_meeting_date",
            meeting_date=date(2099, 1, 1),
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=empty), patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            out = calculate_wirp_meeting_pricing(engine=mock_engine, params=params)
        assert "error" in out
        assert "2099-01-01" in out["error"]

    def test_unsupported_central_bank_returns_error_envelope(self):
        params = WirpMeetingPricingInput(
            central_bank="SNB", selection_mode="next_n_meetings", n_meetings=3,
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=pd.DataFrame()), patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            out = calculate_wirp_meeting_pricing(engine=mock_engine, params=params)
        assert "error" in out
        assert "SNB" in out["error"]
        # Lists the four supported banks
        for cb in ("FOMC", "ECB", "BOE", "BOJ"):
            assert cb in out["error"]

    def test_missing_metric_emits_none_not_zero(self):
        """When a meeting's WIRP_NUM_MOVES is absent (the
        available=false opt-out per ADR 0009 §4), the meeting's
        num_25bp_moves_priced is None — NOT a fabricated zero."""
        raw_df = _build_raw_df(
            meetings=[(date(2026, 6, 17), 3.637, 8.10, 0.0, 0.020)],
            drop_field="WIRP_NUM_MOVES",
        )
        params = WirpMeetingPricingInput(
            central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=1,
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            out = calculate_wirp_meeting_pricing(engine=mock_engine, params=params)
        assert "error" not in out
        meeting = out["meetings"][0]
        assert meeting["num_25bp_moves_priced"] is None, (
            "Missing WIRP_NUM_MOVES emitted as fabricated zero instead of None"
        )
        # The other three fields are populated normally
        assert meeting["implied_policy_rate_pct"] == 3.637
        assert meeting["cumulative_move_prob_pct"] == 8.10


# ===========================================================================
# 5. Schema-layer invariants — extra='forbid' + @model_validator
# ===========================================================================

class TestSchemaInvariants:
    def test_central_bank_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WirpMeetingPricingInput()  # type: ignore[call-arg]

    def test_central_bank_canonicalised(self):
        p = WirpMeetingPricingInput(central_bank="fomc")
        assert p.central_bank == "FOMC"

    def test_central_bank_whitespace_stripped(self):
        p = WirpMeetingPricingInput(central_bank=" ECB ")
        assert p.central_bank == "ECB"

    def test_central_bank_invalid_shape_rejected(self):
        """Pydantic-level rejection: inputs that fail the
        ``[A-Z]{3,4}$`` regex (numeric, wrong length, etc.) are
        rejected at validator time with the ADR 0009 reference."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="ADR 0009"):
            WirpMeetingPricingInput(central_bank="12345")  # not letters
        with pytest.raises(ValidationError, match="ADR 0009"):
            WirpMeetingPricingInput(central_bank="A")  # too short (1 letter)
        with pytest.raises(ValidationError, match="ADR 0009"):
            WirpMeetingPricingInput(central_bank="ABCDE")  # too long (5 letters)
        with pytest.raises(ValidationError, match="ADR 0009"):
            WirpMeetingPricingInput(central_bank="US-FED")  # contains non-letter

    def test_extra_field_rejected(self):
        """Codex P2 lesson from PR #188 — extra='forbid' on Input."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WirpMeetingPricingInput(  # type: ignore[call-arg]
                central_bank="FOMC", random_extra_field=True,
            )

    def test_next_n_with_meeting_date_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="meeting_date must be None"):
            WirpMeetingPricingInput(
                central_bank="FOMC",
                selection_mode="next_n_meetings",
                meeting_date=date(2026, 6, 17),
            )

    def test_specific_date_without_meeting_date_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="meeting_date is required"):
            WirpMeetingPricingInput(
                central_bank="FOMC",
                selection_mode="specific_meeting_date",
            )

    def test_specific_date_with_n_meetings_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="n_meetings must be None"):
            WirpMeetingPricingInput(
                central_bank="FOMC",
                selection_mode="specific_meeting_date",
                meeting_date=date(2026, 6, 17),
                n_meetings=3,
            )

    def test_n_meetings_lower_bound(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WirpMeetingPricingInput(
                central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=0,
            )

    def test_n_meetings_upper_bound(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WirpMeetingPricingInput(
                central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=999,
            )


# ===========================================================================
# 6. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_compute_via_package_init(self):
        from rates_agent.ois.tools.wirp_meeting_pricing import (
            calculate_wirp_meeting_pricing as via_package,
        )
        from rates_agent.ois.tools.wirp_meeting_pricing.compute import (
            calculate_wirp_meeting_pricing as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.ois.tools.wirp_meeting_pricing import (
            WirpMeetingPricingInput as via_package,
        )
        from rates_agent.ois.tools.wirp_meeting_pricing.schemas import (
            WirpMeetingPricingInput as via_schemas,
        )
        from rates_agent.ois.tools.schemas import (
            WirpMeetingPricingInput as via_hub,
        )
        assert via_package is via_schemas
        assert via_package is via_hub


# ===========================================================================
# 7. PR10 — methodology_note disclosure (MANDATORY per the brief)
# ===========================================================================

class TestMethodologyNoteSurface:
    def test_methodology_note_required(self):
        from pydantic import ValidationError
        from rates_agent.ois.tools.wirp_meeting_pricing import (
            WirpMeetingPricingCurrentMetrics,
            WirpMeetingPricingOutput,
        )
        cm = WirpMeetingPricingCurrentMetrics(
            central_bank="FOMC",
            selection_mode="next_n_meetings",
            n_meetings_returned=0,
        )
        with pytest.raises(ValidationError):
            WirpMeetingPricingOutput(  # type: ignore[call-arg]
                current_metrics=cm,
                meetings=[],
            )

    def test_methodology_note_surfaces_mandatory_disclosure(self):
        """The brief's MANDATORY P5 card disclosure: source + NOT
        recomputed + 25bp anchor.  Plus the post-Codex-P0 cumulative
        disclosure (PR #190 fix).
        """
        raw_df = _build_raw_df(meetings=[
            (date(2026, 6, 17), 3.637, 8.10, 0.081, 0.020),
        ])
        params = WirpMeetingPricingInput(
            central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=1,
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            out = calculate_wirp_meeting_pricing(engine=mock_engine, params=params)
        note = out["methodology_note"]
        # Core mandatory disclosures from the brief
        assert "Bloomberg WIRP screen" in note
        assert "ADR 0009" in note
        assert "NOT recomputed" in note or "Not recomputed" in note.lower()
        assert "STIR futures" in note
        assert "25bp" in note
        assert "P12" in note
        # Codex-P0 cumulative disclosure (the LOAD-BEARING fix)
        assert "CUMULATIVE" in note
        assert "-360.1" in note or "-360" in note  # observed range disclosure
        assert "548.0" in note or "548" in note
        assert "DO NOT" in note or "DOES NOT" in note  # the explicit no-identity disclosure
        # The removed-derivation disclaimer
        assert (
            "does NOT emit" in note or "DOES NOT emit" in note
            or "does not emit" in note.lower()
        )
        # WIRP_RATE_CHANGE unit confirmed as percentage points
        assert "PERCENTAGE POINTS" in note or "percentage points" in note


# ===========================================================================
# 8. Identity derivation math — exhaustive corner cases
# ===========================================================================

class TestCumulativeMoveProbVerbatim:
    """Post-Codex-P0 (PR #190 review): WIRP_MOVE_PROB is CUMULATIVE
    per wirp.yml — observed live-DB range -360.1 .. 548.0.  The
    primitive surfaces the value VERBATIM and DOES NOT emit any
    derived hike / hold / cut probability fields.  These tests pin
    the post-fix wire contract and cover the load-bearing cumulative
    values that broke the original derivation.
    """

    @pytest.mark.parametrize("cumulative_prob", [
        # Values inside the naive [-100, 100] range
        8.10,
        -22.0,
        0.0,
        100.0,
        -100.0,
        # Values OUTSIDE [-100, 100] — the empirical cases that
        # broke the original derivation.  These are the load-bearing
        # cumulative-semantics test vectors per the Codex P0 finding.
        -104.9,     # BOE 2025-05-08 — Codex's empirical counter-example
        200.0,      # Two-25bp-hike priced
        -150.0,     # 1.5 cuts priced
        548.0,      # Live-DB max range
        -360.1,     # Live-DB min range
    ])
    def test_cumulative_move_prob_surfaces_verbatim(self, cumulative_prob):
        """No matter the value (in-band or out-of-naive-range), the
        primitive surfaces it verbatim under ``cumulative_move_prob_pct``
        and emits no derived hike/hold/cut fields."""
        raw_df = _build_raw_df(meetings=[
            (date(2026, 6, 17), 3.5, cumulative_prob, 0.0, 0.0),
        ])
        params = WirpMeetingPricingInput(
            central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=1,
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            out = calculate_wirp_meeting_pricing(engine=mock_engine, params=params)
        m = out["meetings"][0]
        # Verbatim — no clamping, no clipping, no derivation.
        assert m["cumulative_move_prob_pct"] == cumulative_prob
        # Removed-field absence pinned for the cumulative case too.
        for removed in (
            "signed_move_prob_pct",
            "hike_prob_pct",
            "cut_prob_pct",
            "hold_prob_pct",
        ):
            assert removed not in m, (
                f"Removed field {removed!r} reappeared in the output "
                f"for cumulative_prob={cumulative_prob!r} — the Codex "
                f"P0 fix has regressed"
            )

    def test_none_propagates_when_move_prob_missing(self):
        """When WIRP_MOVE_PROB is missing (the available=false opt-
        out per ADR 0009 §4), the cumulative_move_prob_pct is None.
        Pin the honest-absence shape — no fallback to 0.0."""
        raw_df = _build_raw_df(
            meetings=[(date(2026, 6, 17), 3.5, 0.0, 0.0, 0.0)],
            drop_field="WIRP_MOVE_PROB",
        )
        params = WirpMeetingPricingInput(
            central_bank="FOMC", selection_mode="next_n_meetings", n_meetings=1,
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.ois.tools.wirp_meeting_pricing."
            "compute.fetch_wirp_meeting_snapshots"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.ois.tools.wirp_meeting_pricing.compute.date",
            _FrozenDate,
        ):
            out = calculate_wirp_meeting_pricing(engine=mock_engine, params=params)
        m = out["meetings"][0]
        assert m["cumulative_move_prob_pct"] is None
