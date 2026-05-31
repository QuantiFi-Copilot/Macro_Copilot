"""tests/workflow/test_resolver_keys.py — PR-3 acceptance suite.

Covers ``shared.workflow.resolver_keys`` — the centralised seam that
translates a ``(domain, mcp_tool_name)`` pair into the resolver-safe
key the global ``PrimitiveResolver`` is registered under.

The plan (``tmp/orchestration.md`` §PR-3) specifies:

  1. ``domain_to_resolver_key`` covers all 6 known substrate domains.
  2. The canonical collision (``get_futures_price_level_tool`` exists
     in BOTH ``policy_futures/`` and ``bond_futures/`` MCP servers)
     resolves to DIFFERENT keys on either side.
  3. The rates resolver (``rates_agent.workflows.
     rates_primitive_resolver``) hands back DIFFERENT
     ``PrimitiveSpec``s for the two keys.
  4. Unknown domains raise ``UnknownDomainError``.
  5. ``KNOWN_DOMAINS`` is the disjoint union of the prefixed and
     bare-name sets (no domain follows both conventions).

Tests run fully offline.  The resolver-smoke test imports the live
rates resolver but never invokes a primitive callable — only metadata
lookup happens.
"""

from __future__ import annotations

import pytest

from orchestrator.open_dag.resolver_keys import (
    KNOWN_DOMAINS,
    UnknownDomainError,
    domain_to_resolver_key,
    _BARE_NAME_DOMAINS,
    _DOMAIN_PREFIXED,
)


# ============================================================================
# CLOSED-FAMILY DISCIPLINE (P8)
# ============================================================================


class TestKnownDomainsClosedFamily:
    def test_known_domains_size(self) -> None:
        # The substrate ships 6 known domains today.  Per P8 closed
        # family, extending requires (a) ADR, (b) updating
        # _DOMAIN_PREFIXED or _BARE_NAME_DOMAINS in lock-step with the
        # orchestrator's Domain enum, (c) bumping this assertion.
        assert len(KNOWN_DOMAINS) == 6, (
            f"KNOWN_DOMAINS size changed to {len(KNOWN_DOMAINS)}.  "
            "Per P8 + P10, extending requires an ADR + lock-step "
            "update of the orchestrator's Domain enum + this bump."
        )

    def test_known_domains_match_orchestrator_enum(self) -> None:
        """Lock-step check: every value in
        ``orchestrator.contracts.Domain`` must appear in
        ``KNOWN_DOMAINS`` and vice-versa.  Guards against silent drift
        when one side is edited without the other."""
        from orchestrator.contracts import Domain

        orchestrator_values = {d.value for d in Domain}
        assert orchestrator_values == KNOWN_DOMAINS, (
            "orchestrator.contracts.Domain values diverge from "
            "shared.workflow.resolver_keys.KNOWN_DOMAINS.\n"
            f"  Orchestrator only: {orchestrator_values - KNOWN_DOMAINS}\n"
            f"  Substrate only:    {KNOWN_DOMAINS - orchestrator_values}\n"
            "P10: keep them in lock-step in the same PR."
        )

    def test_prefixed_and_bare_are_disjoint(self) -> None:
        # The module asserts this at import time, but a runtime check
        # here catches a stale .pyc / hot-reload edge case.
        assert not (_DOMAIN_PREFIXED & _BARE_NAME_DOMAINS), (
            "_DOMAIN_PREFIXED and _BARE_NAME_DOMAINS must be disjoint."
        )

    def test_known_domains_is_their_union(self) -> None:
        assert KNOWN_DOMAINS == (_DOMAIN_PREFIXED | _BARE_NAME_DOMAINS)


# ============================================================================
# THE ADAPTER — happy path per domain
# ============================================================================


class TestDomainToResolverKey:
    @pytest.mark.parametrize("domain", sorted(_BARE_NAME_DOMAINS))
    def test_bare_name_domain_returns_tool_unchanged(
        self, domain: str,
    ) -> None:
        assert domain_to_resolver_key(domain, "some_tool") == "some_tool"
        assert domain_to_resolver_key(domain, "calculate_curve_spread_tool") == (
            "calculate_curve_spread_tool"
        )

    @pytest.mark.parametrize("domain", sorted(_DOMAIN_PREFIXED))
    def test_prefixed_domain_returns_prefix_plus_tool(
        self, domain: str,
    ) -> None:
        assert domain_to_resolver_key(domain, "some_tool") == f"{domain}_some_tool"

    def test_unknown_domain_raises(self) -> None:
        with pytest.raises(UnknownDomainError):
            domain_to_resolver_key("fx", "calculate_spot_tool")

    def test_unknown_domain_error_message_lists_known_domains(self) -> None:
        with pytest.raises(UnknownDomainError, match="Known domains"):
            domain_to_resolver_key("unknown_domain", "tool")


# ============================================================================
# THE CANONICAL COLLISION
# ============================================================================


class TestCollisionResolution:
    """``get_futures_price_level_tool`` exists in BOTH ``policy_futures``
    and ``bond_futures`` MCP servers.  The two domains must resolve to
    DIFFERENT resolver keys, and the global rates resolver must return
    DIFFERENT PrimitiveSpec instances."""

    TOOL = "get_futures_price_level_tool"

    def test_policy_futures_key_is_prefixed(self) -> None:
        key = domain_to_resolver_key("policy_futures", self.TOOL)
        assert key == "policy_futures_get_futures_price_level_tool"

    def test_bond_futures_key_is_bare(self) -> None:
        key = domain_to_resolver_key("bond_futures", self.TOOL)
        assert key == "get_futures_price_level_tool"

    def test_two_keys_are_different(self) -> None:
        a = domain_to_resolver_key("policy_futures", self.TOOL)
        b = domain_to_resolver_key("bond_futures", self.TOOL)
        assert a != b

    def test_rates_resolver_returns_distinct_specs(self) -> None:
        """Round-trip through the LIVE rates resolver: both keys must
        be registered AND resolve to distinct PrimitiveSpec instances.
        No callable is invoked — only metadata lookup."""
        from rates_agent.workflows import rates_primitive_resolver

        k_policy = domain_to_resolver_key("policy_futures", self.TOOL)
        k_bond = domain_to_resolver_key("bond_futures", self.TOOL)

        spec_policy = rates_primitive_resolver(k_policy)
        spec_bond = rates_primitive_resolver(k_bond)

        assert spec_policy is not spec_bond
        assert spec_policy.tool_name == k_policy
        assert spec_bond.tool_name == k_bond
        # The two callables come from different domain packages —
        # confirms the resolver dispatches correctly across the
        # collision.
        assert spec_policy.callable is not spec_bond.callable

    def test_collision_other_tools_also_disambiguate(self) -> None:
        """Beyond the canonical collision, any tool present in BOTH
        prefixed and bare-name conventions must produce different
        keys.  Future tool additions to policy_futures should follow
        the same rule."""
        for tool in (
            "get_futures_volume_oi_tool",
            "scan_bond_futures_extremes_tool",
        ):
            k_policy = domain_to_resolver_key("policy_futures", tool)
            k_bond = domain_to_resolver_key("bond_futures", tool)
            assert k_policy != k_bond
            assert k_policy.startswith("policy_futures_")
            assert k_bond == tool


# ============================================================================
# IDEMPOTENCE
# ============================================================================


class TestIdempotence:
    """The function is pure — same args always give the same result."""

    def test_repeated_calls_same_result(self) -> None:
        for _ in range(5):
            assert domain_to_resolver_key(
                "policy_futures", "get_futures_price_level_tool",
            ) == "policy_futures_get_futures_price_level_tool"

    def test_function_has_no_side_effects(self) -> None:
        # Calling N times then verifying KNOWN_DOMAINS, _DOMAIN_PREFIXED,
        # _BARE_NAME_DOMAINS are unchanged.
        snapshot_known = frozenset(KNOWN_DOMAINS)
        snapshot_prefixed = frozenset(_DOMAIN_PREFIXED)
        snapshot_bare = frozenset(_BARE_NAME_DOMAINS)
        for _ in range(10):
            domain_to_resolver_key("ois", "some_tool")
        assert KNOWN_DOMAINS == snapshot_known
        assert _DOMAIN_PREFIXED == snapshot_prefixed
        assert _BARE_NAME_DOMAINS == snapshot_bare
