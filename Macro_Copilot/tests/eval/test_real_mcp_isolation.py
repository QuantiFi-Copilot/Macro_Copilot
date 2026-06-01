"""tests/eval/test_real_mcp_isolation.py — PR-10D Codex F3.

REAL MCP context-isolation audit.  Codex's complaint about the
PR-10B context-bound proof: the prior test asserted dispatch behaviour
on mock SelectorCallbacks, NOT that each REAL DomainAgentSession's
L2 selector catalogue actually saw only its own MCP server's tools.

This test opens each real DomainAgentSession (with the production
``rates_primitive_resolver``), captures the MCP-visible tool list
that the selector catalogue was built from, and asserts the P11
isolation invariant:

  - Every domain's tool list is non-empty.
  - Every domain's tool list is DISJOINT from every other domain's
    (no primitive name from domain X appears in domain Y's
    selector catalogue).
  - Dropped tools (TERMINAL_ONLY_SNAPSHOT primitives the open-DAG
    executor can't bridge) are reported EXPLICITLY in
    `_dropped_tools`, not silently hidden.

NO LLM calls.  Opens MCP subprocesses, lets them enumerate their
tool registrations, and inspects the resulting per-domain
catalogues.  The plan §6 Proof #2(a) — "each L2 selector's context
contained ONLY its own domain's tool count (P11 held)" — is now
proven against the actual MCP servers, not a mock dispatch.

Skipping discipline
===================

Opening MCP subprocesses spawns child processes per domain — this
test is slow (several seconds).  Skip behind the
``PR10D_MCP_ISOLATION`` env var by default so CI doesn't pay the
cost on every PR.  Set the env var locally OR in a nightly job to
run the audit.
"""

from __future__ import annotations

import os
from typing import Dict, List, Set

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("PR10D_MCP_ISOLATION", "").lower() not in ("1", "true", "yes"),
    reason=(
        "Real MCP isolation audit spawns per-domain subprocesses "
        "(slow).  Set PR10D_MCP_ISOLATION=1 to run."
    ),
)


@pytest.fixture(scope="module")
def opened_domain_sessions():
    """Open one DomainAgentSession per registered domain with the
    production rates_primitive_resolver.  Returns a dict
    {domain: session} the tests use to inspect catalogues."""
    import asyncio

    from orchestrator.config import (
        DOMAIN_MCP_SERVERS, LLM_MAX_TOKENS, LLM_MODEL, LLM_TEMPERATURE,
    )
    from orchestrator.contracts import Domain
    from orchestrator.domain_agent import DomainAgentSession
    from orchestrator.prompts import (
        BOND_FUTURES_SYSTEM_PROMPT,
        INFLATION_INDEXED_BONDS_SYSTEM_PROMPT,
        INFLATION_SWAPS_SYSTEM_PROMPT,
        OIS_SYSTEM_PROMPT,
        POLICY_FUTURES_SYSTEM_PROMPT,
        SOVEREIGN_BONDS_SYSTEM_PROMPT,
    )
    from rates_agent.workflows import rates_primitive_resolver

    _DOMAIN_PROMPTS = {
        Domain.SOVEREIGN_BONDS: SOVEREIGN_BONDS_SYSTEM_PROMPT,
        Domain.INFLATION_INDEXED_BONDS: INFLATION_INDEXED_BONDS_SYSTEM_PROMPT,
        Domain.INFLATION_SWAPS: INFLATION_SWAPS_SYSTEM_PROMPT,
        Domain.OIS: OIS_SYSTEM_PROMPT,
        Domain.POLICY_FUTURES: POLICY_FUTURES_SYSTEM_PROMPT,
        Domain.BOND_FUTURES: BOND_FUTURES_SYSTEM_PROMPT,
    }

    sessions: Dict[Domain, DomainAgentSession] = {}
    for domain in Domain:
        if domain not in DOMAIN_MCP_SERVERS or domain not in _DOMAIN_PROMPTS:
            continue
        sess = DomainAgentSession(
            domain=domain,
            system_prompt=_DOMAIN_PROMPTS[domain],
            mcp_servers=DOMAIN_MCP_SERVERS[domain],
            model_name=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
            primitive_resolver=rates_primitive_resolver,
        )
        asyncio.get_event_loop().run_until_complete(sess.open())
        sessions[domain] = sess

    yield sessions

    # Teardown: close every session's MCP subprocess.
    for sess in sessions.values():
        try:
            asyncio.get_event_loop().run_until_complete(sess.close())
        except Exception:
            pass


def test_every_domain_session_opens_with_nonempty_mcp_tools(
    opened_domain_sessions,
):
    """Each opened DomainAgentSession exposes ``_tool_names`` (the
    raw MCP-visible tool list).  Per P11 each domain MUST see a
    non-empty tool set — its own."""
    for domain, sess in opened_domain_sessions.items():
        assert sess._tool_names, (
            f"PR-10D F3: domain {domain.value!r} opened with EMPTY "
            "MCP tool list — P11 isolation can't be audited if the "
            "domain's own MCP server registered no tools"
        )


def test_every_pair_of_domains_has_disjoint_mcp_tools(
    opened_domain_sessions,
):
    """The P11 isolation invariant: each domain's MCP-visible tool
    list is DISJOINT from every other domain's.  No primitive name
    from domain X appears in domain Y's selector catalogue."""
    domains = list(opened_domain_sessions.items())
    for i, (da, sa) in enumerate(domains):
        tools_a: Set[str] = set(sa._tool_names)
        for db, sb in domains[i + 1:]:
            tools_b: Set[str] = set(sb._tool_names)
            overlap = tools_a & tools_b
            assert not overlap, (
                f"PR-10D F3: P11 violation — domain {da.value!r} and "
                f"domain {db.value!r} share MCP tool names "
                f"{sorted(overlap)!r}.  Each domain's MCP server "
                "must register a disjoint tool name set."
            )


def test_selector_catalogue_per_domain_owns_only_its_tools(
    opened_domain_sessions,
):
    """Each domain's selector catalogue (after composability
    filtering) is a SUBSET of that domain's MCP-visible tool list —
    NOT a superset, NOT a foreign-domain leak."""
    for domain, sess in opened_domain_sessions.items():
        catalogue_names = {entry.mcp_tool_name for entry in sess._tool_catalogue}
        domain_mcp_names = set(sess._tool_names)
        # Catalogue is a subset of the MCP-visible tools.
        foreign = catalogue_names - domain_mcp_names
        assert not foreign, (
            f"PR-10D F3: domain {domain.value!r} selector catalogue "
            f"contains tools NOT in its MCP-visible list: "
            f"{sorted(foreign)!r}"
        )


def test_dropped_tools_are_reported_explicitly_not_silently_hidden(
    opened_domain_sessions,
):
    """Per PR-6A F4 the catalogue filter reports drops with a
    structured DroppedToolEntry (reason + composability).  Audit
    that for every domain, every MCP-visible tool either is in
    the kept catalogue OR appears in the dropped list — nothing
    is silently hidden."""
    for domain, sess in opened_domain_sessions.items():
        all_mcp = set(sess._tool_names)
        kept = {e.mcp_tool_name for e in sess._tool_catalogue}
        dropped = {e.mcp_tool_name for e in sess._dropped_tools}
        accounted = kept | dropped
        # Every MCP tool is accounted for in EITHER kept or dropped.
        missing = all_mcp - accounted
        assert not missing, (
            f"PR-10D F3: domain {domain.value!r} silently dropped "
            f"MCP tools: {sorted(missing)!r}.  Every tool must be "
            "either kept (visible to the Selector LLM) or in the "
            "dropped list (with a structured reason)."
        )


def test_dropped_tools_carry_structured_reason(opened_domain_sessions):
    """Each dropped tool entry carries a non-empty reason string +
    typically a composability classification.  Lets observability /
    debug surfaces explain why a tool isn't visible to the L2
    Selector LLM."""
    for domain, sess in opened_domain_sessions.items():
        for entry in sess._dropped_tools:
            assert entry.reason and entry.reason.strip(), (
                f"PR-10D F3: domain {domain.value!r} dropped tool "
                f"{entry.mcp_tool_name!r} with EMPTY reason"
            )
