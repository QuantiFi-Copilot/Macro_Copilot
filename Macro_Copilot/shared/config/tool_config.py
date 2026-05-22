"""
tool_config.py — Per-tool YAML configuration loader
====================================================

Each tool in the sovereign-bonds / OIS / future-domain tree owns a
``config.yaml`` describing its conventions (z-score window, fill limit,
default Bloomberg field, etc.) and methodology metadata (what the tool
does, assumptions, citations).  This module loads, validates, and
caches those files so the rest of the codebase consumes a typed
``ToolConfig`` instead of calling ``yaml.safe_load`` directly.

Determinism boundary
--------------------
In V1 deterministic mode, conventions are **immutable from the LLM's
reach**.  The MCP wrapper does not expose any convention as an LLM-
overridable parameter.  Conventions become writable in a future
advanced mode through UI controls or per-user presets — never through
LLM phrasing interpretation.  See
``docs/architecture/tool_architecture.md``.

Central-knob discipline (A13)
-----------------------------
Each tool exposes ONLY its central methodological choice as a user-
facing input — the choice that defines what the tool IS (e.g.
``z_score_window_days`` for ``zscore_custom``,
``regression_window_days`` for ``rolling_regression``,
``groupings`` for ``yield_change_decomposition_simple``).  All
ancillary methodology stays in YAML and is NOT user-overridable in
V1.  Structural choices (formulas, sign conventions, anchoring,
solver) are locked in code or guarded with the
``NotImplementedError`` honest-placeholder pattern documented under
``MethodologyMeta.planned_extensions``.

Schema
------
``config.yaml`` has three top-level blocks:

  tool:
    name: <mcp_tool_name>
    domain: <sovereign_bonds | ois | ...>
    description: <one-line human description>
    category: <desk_invariant_primitive | quant_standard_analytic>
              # optional; defaults to 'desk_invariant_primitive'.  See
              # docs/architecture/tool_architecture.md for the
              # honesty-mechanism rationale.

  conventions:
    <key>:
      value: <scalar>
      source: <non-empty string — should reference a documented tag>
      rationale: <non-empty string — why this default>
      valid_range: [lo, hi]   # optional, numeric values only

  methodology:
    what_it_does: <one-paragraph description>
    assumptions: [<short string>, ...]    # optional list
    citations:   [<citation>, ...]         # optional list

Source-tag enforcement
----------------------
For commit 1 of the tool-config refactor, ``source`` is a non-empty
string with no enum constraint.  Commit 6 will tighten this to a
documented enum (``docs/architecture/methodology_sources.md``) once the
source set has stabilised across multiple tools.

Caching
-------
``load_tool_config(path)`` is process-wide-cached by absolute path.
Subsequent calls for the same file return the same ``ToolConfig``
instance; mutations are not supported (the model is frozen).  Tests
that load tweaked-and-rewritten YAMLs should call
``clear_tool_config_cache()`` between cases.

Methodology-version pinning (Phase 0 PR 9)
------------------------------------------
When called with a ``conn`` argument, ``load_tool_config`` also
registers the YAML's parsed content in
``copilot_state.methodology_versions`` (idempotent by
``yaml_content_hash``) and attaches the resulting
``methodology_version_id`` to the returned ``ToolConfig``.

The DB-less path (``conn=None``) is the default for unit tests
and the CLI REPL; the returned config has
``methodology_version_id=None`` and behaves exactly as it did
before PR 9.  Production callers that produce artifacts (the
primitive→operator adapter) pass a connection so the id makes
it onto each ``PrimitiveStep`` and ultimately into
``artifact_metadata.methodology_version_ids``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


# Recognised tool categories.  This is the honesty mechanism for the
# distinction between tools whose name a trader would recognise without
# methodology preface vs tools that are textbook-standard but require
# methodology specification before use.  See
# ``docs/architecture/tool_architecture.md`` for definitions and worked
# examples.
ToolCategory = Literal[
    "desk_invariant_primitive",
    "quant_standard_analytic",
]


# ============================================================================
# CONVENTION
# ============================================================================

class Convention(BaseModel):
    """One configurable convention.

    A scalar value plus the metadata needed to audit it: where the
    value came from, why it was chosen, and (for numeric values) the
    range of legitimate alternatives.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Any = Field(..., description="Scalar value (bool, int, float, or str).")
    source: str = Field(
        ...,
        min_length=1,
        description=(
            "Documented source tag — e.g. 'industry_standard_1y_window', "
            "'bloomberg_field_convention', 'team_judgment_pending_review'. "
            "Will be enforced as an enum in commit 6 of the pilot."
        ),
    )
    rationale: str = Field(
        ...,
        min_length=1,
        description="One-sentence justification for the default value.",
    )
    valid_range: Optional[List[Union[int, float]]] = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "Optional [lo, hi] window of acceptable values.  Numeric "
            "values only; ignored for str/bool conventions."
        ),
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("value")
    @classmethod
    def _value_must_be_scalar(cls, v: Any) -> Any:
        """Reject dicts, lists, None, and any other non-scalar types.

        We accept ``bool`` even though it overlaps with ``int`` because
        boolean conventions are legitimate (e.g. enabling a future
        compounding flag).  ``isinstance(True, int)`` is True in Python,
        so the order doesn't matter — both pass.
        """
        if v is None:
            raise ValueError("value cannot be None")
        if not isinstance(v, (bool, int, float, str)):
            raise ValueError(
                f"value must be scalar (bool/int/float/str), "
                f"got {type(v).__name__}"
            )
        return v

    @model_validator(mode="after")
    def _check_valid_range(self) -> "Convention":
        """Enforce valid_range semantics: numeric-only, ordered, contains value."""
        if self.valid_range is None:
            return self

        # bool is technically int but a range on True/False is nonsense.
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError(
                "valid_range only applies to numeric (int/float) values; "
                f"got value of type {type(self.value).__name__}"
            )

        lo, hi = self.valid_range[0], self.valid_range[1]
        if lo > hi:
            raise ValueError(
                f"valid_range[0]={lo} must be <= valid_range[1]={hi}"
            )
        if not (lo <= self.value <= hi):
            raise ValueError(
                f"value={self.value} is outside valid_range [{lo}, {hi}]"
            )
        return self


# ============================================================================
# TOOL META
# ============================================================================

class ToolMeta(BaseModel):
    """The ``tool:`` block — identity / location of the tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        description=(
            "Tool name as exposed by the MCP server "
            "(e.g. 'calculate_curve_spread_tool')."
        ),
    )
    domain: str = Field(
        ...,
        min_length=1,
        description="Owning domain — 'sovereign_bonds', 'ois', etc.",
    )
    description: str = Field(
        ...,
        min_length=1,
        description="One-line human description.",
    )
    category: ToolCategory = Field(
        default="desk_invariant_primitive",
        description=(
            "Tool category — honesty mechanism for what a trader would "
            "recognise from the name alone:\n"
            "* ``desk_invariant_primitive`` — a trader on any major rates "
            "desk would recognise the tool's name and know what its "
            "inputs and outputs are without methodology preface "
            "(curve_spread, yield_levels, butterfly, cross_market_spread, "
            "curve_move_classifier, zscore_custom, beta_adjusted_spread, "
            "half_life).\n"
            "* ``quant_standard_analytic`` — textbook quant primitive "
            "whose interpretation is universal but whose configuration "
            "must be specified before use (yield_change_decomposition_simple, "
            "rolling_regression, pca_yield_curve, "
            "yield_change_attribution_pca).\n"
            "Defaults to ``desk_invariant_primitive`` so the existing five "
            "migrated tools keep their identity without explicit YAML "
            "edits.  See docs/architecture/tool_architecture.md for "
            "definitions and the per-tool category table."
        ),
    )


# ============================================================================
# METHODOLOGY META
# ============================================================================

class MethodologyMeta(BaseModel):
    """The ``methodology:`` block — drives the future methodology UI card.

    Required ``what_it_does`` so that every tool ships with at least one
    sentence of human-facing methodology.  ``assumptions``,
    ``citations``, and ``planned_extensions`` are optional.

    ``planned_extensions`` is the public ledger of decisions yet to be
    implemented.  When a YAML convention exists with a ``value`` that is
    the only currently-supported option but documents alternatives in
    its rationale (e.g. ``avg_change_method: arithmetic_mean`` with
    duration-weighted variants planned), list those alternatives here.
    The corresponding ``compute()`` should raise ``NotImplementedError``
    with a pointer to this block when an unsupported value is set, so
    the YAML is never silently dishonest.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    what_it_does: str = Field(..., min_length=1)
    assumptions: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    planned_extensions: List[str] = Field(default_factory=list)


# ============================================================================
# TOP-LEVEL TOOL CONFIG
# ============================================================================

class ToolConfig(BaseModel):
    """Validated representation of one ``config.yaml`` file.

    This is what callers consume: a typed object with `.tool`,
    `.conventions`, and `.methodology`.  Frozen so accidental mutations
    don't propagate through the cache.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: ToolMeta
    conventions: Dict[str, Convention] = Field(default_factory=dict)
    methodology: MethodologyMeta

    # Phase 0 PR 9.  Populated by ``load_tool_config`` when a DB
    # connection is supplied; None on the test / CLI path.
    #
    # NOT part of ``conventions_hash`` — the hash recipe captures
    # CONTENT, this field carries a REGISTRY POINTER.  See
    # ``PrimitiveStep`` docstring for the same invariant on the
    # lineage side.
    methodology_version_id: Optional[int] = Field(default=None)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def convention_value(self, key: str) -> Any:
        """Return the value of a convention by key, raising KeyError if missing.

        Sugar over ``self.conventions[key].value`` so callers can write::

            window = config.convention_value("z_score_window_days")

        instead of dereferencing the wrapper twice.
        """
        if key not in self.conventions:
            raise KeyError(
                f"convention {key!r} is not declared in tool "
                f"{self.tool.name!r} (declared: {sorted(self.conventions.keys())})"
            )
        return self.conventions[key].value

    def conventions_hash(self) -> str:
        """Content-hash of the declared ``{name: value}`` pairs.

        Identity-faithful by construction:
          - Two configs with identical convention values produce the
            same hash regardless of insertion order (canonical-JSON
            with ``sort_keys=True``).
          - Methodology / source / rationale / valid_range fields do
            NOT feed the hash — they are documentation, not identity.
            The cross-config lint (``shared.config.lint``) already
            uses this same identity definition: it flags drift on
            ``value`` and ignores documentary fields.
          - A ``Convention.value`` change (e.g. someone bumps
            ``z_score_window_days`` from 252 to 504) changes the hash
            and therefore invalidates any cache key derived from it.

        Used by the primitive→operator adapter
        (``shared.artifacts.adapters.from_time_series``) when
        constructing ``PrimitiveStep.tool_config_hash`` so an artifact
        produced under one YAML is not silently mistaken for one
        produced under a methodology-edited YAML.

        Future-proofing: the hash recipe is intentionally simple
        (``sha256(json.dumps({name: value}, sort_keys=True))``) so
        callers in other languages can reproduce it without depending
        on Pydantic.  Changing the recipe is a breaking change to
        every persisted lineage object — review carefully.
        """
        import hashlib
        import json
        as_pairs = {name: conv.value for name, conv in self.conventions.items()}
        canonical = json.dumps(
            as_pairs, sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ============================================================================
# LOADER + CACHE
# ============================================================================

# Process-wide cache, keyed by absolute resolved path.  Mirrors the
# lazy-engine pattern used elsewhere in the codebase: load once, reuse
# everywhere, no hot-reload in V1.
_CACHE: Dict[Path, ToolConfig] = {}


class ToolConfigError(Exception):
    """Raised when a tool's config.yaml cannot be loaded or validated.

    The error message always names the file path so the developer can
    jump straight to it.
    """


def load_tool_config(
    path: Union[str, Path],
    *,
    conn: Optional["Connection"] = None,
) -> ToolConfig:
    """Load and validate a tool's ``config.yaml``.

    Cached process-wide by absolute path.  Subsequent calls for the
    same file return the same ``ToolConfig`` instance.

    Parameters
    ----------
    path : str or Path
        Path to the tool's ``config.yaml``.  Relative paths are
        resolved against the current working directory.
    conn : Optional[Connection]
        When supplied, the YAML's parsed content is registered in
        ``copilot_state.methodology_versions`` (idempotent by
        ``yaml_content_hash``) and the resulting id is attached to
        the returned ``ToolConfig``.  When ``None`` (the default
        for tests + the CLI REPL), the returned config has
        ``methodology_version_id=None`` and behaves as before
        Phase 0 PR 9.

    Returns
    -------
    ToolConfig
        A frozen, validated config object.

    Raises
    ------
    ToolConfigError
        If the file is missing, malformed, or fails schema validation.
        Wraps the underlying error and prefixes the file path for
        easier debugging.

    Cache + DB-registration interaction
    -----------------------------------
    The path-keyed cache stores the version of the config that was
    FIRST loaded.  If the FIRST load passed ``conn``, the cached
    config carries a ``methodology_version_id``; subsequent loads
    (with or without ``conn``) return the cached object as-is.
    If the FIRST load was without ``conn`` and a later caller
    passes one, we re-register the YAML to attach the id and store
    the new (otherwise byte-identical) config in cache.  This
    keeps the contract simple: once a process has registered a
    YAML, the cached config always has the id.
    """
    p = Path(path).resolve()

    cached = _CACHE.get(p)
    if cached is not None and (conn is None or cached.methodology_version_id is not None):
        return cached

    if not p.is_file():
        raise ToolConfigError(
            f"Tool config not found: {p}\n"
            "Hint: each tool's folder must contain a config.yaml."
        )

    try:
        with p.open("r") as f:
            raw_text = f.read()
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ToolConfigError(f"YAML parse error in {p}: {exc}") from exc

    if raw is None:
        raise ToolConfigError(f"Tool config is empty: {p}")
    if not isinstance(raw, dict):
        raise ToolConfigError(
            f"Tool config root must be a mapping, got "
            f"{type(raw).__name__}: {p}"
        )

    try:
        cfg = ToolConfig(**raw)
    except Exception as exc:
        raise ToolConfigError(
            f"Schema validation failed for {p}:\n{exc}"
        ) from exc

    if conn is not None:
        # Lazy import — ``state.methodology_versions`` imports
        # ``shared.artifacts.lineage`` which has heavyweight
        # dependencies (pandas).  Keeping it lazy means tests / CLI
        # paths that never touch the DB don't pay for the import
        # graph.
        from state.methodology_versions import register_yaml

        version_id = register_yaml(
            raw_text, yaml_path=str(p), conn=conn,
        )
        # Rebuild the frozen model with the id attached.  Pydantic's
        # ``model_copy`` is the supported way to mutate a frozen
        # model into a new instance.
        cfg = cfg.model_copy(update={"methodology_version_id": version_id})

    _CACHE[p] = cfg
    return cfg


def clear_tool_config_cache() -> None:
    """Drop all cached configs.  Call between tests that load tweaked
    versions of the same file."""
    _CACHE.clear()
