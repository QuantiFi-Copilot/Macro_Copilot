"""orchestrator.domain_registry — PR-10F Codex audit gap #2 corrective.

The single source of truth for the closed family of instrument domains
the orchestrator can route to.  Replaces the previous hardcoded-in-five-
places enumeration (orchestrator/contracts.py Domain enum,
orchestrator/config.py DOMAIN_MCP_SERVERS, orchestrator/session.py
_DOMAIN_PROMPTS, orchestrator/open_dag/resolver_keys.py KNOWN_DOMAINS,
and orchestrator/prompts.py per-domain cards) with a single per-folder
declaration pattern: each ``rates_agent/<domain>/__init__.py`` declares
its own metadata, and discovery at import time builds the canonical
``DOMAIN_SPECS`` mapping.

Adding the Nth domain is now genuinely registration-only:

  1. Drop a folder ``rates_agent/<new_domain>/``
  2. In its ``__init__.py``, declare the five required constants:
        __domain_id__ : str
        __domain_label__ : str
        __mcp_server_module__ : str
        __mcp_client_key__ : str
        __resolver_key_convention__ : Literal['bare', 'prefixed']
  3. Add a ``mcp_server.py`` exposing the @mcp.tool() functions.

Zero source edits to ``orchestrator/contracts.py``, ``orchestrator/
config.py``, or ``orchestrator/open_dag/resolver_keys.py`` — the
Domain enum, the per-domain MCP-server config, and the resolver-key
convention sets are all built from DOMAIN_SPECS at import time.

The per-domain SUPERVISOR routing card and SYSTEM_PROMPT content
still ship as content in ``orchestrator/prompts.py`` today (one ADR
revisit could move those onto each domain's ``__init__.py`` as
``__domain_card__`` / ``__domain_child_prompt__`` constants the
prompt is templated from); that's a content-migration follow-up,
not a code-surface gap.

Import-order discipline
-----------------------
This module imports NOTHING from ``orchestrator.contracts``,
``orchestrator.config``, ``orchestrator.prompts``, or
``orchestrator.session`` — those are its CONSUMERS.  It only imports
from stdlib + ``rates_agent`` itself.  This is what lets
``orchestrator.contracts.Domain`` be built from ``DOMAIN_SPECS`` at
the moment the enum class is constructed.

Discovery is a single pass at module-import time.  Folders missing
required constants are SKIPPED with a logger.warning naming the
missing attribute.  Folders that explicitly opt out via
``__domain_skip__ = True`` are silently skipped (lets
``rates_agent/workflows/`` and friends sit as siblings without being
mistaken for domains).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Literal


logger = logging.getLogger(__name__)


# ============================================================================
# DOMAIN SPEC — typed record per discovered domain
# ============================================================================


@dataclass(frozen=True)
class DomainSpec:
    """Frozen record holding one domain's discovered metadata.

    All five fields are required for a folder to be classified as a
    domain.  Folders missing any required field are skipped at
    discovery (with a logger.warning).
    """

    domain_id: str
    domain_label: str
    mcp_server_module: str
    mcp_client_key: str
    resolver_key_convention: Literal["bare", "prefixed"]


# ============================================================================
# DISCOVERY
# ============================================================================


_REQUIRED_CONSTANTS = (
    "__domain_id__",
    "__domain_label__",
    "__mcp_server_module__",
    "__mcp_client_key__",
    "__resolver_key_convention__",
)


def _discover_domains() -> Dict[str, DomainSpec]:
    """Scan ``rates_agent/`` for sub-packages declaring domain metadata.

    Returns a sorted-by-id dict of DomainSpec.  Sub-packages without
    the required constants are skipped (logger.warning).  Sub-packages
    declaring ``__domain_skip__ = True`` are silently skipped.

    Side-effect: imports every discovered sub-package (so its
    ``__init__.py`` runs).  Idempotent.
    """
    discovered: Dict[str, DomainSpec] = {}

    try:
        import rates_agent
    except ImportError:
        logger.warning(
            "domain_registry: cannot import rates_agent; no domains "
            "will be discovered.",
        )
        return discovered

    for module_info in pkgutil.iter_modules(rates_agent.__path__):
        if not module_info.ispkg:
            continue
        full_name = f"rates_agent.{module_info.name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as exc:  # noqa: BLE001 — defensive on third-party imports
            logger.warning(
                "domain_registry: failed to import %s: %s — skipping",
                full_name, exc,
            )
            continue

        if getattr(mod, "__domain_skip__", False):
            continue

        missing = [c for c in _REQUIRED_CONSTANTS if not hasattr(mod, c)]
        if missing:
            # Silent-skip for known non-domain sub-packages
            # (workflows, playbooks, storage) AND any future sibling
            # that simply doesn't declare itself a domain.  Surfacing
            # this as a warning would create noise on every import.
            continue

        spec = DomainSpec(
            domain_id=getattr(mod, "__domain_id__"),
            domain_label=getattr(mod, "__domain_label__"),
            mcp_server_module=getattr(mod, "__mcp_server_module__"),
            mcp_client_key=getattr(mod, "__mcp_client_key__"),
            resolver_key_convention=getattr(
                mod, "__resolver_key_convention__",
            ),
        )
        if spec.resolver_key_convention not in ("bare", "prefixed"):
            logger.warning(
                "domain_registry: %s declares unknown "
                "resolver_key_convention=%r (expected 'bare' or "
                "'prefixed'); skipping.",
                full_name, spec.resolver_key_convention,
            )
            continue
        if spec.domain_id in discovered:
            logger.warning(
                "domain_registry: duplicate domain_id %r (already "
                "registered); skipping %s.",
                spec.domain_id, full_name,
            )
            continue
        discovered[spec.domain_id] = spec

    # Sort by id for deterministic iteration order (Enum members,
    # prompt-block rendering, KNOWN_DOMAINS derivation, etc.).
    return dict(sorted(discovered.items()))


# ============================================================================
# DOMAIN_SPECS — populated at module import.  THIS IS THE REGISTRY.
# ============================================================================


DOMAIN_SPECS: Dict[str, DomainSpec] = _discover_domains()


# ============================================================================
# DERIVED HELPERS — consumers prefer these over rebuilding from DOMAIN_SPECS.
# ============================================================================


def domain_id_set() -> FrozenSet[str]:
    """Frozenset of every discovered domain id."""
    return frozenset(DOMAIN_SPECS.keys())


def prefixed_domain_ids() -> FrozenSet[str]:
    """The set of discovered domains using the 'prefixed' resolver-key
    convention (i.e. their primitive lookup keys must carry a
    ``<domain>_`` prefix to disambiguate from other domains)."""
    return frozenset(
        spec.domain_id
        for spec in DOMAIN_SPECS.values()
        if spec.resolver_key_convention == "prefixed"
    )


def bare_domain_ids() -> FrozenSet[str]:
    """The set of discovered domains using the 'bare' resolver-key
    convention (primitive lookup keys equal the MCP tool name)."""
    return frozenset(
        spec.domain_id
        for spec in DOMAIN_SPECS.values()
        if spec.resolver_key_convention == "bare"
    )


def resolver_key_convention(domain_id: str) -> Literal["bare", "prefixed"]:
    """Look up the resolver-key convention for a given domain."""
    if domain_id not in DOMAIN_SPECS:
        raise KeyError(
            f"domain_registry: unknown domain_id {domain_id!r}; "
            f"known = {sorted(DOMAIN_SPECS.keys())}"
        )
    return DOMAIN_SPECS[domain_id].resolver_key_convention


def domain_ids_sorted() -> List[str]:
    """Sorted list of every discovered domain id."""
    return sorted(DOMAIN_SPECS.keys())


# ============================================================================
# TEST-ONLY MUTATION API
# ============================================================================
#
# The registration-only-domain-growth proof drops a synthetic
# rates_agent/<fixture>/ folder during the test, then needs to
# refresh DOMAIN_SPECS without restarting Python.  These helpers
# exist for that proof; production code never calls them.


def refresh_discovery() -> Dict[str, DomainSpec]:
    """Re-run discovery and replace DOMAIN_SPECS in place.

    Test-only.  Used by the registration-only-domain-growth proof.
    Returns the new mapping.
    """
    global DOMAIN_SPECS
    fresh = _discover_domains()
    DOMAIN_SPECS.clear()
    DOMAIN_SPECS.update(fresh)
    return DOMAIN_SPECS
