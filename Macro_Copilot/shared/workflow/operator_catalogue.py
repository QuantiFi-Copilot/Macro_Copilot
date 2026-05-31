"""shared.workflow.operator_catalogue — the L3 Composer's operator menu.

Renders one ``OperatorCard`` per operator in ``OPERATOR_REGISTRY`` by
fusing two single-source-of-truth surfaces:

  1. Description content (one_line, when-to-use, when-NOT-to-use,
     upstream requirements, downstream pattern, knobs quick-reference,
     example shapes, sibling-operator cross-references) lives ONLY in
     each operator's bundled ``config.yaml`` under the ``card:`` block.
  2. Slot / output structure (artifact types per input slot, the
     operator's output artifact type, whether a slot accepts scalars,
     whether a slot is list-shaped) lives ONLY in ``OPERATOR_REGISTRY``
     via ``SlotDescriptor`` / ``OutputDescriptor``.

The renderer combines both at L3-prompt-build time.  Neither surface
duplicates the other's content — P10 single source of truth (per the
PR-2 acceptance criteria in ``tmp/orchestration.md``).

Why a renderer, not a stored attribute on ``OperatorSpec``
----------------------------------------------------------
- Description content is verbose YAML; pre-loading it into the
  registry would bloat ``OPERATOR_REGISTRY`` and force every reader of
  the registry to pay the YAML-parse cost even when only the slot
  structure is needed.
- Keeping the YAML as the source of truth lets an authoring workflow
  edit the card without restarting the process / re-importing
  modules.
- The renderer is deterministic (no clock, no randomness), so its
  output IS the cacheable artifact L3's Anthropic prompt cache pins.

Caching
-------
``render_operator_card`` is process-cached on ``(operator_name,
config_path_mtime)``.  ``render_operator_catalogue`` is a one-call
helper that loops over ``OPERATOR_REGISTRY`` and returns a frozen
``dict[str, OperatorCard]``.  Cache invalidation is automatic on YAML
changes (mtime-keyed), explicit via ``clear_catalogue_cache()`` for
test isolation.

P9 / P11 invariants
-------------------
- This module does NOT import from ``rates_agent/`` or any
  domain-specific path.
- The rendered cards reference operator names + slot names only — no
  primitive names appear anywhere in this module's output, by design
  (the Composer never sees primitives).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field

from shared.workflow.registry import OPERATOR_REGISTRY, OperatorSpec
from shared.workflow.slots import OutputDescriptor, SlotDescriptor


# ============================================================================
# CONTRACT SHAPES
# ============================================================================


class ExampleShape(BaseModel):
    """One worked example of how the operator fits into a typical DAG.

    Authored in the operator's ``config.yaml`` under
    ``card.example_shapes``.  Surfaced verbatim to the L3 Composer's
    prompt so the few-shot pattern is grounded in the operator's own
    documented examples (no out-of-band example registry).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = Field(..., min_length=1)
    shape: str = Field(..., min_length=1)


class OperatorCard(BaseModel):
    """The L3 Composer's view of one operator.

    Frozen.  Fields are split between *description content* (sourced
    from the operator's ``config.yaml`` ``card:`` block) and
    *structural metadata* (sourced from ``OperatorSpec`` in
    ``OPERATOR_REGISTRY``).  The renderer is responsible for keeping
    them in sync; tests assert no content duplication between the two
    surfaces.

    The card is intentionally close in shape to the YAML so a reader
    can compare side-by-side with the authoring source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity (echoed from registry; cheap to carry on the card).
    operator_name: str = Field(..., min_length=1)

    # ---- Description content (config.yaml-sourced) ---------------
    one_line: str = Field(..., min_length=1)
    when_to_use: Tuple[str, ...] = Field(..., min_length=1)
    when_not_to_use: Tuple[str, ...] = Field(..., min_length=1)
    upstream_requirements: Tuple[str, ...] = ()
    downstream_pattern: Tuple[str, ...] = ()
    knobs_quick_ref: Dict[str, str] = Field(default_factory=dict)
    example_shapes: Tuple[ExampleShape, ...] = ()
    sibling_operators: Dict[str, str] = Field(default_factory=dict)

    # ---- Structural metadata (registry-sourced) ------------------
    input_slots: Dict[str, SlotDescriptor]
    output: OutputDescriptor


# ============================================================================
# ERRORS
# ============================================================================


class OperatorCardError(ValueError):
    """Raised when an operator's ``config.yaml`` is missing the
    ``card:`` block or has a malformed entry."""


# ============================================================================
# YAML PARSING HELPERS
# ============================================================================


def _normalise_sibling_operators(raw: Any) -> Dict[str, str]:
    """Accept the YAML list-of-single-key-dicts shape OR a flat dict
    and normalise to ``Dict[str, str]``.

    The YAML authoring shape (per ``tmp/orchestration.md`` §PR-2) is:

        sibling_operators:
          - rolling_correlation: "..."
          - cointegration:       "..."

    which PyYAML parses to ``[{rolling_correlation: ...}, {...}]``.
    Some authors will write the flat dict shape instead; the renderer
    accepts both so authoring doesn't fight the parser.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out: Dict[str, str] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise OperatorCardError(
                    f"sibling_operators entry must be a single-key mapping; "
                    f"got {type(entry).__name__}: {entry!r}"
                )
            if len(entry) != 1:
                raise OperatorCardError(
                    f"sibling_operators entry must have exactly one key; "
                    f"got {list(entry.keys())}"
                )
            (k, v), = entry.items()
            out[str(k)] = str(v)
        return out
    raise OperatorCardError(
        f"sibling_operators must be a list or a mapping; got "
        f"{type(raw).__name__}"
    )


def _normalise_example_shapes(raw: Any) -> Tuple[ExampleShape, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise OperatorCardError(
            f"example_shapes must be a list; got {type(raw).__name__}"
        )
    out: List[ExampleShape] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise OperatorCardError(
                f"example_shapes entry must be a mapping with "
                f"`description` and `shape` keys; got {entry!r}"
            )
        try:
            out.append(
                ExampleShape(
                    description=str(entry["description"]),
                    shape=str(entry["shape"]),
                )
            )
        except KeyError as exc:
            raise OperatorCardError(
                f"example_shapes entry missing required key {exc.args[0]!r}: "
                f"{entry!r}"
            ) from exc
    return tuple(out)


def _normalise_string_tuple(raw: Any, field: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise OperatorCardError(
            f"{field} must be a list of strings; got {type(raw).__name__}"
        )
    out: List[str] = []
    for entry in raw:
        if not isinstance(entry, (str, int, float)):
            raise OperatorCardError(
                f"{field} entry must be a string; got {type(entry).__name__}: "
                f"{entry!r}"
            )
        out.append(str(entry))
    return tuple(out)


def _normalise_knobs(raw: Any) -> Dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise OperatorCardError(
            f"knobs_quick_ref must be a mapping; got {type(raw).__name__}"
        )
    return {str(k): str(v) for k, v in raw.items()}


# ============================================================================
# CACHE
# ============================================================================


# Cache key: (operator_name, config_path, mtime_ns).  mtime invalidates
# automatically when the YAML changes on disk (covers an authoring
# workflow that edits a card and re-runs without restarting the
# interpreter).
_CARD_CACHE: Dict[Tuple[str, str, int], OperatorCard] = {}


def clear_catalogue_cache() -> None:
    """Drop every cached card.  Test isolation hook."""
    _CARD_CACHE.clear()


# ============================================================================
# RENDERING
# ============================================================================


def _load_card_block(config_path: Path) -> Dict[str, Any]:
    """Parse the operator's ``config.yaml`` and return the ``card:``
    sub-block, or raise ``OperatorCardError`` if missing."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OperatorCardError(
            f"could not read operator config at {config_path}: {exc}"
        ) from exc

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OperatorCardError(
            f"operator config at {config_path} is not valid YAML: {exc}"
        ) from exc

    if not isinstance(doc, dict):
        raise OperatorCardError(
            f"operator config at {config_path} must be a YAML mapping; "
            f"got {type(doc).__name__}"
        )
    card = doc.get("card")
    if card is None:
        raise OperatorCardError(
            f"operator config at {config_path} is missing the `card:` "
            "block.  Per PR-2 of the open-DAG PoC, every registered "
            "operator must declare a card block.  See "
            "`tmp/orchestration.md` §PR-2 for the schema."
        )
    if not isinstance(card, dict):
        raise OperatorCardError(
            f"`card:` block at {config_path} must be a mapping; got "
            f"{type(card).__name__}"
        )
    return card


def render_operator_card(
    operator_name: str,
    *,
    spec: Optional[OperatorSpec] = None,
) -> OperatorCard:
    """Render one operator's card by name.

    Looks up the spec in ``OPERATOR_REGISTRY`` if ``spec`` is not
    supplied (tests can inject a synthetic spec).  Reads the operator's
    ``config.yaml`` ``card:`` block, validates it, and fuses with the
    registry's slot / output structure.

    Process-cached by ``(operator_name, config_path, mtime_ns)`` so
    repeated calls within one process pay the YAML-parse cost exactly
    once until the file changes on disk.

    Raises
    ------
    KeyError
        If ``spec`` is not supplied and ``operator_name`` is not in
        ``OPERATOR_REGISTRY``.
    OperatorCardError
        If the config.yaml is missing, malformed, or its ``card:``
        block fails schema validation.
    """
    if spec is None:
        if operator_name not in OPERATOR_REGISTRY:
            raise KeyError(
                f"operator {operator_name!r} not in OPERATOR_REGISTRY"
            )
        spec = OPERATOR_REGISTRY[operator_name]

    config_path = spec.config_path

    # mtime-keyed cache so an in-flight YAML edit is picked up
    # automatically without explicit cache invalidation.
    try:
        mtime_ns = os.stat(config_path).st_mtime_ns
    except OSError:
        mtime_ns = 0
    cache_key = (operator_name, str(config_path), mtime_ns)
    cached = _CARD_CACHE.get(cache_key)
    if cached is not None:
        return cached

    card_block = _load_card_block(config_path)

    try:
        card = OperatorCard(
            operator_name=operator_name,
            one_line=str(card_block.get("one_line", "")).strip(),
            when_to_use=_normalise_string_tuple(
                card_block.get("when_to_use"), "when_to_use",
            ),
            when_not_to_use=_normalise_string_tuple(
                card_block.get("when_not_to_use"), "when_not_to_use",
            ),
            upstream_requirements=_normalise_string_tuple(
                card_block.get("upstream_requirements"),
                "upstream_requirements",
            ),
            downstream_pattern=_normalise_string_tuple(
                card_block.get("downstream_pattern"),
                "downstream_pattern",
            ),
            knobs_quick_ref=_normalise_knobs(
                card_block.get("knobs_quick_ref"),
            ),
            example_shapes=_normalise_example_shapes(
                card_block.get("example_shapes"),
            ),
            sibling_operators=_normalise_sibling_operators(
                card_block.get("sibling_operators"),
            ),
            input_slots=dict(spec.input_slots),
            output=spec.output,
        )
    except Exception as exc:
        raise OperatorCardError(
            f"failed to render card for operator {operator_name!r} from "
            f"{config_path}: {exc}"
        ) from exc

    _CARD_CACHE[cache_key] = card
    return card


def render_operator_catalogue() -> Dict[str, OperatorCard]:
    """Render one ``OperatorCard`` for every operator in
    ``OPERATOR_REGISTRY``.  Returns a fresh dict keyed by operator
    name.

    This is the artifact the L3 Composer's prompt will embed: the
    Composer sees the full catalogue on every compose call.  Per the
    plan (and per R3 / R4 from the rulings list in
    ``tmp/orchestration.md`` §1), NO shortlister filters the
    catalogue; the Composer reasons over the full set every time.
    The catalogue's total token footprint is bounded by the per-card
    cap enforced in PR-2's tests (≤600 tokens / card; total ≤10K
    tokens at 16 operators).

    Process-cached transitively via ``render_operator_card``'s
    mtime-keyed cache.  Test isolation hook:
    ``clear_catalogue_cache()``.
    """
    return {
        name: render_operator_card(name, spec=spec)
        for name, spec in OPERATOR_REGISTRY.items()
    }


# ============================================================================
# RENDERED CARD → PROMPT STRING (deterministic)
# ============================================================================
#
# The L3 Composer's prompt assembly (PR-7) will produce its own
# prompt-shaped rendering of each card.  This module ships a single
# canonical renderer here so:
#
#   (a) the token-budget test in PR-2 can measure a fixed,
#       deterministic surface,
#   (b) downstream PR-7 can either consume this rendering verbatim or
#       diverge with an ADR — but the default is shared.


def card_to_prompt_block(card: OperatorCard) -> str:
    """Render one ``OperatorCard`` as a deterministic plain-text block
    suitable for inclusion in the L3 Composer's prompt.  No
    YAML/JSON wrapping; sorted-key dict iteration so the output is
    byte-stable across runs (Anthropic-cache-friendly).
    """
    lines: List[str] = []
    lines.append(f"## {card.operator_name}")
    lines.append(card.one_line)
    lines.append("")
    if card.when_to_use:
        lines.append("USE WHEN:")
        for item in card.when_to_use:
            lines.append(f"  - {item}")
    if card.when_not_to_use:
        lines.append("DO NOT USE WHEN:")
        for item in card.when_not_to_use:
            lines.append(f"  - {item}")
    if card.upstream_requirements:
        lines.append("UPSTREAM:")
        for item in card.upstream_requirements:
            lines.append(f"  - {item}")
    if card.downstream_pattern:
        lines.append("DOWNSTREAM:")
        for item in card.downstream_pattern:
            lines.append(f"  - {item}")
    if card.input_slots:
        lines.append("INPUT SLOTS:")
        for slot_name in sorted(card.input_slots.keys()):
            slot = card.input_slots[slot_name]
            shape = "list" if slot.is_list else "single"
            scalar = "scalar-OK" if slot.accepts_scalar else "artifact-only"
            lines.append(
                f"  - {slot_name}: {slot.artifact_type.value} "
                f"({shape}, {scalar}) — {slot.description}"
            )
    lines.append(
        f"OUTPUT: {card.output.artifact_type.value} — {card.output.description}"
    )
    if card.knobs_quick_ref:
        lines.append("KNOBS:")
        for k in sorted(card.knobs_quick_ref.keys()):
            lines.append(f"  - {k}: {card.knobs_quick_ref[k]}")
    if card.example_shapes:
        lines.append("EXAMPLES:")
        for ex in card.example_shapes:
            lines.append(f"  - {ex.description}")
            lines.append(f"    {ex.shape}")
    if card.sibling_operators:
        lines.append("SIBLINGS:")
        for sibling in sorted(card.sibling_operators.keys()):
            lines.append(f"  - {sibling}: {card.sibling_operators[sibling]}")
    return "\n".join(lines)


# ============================================================================
# TOKEN APPROXIMATION
# ============================================================================
#
# The PR-2 token-budget test pins each card's prompt-form size to ≤600
# tokens.  We approximate token count by characters / 4, the standard
# rule-of-thumb for English text — accurate within ~20% for our
# domain.  Using a deterministic local approximation avoids depending
# on tiktoken / the Anthropic tokenizer SDK at test time.


def approx_tokens(text: str) -> int:
    """Approximate token count using the chars/4 rule-of-thumb.
    Accurate within ~20% for English technical prose; sufficient for
    the PR-2 token-budget acceptance criterion."""
    return max(1, (len(text) + 3) // 4)


__all__ = [
    "ExampleShape",
    "OperatorCard",
    "OperatorCardError",
    "render_operator_card",
    "render_operator_catalogue",
    "card_to_prompt_block",
    "approx_tokens",
    "clear_catalogue_cache",
]
