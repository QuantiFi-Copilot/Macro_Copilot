"""shared.workflow.resolver_keys — substrate-owned domain-to-resolver-key adapter.

Centralises the convention that translates a ``(domain, mcp_tool_name)``
pair into the key the global ``PrimitiveResolver`` is registered under.

Why this module exists
----------------------
Today's global resolver (``rates_agent.workflows.rates_primitive_resolver``)
already disambiguates a real collision: the MCP tool name
``get_futures_price_level_tool`` exists in BOTH ``policy_futures/`` and
``bond_futures/`` MCP servers.  P11 keeps the names safe inside each
domain's MCP subprocess (each L2 selector only sees its own server),
but the global resolver dispatch needs ONE canonical key per primitive.
The existing ad-hoc convention is:

  - ``bond_futures`` primitives keep their bare MCP tool names.
  - ``policy_futures`` primitives get a ``policy_futures_`` prefix.
  - Every other domain keeps bare MCP tool names.

That convention is correct for today's universe but lives implicitly in
``rates_agent/workflows/__init__.py``.  PR-3 of the open-DAG PoC needs a
single deterministic seam where ``BoundLeaf`` (the L2 Selector's output)
gets translated from the visible ``mcp_tool_name`` it bound to into the
``resolver_tool_key`` the executor dispatches on.  That seam is this
module.

A future cleanup to a uniform ``<domain>::<tool>`` convention is then a
one-file change here — every other PR (Composer, Selectors, Assembler,
Executor) stays unchanged.

Finance-blindness (P9 / P11 / R10)
----------------------------------
This module sits in ``shared/workflow/`` and is part of the substrate.
It does NOT import from ``rates_agent/`` (P11) or from
``orchestrator/`` (one layer above the substrate).  The closed set of
known domains is declared HERE as plain strings — exactly the same
strings the orchestrator's ``Domain`` enum already uses for its
``.value`` field, so callers in the orchestrator can pass
``Domain.POLICY_FUTURES.value`` (or its raw string equivalent) and the
substrate validates against the set without taking a typed dependency
on the orchestrator's enum.  Per P10, when the orchestrator's enum is
extended (an ADR-recorded decision), the substrate's known-domain set
must be updated in lock-step in the same PR.

Closed-family discipline (P8)
-----------------------------
``KNOWN_DOMAINS`` is a closed set.  Extending it requires:
  1. The orchestrator's ``Domain`` enum gains the new value (an ADR).
  2. ``_DOMAIN_PREFIXED`` or ``_BARE_NAME_DOMAINS`` gains the new entry
     in this file.
  3. The global resolver in ``rates_agent/workflows/__init__.py`` (or
     its successor) registers the new domain's primitives with the
     matching key convention.
  4. The PR-3 tests in ``tests/workflow/test_resolver_keys.py`` are
     extended to cover the new domain.

The substrate ships the FULL set up-front so a typo at L1 or L2 surfaces
deterministically here, not as a confusing resolver miss inside the
executor.

Scaling-claim boundary (PR-10E Codex audit gap #5)
--------------------------------------------------
This file is part of the orchestrator's CONFIG SURFACE, not its
CODE SURFACE.  The PoC's 'registration-only growth' invariant
(``tmp/orchestration.md`` §0) covers primitive + operator growth
only.  Adding the Nth domain is an ADR-gated source change that
updates this file in lock-step with ``orchestrator/contracts.py``
(Domain enum), ``orchestrator/config.py`` (DOMAIN_MCP_SERVERS),
``orchestrator/prompts.py`` (new ``<DOMAIN>_SYSTEM_PROMPT`` constant
+ extension to SUPERVISOR_SYSTEM_PROMPT's AVAILABLE DOMAINS /
DOMAIN SIGNALS cards), and ``orchestrator/session.py``
(``_DOMAIN_PROMPTS`` + ``_build_domain_boundaries`` labels).

The orchestrator's CODE SURFACE — composer, validator, executor,
coverage gate, Boundary A / B, synthesis — stays byte-for-byte
unchanged on domain growth.  This file is DELIBERATELY excluded
from ``tests/eval/test_scaling_proofs.py``'s registration-only
proof's invariant-files list because it (correctly) needs to grow
when a domain is added.
"""

from __future__ import annotations

from typing import FrozenSet


# ============================================================================
# CLOSED DOMAIN FAMILY
# ============================================================================
#
# These string values mirror ``orchestrator.contracts.Domain``'s
# ``.value`` field for each enum member.  Per the module docstring, the
# substrate carries the strings rather than the typed enum to keep the
# layering clean (substrate → orchestrator import would invert the
# dependency direction).  When the orchestrator's Domain enum is
# extended, update this file in lock-step (P10).


# Domains whose primitives are registered in the global resolver under
# a key prefixed with the domain name + underscore + the MCP tool name.
# Today only ``policy_futures`` is in this set; that's because two of
# its tools collide with ``bond_futures`` tool names by raw MCP name
# (``get_futures_price_level_tool``,
# ``get_futures_volume_oi_tool``-equivalent shapes), and the resolver
# uses the prefix to disambiguate.
_DOMAIN_PREFIXED: FrozenSet[str] = frozenset({
    "policy_futures",
})


# Domains whose primitives are registered under their bare MCP tool name
# (no domain prefix).  Five today: every domain except ``policy_futures``.
_BARE_NAME_DOMAINS: FrozenSet[str] = frozenset({
    "sovereign_bonds",
    "ois",
    "inflation_indexed_bonds",
    "inflation_swaps",
    "bond_futures",
})


# The closed set of known domains.  Disjoint union of the two convention
# sets; ``_DOMAIN_PREFIXED ∩ _BARE_NAME_DOMAINS`` MUST be empty (each
# domain follows exactly one convention).  A module-import-time assert
# below enforces the invariant so a registration drift surfaces at
# import, not at validation time.
KNOWN_DOMAINS: FrozenSet[str] = _DOMAIN_PREFIXED | _BARE_NAME_DOMAINS


assert not (_DOMAIN_PREFIXED & _BARE_NAME_DOMAINS), (
    "shared.workflow.resolver_keys: a domain may follow EITHER the "
    "prefixed OR the bare-name convention, never both.  Intersection: "
    f"{sorted(_DOMAIN_PREFIXED & _BARE_NAME_DOMAINS)}.  Fix by removing "
    "the offending entry from one of the two sets."
)


# ============================================================================
# ERRORS
# ============================================================================


class UnknownDomainError(ValueError):
    """Raised by ``domain_to_resolver_key`` when called with a domain
    name not in ``KNOWN_DOMAINS``.  Subclasses ``ValueError`` so
    upstream ``except ValueError`` handlers (P6 transport-boundary
    pattern) catch it without special-casing."""


# ============================================================================
# THE ADAPTER
# ============================================================================


def domain_to_resolver_key(domain: str, mcp_tool_name: str) -> str:
    """Translate ``(domain, mcp_tool_name)`` into the key the global
    ``PrimitiveResolver`` is registered under.

    Centralised so a future migration to uniform ``<domain>::<tool>``
    namespacing is a one-file change.  Every PR-3+ component that
    constructs a ``BoundLeaf`` MUST go through this function — never
    hand-roll the prefixing.

    Parameters
    ----------
    domain :
        One of ``KNOWN_DOMAINS``.  Pass the ``.value`` string of an
        ``orchestrator.contracts.Domain`` enum member, or the raw
        string directly.  An unknown value raises ``UnknownDomainError``.
    mcp_tool_name :
        The visible tool name the L2 Selector saw via its MCP
        subprocess (e.g. ``"calculate_curve_spread_tool"``,
        ``"get_futures_price_level_tool"``).

    Returns
    -------
    str
        The resolver-safe key.  Equal to ``mcp_tool_name`` for bare-name
        domains; equal to ``f"{domain}_{mcp_tool_name}"`` for prefixed
        domains.

    Raises
    ------
    UnknownDomainError
        If ``domain`` is not in ``KNOWN_DOMAINS``.

    Examples
    --------
    The single canonical collision:

        >>> domain_to_resolver_key("policy_futures", "get_futures_price_level_tool")
        'policy_futures_get_futures_price_level_tool'
        >>> domain_to_resolver_key("bond_futures", "get_futures_price_level_tool")
        'get_futures_price_level_tool'
    """
    if domain in _DOMAIN_PREFIXED:
        return f"{domain}_{mcp_tool_name}"
    if domain in _BARE_NAME_DOMAINS:
        return mcp_tool_name
    raise UnknownDomainError(
        f"domain_to_resolver_key: unknown domain {domain!r}.  Known "
        f"domains: {sorted(KNOWN_DOMAINS)}.  If a new domain has been "
        "added to orchestrator.contracts.Domain, update "
        "shared.workflow.resolver_keys to declare its keying "
        "convention (bare-name OR prefixed) in lock-step (P10)."
    )


__all__ = [
    "KNOWN_DOMAINS",
    "UnknownDomainError",
    "domain_to_resolver_key",
]
