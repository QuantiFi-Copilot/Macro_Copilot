"""Multi-component primitive → operator composition contract tests.

Locks the standardized mechanism that lets a leaf bind ONE named
component of a multi-component primitive (e.g. PCA's ``pc1`` factor-score
series) as a normal ``Series`` via ``output_field`` — the fix for the
"correlate PC1 with 5Y breakeven" open-DAG case.

Three layers, all deterministic (no DB, no LLM):
  1. the Series-bridge resolver (``_resolve_list_component_series``) finds
     a ``List[TimeSeries]`` element by ``series_name`` token;
  2. PCA declares its component keys in ``output_field_units`` and the
     selector exposes them (does NOT drop them);
  3. the composability audit still classifies every primitive (no
     UNDECLARED) and PCA stays BRIDGEABLE_SERIES (no regression).

See docs_revamped/02_components/primitive/README.md (the multi-component
selectable-component contract).
"""
from __future__ import annotations

from typing import List

import pytest
from pydantic import BaseModel, ConfigDict

from shared.schemas import TimeSeries
from shared.schemas.time_series import TimeSeriesUnits
from shared.artifacts.adapters.from_time_series import (
    _is_time_series_list_type,
    _resolve_list_component_series,
    _series_name_tokens,
)
from orchestrator.selectors import (
    _series_typed_fields,
    _has_time_series_list_field,
)
from orchestrator.open_dag.composability_audit import (
    audit_resolver,
    classify_primitive,
    Composability,
)
from rates_agent.workflows import (
    rates_primitive_resolver,
    known_rates_primitives,
)


def _ts(name: str) -> TimeSeries:
    return TimeSeries(
        series_name=name, units=TimeSeriesUnits.RATIO, description="f", rows=[],
    )


class _FitOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_series_factors: List[TimeSeries]


# ---------------------------------------------------------------------------
# 1. Bridge resolver
# ---------------------------------------------------------------------------

def test_resolver_finds_component_by_series_name_token():
    v = _FitOut(time_series_factors=[
        _ts("UST_pc1_factor_daily"),
        _ts("UST_pc2_factor_daily"),
        _ts("UST_pc3_factor_daily"),
    ])
    got = _resolve_list_component_series(v, _FitOut, "pc1")
    assert got is not None
    assert got[0].series_name == "UST_pc1_factor_daily"
    assert got[1] == "time_series_factors"


def test_resolver_exact_token_not_substring():
    # 'pc1' must NOT match 'pc10'.
    v = _FitOut(time_series_factors=[
        _ts("UST_pc1_factor_daily"), _ts("UST_pc10_factor_daily"),
    ])
    assert _resolve_list_component_series(v, _FitOut, "pc1")[0].series_name == "UST_pc1_factor_daily"
    assert _resolve_list_component_series(v, _FitOut, "pc10")[0].series_name == "UST_pc10_factor_daily"


def test_resolver_case_insensitive():
    v = _FitOut(time_series_factors=[_ts("UST_pc2_factor_daily")])
    assert _resolve_list_component_series(v, _FitOut, "PC2") is not None


def test_resolver_miss_returns_none():
    v = _FitOut(time_series_factors=[_ts("UST_pc1_factor_daily")])
    assert _resolve_list_component_series(v, _FitOut, "nope") is None


def test_resolver_ambiguous_raises():
    v = _FitOut(time_series_factors=[_ts("a_pc1_x"), _ts("b_pc1_y")])
    with pytest.raises(ValueError, match="ambiguous"):
        _resolve_list_component_series(v, _FitOut, "pc1")


def test_is_time_series_list_type_detection():
    assert _is_time_series_list_type(_FitOut.model_fields["time_series_factors"].annotation)

    class _Plain(BaseModel):
        ts: TimeSeries
        s: List[str] = []
    assert not _is_time_series_list_type(_Plain.model_fields["ts"].annotation)
    assert not _is_time_series_list_type(_Plain.model_fields["s"].annotation)


def test_series_name_tokens():
    assert _series_name_tokens("UST_pc1_factor_daily") == {"ust", "pc1", "factor", "daily"}


# ---------------------------------------------------------------------------
# 2. PCA declares + selector exposes its component keys
# ---------------------------------------------------------------------------

def test_pca_declares_component_output_fields():
    spec = rates_primitive_resolver("calculate_pca_yield_curve_tool")
    assert set(spec.output_field_units.keys()) == {"pc1", "pc2", "pc3"}


def test_selector_exposes_pca_components():
    """The selector's BRIDGEABLE_SERIES filter must KEEP the PCA
    component keys (they are List[TimeSeries] components, not top-level
    fields) — else the leaf can't bind and the pipeline refuses."""
    spec = rates_primitive_resolver("calculate_pca_yield_curve_tool")
    canon = set(_series_typed_fields(spec.output_class))
    has_list = _has_time_series_list_field(spec.output_class)
    top = set(spec.output_class.model_fields.keys())
    available = tuple(
        f for f in spec.output_field_units
        if f in canon or (has_list and f not in top)
    )
    assert available == ("pc1", "pc2", "pc3")


# ---------------------------------------------------------------------------
# 3. No regression in the composability audit (all tools classified)
# ---------------------------------------------------------------------------

def test_all_primitives_classified_no_undeclared():
    audit = audit_resolver(rates_primitive_resolver, sorted(known_rates_primitives()))
    undeclared = [n for n, e in audit.items() if e.classification == Composability.UNDECLARED]
    assert not undeclared, f"UNDECLARED primitives: {undeclared}"


def test_pca_stays_bridgeable_series():
    spec = rates_primitive_resolver("calculate_pca_yield_curve_tool")
    assert classify_primitive(spec).classification == Composability.BRIDGEABLE_SERIES
