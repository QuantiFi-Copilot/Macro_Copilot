"""tests/eval/synthetic_primitive_59.py — PR-10 scaling proof fixture.

Synthesises a 59th primitive that pretends to belong to one domain
(sovereign_bonds by default) WITHOUT modifying the real
``rates_agent/`` sources.  Used by the **registration-only growth**
scaling proof in ``tests/eval/test_scaling_proofs.py``:

  > Add a synthetic 59th primitive to one domain ... Assert via
  > ``git diff`` that ``orchestrator/open_dag/composer.py``,
  > ``orchestrator/open_dag/coverage_gate.py``,
  > ``shared/workflow/validate.py``, ``shared/workflow/executor.py``,
  > ``orchestrator/prompts.py:SUPERVISOR_SYSTEM_PROMPT``, and every
  > OTHER domain's MCP server file are byte-for-byte unchanged.
  > Then prove a fresh query using the new tools composes correctly.

Per §PR-10 the proof is **registration-only growth** — the existence
of a new primitive in a domain's catalogue is the ONLY change needed
to make a fresh query compose.  This fixture provides the typed
``PrimitiveSpec`` + a resolver shim that delegates to the real
resolver for known names and returns the synthetic spec for the new
one.

Finance-blindness
=================

The synthetic primitive's *Input / *Output classes carry generic
finance vocabulary (``yield_synthetic``) but the architecture point
the proof makes is layer-independent: registration ⊕ composition
without touching the substrate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from shared.workflow.registry import PrimitiveResolver, PrimitiveSpec


# ============================================================================
# SYNTHETIC PRIMITIVE 59 — input + output + spec
# ============================================================================


class Synthetic59Input(BaseModel):
    """Minimal *Input schema for the 59th primitive.  Only fields
    necessary for the spec to be type-legal; never executed."""

    curve_family: str = Field(default="SYNTHETIC")
    tenor: str = Field(default="10Y")


class Synthetic59Output(BaseModel):
    """Minimal *Output schema; declares a ``time_series`` field so
    the composability audit classifies it as BRIDGEABLE_SERIES."""

    time_series: dict = Field(default_factory=dict)


def _synthetic_callable(**kw):
    """No-op callable — the scaling proof never actually invokes it.
    The test asserts SHAPE composition, not execution success."""
    return {}


SYNTHETIC_PRIMITIVE_59_NAME: str = "synthetic_primitive_59_tool"


SYNTHETIC_PRIMITIVE_59_SPEC: PrimitiveSpec = PrimitiveSpec(
    tool_name=SYNTHETIC_PRIMITIVE_59_NAME,
    callable=_synthetic_callable,
    input_class=Synthetic59Input,
    output_class=Synthetic59Output,
    config_path=Path(__file__).parent / "synthetic_primitive_59_config.yaml",
    output_field_units={"time_series": "bps"},
    output_artifact_type="Series",
)


def wrap_resolver_with_synthetic_59(
    base_resolver: PrimitiveResolver,
) -> PrimitiveResolver:
    """Return a resolver that returns SYNTHETIC_PRIMITIVE_59_SPEC for
    its tool_name, and delegates every other lookup to ``base_resolver``.

    Used by the scaling proof to simulate adding the 59th primitive
    without modifying ``rates_agent/`` source files.
    """

    def wrapped(tool_name: str) -> PrimitiveSpec:
        if tool_name == SYNTHETIC_PRIMITIVE_59_NAME:
            return SYNTHETIC_PRIMITIVE_59_SPEC
        return base_resolver(tool_name)

    return wrapped


__all__ = [
    "SYNTHETIC_PRIMITIVE_59_NAME",
    "SYNTHETIC_PRIMITIVE_59_SPEC",
    "Synthetic59Input",
    "Synthetic59Output",
    "wrap_resolver_with_synthetic_59",
]
