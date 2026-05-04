"""shared.artifacts.lineage — content-addressed provenance for artifacts.

Per build plan v5 / pre-Week-1 decision #2: lineage is content-addressed
from day one.  A ``LineageStep``'s hash is over
``(name, version, params_canonical_json, sorted_input_lineage_hashes)``
so two artifacts produced by the same operator with the same parameters
on the same upstream lineage have identical hashes.  Caching is deferred
but the hash slot is reserved and load-bearing for the workflow-level
methodology summary derived programmatically from lineage.

Step kinds in v1:

  - ``FetchStep``    — DB query via ``fetch_single_tenor`` /
    ``fetch_tenor_group``.  Records curve_family, tenor(s),
    field_name, start_date.
  - ``CleanStep``    — ``clean_single_series`` invocation; records
    ffill_limit and other cleaning params actually used.
  - ``AdapterStep``  — ``raw_dataframe_to_artifact_series`` (or
    sibling); records adapter version + units assigned.
  - ``PrimitiveStep`` — a per-tool-folder primitive in
    ``rates_agent.{sovereign_bonds,ois}.tools.*`` (e.g.
    ``calculate_ois_curve_spread_tool``).  Records the MCP tool
    name, the primitive's ``*Input.model_dump()``, the bundled
    config's content hash, the ``time_series*`` field that was
    extracted, and the snapshot ``as_of_date``.  The primitive→
    operator adapter (``shared.artifacts.adapters.from_time_series``)
    constructs this step when it lifts a primitive's
    canonical ``TimeSeries`` payload into a typed ``Series``
    artifact.  See the build-plan v5 / Phase 1B bridge milestone.
  - ``OperatorStep`` — any operator in ``shared.operators.*``;
    records operator name + version + canonical-JSON params + the
    list of input lineage hashes consumed.

Adding a new step kind requires adding a class here AND extending the
discriminator union below — same closed-family discipline as
``MissingnessPolicy``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field


# Type alias for the hexdigest hash strings; promotes readability.
LineageHash = str


def _canonical_json(obj: Any) -> str:
    """Stable JSON serialization for hashing.

    Sorted keys, no whitespace, default=str so dates/Pydantic models
    serialize deterministically.  Two equivalent ``params`` dicts
    produce the same string regardless of insertion order — that is
    what makes the hash content-addressed.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _compute_step_hash(
    *,
    kind: str,
    name: str,
    version: str,
    params: Dict[str, Any],
    input_hashes: Tuple[LineageHash, ...],
) -> LineageHash:
    """Hash a step's identity for content-addressing.

    The exact recipe is fixed for v1.  Changing it (different
    canonicalization, different field set) is a breaking change to
    every persisted lineage object — review carefully.
    """
    payload = {
        "kind": kind,
        "name": name,
        "version": version,
        "params": params,
        # Sort to make hash invariant to caller's input ordering when
        # that ordering is not load-bearing.  Operators that DO depend
        # on input order (none in v1) must encode the order in
        # ``params`` instead.
        "input_hashes": sorted(input_hashes),
    }
    raw = _canonical_json(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================================
# STEP KINDS (closed family)
# ============================================================================


class FetchStep(BaseModel):
    """A DB-fetch step.

    Records *what was queried*, not the row payload.  The payload lives
    on the ``Series`` artifact; the fetch parameters are what the
    methodology summary needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["fetch"] = "fetch"
    name: Literal["fetch_single_tenor", "fetch_tenor_group", "fetch_cross_market_pair"]
    version: str = "1.0.0"
    params: Dict[str, Any]  # curve_family, tenor(s), field_name, start_date, ...
    hash: LineageHash

    @classmethod
    def build(
        cls,
        *,
        name: str,
        version: str,
        params: Dict[str, Any],
    ) -> "FetchStep":
        h = _compute_step_hash(
            kind="fetch",
            name=name,
            version=version,
            params=params,
            input_hashes=(),
        )
        return cls(name=name, version=version, params=params, hash=h)  # type: ignore[arg-type]


class CleanStep(BaseModel):
    """A ``clean_single_series`` (or sibling) cleaning step.

    Persists ``input_hashes`` so the chain of upstream identities is
    recoverable from the step alone (Codex P1 follow-up: a step's
    derived ``hash`` uniquely identifies the inputs but does not let
    a methodology summary walk back into them without that list)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["clean"] = "clean"
    name: Literal["clean_single_series"]
    version: str = "1.0.0"
    params: Dict[str, Any]  # ffill_limit, dedup_keep, ...
    input_hashes: Tuple[LineageHash, ...] = ()
    hash: LineageHash

    @classmethod
    def build(
        cls,
        *,
        name: str,
        version: str,
        params: Dict[str, Any],
        input_hashes: Tuple[LineageHash, ...],
    ) -> "CleanStep":
        h = _compute_step_hash(
            kind="clean",
            name=name,
            version=version,
            params=params,
            input_hashes=input_hashes,
        )
        return cls(  # type: ignore[arg-type]
            name=name, version=version, params=params,
            input_hashes=input_hashes, hash=h,
        )


class AdapterStep(BaseModel):
    """An adapter step that converts a non-artifact input into a typed
    artifact (e.g., ``raw_dataframe_to_artifact_series``).

    Persists ``input_hashes`` so the upstream-identity chain is
    recoverable from the step alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["adapter"] = "adapter"
    name: str  # e.g., "raw_dataframe_to_artifact_series"
    version: str = "1.0.0"
    params: Dict[str, Any]  # series_key, units, source_kind, source_params, ...
    input_hashes: Tuple[LineageHash, ...] = ()
    hash: LineageHash

    @classmethod
    def build(
        cls,
        *,
        name: str,
        version: str,
        params: Dict[str, Any],
        input_hashes: Tuple[LineageHash, ...],
    ) -> "AdapterStep":
        h = _compute_step_hash(
            kind="adapter",
            name=name,
            version=version,
            params=params,
            input_hashes=input_hashes,
        )
        return cls(
            name=name, version=version, params=params,
            input_hashes=input_hashes, hash=h,
        )


class PrimitiveStep(BaseModel):
    """A per-tool-folder primitive step.

    Records *which primitive was invoked* and *what identity bits
    define this artifact*.  Constructed by the primitive→operator
    adapter (``shared.artifacts.adapters.from_time_series``) when it
    lifts a primitive's canonical ``TimeSeries`` payload into a
    typed ``Series``.

    Schema design — what feeds the hash, what is bookkeeping
    ----------------------------------------------------------
    The step body has two field categories:

      Identity-bearing (folded into the hash via ``build()``):
        - ``name``               — the MCP tool name (e.g.
          ``calculate_ois_curve_spread_tool``).  Plain ``str`` (not
          ``Literal``) because the primitive family is extensible
          and lineage must not couple to a fixed tool registry.
        - ``params``             — the primitive's ``*Input.model_dump()``.
        - ``tool_config_hash``   — content-hash of the bundled
          ``ToolConfig.conventions`` block.  Two calls with the
          same ``*Input`` but different YAMLs (e.g. someone bumped
          ``z_score_window_days``) MUST produce different step
          hashes — that's what this captures.
        - ``output_field``       — which ``time_series*`` field of
          the primitive's output was extracted (e.g.
          ``time_series_spread`` vs ``time_series_zscore``).
          Different field → different artifact → different hash.
        - ``as_of_date``         — the snapshot's
          ``current_metrics.as_of_date``.  Replay-determinism: a
          re-run tomorrow with the same params has a different
          ``as_of_date`` (DB has a new latest), and the hash
          reflects that the underlying data is different.
        - ``input_hashes``       — always ``()`` in v1 (primitives
          fetch from the DB themselves; they have no upstream
          artifact inputs).  Slot reserved for Phase 2 primitives
          that ever accept an artifact input.

      Bookkeeping-only (NOT in the hash):
        - ``tool_config_path``   — the path the YAML was loaded
          from.  Two callers loading the same YAML from different
          paths (test fixture vs prod) MUST produce the same hash
          if the content is identical.  So path is metadata for
          human debugging, not identity.

    Why the four identity bits ride inside the hash via a derived
    dict instead of through an extended ``_compute_step_hash``
    recipe: extending the recipe is a breaking change to every
    persisted lineage object across the codebase.  Folding the
    primitive identity bits into a derived ``hashed_params`` dict
    inside ``build()`` keeps the recipe untouched, which keeps
    every existing ``FetchStep`` / ``CleanStep`` / ``AdapterStep``
    / ``OperatorStep`` hash stable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["primitive"] = "primitive"
    name: str  # e.g., "calculate_ois_curve_spread_tool"
    version: str = "1.0.0"
    params: Dict[str, Any]  # primitive's *Input.model_dump()
    tool_config_hash: str
    tool_config_path: Optional[str] = None  # NOT in hash
    output_field: str  # e.g., "time_series_spread"
    as_of_date: str  # ISO YYYY-MM-DD from the primitive snapshot
    input_hashes: Tuple[LineageHash, ...] = ()  # always () in v1
    hash: LineageHash

    @classmethod
    def build(
        cls,
        *,
        name: str,
        version: str = "1.0.0",
        params: Dict[str, Any],
        tool_config_hash: str,
        output_field: str,
        as_of_date: str,
        tool_config_path: Optional[str] = None,
        input_hashes: Tuple[LineageHash, ...] = (),
    ) -> "PrimitiveStep":
        # Fold the primitive identity bits into a derived dict so
        # the existing _compute_step_hash recipe applies unchanged.
        # Keys are alphabetized by _canonical_json (sort_keys=True),
        # so the order they're added here is irrelevant.
        hashed_params: Dict[str, Any] = {
            "input_params": params,
            "tool_config_hash": tool_config_hash,
            "output_field": output_field,
            "as_of_date": as_of_date,
        }
        h = _compute_step_hash(
            kind="primitive",
            name=name,
            version=version,
            params=hashed_params,
            input_hashes=input_hashes,
        )
        return cls(
            name=name,
            version=version,
            params=params,
            tool_config_hash=tool_config_hash,
            tool_config_path=tool_config_path,
            output_field=output_field,
            as_of_date=as_of_date,
            input_hashes=input_hashes,
            hash=h,
        )


class OperatorStep(BaseModel):
    """A central-operator step (any tool under ``shared.operators.*``).

    Persists ``input_hashes`` AND optional ``auxiliary_lineages`` for
    binary / N-ary operators whose right-hand (or auxiliary) inputs
    have their own provenance chains.

    Why both:

      - ``input_hashes``        : enough to identify upstream by hash
        and to reconstruct what was consumed when the system has a
        content-addressed lineage cache (deferred).
      - ``auxiliary_lineages``  : the actual ``Lineage`` chains for
        non-primary inputs, embedded in this step.  Required today
        because there is no lineage cache yet — a methodology summary
        that walks ``head.lineage`` end-to-end would otherwise lose
        the right operand's chain entirely (Codex P1 follow-up on
        ``series_arithmetic``).

    The output ``Series.lineage`` is composed by appending this step
    to the *primary* (left/first) input's lineage; auxiliary inputs'
    chains live inside the step.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["operator"] = "operator"
    name: str  # e.g., "align_series"
    version: str = "1.0.0"
    params: Dict[str, Any]
    input_hashes: Tuple[LineageHash, ...] = ()
    auxiliary_lineages: Tuple["Lineage", ...] = ()
    hash: LineageHash

    @classmethod
    def build(
        cls,
        *,
        name: str,
        version: str,
        params: Dict[str, Any],
        input_hashes: Tuple[LineageHash, ...],
        auxiliary_lineages: Tuple["Lineage", ...] = (),
    ) -> "OperatorStep":
        h = _compute_step_hash(
            kind="operator",
            name=name,
            version=version,
            params=params,
            input_hashes=input_hashes,
        )
        return cls(
            name=name, version=version, params=params,
            input_hashes=input_hashes,
            auxiliary_lineages=auxiliary_lineages,
            hash=h,
        )


# Discriminated union over the closed family.  Pydantic picks the right
# concrete class by ``kind`` on deserialization, so ``Lineage`` round-
# trips through JSON without losing type information.
#
# Order is for documentation only; the discriminator resolves by
# ``kind`` value, not by union position.  Adding a new step kind
# requires extending this union AND adding the class above —
# closed-family discipline.
LineageStep = Annotated[
    Union[FetchStep, CleanStep, AdapterStep, PrimitiveStep, OperatorStep],
    Field(discriminator="kind"),
]


# ============================================================================
# LINEAGE
# ============================================================================


class Lineage(BaseModel):
    """Ordered chain of steps that produced an artifact.

    ``steps[0]`` is the earliest step (typically a ``FetchStep``);
    ``steps[-1]`` is the most recent (the operator that just emitted
    the artifact).  ``head_hash`` mirrors ``steps[-1].hash`` for cheap
    equality / dedup without walking the chain.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: List[LineageStep] = Field(..., min_length=1)
    head_hash: LineageHash

    @classmethod
    def from_steps(cls, steps: List[LineageStep]) -> "Lineage":
        if not steps:
            raise ValueError("Lineage must contain at least one step.")
        return cls(steps=steps, head_hash=steps[-1].hash)

    def append(self, step: LineageStep) -> "Lineage":
        """Return a NEW Lineage with ``step`` appended.

        ``Lineage`` is frozen — mutation is forbidden.  Use this to
        build longer chains."""
        return Lineage.from_steps(list(self.steps) + [step])


# Resolve the recursive forward reference: OperatorStep.auxiliary_lineages
# is Tuple["Lineage", ...].  Pydantic must rebuild the model now that
# ``Lineage`` is defined.
OperatorStep.model_rebuild()


__all__ = [
    "LineageHash",
    "LineageStep",
    "Lineage",
    "FetchStep",
    "CleanStep",
    "AdapterStep",
    "PrimitiveStep",
    "OperatorStep",
]
