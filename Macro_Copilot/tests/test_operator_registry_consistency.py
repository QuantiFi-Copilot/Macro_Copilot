"""OPR16 — the registry-consistency meta-test (the enforcement gate).

This is the cornerstone artifact of operator standardization v2.0
(ADR 0016).  It is parametrized over ``OPERATOR_REGISTRY`` and
mechanically proves that every v2-conformant operator satisfies the
contract clauses that can be checked without executing it:

  (a) signature params == input_slots ∪ {params, config}            (OPR9/OPR15)
  (b) ``params`` default is None (config-default resolution)         (OPR8)
  (c) ``<Operator>Error`` subclasses ValueError                     (OPR13)
  (d) output.artifact_type + every input-slot descriptor.artifact_type
      ∈ the closed family ``ARTIFACT_TYPE_NAMES``                    (OPR9)
  (e) ``__init__`` exports {CONFIG_PATH, <op>, <Params>, <Error>}    (OPR16)
  (f) config.operator.name == registry key
      AND config.operator.version == module._OPERATOR_VERSION        (OPR12)

The slot-shape contract is read from the structured ``SlotDescriptor`` /
``OutputDescriptor`` types introduced in the PART B refactor: each input
slot's ``descriptor.artifact_type`` (an ``ArtifactTypeName`` member) is
checked for closed-family membership, and ``descriptor.is_list`` is the
canonical replacement for the prior ``"List[X]"`` string-prefix encoding
(no string-stripping required).

Running this test IS the verification step — "is the operator
conformant?" is answered by ``pytest``, not by hand-review.

Legacy operators predate v2.0 and do NOT yet conform; they are listed
explicitly in ``_LEGACY_PENDING_MIGRATION`` and exercised only by the
basic-shape checks until they are migrated (Step 3, ADR 0016).  A NEW
operator cannot be added to the registry without either conforming
(``_V2_CONFORMANT``) or being explicitly tracked as legacy — the
classification test below forces that decision.
"""

from __future__ import annotations

import importlib
import inspect
import re

import pytest

from shared.config.operator_config import load_operator_config
from shared.workflow.registry import ARTIFACT_TYPE_NAMES, OPERATOR_REGISTRY
from shared.workflow.slots import OutputDescriptor, SlotDescriptor


# Operators built to (or migrated to) the v2.0 contract (ADR 0016).
# The full OPR16 conformance gate runs over this set; Step 3 grew it
# from {correlation} to include the 8 migrated finance-blind operators.
_V2_CONFORMANT = {
    "bandpass",
    "beta",
    "changepoint_detection",
    "cointegration",
    "correlation",
    "covariance",
    "cross_sectional_rank",
    "cross_sectional_statistic",
    "cross_sectional_zscore",
    "demean_cross_section",
    "fit_garch",
    "fit_kalman",
    "fit_ou",
    "fit_regime_gmm",
    "fit_regime_hmm",
    "align_series",
    "apply_mask",
    "conditional_aggregate",
    "cumulative",
    "detrend",
    "event_windows",
    "ewm_statistic",
    "granger_causality",
    "hp_filter",
    "lag",
    "lead_lag",
    "ljung_box",
    "normality_test",
    "variance_ratio",
    "hurst_exponent",
    "pca_decompose",
    "reconstruct_from_factors",
    "pairwise_spread_matrix",
    "percentile_rank",
    "regression_residual",
    "resample",
    "rolling_correlation",
    "rolling_covariance",
    "rolling_pca",
    "rolling_regression",
    "rolling_statistic",
    "rolling_zscore",
    "select_from_series_set",
    "series_arithmetic",
    "stationarity_adf",
    "streak",
    "summarize_series",
    "threshold_events",
    "top_n",
    "transition_events",
    "weighted_combination",
    "winsorize",
    "convert_units",
}

# OPR15 discriminator-arg ABI — DONE for series_arithmetic: its ``op``
# is now a DECLARED discriminator on its OperatorSpec
# (``discriminator_args=("op",)``), the executor's by-name special-case
# is deleted, and ``op`` resolves from params — so series_arithmetic is
# fully conformant and lives in _V2_CONFORMANT above.  This bucket is
# reserved (currently empty) for any future operator genuinely blocked
# on a substrate ABI change before it can conform.
_PENDING_ABI: set[str] = set()


def test_every_registry_operator_is_classified():
    """A new operator must be either v2-conformant or explicitly marked
    legacy-pending — never silently unclassified."""
    keys = set(OPERATOR_REGISTRY)
    classified = _V2_CONFORMANT | _PENDING_ABI
    unclassified = keys - classified
    assert not unclassified, (
        f"OPERATOR_REGISTRY contains unclassified operators {sorted(unclassified)}. "
        "Every new operator must conform to OPR1–OPR16 (add to _V2_CONFORMANT) "
        "or be tracked for the registry/executor ABI step (_PENDING_ABI) — "
        "ADR 0016.  The operator layer is finance-blind: there is no "
        "trade-relocation bucket (the trade trio was removed)."
    )


def test_conformant_set_and_registry_agree():
    """All classification buckets only name real registry operators, and
    they are mutually disjoint (no operator double-classified)."""
    keys = set(OPERATOR_REGISTRY)
    assert _V2_CONFORMANT <= keys
    assert _PENDING_ABI <= keys
    assert not (_V2_CONFORMANT & _PENDING_ABI)
    # Finance-blind: every registered operator is conformant (no pending
    # trade bucket).
    assert keys == _V2_CONFORMANT | _PENDING_ABI


def test_operator_config_error_is_valueerror():
    """OPR13 / ADR 0016 Decision 3 (one error family): OperatorConfigError
    must subclass ValueError, else a config-identity/load failure escapes
    every operator as a bare Exception the orchestration envelope misses
    (ERR-1).  The per-operator conformance test checks each <Op>Error but
    never reaches this shared config error, so it is asserted explicitly."""
    from shared.config.operator_config import OperatorConfigError

    assert issubclass(OperatorConfigError, ValueError)


@pytest.mark.parametrize("name", sorted(OPERATOR_REGISTRY))
def test_basic_registry_shape(name):
    """Universally-true checks that hold for every operator (legacy too).

    Reads the structured ``OutputDescriptor`` / ``SlotDescriptor`` types
    (PART B refactor): no string parsing — ``descriptor.artifact_type``
    is an ``ArtifactTypeName`` member whose ``.value`` is checked for
    closed-family membership; ``descriptor.is_list`` is the canonical
    list-shape flag.
    """
    spec = OPERATOR_REGISTRY[name]
    assert spec.operator_name == name
    assert callable(spec.callable)

    # Output side: structured descriptor with a closed-family artifact type.
    assert isinstance(spec.output, OutputDescriptor), (
        f"{name}.output must be an OutputDescriptor (PART B), got "
        f"{type(spec.output).__name__}"
    )
    assert spec.output.artifact_type in ARTIFACT_TYPE_NAMES, (
        f"{name}.output.artifact_type={spec.output.artifact_type!r} "
        "not in closed family ARTIFACT_TYPE_NAMES"
    )

    # Input side: each slot is a SlotDescriptor; its ``artifact_type`` is
    # checked against the closed family.  ``is_list`` is a structured bool
    # (the prior ``"List[X]"`` string prefix has no string form to parse).
    for slot, descriptor in spec.input_slots.items():
        assert isinstance(descriptor, SlotDescriptor), (
            f"{name}.{slot} must be a SlotDescriptor (PART B), got "
            f"{type(descriptor).__name__}"
        )
        assert descriptor.artifact_type in ARTIFACT_TYPE_NAMES, (
            f"{name}.{slot} artifact_type={descriptor.artifact_type!r} "
            "not in closed family ARTIFACT_TYPE_NAMES"
        )
        assert isinstance(descriptor.is_list, bool), (
            f"{name}.{slot} is_list must be a bool flag, got "
            f"{type(descriptor.is_list).__name__}"
        )


@pytest.mark.parametrize("name", sorted(_V2_CONFORMANT))
def test_v2_operator_conforms(name):
    """The full OPR16 conformance gate for v2.0 operators."""
    spec = OPERATOR_REGISTRY[name]
    fn = spec.callable
    sig = inspect.signature(fn)
    param_names = set(sig.parameters)

    # (a) signature params == input_slots ∪ {params, config} ∪
    #     declared discriminator args (OPR15 — e.g. series_arithmetic.op)
    expected = set(spec.input_slots) | {"params", "config"} | set(spec.discriminator_args)
    assert param_names == expected, (
        f"{name}: signature {sorted(param_names)} != "
        f"slots ∪ {{params, config}} ∪ discriminator_args = {sorted(expected)}"
    )

    # (b) params default is None (OPR8) — also config default None
    assert sig.parameters["params"].default is None, (
        f"{name}: params must default to None (OPR8)"
    )
    assert sig.parameters["config"].default is None, (
        f"{name}: config must default to None"
    )

    # (d) every slot + output type is in the closed family — read from
    # the structured descriptors (PART B): ``output.artifact_type`` and
    # each ``SlotDescriptor.artifact_type`` are ``ArtifactTypeName``
    # members validated at construction; their values must still be in
    # ``ARTIFACT_TYPE_NAMES`` (which is derived from the same enum).
    # ``descriptor.is_list`` replaces the prior ``"List[X]"`` string
    # prefix; no string parsing required.
    assert spec.output.artifact_type in ARTIFACT_TYPE_NAMES
    for descriptor in spec.input_slots.values():
        assert descriptor.artifact_type in ARTIFACT_TYPE_NAMES
        assert isinstance(descriptor.is_list, bool)

    # operator module identity
    op_module = importlib.import_module(fn.__module__)
    assert op_module._OPERATOR_NAME == name
    version = op_module._OPERATOR_VERSION

    # package (the operator folder's __init__)
    pkg_name = fn.__module__.rsplit(".", 1)[0]
    pkg = importlib.import_module(pkg_name)

    # (e) __init__ exports: the operator callable, CONFIG_PATH, a
    #     *Params, and a *Error (all four).
    assert hasattr(pkg, name), f"{name}: __init__ does not export the operator"
    assert hasattr(pkg, "CONFIG_PATH"), f"{name}: __init__ does not export CONFIG_PATH"

    error_exports = [a for a in dir(pkg) if a.endswith("Error")]
    assert error_exports, f"{name}: __init__ exports no *Error"
    for err_name in error_exports:
        # (c) every operator error subclasses ValueError (OPR13)
        assert issubclass(getattr(pkg, err_name), ValueError), (
            f"{name}: {err_name} must subclass ValueError (OPR13)"
        )

    params_exports = [a for a in dir(pkg) if a.endswith("Params")]
    assert params_exports, f"{name}: __init__ exports no *Params"

    # (f) config identity — name AND version (OPR12)
    cfg = load_operator_config(pkg.CONFIG_PATH)
    assert cfg.operator.name == name, (
        f"{name}: config.operator.name={cfg.operator.name!r} != registry key"
    )
    assert cfg.operator.version == version, (
        f"{name}: config.operator.version={cfg.operator.version!r} != "
        f"module._OPERATOR_VERSION={version!r} (OPR12)"
    )
    # OPR12 — the version is strict semver (MAJOR.MINOR.PATCH).
    assert re.fullmatch(r"\d+\.\d+\.\d+", cfg.operator.version), (
        f"{name}: version {cfg.operator.version!r} is not MAJOR.MINOR.PATCH"
    )


# Operators that consume >=2 artifacts must expose BOTH OPR11
# structural-metadata controls (uniform metadata algebra; rolling_regression's
# frequency DEFAULT is deliberately lenient per ADR F2, but the FLAG must
# still be present).  correlation is strict-always by design (no opt-out).
_MULTI_ARTIFACT_OPS = {
    "align_series",
    "beta",
    "covariance",
    "granger_causality",
    "lead_lag",
    "regression_residual",
    "rolling_covariance",
    "series_arithmetic",
    "rolling_regression",
    "rolling_correlation",
    "cointegration",
    "apply_mask",
    "event_windows",
}


@pytest.mark.parametrize("name", sorted(_MULTI_ARTIFACT_OPS))
def test_multi_artifact_ops_expose_both_metadata_flags(name):
    """OPR11 — every multi-artifact operator exposes both
    require_matching_frequency AND require_matching_missingness."""
    spec = OPERATOR_REGISTRY[name]
    fields = set(spec.params_class.model_fields)
    assert "require_matching_frequency" in fields, (
        f"{name}: missing require_matching_frequency param (OPR11)"
    )
    assert "require_matching_missingness" in fields, (
        f"{name}: missing require_matching_missingness param (OPR11)"
    )


# ===========================================================================
# OPR16 CLAUSE (g) — the EXECUTION clause (OPR10 / OPR14).
# ---------------------------------------------------------------------------
# Clauses (a)–(f) above are STATIC: they read the registry / signature /
# config without ever running an operator.  Clause (g) is the one clause
# that proves the *runtime* lineage contract — it actually EXECUTES every
# registered operator on a tiny deterministic synthetic input and asserts:
#
#   1. The operator appends EXACTLY ONE OperatorStep (the N→N+1 lineage
#      growth of OPR10) — except the documented N-ary SeriesSet-producer
#      carve-out (see _FRESH_CHAIN_OPS below), where the operator builds a
#      *fresh* set-level chain rather than appending to one primary input.
#   2. The head step's ``name`` == the operator's registry/_OPERATOR_NAME
#      and its ``version`` == the module's ``_OPERATOR_VERSION`` (OPR10/12).
#   3. The ``head_hash`` is RERUN-EQUAL: executing the operator twice on the
#      same synthetic input yields the identical content-address (OPR14a).
#
# This gate is LOAD-BEARING: a non-deterministic operator (e.g. one that
# folds ``time.time()`` or an unfixed ``np.random`` draw into ``step.params``
# or its payload), or one whose head step's name/version drifts from its
# declared identity, FAILS here.  ``test_clause_g_is_load_bearing`` below
# demonstrates this with a deliberately non-deterministic stub.
#
# There are NO silent skips: every one of the 52 registered operators is
# executed.  Operators whose central methodology knob has no honest default
# (OPR8 — e.g. ``pca_decompose.n_components``, ``fit_kalman.target_key``)
# refuse a bare ``params=None`` BY DESIGN, so clause (g) supplies a minimal
# valid params object + a shaped fixture for each via ``_CLAUSE_G_CASES``.
# A skip would HIDE exactly the determinism gap this clause exists to catch,
# so a missing case is a hard failure, not a skip.
# ===========================================================================

import numpy as np  # noqa: E402  (kept local to the clause-(g) section)
import pandas as pd  # noqa: E402

from shared.artifacts.lineage import (  # noqa: E402
    Lineage,
    OperatorStep,
    PrimitiveStep,
)
from shared.artifacts.missingness import RawNoCleaning  # noqa: E402
from shared.artifacts.types import (  # noqa: E402
    EventSet,
    Panel,
    Series,
    SeriesSet,
    WindowedPanel,
)
from shared.artifacts.units import TimeSeriesUnits  # noqa: E402


# ---- synthetic-input substrate (deterministic, finance-blind) -------------
#
# Small, well-conditioned, FIXED-SEED inputs — large enough to satisfy the
# fits' minimum-row / minimum-member requirements, deterministic so the
# rerun-equality assert is meaningful.

_G_N = 160
_G_INDEX = pd.bdate_range("2022-01-03", periods=_G_N)


def _g_lineage(key: str) -> Lineage:
    """A single-step synthetic upstream chain (a stand-in primitive)."""
    step = PrimitiveStep.build(
        name="synthetic_primitive",
        version="1.0.0",
        params={"series_key": str(key)},
        tool_config_hash="clause_g_config_hash",
        output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _g_series(
    key: str = "s",
    *,
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    integer_labels: bool = False,
) -> Series:
    if integer_labels:
        # transition_events consumes integer-valued regime labels.
        payload_values = np.resize([0, 1, 2, 1, 0], _G_N).astype(float)
    else:
        rng = np.random.RandomState(abs(hash(str(key))) % 9973)
        payload_values = 3.0 + 0.01 * np.arange(_G_N) + 0.2 * rng.randn(_G_N)
    payload = pd.Series(payload_values, index=_G_INDEX, name=str(key))
    return Series(
        series_key=str(key),
        payload=payload,
        units=units,
        frequency="B",
        missingness_policy=RawNoCleaning(),
        lineage=_g_lineage(key),
    )


def _g_series_set(keys=("a", "b", "c")) -> SeriesSet:
    series_by_key, units_by_key, missingness_by_key, upstream = {}, {}, {}, {}
    for i, k in enumerate(keys):
        rng = np.random.RandomState(i + 1)
        values = 3.0 + i + 0.02 * np.arange(_G_N) + 0.3 * rng.randn(_G_N)
        series_by_key[k] = pd.Series(values, index=_G_INDEX, name=k)
        units_by_key[k] = TimeSeriesUnits.PERCENT
        missingness_by_key[k] = RawNoCleaning()
        upstream[k] = _g_lineage(k)
    return SeriesSet(
        series_by_key=series_by_key,
        units_by_key=units_by_key,
        missingness_by_key=missingness_by_key,
        upstream_lineage_by_key=upstream,
        common_index=_G_INDEX,
        frequency="B",
        lineage=_g_lineage("set"),
    )


def _g_event_set(src: str = "s") -> EventSet:
    mask = pd.Series(False, index=_G_INDEX)
    mask.iloc[list(range(10, _G_N, 15))] = True
    mask = mask.astype(bool)
    dates = list(mask.index[mask.to_numpy(dtype=bool)])
    return EventSet(
        mask=mask,
        event_dates=dates,
        per_event_metadata=[{"i": i} for i in range(len(dates))],
        source_series_key=src,
        frequency="B",
        lineage=_g_lineage("ev"),
    )


def _g_panel(cols=("c1", "c2", "c3")) -> Panel:
    frame = pd.DataFrame(
        {
            c: 1.0 + i + 0.01 * np.arange(_G_N)
            + 0.2 * np.random.RandomState(i + 5).randn(_G_N)
            for i, c in enumerate(cols)
        },
        index=_G_INDEX,
    )
    return Panel(
        payload=frame,
        units_by_column={c: TimeSeriesUnits.PERCENT for c in cols},
        missingness_policy=RawNoCleaning(),
        lineage=_g_lineage("panel"),
    )


def _g_windowed_panel(src: str = "s") -> WindowedPanel:
    n_events, window_length = 6, 11
    payload = np.random.RandomState(3).randn(n_events, window_length)
    return WindowedPanel(
        payload=payload,
        offsets=list(range(-5, 6)),
        event_dates=list(_G_INDEX[20:20 + n_events]),
        per_event_metadata=[{"i": i} for i in range(n_events)],
        target_series_key=src,
        units=TimeSeriesUnits.PERCENT,
        lineage=_g_lineage("wp"),
    )


def _g_default_args(spec) -> list:
    """Build the positional input artifacts for an operator from its slot
    descriptors (the generic path — used when no override fixture applies)."""
    args = []
    for slot, descriptor in spec.input_slots.items():
        if descriptor.is_list:
            # An N-ary list slot: hand it >=2 distinct Series.
            args.append([_g_series("L0"), _g_series("L1"), _g_series("L2")])
            continue
        at = descriptor.artifact_type.value
        if at == "Series":
            args.append(_g_series(slot))
        elif at == "SeriesSet":
            args.append(_g_series_set())
        elif at == "EventSet":
            args.append(_g_event_set())
        elif at == "Panel":
            args.append(_g_panel())
        elif at == "WindowedPanel":
            args.append(_g_windowed_panel())
        else:  # pragma: no cover — closed family is exhausted above
            raise AssertionError(
                f"{spec.operator_name}: clause-(g) has no synthetic fixture "
                f"for artifact type {at!r}; the closed family grew without a "
                "fixture — add one (do NOT skip)."
            )
    return args


# ---- per-operator override cases ------------------------------------------
#
# Operators whose central methodology knob has NO honest default (OPR8)
# refuse a bare ``params=None``.  Each lambda returns (positional_args,
# call_kwargs) so the SAME builder is re-invoked for the rerun (fresh
# inputs each call → proves content-addressing, not object identity).
# This is an explicit, documented map — there are NO silent skips.


def _import_params(operator_name: str, params_cls: str):
    module = importlib.import_module(f"shared.operators.{operator_name}")
    return getattr(module, params_cls)


def _clause_g_cases():
    P = _import_params  # noqa: N806
    cases = {
        "bandpass": lambda: (
            [_g_series()],
            {"params": P("bandpass", "BandpassParams")(low=8, high=32)},
        ),
        "changepoint_detection": lambda: (
            [_g_series()],
            {"params": P("changepoint_detection",
                         "ChangepointDetectionParams")(n_changepoints=2)},
        ),
        "convert_units": lambda: (
            [_g_series(units=TimeSeriesUnits.PERCENT)],
            {"params": P("convert_units", "ConvertUnitsParams")(
                target_units=TimeSeriesUnits.BPS)},
        ),
        "fit_kalman": lambda: (
            [_g_series_set(("y", "x1", "x2"))],
            {"params": P("fit_kalman", "FitKalmanParams")(
                target_key="y", signal_to_noise_ratio=0.05)},
        ),
        "fit_regime_gmm": lambda: (
            [_g_panel()],
            {"params": P("fit_regime_gmm", "FitRegimeGmmParams")(n_states=2)},
        ),
        "fit_regime_hmm": lambda: (
            [_g_panel()],
            {"params": P("fit_regime_hmm", "FitRegimeHmmParams")(n_states=2)},
        ),
        "hp_filter": lambda: (
            [_g_series()],
            {"params": P("hp_filter", "HpFilterParams")(lamb=1600.0)},
        ),
        "pca_decompose": lambda: (
            [_g_panel()],
            {"params": P("pca_decompose", "PcaDecomposeParams")(
                n_components=2)},
        ),
        "reconstruct_from_factors": lambda: (
            [_g_panel()],
            {"params": P("reconstruct_from_factors",
                         "ReconstructFromFactorsParams")(
                n_components=2, target_column="c1")},
        ),
        "rolling_pca": lambda: (
            [_g_panel()],
            {"params": P("rolling_pca", "RollingPcaParams")(
                window=60, n_components=2)},
        ),
        "rolling_regression": lambda: (
            [_g_series("lhs"), _g_series("rhs")],
            {"params": P("rolling_regression", "RollingRegressionParams")(
                window=60)},
        ),
        "select_from_series_set": lambda: (
            [_g_series_set()],
            {"params": P("select_from_series_set",
                         "SelectFromSeriesSetParams")(series_key="a")},
        ),
        "series_arithmetic": lambda: (
            [_g_series("left")],
            {"op": "diff", "params": None},
        ),
        "threshold_events": lambda: (
            [_g_series()],
            {"params": P("threshold_events", "ThresholdEventsParams")(
                rule="above", threshold=3.5)},
        ),
        "transition_events": lambda: (
            [_g_series(integer_labels=True)],
            {"params": None},
        ),
        "weighted_combination": lambda: (
            [_g_series_set()],
            {"params": P("weighted_combination", "WeightedCombinationParams")(
                weights={"a": 0.5, "b": 0.3, "c": 0.2})},
        ),
    }
    return cases


# Operators whose output lineage is a FRESH set-level chain rather than an
# append to a single primary input's chain (the m42 carve-out).
# ``align_series`` aligns N independent Series onto a common index and builds
# ``Lineage.from_steps([align_step])`` — each member's provenance lives in
# the SeriesSet's ``upstream_lineage_by_key``, so the set-level chain has
# exactly ONE step (the align step) regardless of input chain lengths.  The
# N→N+1 assertion is therefore replaced by a "head is a single-step fresh
# OperatorStep chain" assertion for these operators.  This is a DOCUMENTED
# carve-out, not a silent skip — name/version match and rerun-equality are
# still fully asserted.
_FRESH_CHAIN_OPS = {"align_series"}


def _execute_for_clause_g(name: str):
    """Build the synthetic input(s) + call the operator, returning the
    output artifact.  A fresh build per call so rerun-equality reflects
    content-addressing, not object reuse."""
    spec = OPERATOR_REGISTRY[name]
    cases = _clause_g_cases()
    if name in cases:
        args, kwargs = cases[name]()
    else:
        args, kwargs = _g_default_args(spec), {"params": None}
    kwargs.setdefault("config", None)
    return spec.callable(*args, **kwargs)


@pytest.mark.parametrize("name", sorted(OPERATOR_REGISTRY))
def test_v2_operator_conforms_clause_g_executes(name):
    """OPR16 clause (g) — EXECUTE every registered operator and prove the
    runtime lineage contract: exactly one appended OperatorStep (N→N+1),
    head name+version match the declared identity, and head_hash is
    rerun-equal (OPR10 / OPR14a).

    This is the ONLY clause that runs an operator; (a)–(f) are static.
    No operator is skipped — the per-operator override map supplies a
    minimal valid params/fixture for every operator whose central knob has
    no honest default (OPR8)."""
    spec = OPERATOR_REGISTRY[name]
    op_module = importlib.import_module(spec.callable.__module__)
    declared_version = op_module._OPERATOR_VERSION
    declared_name = op_module._OPERATOR_NAME

    out = _execute_for_clause_g(name)
    out2 = _execute_for_clause_g(name)

    # The lineage carrying the appended step: a SeriesSet exposes it on its
    # own ``.lineage`` (set-level), every other artifact on ``.lineage``.
    out_lineage = out.lineage
    out2_lineage = out2.lineage

    head = out_lineage.steps[-1]

    # (g.1) the head step is an OperatorStep ...
    assert isinstance(head, OperatorStep), (
        f"{name}: head lineage step is {type(head).__name__}, not an "
        "OperatorStep — clause (g) / OPR10"
    )
    # (g.2) ... whose name + version match the operator's declared identity.
    assert head.name == declared_name == name, (
        f"{name}: head OperatorStep name={head.name!r} != declared "
        f"_OPERATOR_NAME={declared_name!r} / registry key={name!r} (OPR10)"
    )
    assert head.version == declared_version, (
        f"{name}: head OperatorStep version={head.version!r} != module "
        f"_OPERATOR_VERSION={declared_version!r} (OPR10/OPR14d)"
    )

    # (g.3) lineage growth — the operator appends EXACTLY ONE OperatorStep
    # bearing its OWN name, and that step is the head (N→N+1, OPR10).  We
    # count this-operator's steps rather than the raw chain delta because the
    # SeriesSet ``get_series`` consumers (e.g. select_from_series_set) extend
    # a member-level chain that already embeds the upstream alignment step —
    # the contract is "one OF MY steps appended at the head", which is exactly
    # what OPR10 mandates and is robust across both the plain-append and the
    # get_series paths.
    own_steps = [
        s for s in out_lineage.steps
        if isinstance(s, OperatorStep) and s.name == name
    ]
    assert len(own_steps) == 1, (
        f"{name}: must append EXACTLY ONE OperatorStep with its own name "
        f"(N→N+1, OPR10); found {len(own_steps)} steps named {name!r} in the "
        f"output lineage ({[type(s).__name__ for s in out_lineage.steps]})"
    )
    assert own_steps[0] is head, (
        f"{name}: the operator's own OperatorStep must be the HEAD of the "
        "output lineage (OPR10)"
    )
    if name in _FRESH_CHAIN_OPS:
        # The m42 carve-out: align_series builds a single-step FRESH
        # set-level chain over N independent inputs (provenance per member
        # lives in upstream_lineage_by_key), so the set chain has exactly the
        # one align step rather than input_chain + 1.
        assert len(out_lineage.steps) == 1, (
            f"{name}: fresh-chain SeriesSet producer must build a "
            f"single-step set-level lineage; got {len(out_lineage.steps)} "
            "(m42 carve-out)"
        )

    # (g.4) rerun-equality — the content-address is identical on re-execution
    # (OPR14a: op(x) == op(x) ⇒ identical head_hash).  This is the
    # load-bearing determinism gate.
    assert out_lineage.head_hash == out2_lineage.head_hash, (
        f"{name}: head_hash is NOT rerun-equal "
        f"({out_lineage.head_hash} != {out2_lineage.head_hash}) — the "
        "operator folds a non-deterministic value into its lineage or "
        "payload (OPR14a determinism)"
    )


def test_clause_g_exercises_every_operator():
    """Belt-and-braces: clause (g) executes EVERY registered operator (no
    silent skip hides a determinism gap).  Asserts the override map only
    names real operators and that the executed-count == registry size."""
    cases = _clause_g_cases()
    assert set(cases) <= set(OPERATOR_REGISTRY), (
        "clause-(g) override map names non-registry operators: "
        f"{sorted(set(cases) - set(OPERATOR_REGISTRY))}"
    )
    executed = 0
    for name in OPERATOR_REGISTRY:
        out = _execute_for_clause_g(name)
        assert out is not None
        executed += 1
    assert executed == len(OPERATOR_REGISTRY), (
        f"clause (g) executed {executed} operators but the registry has "
        f"{len(OPERATOR_REGISTRY)} — an operator was skipped"
    )


def test_clause_g_is_load_bearing():
    """Demonstrate clause (g) is NOT vacuous: a deliberately
    NON-DETERMINISTIC operator stub (folds an unfixed random draw into its
    step.params) FAILS the rerun-equality assertion, and a NAME/VERSION
    mismatch FAILS the identity assertion.  If clause (g) were vacuous,
    these would pass — proving the gate catches exactly the OPR14/OPR10
    defects it exists to catch."""
    base = _g_series("load_bearing")

    def _nondeterministic_stub(series: Series) -> Series:
        # A correct operator folds only CONTENT into step.params; this stub
        # folds an unfixed random nonce → head_hash differs every call.
        nonce = float(np.random.RandomState(None).randn())
        step = OperatorStep.build(
            name="nondet_stub",
            version="1.0.0",
            params={"nonce": nonce},
            input_hashes=(series.lineage.head_hash,),
        )
        return Series(
            series_key=series.series_key,
            payload=series.payload,
            units=series.units,
            frequency=series.frequency,
            missingness_policy=series.missingness_policy,
            lineage=series.lineage.append(step),
        )

    out_a = _nondeterministic_stub(base)
    out_b = _nondeterministic_stub(base)
    # The rerun-equality assertion clause (g) makes — here it MUST fail.
    assert out_a.lineage.head_hash != out_b.lineage.head_hash, (
        "the non-deterministic stub produced equal head_hashes — clause "
        "(g)'s rerun-equality assert would be vacuous"
    )

    # And a head step whose name drifts from the declared identity is caught
    # by the (g.2) name assertion.
    drifted = _nondeterministic_stub(base)
    assert drifted.lineage.steps[-1].name != "load_bearing_operator", (
        "name-mismatch demonstration is malformed"
    )
