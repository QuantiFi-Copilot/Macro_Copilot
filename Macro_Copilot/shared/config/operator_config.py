"""shared.config.operator_config — Per-operator configuration loader.

Per build plan v5: operator configs live alongside operator code under
``shared/operators/<operator_name>/config.yaml``.  Path determines kind
(no ``kind:`` field added to existing tool YAMLs).  This loader is
deliberately separate from ``tool_config.py`` because:

  - tools own finance concepts (``domain``, ``category``) — operators do not
  - tools' methodology lives under ``conventions:`` — operators' lives
    under ``defaults:`` for the consequential variants of the
    structural method family they own
  - cross-config drift semantics differ (build plan v5 / R4)

Schema (top-level blocks, all required):

    operator:
      name: <operator_name>
      method_family: <alignment | arithmetic | masking | windowing |
                      aggregation | ranking | mapping>
      version: "<semver>"
      description: <one-line>

    defaults:                 # consequential method variants the
      <variant_key>:          # operator exposes; keys here align with
        value: <scalar>       # the operator's input schema parameters
        source: <non-empty>
        rationale: <non-empty>
        valid_values: [...]   # optional: closed set of allowed values
                              # — surfaces a validation hook for the
                              #   default-drift lint check (R4)

    methodology:
      what_it_does: <paragraph>
      planned_extensions: [...]    # optional list

Caching is process-wide on absolute path, mirroring ``tool_config``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


# Closed set of method families the operator layer knows about.  See
# docs/architecture/operator_architecture.md for the rationale.
# Phase 1 PR 12 adds the three trade-lifecycle families used by the
# backtest archetype: construction (EventSet → TradeSet), evaluation
# (TradeSet + price Panel → P&L Panel), and summary (P&L Panel →
# scalar metrics).
OperatorMethodFamily = Literal[
    "alignment",
    "arithmetic",
    "masking",
    "windowing",
    "aggregation",
    "ranking",
    "mapping",
    # v2.0 (ADR 0016) — the composition-toolbox families.
    "statistical_relationship",
    "single_series_transform",
    "cross_sectional",
    "unit_conversion",
]


class OperatorConfigError(ValueError):
    """Raised on missing / malformed / invalid operator config YAML.

    Subclasses ``ValueError`` (OPR13 / ADR 0016 Decision 3 — one error
    family): a config-identity or load failure inside any operator must
    surface as a ``ValueError`` so the orchestration/template layer's
    ``except ValueError`` envelope catches it uniformly, rather than
    escaping as a bare ``Exception`` through all 9 operators (ERR-1).
    """


# ============================================================================
# DEFAULT (one entry inside ``defaults:``)
# ============================================================================


class OperatorDefault(BaseModel):
    """One default entry — value + provenance for a method-family variant.

    ``valid_values`` is optional: when present, the loader checks that
    ``value`` is in the set, AND the lint default-drift check (build
    plan v5 / R4) uses it to know which keys are comparable across
    operators in the same family.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Any
    source: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    valid_values: Optional[List[Any]] = None

    def model_post_init(self, __context: Any) -> None:
        if self.valid_values is not None and self.value not in self.valid_values:
            raise OperatorConfigError(
                f"OperatorDefault.value={self.value!r} is not in "
                f"valid_values={self.valid_values!r}."
            )


# ============================================================================
# OPERATOR META
# ============================================================================


class OperatorMeta(BaseModel):
    """The ``operator:`` block — identity + structural method family."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    method_family: OperatorMethodFamily
    version: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)


# ============================================================================
# METHODOLOGY
# ============================================================================


class OperatorMethodologyMeta(BaseModel):
    """The ``methodology:`` block — narrative + planned extensions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    what_it_does: str = Field(..., min_length=1)
    planned_extensions: Optional[List[str]] = None


# ============================================================================
# TOP-LEVEL CONFIG
# ============================================================================


class OperatorConfig(BaseModel):
    """Validated representation of an operator's ``config.yaml``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operator: OperatorMeta
    defaults: Dict[str, OperatorDefault]
    methodology: OperatorMethodologyMeta
    # PR-2 of the open-DAG PoC: each operator's `config.yaml` carries a
    # `card:` block with the LLM-grade description content the L3
    # Composer's prompt embeds (when-to-use, when-NOT-to-use, sibling
    # cross-references, example shapes, etc.).  OperatorConfig itself
    # treats the block opaquely — its schema is owned and validated by
    # ``shared.workflow.operator_catalogue`` (the catalogue renderer
    # reads the YAML directly).  Declaring the field here just lifts
    # the `extra="forbid"` rejection so the operator's own runtime
    # config-identity check (_check_config_identity) keeps working
    # alongside the new card content.  Optional so legacy fixtures
    # without a card block still load.
    card: Optional[Dict[str, Any]] = None

    def default_value(self, key: str) -> Any:
        """Return the scalar default for a variant key, or raise."""
        if key not in self.defaults:
            raise OperatorConfigError(
                f"OperatorConfig for {self.operator.name!r} has no default "
                f"named {key!r}.  Available: {sorted(self.defaults)}."
            )
        return self.defaults[key].value


# ============================================================================
# LOADER (process-wide cache, mirrors tool_config)
# ============================================================================


_OPERATOR_CONFIG_CACHE: Dict[Path, OperatorConfig] = {}


def load_operator_config(path: Path) -> OperatorConfig:
    """Read + validate an operator config YAML.

    Process-wide cached on absolute path — subsequent calls return the
    same frozen ``OperatorConfig`` instance.  Tests that mutate-and-
    rewrite YAMLs should call ``clear_operator_config_cache()`` between
    cases.
    """
    abs_path = path.resolve()
    if abs_path in _OPERATOR_CONFIG_CACHE:
        return _OPERATOR_CONFIG_CACHE[abs_path]

    if not abs_path.is_file():
        raise OperatorConfigError(
            f"Operator config not found: {abs_path}"
        )

    try:
        raw = yaml.safe_load(abs_path.read_text())
    except yaml.YAMLError as exc:
        raise OperatorConfigError(
            f"Operator config {abs_path} is not valid YAML: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise OperatorConfigError(
            f"Operator config {abs_path} must be a top-level mapping; "
            f"got {type(raw).__name__}."
        )

    try:
        cfg = OperatorConfig.model_validate(raw)
    except Exception as exc:
        raise OperatorConfigError(
            f"Operator config {abs_path} failed schema validation: {exc}"
        ) from exc

    _OPERATOR_CONFIG_CACHE[abs_path] = cfg
    return cfg


def clear_operator_config_cache() -> None:
    """Reset the process-wide cache — primarily for tests."""
    _OPERATOR_CONFIG_CACHE.clear()


# ============================================================================
# CONFIG IDENTITY CHECK (OPR12 — name AND version)
# ============================================================================


def _check_config_identity(config: Any, name: str, version: str) -> None:
    """Assert a loaded ``OperatorConfig`` matches the calling operator's
    identity — BOTH ``name`` AND ``version`` (OPR12).

    Every v2.0 operator calls this immediately after loading its config
    so a foreign config (wrong ``name``) or a drifted YAML (wrong
    ``version``) surfaces loudly via ``OperatorConfigError`` instead of
    silently supplying the wrong defaults.  The version check matters
    because ``_OPERATOR_VERSION`` is folded into the lineage hash — a
    YAML version that diverges from the module constant would break
    replay identity (OPR12 / P4).
    """
    if not isinstance(config, OperatorConfig):
        raise OperatorConfigError(
            f"{name}: 'config' must be an OperatorConfig instance; "
            f"got {type(config).__name__}."
        )
    if config.operator.name != name:
        raise OperatorConfigError(
            f"{name}: config name mismatch — expected {name!r}, got "
            f"{config.operator.name!r}."
        )
    if config.operator.version != version:
        raise OperatorConfigError(
            f"{name}: config version mismatch — expected {version!r}, "
            f"got {config.operator.version!r}.  The lineage hash folds "
            "_OPERATOR_VERSION, so a drifted YAML version breaks replay "
            "identity (OPR12 / P4)."
        )


__all__ = [
    "OperatorMethodFamily",
    "OperatorDefault",
    "OperatorMeta",
    "OperatorMethodologyMeta",
    "OperatorConfig",
    "OperatorConfigError",
    "load_operator_config",
    "clear_operator_config_cache",
    "_check_config_identity",
]
