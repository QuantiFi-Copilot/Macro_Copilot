"""tests/test_orchestrator_domains.py — Domain end-to-end wiring tests.

The supervisor/child architecture requires that every entry in the
``Domain`` enum is reachable through THREE coupled surfaces:

  1. ``orchestrator.contracts.Domain``        — the routing key
  2. ``orchestrator.config.DOMAIN_MCP_SERVERS`` — the MCP subprocess map
  3. ``orchestrator.session._DOMAIN_PROMPTS``   — the system-prompt map

These tests pin that contract.  Adding a new domain entry without the
matching MCP-server config and system prompt would silently drop a
domain at session-open time (see ``CopilotSession.open`` in
``orchestrator/session.py``: any domain missing from either surface is
``logger.warning``-skipped, which is invisible to a test suite that only
asserts the enum).

Test scope
----------
- Plumbing only.  No live LLM call, no MCP subprocess spawn.
- Pure import-graph validation against the orchestrator's own
  in-memory state.
"""

from __future__ import annotations

import inspect

from orchestrator.config import DOMAIN_MCP_SERVERS, MCP_SERVERS
from orchestrator.contracts import Domain


class TestDomainEnumMembers:
    """Pin the explicit membership of the Domain enum.  Adding a new
    domain is a real architectural decision — this test exists so a
    rename or accidental removal is caught explicitly rather than via
    a downstream ``KeyError``."""

    def test_sovereign_bonds_present(self):
        assert Domain.SOVEREIGN_BONDS.value == "sovereign_bonds"

    def test_ois_present(self):
        assert Domain.OIS.value == "ois"

    def test_inflation_indexed_bonds_present(self):
        assert Domain.INFLATION_INDEXED_BONDS.value == "inflation_indexed_bonds"

    def test_inflation_swaps_present(self):
        assert Domain.INFLATION_SWAPS.value == "inflation_swaps"

    def test_fx_present(self):
        assert Domain.FX.value == "fx"


class TestDomainMcpServersMapping:
    """Every Domain enum member MUST resolve to a non-empty MCP-server
    config dict.  CopilotSession.open silently skips any domain missing
    from this map; a reviewer can no longer rely on the warning log to
    catch the omission."""

    def test_every_domain_has_mcp_server_config(self):
        missing = [d for d in Domain if d not in DOMAIN_MCP_SERVERS]
        assert not missing, (
            f"Domains missing from DOMAIN_MCP_SERVERS: "
            f"{[d.value for d in missing]}.  Each Domain member must "
            "have an explicit MCP-server config or CopilotSession.open "
            "will silently skip it."
        )

    def test_every_mapping_is_a_non_empty_dict(self):
        for domain, cfg in DOMAIN_MCP_SERVERS.items():
            assert isinstance(cfg, dict) and cfg, (
                f"{domain.value} maps to {cfg!r}; must be a non-empty "
                "dict suitable for MultiServerMCPClient."
            )

    def test_every_mapping_points_at_a_known_mcp_server(self):
        """Each per-domain config dict's values should be the same
        objects registered in ``MCP_SERVERS`` — one source of truth for
        the subprocess shape, then re-keyed under the public-facing
        domain name."""
        registered_values = list(MCP_SERVERS.values())
        for domain, cfg in DOMAIN_MCP_SERVERS.items():
            for server_name, server_cfg in cfg.items():
                assert server_cfg in registered_values, (
                    f"{domain.value} → {server_name} points at a config "
                    "dict that is not registered in MCP_SERVERS — a "
                    "drift risk because subprocess env / args won't be "
                    "kept in sync."
                )

    def test_inflation_indexed_bonds_uses_dedicated_subprocess(self):
        """The linker domain must spawn its own MCP subprocess (not
        share the sovereign or OIS one).  Sharing would let
        sovereign-side tools answer linker queries via the same MCP
        client — exactly the proxy violation the instrument_type guard
        was added to prevent."""
        cfg = DOMAIN_MCP_SERVERS[Domain.INFLATION_INDEXED_BONDS]
        assert cfg, "linker domain has empty MCP server config"
        # The single registered server must be the linker MCP server,
        # not the sovereign or OIS one.
        assert cfg == {
            "inflation_indexed_bonds": MCP_SERVERS[
                "inflation_indexed_bonds_agent"
            ],
        }

    def test_inflation_swaps_uses_dedicated_subprocess(self):
        """The inflation-swaps domain must spawn its own MCP
        subprocess, distinct from the linker / sovereign / OIS
        subprocesses.  Sharing would let linker-side tools answer ZCIS
        queries (or vice versa) via the same MCP client — a proxy
        violation forbidden by the same instrument_type / pricing_type
        guard the ZCIS rate-level tool ships with."""
        cfg = DOMAIN_MCP_SERVERS[Domain.INFLATION_SWAPS]
        assert cfg, "inflation_swaps domain has empty MCP server config"
        assert cfg == {
            "inflation_swaps": MCP_SERVERS["inflation_swaps_agent"],
        }

    def test_fx_uses_dedicated_subprocess(self):
        """The FX domain must spawn its own MCP subprocess, distinct
        from every rates / OIS / inflation subprocess.  Sharing would
        let rates-side tools answer FX queries via the same MCP
        client — a P11 proxy violation (rates and FX are different
        instrument families with disjoint substrate tables)."""
        cfg = DOMAIN_MCP_SERVERS[Domain.FX]
        assert cfg, "fx domain has empty MCP server config"
        assert cfg == {
            "fx": MCP_SERVERS["fx_agent"],
        }


class TestDomainPrompts:
    """Each Domain enum member MUST have a registered system prompt.
    Missing entries are silently skipped at session-open time — same
    silent-drop pattern as DOMAIN_MCP_SERVERS."""

    def _domain_prompts(self) -> dict:
        from orchestrator.session import _DOMAIN_PROMPTS  # noqa: WPS437
        return _DOMAIN_PROMPTS

    def test_every_domain_has_system_prompt(self):
        prompts = self._domain_prompts()
        missing = [d for d in Domain if d not in prompts]
        assert not missing, (
            f"Domains missing from _DOMAIN_PROMPTS: "
            f"{[d.value for d in missing]}.  CopilotSession.open will "
            "silently skip these."
        )

    def test_inflation_indexed_bonds_prompt_is_linker_specific(self):
        """The linker prompt must scope the agent to linker tools and
        explicitly forbid nominal-sovereign curve families.  Without
        this, the LLM would happily call the linker tool with curve
        families like UST and rely on the runtime guard to refuse —
        avoidable round-trips that look like errors to the user."""
        prompts = self._domain_prompts()
        prompt = prompts[Domain.INFLATION_INDEXED_BONDS]
        # Curve families it owns.
        assert "USD_TIPS" in prompt
        assert "GBP_LINKER" in prompt
        # Explicit out-of-scope routing for nominal curves.
        assert "out_of_scope" in prompt or "out of scope" in prompt.lower()
        # The "real yield" terminology discipline (separates linker from
        # nominal language end-to-end).
        assert "real yield" in prompt.lower()

    def test_inflation_swaps_prompt_is_zcis_specific(self):
        """The inflation-swaps prompt must scope the agent to ZCIS
        tools and explicitly forbid the linker / sovereign / OIS
        domains.  Without this, the LLM would route linker breakeven
        or nominal yield questions here and either hit a runtime guard
        or silently misinterpret the output."""
        prompts = self._domain_prompts()
        prompt = prompts[Domain.INFLATION_SWAPS]
        # Curve families it owns.
        assert "USD_ZCIS" in prompt
        assert "EUR_ZCIS" in prompt
        assert "GBP_ZCIS" in prompt
        # Explicit out-of-scope routing for sibling domains.
        assert "out_of_scope" in prompt or "out of scope" in prompt.lower()
        # The "ZCIS rate" / "inflation-swap rate" terminology
        # discipline (separates ZCIS from linker bond-implied
        # breakeven and from OIS rate end-to-end).
        assert "ZCIS" in prompt

    def test_fx_prompt_is_fx_specific(self):
        """The FX prompt must scope the agent to G10 FX spot / carry
        tools and explicitly forbid rates / OIS / inflation / credit /
        equity domains.  Without this, the LLM would route nominal
        yield or OIS swap questions here and either hit a runtime
        guard or silently misinterpret the output."""
        prompts = self._domain_prompts()
        prompt = prompts[Domain.FX]
        # G10 pairs it owns.
        assert "EURUSD" in prompt
        assert "USDJPY" in prompt
        # The substrate it operates on.
        assert "spot" in prompt.lower()
        assert "carry" in prompt.lower()
        # Explicit out-of-scope routing for sibling domains.
        assert "out_of_scope" in prompt or "out of scope" in prompt.lower()
        # The FX terminology discipline — pair names rather than
        # base-currency-relative language.
        assert "G10" in prompt


class TestDomainBoundariesLabel:
    """The multi-domain fan-out builds per-domain "stay in your lane"
    instructions from a label dictionary inside session.py.  Missing
    entries fall back to the bare enum value — a UX regression for
    multi-domain queries that route to the linker domain.
    """

    def test_inflation_indexed_bonds_has_friendly_label(self):
        from orchestrator.session import _build_domain_boundaries

        boundaries = _build_domain_boundaries(
            [Domain.SOVEREIGN_BONDS, Domain.INFLATION_INDEXED_BONDS]
        )
        linker_text = boundaries[Domain.INFLATION_INDEXED_BONDS]
        # The friendly label, not the bare enum value.
        assert "inflation-linked bonds" in linker_text
        # And the sibling label survives so the boundary names what
        # the OTHER agent owns explicitly.
        assert "cash sovereign bonds" in linker_text

    def test_inflation_swaps_has_friendly_label(self):
        from orchestrator.session import _build_domain_boundaries

        boundaries = _build_domain_boundaries(
            [Domain.SOVEREIGN_BONDS, Domain.INFLATION_SWAPS]
        )
        zcis_text = boundaries[Domain.INFLATION_SWAPS]
        # The friendly label, not the bare enum value.
        assert "zero-coupon inflation swaps" in zcis_text
        # And the sibling label survives.
        assert "cash sovereign bonds" in zcis_text

    def test_fx_has_friendly_label(self):
        from orchestrator.session import _build_domain_boundaries

        boundaries = _build_domain_boundaries(
            [Domain.SOVEREIGN_BONDS, Domain.FX]
        )
        fx_text = boundaries[Domain.FX]
        # The friendly label, not the bare enum value.
        assert "G10 FX spot and forwards" in fx_text
        # And the sibling label survives so the boundary names what
        # the OTHER agent owns explicitly.
        assert "cash sovereign bonds" in fx_text


class TestSupervisorPromptMentionsLinkerDomain:
    """The supervisor LLM cannot route to a domain it has never been
    told about.  Adding the domain to the enum + config is necessary
    but not sufficient — the supervisor system prompt must enumerate
    the new domain in its AVAILABLE DOMAINS section."""

    def test_supervisor_prompt_advertises_linker_domain(self):
        from orchestrator.prompts import SUPERVISOR_SYSTEM_PROMPT

        assert "inflation_indexed_bonds" in SUPERVISOR_SYSTEM_PROMPT, (
            "supervisor prompt does not advertise the linker domain — "
            "the LLM cannot pick a domain it has not been told exists."
        )
        # The signal vocabulary the supervisor needs to discriminate
        # linker queries from nominal sovereign queries.
        for needle in ("TIPS", "real yield"):
            assert needle in SUPERVISOR_SYSTEM_PROMPT, (
                f"supervisor prompt missing linker signal {needle!r}"
            )

    def test_supervisor_prompt_advertises_inflation_swaps_domain(self):
        from orchestrator.prompts import SUPERVISOR_SYSTEM_PROMPT

        assert "inflation_swaps" in SUPERVISOR_SYSTEM_PROMPT, (
            "supervisor prompt does not advertise the inflation_swaps "
            "domain — the LLM cannot pick a domain it has not been "
            "told exists."
        )
        # The signal vocabulary the supervisor needs to discriminate
        # ZCIS queries from sibling rates queries.
        for needle in ("ZCIS", "USD_ZCIS"):
            assert needle in SUPERVISOR_SYSTEM_PROMPT, (
                f"supervisor prompt missing inflation_swaps signal "
                f"{needle!r}"
            )

    def test_supervisor_prompt_advertises_fx_domain(self):
        from orchestrator.prompts import SUPERVISOR_SYSTEM_PROMPT

        # The domain id is "fx" — assert it's advertised as a routing
        # target, distinct from incidental mentions of FX in commentary
        # on other domains' out-of-scope lists.
        assert "- fx" in SUPERVISOR_SYSTEM_PROMPT, (
            "supervisor prompt does not advertise the fx domain in its "
            "AVAILABLE DOMAINS section — the LLM cannot pick a domain "
            "it has not been told exists."
        )
        # The signal vocabulary the supervisor needs to route FX queries.
        for needle in ("EURUSD", "carry", "spot"):
            assert needle in SUPERVISOR_SYSTEM_PROMPT, (
                f"supervisor prompt missing fx signal {needle!r}"
            )


class TestDomainAgentSessionConstruction:
    """Smoke-test that DomainAgentSession can be constructed for the
    new domain with the registered prompt + MCP config.  No subprocess
    is spawned — this validates the import graph and constructor
    signature only.
    """

    def test_can_construct_for_inflation_indexed_bonds(self):
        from orchestrator.domain_agent import DomainAgentSession
        from orchestrator.session import _DOMAIN_PROMPTS

        session = DomainAgentSession(
            domain=Domain.INFLATION_INDEXED_BONDS,
            system_prompt=_DOMAIN_PROMPTS[Domain.INFLATION_INDEXED_BONDS],
            mcp_servers=DOMAIN_MCP_SERVERS[Domain.INFLATION_INDEXED_BONDS],
            model_name="claude-test",
            temperature=0.0,
            max_tokens=1024,
        )
        assert session.domain is Domain.INFLATION_INDEXED_BONDS
        # Construction must not eagerly spawn a subprocess.  The
        # spawn happens lazily on first use via session.open().
        assert getattr(session, "_is_open", False) is False

    def test_can_construct_for_inflation_swaps(self):
        from orchestrator.domain_agent import DomainAgentSession
        from orchestrator.session import _DOMAIN_PROMPTS

        session = DomainAgentSession(
            domain=Domain.INFLATION_SWAPS,
            system_prompt=_DOMAIN_PROMPTS[Domain.INFLATION_SWAPS],
            mcp_servers=DOMAIN_MCP_SERVERS[Domain.INFLATION_SWAPS],
            model_name="claude-test",
            temperature=0.0,
            max_tokens=1024,
        )
        assert session.domain is Domain.INFLATION_SWAPS
        # Construction must not eagerly spawn a subprocess.  The
        # spawn happens lazily on first use via session.open().
        assert getattr(session, "_is_open", False) is False

    def test_can_construct_for_fx(self):
        from orchestrator.domain_agent import DomainAgentSession
        from orchestrator.session import _DOMAIN_PROMPTS

        session = DomainAgentSession(
            domain=Domain.FX,
            system_prompt=_DOMAIN_PROMPTS[Domain.FX],
            mcp_servers=DOMAIN_MCP_SERVERS[Domain.FX],
            model_name="claude-test",
            temperature=0.0,
            max_tokens=1024,
        )
        assert session.domain is Domain.FX
        # Construction must not eagerly spawn a subprocess.  The
        # spawn happens lazily on first use via session.open().
        assert getattr(session, "_is_open", False) is False
