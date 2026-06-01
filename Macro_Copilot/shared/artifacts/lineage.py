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

import datetime as _dt
import hashlib
import json
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Type alias for the hexdigest hash strings; promotes readability.
LineageHash = str


def _canonicalize_for_hash(obj: Any) -> Any:
    """Recursively convert ``obj`` to a strictly JSON-serializable form with
    stable representations across Python / NumPy / Pandas versions.

    The original implementation relied on ``json.dumps(..., default=str)``,
    which silently called ``str()`` on any non-JSON-native value.  That
    fallback is the source of cross-version drift: ``str(np.float64(0.1))``
    can differ between NumPy versions, ``str(pd.Timestamp(...))`` can
    differ between Pandas versions, and a user-defined ``__str__`` makes
    the hash a function of code that has nothing to do with identity.

    This function replaces that silent fallback with an explicit allowlist:

      - ``None``, ``bool``, ``int``, ``str``                — passed through
      - ``float``                                            — passed through;
        ``NaN`` / ``Infinity`` are rejected (no canonical JSON form anyway)
      - ``list`` / ``tuple``                                 — recursively
        canonicalized; tuples become lists for JSON purposes (order
        preserved)
      - ``dict``                                             — keys must be
        ``str``; values recursively canonicalized
      - ``datetime.date`` / ``datetime.datetime``            — ISO 8601 string
      - NumPy scalar (anything with ``.item()`` returning a Python native)
                                                             — unwrapped via
        ``.item()`` and re-canonicalized
      - ``.isoformat()``-capable (e.g. ``pd.Timestamp``)     — ISO 8601 string
        via ``.isoformat()`` (Pandas-stable representation)

    Anything else raises ``TypeError`` with a clear message.  This is the
    "fail loudly" half of the determinism contract: silent fallbacks are
    what create drift, so we don't have them.

    Closes ``docs/technical_debt.md`` item #20 for the lineage layer.
    """
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        # bool is a subclass of int; the bool branch above catches it first.
        return obj
    if isinstance(obj, float):
        # Reject IEEE-754 non-finite values.  ``allow_nan=False`` on
        # ``json.dumps`` below would also raise, but raising here keeps the
        # error message specific to the offending key/value.
        if obj != obj:  # NaN; NaN != NaN is True.
            raise ValueError(
                "NaN is not allowed in hashable params (no canonical JSON form)"
            )
        if obj in (float("inf"), float("-inf")):
            raise ValueError(
                "Infinity is not allowed in hashable params (no canonical JSON form)"
            )
        return obj
    if isinstance(obj, (list, tuple)):
        return [_canonicalize_for_hash(x) for x in obj]
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"dict keys must be strings for hashing, got "
                    f"{type(k).__name__}: {k!r}"
                )
            out[k] = _canonicalize_for_hash(v)
        return out
    # ``datetime.datetime`` is a subclass of ``datetime.date``; this branch
    # catches both.  ``isoformat()`` is well-defined and stable.
    if isinstance(obj, _dt.date):
        return obj.isoformat()

    # NumPy scalars (np.int64, np.float64, np.bool_) expose ``.item()`` and
    # return a Python native that we can re-canonicalize.  Use ``getattr``
    # so the lineage module does not import numpy unconditionally.
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return _canonicalize_for_hash(item())
        except (ValueError, TypeError):
            # Fall through to the .isoformat() path and the final raise.
            pass

    # pd.Timestamp and other isoformat-capable types.
    iso = getattr(obj, "isoformat", None)
    if callable(iso):
        try:
            result = iso()
            if isinstance(result, str):
                return result
        except Exception:
            pass

    raise TypeError(
        f"Cannot canonicalize {type(obj).__name__} for hashing: {obj!r}. "
        "Allowed inputs: None, bool, int, float (finite), str, list, tuple, "
        "dict (str keys), datetime.date, datetime.datetime, numpy scalar "
        "(via .item()), pandas.Timestamp (via .isoformat())."
    )


def _canonical_json(obj: Any) -> str:
    """Stable JSON serialization for hashing.

    Two equivalent ``params`` dicts produce the same string regardless of:

      - key insertion order (``sort_keys=True``),
      - whitespace formatting (compact separators, no spaces),
      - Python / NumPy / Pandas version differences (explicit
        canonicalization upfront in :func:`_canonicalize_for_hash`
        rejects types whose ``str()`` output may drift).

    ``allow_nan=False`` is a belt-and-braces defense — the
    canonicalization step already rejects ``NaN`` / ``Infinity``, but
    pinning the JSON encoder here means a future canonicalize bug cannot
    silently produce non-strict JSON.
    """
    canon = _canonicalize_for_hash(obj)
    return json.dumps(
        canon,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    )


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


def _expected_step_hash(step: Any) -> LineageHash:
    """Recompute a step's content hash exactly as its ``.build()`` does.

    The basis for the ART9 ``.build()``-only integrity guard: a step
    whose stored ``hash`` does not equal this recomputation was forged or
    raw-constructed, and is rejected at ``Lineage`` construction.
    ``PrimitiveStep`` folds four identity bits into a derived dict (see
    its docstring), so it is reconstructed here the same way.
    """
    kind = step.kind
    input_hashes = tuple(getattr(step, "input_hashes", ()) or ())
    if kind == "primitive":
        params = {
            "input_params": step.params,
            "tool_config_hash": step.tool_config_hash,
            "output_field": step.output_field,
            "as_of_date": step.as_of_date,
        }
        # PR-10D Codex F5: mirror the optional new identity-bearing
        # fields exactly as PrimitiveStep.build folds them in (only
        # when populated, preserving back-compat).
        dcf = getattr(step, "data_content_fingerprint", None)
        if dcf is not None:
            params["data_content_fingerprint"] = dcf
        dv = getattr(step, "data_vintage", None)
        if dv is not None:
            params["data_vintage"] = dv
    else:
        params = step.params
    return _compute_step_hash(
        kind=kind,
        name=step.name,
        version=step.version,
        params=params,
        input_hashes=input_hashes,
    )


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
        - ``tool_config_path``        — the path the YAML was loaded
          from.  Two callers loading the same YAML from different
          paths (test fixture vs prod) MUST produce the same hash
          if the content is identical.  So path is metadata for
          human debugging, not identity.
        - ``methodology_version_id``  — Phase 0 PR 9.  Pointer into
          ``copilot_state.methodology_versions`` letting the metadata
          layer answer "which stored YAML version produced this
          step" without re-hashing.  NOT in the hash because the
          YAML CONTENT is already folded in via
          ``tool_config_hash``: two YAMLs whose content is byte-
          identical have the same ``methodology_version_id`` AND
          the same ``tool_config_hash``, so adding the registry
          id to the hash would be redundant.  Pinning it as
          hash-excluded keeps PR 2's pinned-hash invariant intact
          AND lets a future YAML rename (path change, content
          unchanged) NOT invalidate any cached lineage.

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
    # Phase 0 PR 9.  NOT in hash — pointer into the
    # ``methodology_versions`` registry.  See class docstring under
    # "Bookkeeping-only (NOT in the hash)" for the invariant.
    methodology_version_id: Optional[int] = None
    output_field: str  # e.g., "time_series_spread"
    as_of_date: str  # ISO YYYY-MM-DD from the primitive snapshot
    input_hashes: Tuple[LineageHash, ...] = ()  # always () in v1
    # PR-10D Codex F5: lineage hardening — explicit data content +
    # vintage fields.
    #
    # ``data_content_fingerprint`` — stable SHA-256 over canonicalised
    # TimeSeries content (series_name + units + dates + values).  When
    # populated, IS in the hash.  Captures the "if the vendor revises
    # the SAME date's value, the lineage hash MUST change" property
    # the original contract demanded.  Optional + nullable for
    # backwards compat: existing PrimitiveStep.build calls that don't
    # supply it produce byte-identical hashes to before.
    #
    # ``data_vintage`` — explicit vendor data-vintage stamp.  V1
    # implementation: same value as ``as_of_date`` (which IS the
    # snapshot's vintage stamp from current_metrics).  Adds the
    # vintage as a NAMED first-class field so downstream lineage
    # consumers don't have to infer it from as_of_date.  Optional +
    # nullable for back-compat.
    data_content_fingerprint: Optional[str] = None
    data_vintage: Optional[str] = None
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
        methodology_version_id: Optional[int] = None,
        input_hashes: Tuple[LineageHash, ...] = (),
        data_content_fingerprint: Optional[str] = None,
        data_vintage: Optional[str] = None,
    ) -> "PrimitiveStep":
        # Fold the primitive identity bits into a derived dict so
        # the existing _compute_step_hash recipe applies unchanged.
        # Keys are alphabetized by _canonical_json (sort_keys=True),
        # so the order they're added here is irrelevant.
        #
        # IMPORTANT: ``methodology_version_id`` is NOT folded into
        # this dict.  The YAML content it points at is already
        # captured in ``tool_config_hash``; adding the registry id
        # would couple the hash to per-DB auto-increment values
        # (which are NOT stable across deploys / restores) and
        # break PR 2's pinned-hash invariant.  This omission is
        # the invariant the test
        # ``tests/state/test_hash_stability.py`` enforces.
        hashed_params: Dict[str, Any] = {
            "input_params": params,
            "tool_config_hash": tool_config_hash,
            "output_field": output_field,
            "as_of_date": as_of_date,
        }
        # PR-10D Codex F5: when the caller supplies the new
        # identity-bearing fields, fold them into the hash.  When
        # absent (the default, for backwards compat with every
        # pre-PR-10D primitive call site), the hash is byte-
        # identical to the V1 recipe — tests/state/test_hash_stability.py
        # continues to pass unchanged.  Once a primitive
        # adopter passes ``data_content_fingerprint=...``, the
        # SAME-params + SAME-as_of_date but DIFFERENT-vendor-data
        # case produces a different hash — closing the original
        # contract's "vendor data revision MUST change the hash"
        # gap.
        if data_content_fingerprint is not None:
            hashed_params["data_content_fingerprint"] = data_content_fingerprint
        if data_vintage is not None:
            hashed_params["data_vintage"] = data_vintage
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
            methodology_version_id=methodology_version_id,
            output_field=output_field,
            as_of_date=as_of_date,
            input_hashes=input_hashes,
            data_content_fingerprint=data_content_fingerprint,
            data_vintage=data_vintage,
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

    @model_validator(mode="after")
    def _verify_integrity(self) -> "Lineage":
        # LIN-3 (.build()-only): every step's hash must match its
        # recomputed content — a raw-constructed / forged step hash is
        # rejected (ART9).
        for i, step in enumerate(self.steps):
            if step.hash != _expected_step_hash(step):
                raise ValueError(
                    f"Lineage step {i} ('{step.name}', kind={step.kind}) "
                    "hash does not match its content; steps must be built via "
                    ".build(), never raw-constructed with a supplied hash "
                    "(ART9 integrity)."
                )
        # LIN-1: head_hash must mirror the last step's hash (a forged
        # head_hash is rejected).
        if self.head_hash != self.steps[-1].hash:
            raise ValueError(
                "Lineage.head_hash does not match steps[-1].hash — a forged "
                "head_hash is rejected; build via from_steps()/append() "
                "(ART9)."
            )
        # LIN-2: chain connectivity — each step after the first must
        # consume its immediate predecessor (its hash appears in the
        # step's input_hashes).  Roots (input_hashes == ()) are exempt;
        # membership not equality, because binary / N-ary ops carry
        # multiple input hashes.
        for i in range(1, len(self.steps)):
            cur_inputs = tuple(getattr(self.steps[i], "input_hashes", ()) or ())
            if cur_inputs and self.steps[i - 1].hash not in cur_inputs:
                raise ValueError(
                    f"Lineage chain is disconnected at step {i} "
                    f"('{self.steps[i].name}'): its input_hashes do not "
                    f"include the predecessor '{self.steps[i - 1].name}' hash "
                    "(ART9 connectivity)."
                )
        return self

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


# ============================================================================
# PARAM SANITISATION (OPR10 — finite-or-None before hashing)
# ============================================================================


def sanitize_params_for_lineage(params: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively map non-finite floats (``NaN`` / ``+-Inf``) to
    ``None`` so a params dict is safe to hash via
    :func:`_compute_step_hash` (which rejects ``NaN`` / ``Inf`` — there
    is no canonical-JSON form for them).

    Operators that fold a *computed* float into their ``OperatorStep``
    params (a dispersion, a per-event statistic that may be ``NaN`` on a
    degenerate input) MUST pass the dict through this helper before
    ``OperatorStep.build`` — so a legitimate degenerate result becomes
    ``None`` (JSON-canonical) instead of crashing the lineage layer.
    This is the OPR10 fix for the class of crash where a computed
    ``math.nan`` reached the hasher (e.g. ``summarize_series`` on its
    default path).

    ``None`` / ``str`` / ``bool`` / ``int`` and finite floats pass
    through unchanged; nested dicts and lists/tuples are recursed
    (tuples become lists, matching the canonicalizer's tuple handling).
    """
    import math

    def _clean(obj: Any) -> Any:
        if isinstance(obj, bool):
            return obj  # bool is an int subclass — preserve it as-is
        if isinstance(obj, float):
            return obj if math.isfinite(obj) else None
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_clean(x) for x in obj]
        return obj

    return _clean(params)


__all__ = [
    "LineageHash",
    "LineageStep",
    "Lineage",
    "FetchStep",
    "CleanStep",
    "AdapterStep",
    "PrimitiveStep",
    "OperatorStep",
    "sanitize_params_for_lineage",
]
