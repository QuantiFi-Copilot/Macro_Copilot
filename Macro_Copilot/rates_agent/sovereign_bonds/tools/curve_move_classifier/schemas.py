"""Pydantic schemas for the curve_move_classifier tool.

Renamed from the legacy ``CurveRegime*`` family.  The previous name
overclaimed: this tool classifies a single observed move, not a
persistence state.

Validation layering
-------------------
- ``front_tenor != back_tenor`` is an invariant — encoded as a
  ``@model_validator`` here.
- ``lookback_period`` must be one of the labels listed in
  ``config.yaml``'s ``allowed_lookback_periods`` convention.  This is
  also enforced at the Pydantic layer (see
  ``_lookback_period_must_be_allowed`` below) so direct API callers
  get a 422 input-validation error, not a generic 500 or a "compute
  returned an error envelope".  Loading the allow-set lazily from the
  bundled config keeps the schema and YAML in lock-step; tests that
  pass a custom ToolConfig still work because ``compute()`` validates
  again against the runtime config (defence in depth).
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_field_name`` convention".  Callers can still
  override per-query.  ``compute()`` is the one place that resolves
  the sentinel against the active config, so editing
  ``default_field_name`` in YAML actually changes runtime behaviour.

Validators that encode invariants (front/back tenors must differ) stay
here in code; they are not configurable.

No canonical TimeSeries output (by design)
------------------------------------------
The legacy-sovereign TimeSeries tech-debt cleanup migrated four other
tools (``yield_levels``, ``curve_spread``, ``butterfly``,
``cross_market_spread``) to emit canonical
``shared.schemas.time_series.TimeSeries`` fields (e.g.
``time_series``, ``time_series_spread``, ``time_series_zscore``) for
the upcoming primitive-to-operator bridge.
``curve_move_classifier`` is deliberately NOT migrated: it is a
single-observation classifier that emits one set of regime tags per
call.  There is no historical time series being computed here —
adding canonical TimeSeries fields would require a new compute path
that classifies every prior day in the lookback window, which is a
different computation, not a wire-format migration.

If a future workflow needs a historical regime time series, that is a
new sibling tool (e.g. ``curve_move_classifier_panel``), not an
additive output on this one.  Documented here so the absence is
intentional, not an oversight.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator


def _bundled_allowed_lookback_periods() -> set[str]:
    """Read the allowed lookback set from the bundled ``config.yaml``.

    Looked up lazily inside the validator so circular-import risk is
    zero (the schema doesn't import compute or ToolConfig at module-
    load time; only the validator path touches them).  Cached by the
    underlying ``load_tool_config`` so this is a one-time cost.

    If the bundled YAML can't be loaded for any reason, we fall back
    to the historical default set.  The validator then short-circuits
    against the fallback and ``compute()`` will surface any deeper
    config error the ordinary way.
    """
    try:
        # Local imports to avoid a top-of-module cycle through compute.
        from rates_agent.sovereign_bonds.tools.curve_move_classifier.compute import (
            CONFIG_PATH,
        )
        from shared.config import load_tool_config

        cfg = load_tool_config(CONFIG_PATH)
        csv = cfg.convention_value("allowed_lookback_periods")
        return {p.strip() for p in str(csv).split(",") if p.strip()}
    except Exception:
        # Defensive fallback — historical set.  compute() still does
        # the strict per-call validation against its own (possibly
        # custom) ToolConfig, so this branch never silently breaks
        # tests that pass a stub config.
        return {"1d", "5d", "22d", "63d"}


class CurveMoveInput(BaseModel):
    """Parameters the LLM extracts to classify a curve move."""

    curve_family: str = Field(
        ...,
        description=(
            "Curve family identifier as stored in instrument_master.  "
            "Accepts any curve_family declared in a tenor-keyed "
            "playbook under rates_agent/playbooks/ — sovereign "
            "benchmarks ('UST', 'DE_BUND', 'IT_BTP', 'FR_OAT', "
            "'ES_BONO', 'UK_GILT', 'JGB', 'CANADA_GOVT', 'AU_GOVT'), "
            "OIS curves ('USD_SOFR_OIS', 'EUR_ESTR_OIS', "
            "'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', 'CAD_OIS'), "
            "inflation swaps ('USD_ZCIS', 'EUR_ZCIS', 'GBP_ZCIS'), and "
            "sovereign linker real-yield curves ('USD_TIPS', "
            "'GBP_LINKER', 'EUR_FR_LINKER', 'CAD_RRB').  The "
            "4-quadrant classification math is curve-family-agnostic; "
            "front-leg minus back-leg change in the underlying "
            "observation (yield, par rate, or breakeven) is the "
            "signal regardless of family."
        ),
    )
    front_tenor: str = Field(
        default="2Y",
        description="The front-end (short) leg.  Default '2Y'.",
    )
    back_tenor: str = Field(
        default="10Y",
        description="The back-end (long) leg.  Default '10Y'.",
    )
    lookback_period: str = Field(
        default="1d",
        description=(
            "Discrete period over which the move is measured: '1d' "
            "(today's move), '5d' (weekly), '22d' (monthly), '63d' "
            "(quarterly).  The full set of allowed values comes from "
            "the tool's config.yaml — see allowed_lookback_periods."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None (default), the "
            "tool auto-discovers the field from the owning playbook's "
            "``target_metrics[0].bloomberg_field`` — different per "
            "playbook: sovereign benchmarks + linkers use "
            "'YLD_YTM_MID', OIS curves use 'PX_LAST', ZCIS curves use "
            "'PX_MID'.  Falls through to the YAML's "
            "``default_field_name`` ('YLD_YTM_MID') as a final "
            "fallback so sovereign callers see identical behaviour to "
            "the pre-A4 path.  Pass an explicit field name to override "
            "per query."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @model_validator(mode="after")
    def _tenors_must_differ(self) -> "CurveMoveInput":
        if self.front_tenor == self.back_tenor:
            raise ValueError(
                f"front_tenor and back_tenor must be different, but "
                f"both are '{self.front_tenor}'."
            )
        return self

    @model_validator(mode="after")
    def _lookback_period_must_be_allowed(self) -> "CurveMoveInput":
        """Reject lookback labels not in the bundled config's
        ``allowed_lookback_periods`` set at the input-validation layer.

        ``compute()`` re-validates against its own (possibly custom)
        ToolConfig — that catches the case where a test or future
        advanced-mode caller passes a config with a narrower /
        broader allow-set.  The schema layer only protects against
        the LLM picking a label outside the production-deployed set.
        """
        allowed = _bundled_allowed_lookback_periods()
        if self.lookback_period not in allowed:
            raise ValueError(
                f"lookback_period '{self.lookback_period}' is not in the "
                f"allowed set {sorted(allowed)}.  Update "
                f"config.yaml's allowed_lookback_periods convention to "
                f"add new labels (and add the corresponding iloc-offset "
                f"mapping in compute._PERIOD_OFFSETS)."
            )
        return self


class CurveMoveCurrentMetrics(BaseModel):
    """Snapshot classification + supporting numbers."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    prior_date: str = Field(..., description="The comparison date at the start of the lookback.")
    curve_family: str
    lookback_period: str = Field(..., description="The lookback period used.")
    spread_label: str = Field(..., description="Human-readable label, e.g. '2s10s'.")

    # NOTE: the field name is ``classification`` rather than the legacy
    # ``regime_tag``.  Same content; the rename completes the move
    # away from the misleading "regime" word.  ``regime_tag`` is NOT
    # provided as a backward-compat alias — every caller that read
    # ``regime_tag`` is being migrated in this same commit.
    classification: str = Field(
        ...,
        description=(
            "One of BULL_STEEPENER, BEAR_STEEPENER, BULL_FLATTENER, "
            "BEAR_FLATTENER, PARALLEL_SHIFT, TWIST."
        ),
    )
    description: str = Field(
        ...,
        description="Plain-English explanation of the classification.",
    )

    front_tenor: str
    back_tenor: str
    # PR14 wire-format honesty (Round 3 A4, post-Codex review): the
    # field names are ``_level_*`` (not ``_yield_*``) because this
    # primitive is now curve-family-agnostic and the underlying
    # observation may be a sovereign yield, an OIS par rate, an
    # inflation swap rate, or a linker real yield depending on the
    # bound ``curve_family``.  "Level" is the unit-agnostic name for
    # the per-tenor observation; the description names the actual
    # underlying per curve_family.
    front_level_current: Optional[float] = Field(
        None,
        description=(
            "Current level on the front leg (percent).  Underlying "
            "observation depends on the bound curve_family: sovereign "
            "yield, OIS par rate, inflation swap rate, or linker real "
            "yield.  Field name is unit-agnostic; the playbook owns "
            "the observation semantics."
        ),
    )
    back_level_current: Optional[float] = Field(
        None,
        description=(
            "Current level on the back leg (percent).  Underlying "
            "observation depends on the bound curve_family — see "
            "front_level_current."
        ),
    )
    front_level_prior: Optional[float] = Field(
        None,
        description=(
            "Prior level on the front leg (percent) at the start of "
            "the lookback window.  Underlying observation depends on "
            "the bound curve_family — see front_level_current."
        ),
    )
    back_level_prior: Optional[float] = Field(
        None,
        description=(
            "Prior level on the back leg (percent) at the start of "
            "the lookback window.  Underlying observation depends on "
            "the bound curve_family — see front_level_current."
        ),
    )
    front_change_bps: Optional[float] = Field(None, description="Change in the front leg over the lookback (bps).")
    back_change_bps: Optional[float] = Field(None, description="Change in the back leg over the lookback (bps).")
    spread_current_bps: Optional[float] = Field(None, description="Current spread (back − front) in bps.")
    spread_prior_bps: Optional[float] = Field(None, description="Prior spread (back − front) in bps.")
    spread_change_bps: Optional[float] = Field(None, description="Change in the spread over the lookback (bps).")


class CurveMoveOutput(BaseModel):
    """Top-level response for the curve-move classifier."""

    current_metrics: CurveMoveCurrentMetrics


__all__ = [
    "CurveMoveInput",
    "CurveMoveCurrentMetrics",
    "CurveMoveOutput",
]
