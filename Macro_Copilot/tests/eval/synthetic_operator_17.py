"""tests/eval/synthetic_operator_17.py — PR-10 scaling proof fixture.

Synthesises a 17th operator that simulates being registered in the
substrate's ``OPERATOR_REGISTRY`` WITHOUT permanently modifying it.
Used by the **registration-only growth** scaling proof in
``tests/eval/test_scaling_proofs.py``.

The fixture exposes:
  - ``SyntheticOperator17Params``: Pydantic *Params class.
  - ``SYNTHETIC_OPERATOR_17_SPEC``: the typed OperatorSpec.
  - ``inject_synthetic_operator_17`` /
    ``remove_synthetic_operator_17``: context-manager-friendly
    helpers that mutate / restore ``OPERATOR_REGISTRY`` in place
    (test isolation hook).

The synthetic operator's signature is the simplest possible:
1 Series input -> 1 Series output.  That's enough for the
registration-proof — the architecture point is that adding an
operator to the registry is the ONLY change needed, NOT a
re-render of every Composer prompt block.

Finance-blindness
=================

The synthetic operator carries finance-blind semantics: it's a
generic "scaling transform" (multiply by params.scale).  No
finance vocabulary appears.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, Field

from shared.workflow.registry import OPERATOR_REGISTRY, OperatorSpec
from shared.workflow.slots import OutputDescriptor, SlotDescriptor


SYNTHETIC_OPERATOR_17_NAME: str = "synthetic_operator_17"


class SyntheticOperator17Params(BaseModel):
    """Minimal *Params for the 17th operator — a single scale
    knob."""

    scale: float = Field(default=1.0)


def _synthetic_op_callable(*args, **kwargs):
    """No-op callable; the scaling proof never invokes it."""
    return None


SYNTHETIC_OPERATOR_17_SPEC: OperatorSpec = OperatorSpec(
    operator_name=SYNTHETIC_OPERATOR_17_NAME,
    callable=_synthetic_op_callable,
    params_class=SyntheticOperator17Params,
    config_path=Path(__file__).parent / "synthetic_operator_17_config.yaml",
    input_slots={
        "series": SlotDescriptor.of(
            "Series",
            "Source Series to scale by ``params.scale``.",
        ),
    },
    output=OutputDescriptor.of(
        "Series",
        "Synthetic scaled Series.",
    ),
)


def inject_synthetic_operator_17() -> None:
    """Insert the synthetic operator into the live OPERATOR_REGISTRY.

    Idempotent — repeated calls are a no-op.  The scaling proof
    uses ``with_synthetic_operator_17()`` for context-managed
    isolation; this function exists for test fixtures that need
    explicit setup/teardown.
    """
    OPERATOR_REGISTRY[SYNTHETIC_OPERATOR_17_NAME] = SYNTHETIC_OPERATOR_17_SPEC


def remove_synthetic_operator_17() -> None:
    """Restore the OPERATOR_REGISTRY by removing the synthetic
    operator.  Idempotent — safe to call when not present."""
    OPERATOR_REGISTRY.pop(SYNTHETIC_OPERATOR_17_NAME, None)


@contextlib.contextmanager
def with_synthetic_operator_17() -> Iterator[None]:
    """Context manager: register the synthetic operator, yield, then
    restore.  Use in tests to avoid cross-test pollution::

        with with_synthetic_operator_17():
            ...  # registry has 17 operators
        # registry restored to 16
    """
    was_present = SYNTHETIC_OPERATOR_17_NAME in OPERATOR_REGISTRY
    inject_synthetic_operator_17()
    try:
        yield
    finally:
        if not was_present:
            remove_synthetic_operator_17()


__all__ = [
    "SYNTHETIC_OPERATOR_17_NAME",
    "SYNTHETIC_OPERATOR_17_SPEC",
    "SyntheticOperator17Params",
    "inject_synthetic_operator_17",
    "remove_synthetic_operator_17",
    "with_synthetic_operator_17",
]
