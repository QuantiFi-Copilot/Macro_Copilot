"""OPR16 — the registry-consistency meta-test (the enforcement gate).

This is the cornerstone artifact of operator standardization v2.0
(ADR 0016).  It is parametrized over ``OPERATOR_REGISTRY`` and
mechanically proves that every v2-conformant operator satisfies the
contract clauses that can be checked without executing it:

  (a) signature params == input_slots ∪ {params, config}            (OPR9/OPR15)
  (b) ``params`` default is None (config-default resolution)         (OPR8)
  (c) ``<Operator>Error`` subclasses ValueError                     (OPR13)
  (d) output_type + every input-slot type ∈ the closed family       (OPR9)
  (e) ``__init__`` exports {CONFIG_PATH, <op>, <Params>, <Error>}    (OPR16)
  (f) config.operator.name == registry key
      AND config.operator.version == module._OPERATOR_VERSION        (OPR12)

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

import pytest

from shared.config.operator_config import load_operator_config
from shared.workflow.registry import ARTIFACT_TYPE_NAMES, OPERATOR_REGISTRY


# Operators built to (or migrated to) the v2.0 contract (ADR 0016).
# The full OPR16 conformance gate runs over this set; Step 3 grew it
# from {correlation} to include the 8 migrated finance-blind operators.
_V2_CONFORMANT = {
    "correlation",
    "align_series",
    "apply_mask",
    "conditional_aggregate",
    "event_windows",
    "rolling_regression",
    "select_from_series_set",
    "series_arithmetic",
    "summarize_series",
    "threshold_events",
}

# OPR15 discriminator-arg ABI — DONE for series_arithmetic: its ``op``
# is now a DECLARED discriminator on its OperatorSpec
# (``discriminator_args=("op",)``), the executor's by-name special-case
# is deleted, and ``op`` resolves from params — so series_arithmetic is
# fully conformant and lives in _V2_CONFORMANT above.  This bucket is
# reserved (currently empty) for any future operator genuinely blocked
# on a substrate ABI change before it can conform.
_PENDING_ABI: set[str] = set()

# The trade-lifecycle operators are finance-AWARE (P&L, financing,
# day-count, Sharpe) and are slated for RELOCATION to a backtest
# primitive set (ADR 0016 decision 1) — they are NOT migrated to
# operator conformance; relocation is a separate workstream/ADR.
_PENDING_TRADE_RELOCATION = {
    "construct_trades",
    "evaluate_trades",
    "summarize_trades",
}


def _strip_list(slot_type: str) -> str:
    if slot_type.startswith("List[") and slot_type.endswith("]"):
        return slot_type[len("List["):-1]
    return slot_type


def test_every_registry_operator_is_classified():
    """A new operator must be either v2-conformant or explicitly marked
    legacy-pending — never silently unclassified."""
    keys = set(OPERATOR_REGISTRY)
    classified = _V2_CONFORMANT | _PENDING_ABI | _PENDING_TRADE_RELOCATION
    unclassified = keys - classified
    assert not unclassified, (
        f"OPERATOR_REGISTRY contains unclassified operators {sorted(unclassified)}. "
        "Every new operator must conform to OPR1–OPR16 (add to _V2_CONFORMANT), "
        "be tracked for the registry/executor ABI step (_PENDING_ABI), or be "
        "tracked for relocation (_PENDING_TRADE_RELOCATION) — ADR 0016."
    )


def test_conformant_set_and_registry_agree():
    """All classification buckets only name real registry operators, and
    they are mutually disjoint (no operator double-classified)."""
    keys = set(OPERATOR_REGISTRY)
    assert _V2_CONFORMANT <= keys
    assert _PENDING_ABI <= keys
    assert _PENDING_TRADE_RELOCATION <= keys
    assert not (_V2_CONFORMANT & _PENDING_ABI)
    assert not (_V2_CONFORMANT & _PENDING_TRADE_RELOCATION)
    assert not (_PENDING_ABI & _PENDING_TRADE_RELOCATION)


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
    """Universally-true checks that hold for every operator (legacy too)."""
    spec = OPERATOR_REGISTRY[name]
    assert spec.operator_name == name
    assert callable(spec.callable)
    assert spec.output_type in ARTIFACT_TYPE_NAMES, (
        f"{name}.output_type={spec.output_type!r} not in closed family"
    )
    for slot, slot_type in spec.input_slots.items():
        base = _strip_list(slot_type)
        assert base in ARTIFACT_TYPE_NAMES, (
            f"{name}.{slot} type {slot_type!r} not in closed family"
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

    # (d) every slot + output type is in the closed family
    assert spec.output_type in ARTIFACT_TYPE_NAMES
    for slot_type in spec.input_slots.values():
        assert _strip_list(slot_type) in ARTIFACT_TYPE_NAMES

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
