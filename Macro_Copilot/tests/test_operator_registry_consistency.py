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
    "beta",
    "cointegration",
    "correlation",
    "covariance",
    "align_series",
    "apply_mask",
    "conditional_aggregate",
    "event_windows",
    "granger_causality",
    "lead_lag",
    "percentile_rank",
    "regression_residual",
    "rolling_correlation",
    "rolling_covariance",
    "rolling_regression",
    "rolling_statistic",
    "rolling_zscore",
    "select_from_series_set",
    "series_arithmetic",
    "summarize_series",
    "threshold_events",
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
