"""
compute.py — Deterministic curve-move classifier (config-driven)
=================================================================

Renamed from the legacy ``classify_curve_regime`` to
``classify_curve_move``.  Same six-tag taxonomy, same 4-quadrant
math; the only behavioural change is that thresholds now flow from
the bundled ``config.yaml`` rather than module-level constants.

Why rename
----------
"Regime" in rates / macro implies a persistence state inferred over
time (Markov-switching / HMM territory).  This tool does no such
inference — it classifies one observed move over a fixed lookback.
``curve_move_classifier`` is honest; ``curve_regime`` overclaimed.
The earlier name is preserved nowhere; this commit migrates every
caller in the same change.

Curve-family-agnostic scope (Round 3 Stage 2, work item A4 — PR5
coverage extension)
-------------------------------------------------------------------
The primitive accepts ANY tenor-keyed rates curve_family declared
in any playbook under ``rates_agent/playbooks/``.  Today that
covers sovereign benchmarks (UST / DE_BUND / IT_BTP / FR_OAT /
ES_BONO / UK_GILT / JGB / CANADA_GOVT / AU_GOVT), OIS curves
(USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS /
CAD_OIS), inflation swaps (USD_ZCIS / EUR_ZCIS / GBP_ZCIS), and
sovereign linker real-yield curves (USD_TIPS / GBP_LINKER /
EUR_FR_LINKER / CAD_RRB).  The 4-quadrant classification math is
curve-family-agnostic — front-leg minus back-leg change in the
underlying observation (yield, par rate, or breakeven) is the
signal regardless of which family is being classified.  Per the
primitive runbook's "When NOT to use this runbook" section, this is
the PR5 coverage-extension path (extend an existing primitive's
allowed input values) rather than shipping a sibling per family.

Per-playbook field-name discovery
---------------------------------
Different playbooks declare different Bloomberg primary fields:
sovereign benchmarks + linkers use ``YLD_YTM_MID``; OIS curves use
``PX_LAST``; ZCIS curves use ``PX_MID``.  The primitive auto-
discovers each curve_family's default field from the owning
playbook's ``target_metrics[0].bloomberg_field`` so callers do not
need to know the per-vendor field convention.  Resolution priority
when ``params.field_name`` is None:
  1. The owning playbook's ``target_metrics[0].bloomberg_field``.
  2. The YAML ``default_field_name`` (preserved as a final fallback,
     identical to sovereign's ``YLD_YTM_MID`` so legacy behaviour is
     unchanged for sovereign callers).
An explicit non-None ``params.field_name`` always wins.

The discovery helpers below are a deliberate near-copy of the
matching helpers in ``rates_agent/sovereign_bonds/tools/
pca_yield_curve/compute.py`` (Round 3 A3).  A future cleanup PR
will extract the shared logic to a single ``rates_agent/playbooks/``
helper module once both A3 and A4 have landed independently;
duplicating per-primitive for now keeps the A3 and A4 PRs
independently reviewable + revertable.

Convention -> primitive wiring
------------------------------
Every methodology choice (parallel threshold, move threshold, fill
limit, default field, allowed lookback periods, avg-aggregation
method) flows from ``config.yaml`` and is passed explicitly to
either ``shared.analytics.curve_move.classify_curve_move`` or to
``shared.analytics.spreads.pivot_and_align_tenors``.  No hardcoded
methodology constants.

Honest placeholders
-------------------
``avg_change_method`` is a YAML convention that today only supports
``arithmetic_mean``.  The methodology block in ``config.yaml`` lists
the planned alternatives (``duration_weighted``, ``dv01_weighted``,
``back_leg_only``).  When the convention is set to anything other
than ``arithmetic_mean``, ``compute()`` raises
``NotImplementedError`` with a pointer to that block.  This makes
the YAML field real (you can edit it; the system fails loudly with
a self-documenting error) without silently falling back to the
default — see the discussion in commit 6 of the tool-config pilot.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.curve_move_classifier.schemas import (
    CurveMoveCurrentMetrics,
    CurveMoveInput,
    CurveMoveOutput,
)
from shared.analytics.curve_move import classify_curve_move
from shared.analytics.playbook_discovery import (
    playbook_default_field_for_curve_family,
)
from shared.analytics.rates_fetch import fetch_tenor_group, latest_trade_date
from shared.analytics.spreads import pivot_and_align_tenors, safe_float
from shared.config import ToolConfig, load_tool_config


# Bundled config — relative to this file.  Loaded lazily on first call.
# Public symbol so external callers can build their own ToolConfig
# from the same source the tool uses.
CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# The per-playbook field-name auto-discovery helper
# (``playbook_default_field_for_curve_family``) lives in
# ``shared.analytics.playbook_discovery`` — single source of truth
# for the multi-playbook scanner (per P10).  Round 3 A3 (PR #195)
# introduced the shared module; this A4 PR consumes it rather than
# duplicating the scanner per-folder.


# ============================================================================
# DOMAIN-LOCAL CONFIG
# ============================================================================
# Map lookback_period labels to iloc offsets.  After ffill the series
# is approximately trading-daily; this table defines how many rows
# back each label reaches.  Kept local because the labels are a
# product UX choice, not shared analytics.  The set of allowed
# labels is gated by the ``allowed_lookback_periods`` convention in
# config.yaml — but the iloc-offset mapping (the SEMANTIC of each
# label) lives here in code because it's not a methodology
# tweak so much as a label-to-offset binding.  If you add a label
# to the YAML, add it here too.
_PERIOD_OFFSETS: dict[str, int] = {
    "1d": 2,    # current vs previous (iloc[-1] vs iloc[-2])
    "5d": 6,    # current vs 5 trading days ago
    "22d": 22,  # current vs ~1 month ago
    "63d": 63,  # current vs ~3 months ago
}


# Per-tag plain-English narration.  Kept domain-local because these
# sentences are written for a PM reading a sovereign-curve move; an
# OIS variant would phrase the macro intuition differently.
_CLASSIFICATION_DESCRIPTIONS: dict[str, str] = {
    "BULL_STEEPENER": (
        "Yields fell and the curve steepened — the front end rallied "
        "more than the back end.  Typically signals dovish repricing "
        "or flight-to-quality demand concentrated in shorter maturities."
    ),
    "BEAR_STEEPENER": (
        "Yields rose and the curve steepened — the back end sold off "
        "more than the front end.  Typically signals rising term premium, "
        "inflation fears, or increased supply expectations."
    ),
    "BULL_FLATTENER": (
        "Yields fell and the curve flattened — the back end rallied "
        "more than the front end.  Typically signals a flight-to-duration "
        "bid or expectations of prolonged low rates."
    ),
    "BEAR_FLATTENER": (
        "Yields rose and the curve flattened — the front end sold off "
        "more than the back end.  Typically signals hawkish central bank "
        "repricing with rate hikes being pulled forward."
    ),
    "PARALLEL_SHIFT": (
        "Both legs moved roughly together with minimal change in curve "
        "slope.  The shape of the curve was preserved."
    ),
    "TWIST": (
        "The front end and back end moved in opposite directions — a "
        "curve twist.  This often reflects a central bank surprise or "
        "a shift in the relative supply/demand for short vs long duration."
    ),
}


# ============================================================================
# PUBLIC API
# ============================================================================

def classify_curve_move_compute(
    engine: Engine,
    params: CurveMoveInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """
    Classify the curve move over a discrete lookback period.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine.
    params : CurveMoveInput
        Validated input.
    config : ToolConfig, optional
        Bundled config.yaml is auto-loaded when None; tests pass a
        custom ToolConfig to exercise convention overrides.

    Returns
    -------
    dict
        Serialised ``CurveMoveOutput``, or ``{"error": "..."}`` on
        recoverable failure.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    parallel_threshold = config.convention_value("parallel_threshold_bps")
    move_threshold = config.convention_value("move_threshold_bps")
    ffill_limit = config.convention_value("ffill_limit_days")
    avg_method = config.convention_value("avg_change_method")
    default_field_name = config.convention_value("default_field_name")
    allowed_periods_csv = config.convention_value("allowed_lookback_periods")
    allowed_periods = {
        p.strip() for p in allowed_periods_csv.split(",") if p.strip()
    }

    # ------------------------------------------------------------------
    # Field-name resolution (Round 3 A4 — curve-family-agnostic).
    # Priority chain when params.field_name is None:
    #   1. Explicit params.field_name (LLM / API caller override).
    #   2. The owning playbook's target_metrics[0].bloomberg_field
    #      (auto-discovered per curve_family — different per playbook:
    #      sovereign + linker use YLD_YTM_MID, OIS uses PX_LAST, ZCIS
    #      uses PX_MID).
    #   3. The YAML's ``default_field_name`` (final fallback;
    #      preserved as YLD_YTM_MID for sovereign backward compat —
    #      sovereign_bonds.yml's target_metrics[0] is also
    #      YLD_YTM_MID, so sovereign callers see no change in behaviour).
    # ------------------------------------------------------------------
    if params.field_name is not None:
        field_name_resolved = params.field_name
    else:
        discovered_field = playbook_default_field_for_curve_family(
            params.curve_family
        )
        field_name_resolved = (
            discovered_field if discovered_field else default_field_name
        )

    # ------------------------------------------------------------------
    # Honest placeholder — fail loudly on not-yet-implemented methods
    # ------------------------------------------------------------------
    if avg_method != "arithmetic_mean":
        raise NotImplementedError(
            f"avg_change_method={avg_method!r} is documented in this tool's "
            f"config.yaml as a future-supported value (see "
            f"methodology.planned_extensions) but is not yet implemented.  "
            f"V1 supports only 'arithmetic_mean'.  Either restore the "
            f"default value in config.yaml or implement the alternative."
        )

    # ------------------------------------------------------------------
    # Validate lookback_period against the YAML's allowed set
    # ------------------------------------------------------------------
    if params.lookback_period not in allowed_periods:
        return {
            "error": (
                f"Invalid lookback_period '{params.lookback_period}'.  "
                f"Allowed values from config.yaml: "
                f"{sorted(allowed_periods)}."
            )
        }

    offset = _PERIOD_OFFSETS.get(params.lookback_period)
    if offset is None:
        # Defensive: the YAML lists a label that compute() doesn't know
        # how to map to an iloc-offset.  This is a code-vs-config drift
        # that the planned_extensions block warns about.
        return {
            "error": (
                f"Lookback label '{params.lookback_period}' is allowed by "
                f"config.yaml but has no iloc-offset mapping in "
                f"compute.py._PERIOD_OFFSETS.  Add the mapping in code "
                f"to match the YAML extension."
            )
        }

    # ------------------------------------------------------------------
    # 1. Fetch enough history for the offset plus some buffer
    # ------------------------------------------------------------------
    # No z-score warm-up needed (deterministic classifier, not statistical);
    # buffer is just enough to absorb holidays and weekends around the
    # offset.
    buffer_days = max(offset * 3, 60)
    # Anchor the window to the latest available trade_date for this curve/field,
    # NOT date.today(): a 1d/5d lookback is shorter than a weekend/holiday/
    # ingestion gap, so anchoring on "now" yields an empty window the moment the
    # data lags.  Falls back to date.today() only when the universe has no rows.
    anchor = (
        latest_trade_date(
            engine,
            curve_family=params.curve_family,
            field_name=field_name_resolved,
        )
        or date.today()
    )
    start_date = anchor - timedelta(days=buffer_days)

    raw_df = fetch_tenor_group(
        engine=engine,
        curve_family=params.curve_family,
        tenors=[params.front_tenor, params.back_tenor],
        field_name=field_name_resolved,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenors=['{params.front_tenor}', '{params.back_tenor}'], "
                f"field='{field_name_resolved}' since {start_date.isoformat()}."
            )
        }

    # ------------------------------------------------------------------
    # 2. Validate both tenors are present
    # ------------------------------------------------------------------
    available_tenors = set(raw_df["tenor"].unique())
    missing = {params.front_tenor, params.back_tenor} - available_tenors
    if missing:
        return {
            "error": (
                f"Missing tenor data for {missing} in "
                f"'{params.curve_family}'.  "
                f"Available: {sorted(available_tenors)}."
            )
        }

    # ------------------------------------------------------------------
    # 3. Pivot + align holiday gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.front_tenor, params.back_tenor),
        ffill_limit=ffill_limit,
    )

    if len(wide) < offset:
        return {
            "error": (
                f"Insufficient data for a {params.lookback_period} lookback "
                f"on '{params.curve_family}' "
                f"{params.front_tenor}/{params.back_tenor}.  Only "
                f"{len(wide)} observations available, need at least {offset}."
            )
        }

    # ------------------------------------------------------------------
    # 4. Compute bps changes and aggregate
    # ------------------------------------------------------------------
    current_front = float(wide[params.front_tenor].iloc[-1])
    current_back = float(wide[params.back_tenor].iloc[-1])
    prior_front = float(wide[params.front_tenor].iloc[-offset])
    prior_back = float(wide[params.back_tenor].iloc[-offset])

    front_change_bps = round((current_front - prior_front) * 100, 2)
    back_change_bps = round((current_back - prior_back) * 100, 2)
    spread_change_bps = round(back_change_bps - front_change_bps, 2)

    # avg_method already validated above as 'arithmetic_mean'.
    avg_change_bps = round((front_change_bps + back_change_bps) / 2, 2)

    current_spread_bps = round((current_back - current_front) * 100, 2)
    prior_spread_bps = round((prior_back - prior_front) * 100, 2)

    # ------------------------------------------------------------------
    # 5. Classify via shared primitive (config-driven thresholds)
    # ------------------------------------------------------------------
    classification = classify_curve_move(
        front_change_bps=front_change_bps,
        back_change_bps=back_change_bps,
        spread_change_bps=spread_change_bps,
        avg_change_bps=avg_change_bps,
        parallel_threshold_bps=parallel_threshold,
        move_threshold_bps=move_threshold,
    )

    # ------------------------------------------------------------------
    # 6. Build output
    # ------------------------------------------------------------------
    as_of = wide.index[-1].strftime("%Y-%m-%d")
    prior_date = wide.index[-offset].strftime("%Y-%m-%d")

    spread_label = (
        f"{params.front_tenor.replace('Y', '')}s"
        f"{params.back_tenor.replace('Y', '')}s"
    )

    metrics = CurveMoveCurrentMetrics(
        as_of_date=as_of,
        prior_date=prior_date,
        curve_family=params.curve_family,
        lookback_period=params.lookback_period,
        spread_label=spread_label,
        classification=classification,
        description=_CLASSIFICATION_DESCRIPTIONS.get(classification, ""),
        front_tenor=params.front_tenor,
        back_tenor=params.back_tenor,
        front_level_current=safe_float(current_front),
        back_level_current=safe_float(current_back),
        front_level_prior=safe_float(prior_front),
        back_level_prior=safe_float(prior_back),
        front_change_bps=front_change_bps,
        back_change_bps=back_change_bps,
        spread_current_bps=current_spread_bps,
        spread_prior_bps=prior_spread_bps,
        spread_change_bps=spread_change_bps,
    )

    output = CurveMoveOutput(current_metrics=metrics)
    return output.model_dump()
