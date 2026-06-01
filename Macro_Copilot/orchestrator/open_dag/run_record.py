"""orchestrator.open_dag.run_record — PR-9A of the open-DAG PoC.

The **run-level lineage record** that joins:

  - the substrate's content-addressed compute chain (``Lineage`` from
    ``shared/artifacts/lineage.py``), and
  - the open-DAG intent chain (``IntentChain`` from
    ``orchestrator/open_dag/intent_chain.py``).

Together these two records form the persisted artifact for one
open-DAG pipeline run.  The compute chain answers "what was
computed"; the intent chain answers "what was intended"; the join is
what the plan §PR-9 calls "the full intent chain alongside the
compute chain."

Why this lives in ``orchestrator/open_dag/`` instead of ``shared/``
==================================================================

The PR-9 plan text literally lists
``shared/artifacts/lineage.py`` as the file to extend.  But
``IntentChain`` references ``RouteDecision`` (from
``orchestrator/contracts.py``), ``GateVerdict`` (from
``orchestrator/open_dag/coverage_gate.py``), and other agent-layer
types.  ``shared/artifacts/lineage.py`` is the finance-blind
substrate — importing those would invert the layer.

PR-9A's resolution: keep ``IntentChain`` at
``orchestrator/open_dag/intent_chain.py`` (where the agent-layer
imports are legal), put the JOIN here at
``orchestrator/open_dag/run_record.py`` (also agent-layer; legal to
import both ``Lineage`` and ``IntentChain``).  The PR-10 orchestrator
persists ``RunLineage`` as the per-run record.

This honours the plan's intent (record IntentChain alongside compute
chain) while respecting finance-blindness.  The acceptance criterion
is met by the join existing as a typed record + tests that assert it
carries both.

Two states
==========

A ``RunLineage`` has two shapes depending on whether execution
happened:

  - **Executed run** (gate verdict was PASS): ``compute_lineage`` is
    populated; ``intent_chain.gate.status == "PASS"``.
  - **Refused / clarified run** (gate verdict was REFUSE or
    CLARIFY): ``compute_lineage`` is None (no execution ran);
    ``intent_chain.gate.status`` is REFUSE or CLARIFY.

Model validator enforces consistency: when gate.status != PASS,
compute_lineage must be None; when gate.status == PASS,
compute_lineage MAY be None (the gate passed but the orchestrator
chose not to execute — e.g. dry-run mode) but if supplied must be a
valid Lineage.

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/`` (agent layer).  Its
imports are:
  - ``orchestrator.open_dag.intent_chain.IntentChain`` (agent).
  - ``shared.artifacts.lineage.Lineage`` (substrate).

The first dependency is mandatory for the join's purpose; the
second is a substrate→agent dependency direction that's always
legal (the substrate doesn't import this module back).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.open_dag.intent_chain import IntentChain
from shared.artifacts.lineage import Lineage


# ============================================================================
# RUN LINEAGE — the typed join
# ============================================================================


class RunLineage(BaseModel):
    """The per-run lineage record joining intent chain + compute
    chain.

    Frozen.  Round-trips through Pydantic + JSON for storage in the
    PR-10 orchestrator's persistence layer.

    Two-mode contract (model_validator-enforced):

      - ``intent_chain.gate.status == "PASS"`` AND
        ``compute_lineage`` populated:
            an executed run.  ``head_hash`` returns
            ``compute_lineage.head_hash``.
      - ``intent_chain.gate.status == "PASS"`` AND
        ``compute_lineage is None``:
            allowed (the orchestrator may have skipped execution for
            dry-run / planning purposes).  ``head_hash`` returns None.
      - ``intent_chain.gate.status != "PASS"``:
            ``compute_lineage`` MUST be None.  The gate refused /
            clarified; the executor never ran.

    Acceptance helpers (used by PR-10's persistence + lineage
    introspection):
      - ``is_executed``: True iff compute_lineage is populated.
      - ``is_answerable``: mirrors IntentChain.is_answerable.
      - ``head_hash``: substrate Lineage's content-addressed identifier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_chain: IntentChain = Field(
        ...,
        description=(
            "The four-record intent chain (router / selectors / "
            "composer / gate) — the 'what was intended' half of the "
            "run.  Always populated; even REFUSED runs have an "
            "IntentChain so the user-facing layer can surface the "
            "refusal message."
        ),
    )
    compute_lineage: Optional[Lineage] = Field(
        default=None,
        description=(
            "The substrate Lineage chain — the 'what was computed' "
            "half of the run.  None when the gate refused / clarified "
            "(no execution ran) OR when the orchestrator skipped "
            "execution (dry-run / planning mode).  When populated, "
            "the steps[-1].hash matches head_hash."
        ),
    )
    # PR-10D Codex F5: closed-family determinism bucket for the
    # answer/run record.  Surfaces the "what kind of outcome is this"
    # signal that L6 lineage and downstream observability consume.
    # The original contract called for this so a wrong answer is
    # diagnosable: a PR can distinguish "executed clean" from "gate
    # blocked" from "dry-run" at one structured field, instead of
    # re-deriving it from compute_lineage's presence.
    #
    # ``Optional`` + AUTO-DERIVED when None: callers don't need to
    # pass it explicitly.  The mode='before' validator below derives
    # it from (gate_status, compute_lineage) before Pydantic
    # construction so existing call sites stay backwards-compatible.
    # Explicit values pass through unchanged AND are double-checked
    # by the mode='after' validator for consistency.
    determinism_bucket: Optional[Literal[
        "EXECUTED_CONTENT_ADDRESSED",  # gate PASS + executor ran + hash present
        "GATE_REFUSED_NO_EXECUTION",   # gate REFUSE / CLARIFY → no execution
        "DRY_RUN_NO_COMPUTE",          # gate PASS but no executor wired
    ]] = Field(
        default=None,
        description=(
            "PR-10D Codex F5: which determinism category does this "
            "run record fall into?  Auto-derived from (gate_status, "
            "compute_lineage is not None) when not supplied, so "
            "callers don't need to pass it explicitly.  Explicit "
            "values are double-checked by the model validator."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_determinism_bucket(cls, values: Any) -> Any:
        """PR-10D Codex F5: auto-derive determinism_bucket from
        (gate_status, compute_lineage) when the caller didn't
        supply one."""
        if not isinstance(values, dict):
            return values
        if values.get("determinism_bucket") is not None:
            return values  # explicit value — pass through
        intent_chain = values.get("intent_chain")
        if intent_chain is None:
            return values  # let the ctor's missing-field error fire
        gate_status = getattr(
            getattr(intent_chain, "gate", None), "status", None,
        )
        compute_lineage = values.get("compute_lineage")
        if gate_status != "PASS":
            derived = "GATE_REFUSED_NO_EXECUTION"
        elif compute_lineage is None:
            derived = "DRY_RUN_NO_COMPUTE"
        else:
            derived = "EXECUTED_CONTENT_ADDRESSED"
        values["determinism_bucket"] = derived
        return values

    @model_validator(mode="after")
    def _validate_consistency(self) -> "RunLineage":
        gate_status = self.intent_chain.gate.status
        if gate_status != "PASS" and self.compute_lineage is not None:
            raise ValueError(
                f"RunLineage: gate status is {gate_status!r} "
                "(REFUSE / CLARIFY) but compute_lineage was supplied. "
                "When the gate refused / clarified, the executor "
                "never ran — compute_lineage MUST be None."
            )
        # PR-10D Codex F5: double-check the determinism bucket value
        # matches the derived expectation (catches a caller that
        # passed a wrong explicit value).
        if gate_status != "PASS":
            expected = "GATE_REFUSED_NO_EXECUTION"
        elif self.compute_lineage is None:
            expected = "DRY_RUN_NO_COMPUTE"
        else:
            expected = "EXECUTED_CONTENT_ADDRESSED"
        if self.determinism_bucket != expected:
            raise ValueError(
                f"RunLineage: determinism_bucket={self.determinism_bucket!r} "
                f"is inconsistent with (gate_status={gate_status!r}, "
                f"has_compute_lineage={self.compute_lineage is not None}).  "
                f"Expected {expected!r}."
            )
        return self

    @property
    def is_executed(self) -> bool:
        """True iff this run actually executed (compute_lineage
        populated).  False on REFUSE / CLARIFY gate verdicts OR on
        PASS-but-dry-run paths."""
        return self.compute_lineage is not None

    @property
    def is_answerable(self) -> bool:
        """True iff the intent chain is answerable (gate PASSED and
        no upstream layer refused) AND the run actually executed
        (compute_lineage populated).

        Mirrors IntentChain.is_answerable with the additional
        requirement that execution happened — the L6 answer renderer
        needs both signals.
        """
        return self.intent_chain.is_answerable and self.is_executed

    @property
    def head_hash(self) -> Optional[str]:
        """The substrate compute chain's content-addressed head hash,
        or None when the run did not execute.

        Used by the L6 provenance footer (PR-9's render_provenance_
        footer) and PR-10's lineage indexing / dedup."""
        if self.compute_lineage is None:
            return None
        return self.compute_lineage.head_hash

    @property
    def workflow_id(self) -> str:
        """Convenience: the workflow_id from the composer record.
        Empty string on composer refusal."""
        return self.intent_chain.composer.workflow_id

    @property
    def user_prompt(self) -> str:
        """Convenience: the original user prompt pinned at the head
        of the intent chain."""
        return self.intent_chain.user_prompt

    # ---- summary helpers for PR-10 persistence / debugging ----

    def summary(self) -> Dict[str, Any]:
        """Render a one-line-per-key summary the orchestrator can log
        or persist as a side-channel index entry.  Includes the gate
        status, the workflow_id (when bound), head_hash (when
        executed), and a compact selector / composer breakdown.

        Deterministic — same RunLineage produces byte-stable output.
        """
        return {
            "gate_status": self.intent_chain.gate.status,
            "is_executed": self.is_executed,
            "is_answerable": self.is_answerable,
            "workflow_id": self.workflow_id,
            "head_hash": self.head_hash,
            "intent_tag": (
                self.intent_chain.router.intent_tag.value
                if self.intent_chain.router.intent_tag is not None
                else None
            ),
            "decomposition_size": len(self.intent_chain.router.decomposition),
            "selector_count": len(self.intent_chain.selectors),
            "selector_refusals": sum(
                1 for s in self.intent_chain.selectors if s.is_refusal
            ),
            "composer_refused": self.intent_chain.composer.is_refusal,
            "operator_count": len(self.intent_chain.composer.operator_names),
            "soft_warning_count": len(self.intent_chain.gate.soft_warnings),
        }


# ============================================================================
# CONSTRUCTORS — PR-10 will call these from the orchestrator
# ============================================================================


def build_run_lineage(
    *,
    intent_chain: IntentChain,
    compute_lineage: Optional[Lineage] = None,
) -> RunLineage:
    """Build a ``RunLineage`` from its two halves.

    Equivalent to the Pydantic constructor; named here so PR-10's
    wiring layer has a stable factory function to import (mirrors
    the pattern of ``IntentChain.from_inputs``).
    """
    return RunLineage(
        intent_chain=intent_chain,
        compute_lineage=compute_lineage,
    )


__all__ = [
    "RunLineage",
    "build_run_lineage",
]
