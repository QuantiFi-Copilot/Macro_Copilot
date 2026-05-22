# ADR 0006 — Inflation domain agents: extending the `Domain` closed family with `inflation_indexed_bonds` and `inflation_swaps`

**Status:** Accepted
**Date:** 2026-05-21
**Builds on:** No prior ADR — this is the first domain-agent ADR. The data substrate the two agents read is already on `build`, populated by the `inflation_indexed_bonds.yml`, `inflation_swaps.yml`, and `inflation_references.yml` playbooks in `rates_agent/playbooks/`.
**Operationalises principles:** P8 (closed-family discipline — the `Domain` enum is a closed family; this records its extension), P11 (domain isolation — each agent owns only its tools and gets its own MCP subprocess; P11 explicitly requires an ADR for a new domain), P3 (consistency by contract — the two new agents follow the same sub-agent shape as `sovereign_bonds` / `ois`), P1 (built right — no placeholder wiring).
**Scope:** Records the `Domain` closed-family extension carried by the inflation-primitive reconciliation PR (`build` ← `primitive_automation`). That PR lands the 15 inflation primitives, their two MCP servers, and the orchestrator wiring. This ADR is the closed-family decision record for the two new domains; it lands in the same PR per the docs-tree ADR discipline.

---

## Context

`orchestrator.contracts.Domain` is a closed enumeration — the set of domain specialists the supervisor can route to. Until now it had two members: `SOVEREIGN_BONDS` and `OIS`. Under P8 it is a closed family; under P11 the set of domain agents is itself a closed family and *"new domains require an ADR."* No prior ADR covered a domain extension because none had happened.

Two complete rates sub-agents were built on the `primitive_automation` branch, never wired into `build`:

- **`inflation_indexed_bonds`** — 9 sovereign-linker primitives: real-yield level, bond-implied breakeven (level / forward / curve-spread / butterfly / cross-country), and real-yield curve-spread / butterfly / cross-country spread.
- **`inflation_swaps`** — 6 zero-coupon inflation swap (ZCIS) primitives: rate level, curve spread, forward, cross-market spread, butterfly, and the swap-vs-bond breakeven basis.

The data layer these agents read is already on `build` — the `inflation_indexed_bonds.yml` / `inflation_swaps.yml` / `inflation_references.yml` playbooks populate the linker and ZCIS curve families. Only the L2 primitive layer and the orchestrator wiring were stranded on the other branch. The reconciliation PR brings them onto `build` additively. Recognising the two new domains is a `Domain` closed-family extension — which P8 and P11 say must be ADR-recorded. This is that record.

## Decision

1. **Extend the `Domain` enum** with two members:
   - `INFLATION_INDEXED_BONDS = "inflation_indexed_bonds"`
   - `INFLATION_SWAPS = "inflation_swaps"`

2. **Wire each domain through the three coupled surfaces** the supervisor/child architecture requires — and that `tests/test_orchestrator_domains.py` pins:
   - `orchestrator.contracts.Domain` — the routing key.
   - `orchestrator.config.DOMAIN_MCP_SERVERS` — the per-domain MCP subprocess map.
   - `orchestrator.session._DOMAIN_PROMPTS` — the per-domain system-prompt map.
   Plus the supervisor router prompt (`SUPERVISOR_SYSTEM_PROMPT` — AVAILABLE DOMAINS + DOMAIN SIGNALS), the multi-domain boundary labels in `_build_domain_boundaries`, and the rates workflow resolver registry in `rates_agent/workflows/__init__.py`.

3. **Each domain gets its own MCP subprocess** — `inflation_indexed_bonds_agent` and `inflation_swaps_agent` are distinct entries in `MCP_SERVERS`, each spawning its own `mcp_server.py`. This is P11 hard isolation at the client level: a linker child physically cannot see ZCIS tools, and vice versa.

4. **Two domains, not one.** Sovereign-linker bonds (real yields, bond-implied breakeven inflation) and zero-coupon inflation swaps (ZCIS) are distinct instrument families. They have disjoint curve families (`USD_TIPS` / `GBP_LINKER` / `EUR_FR_LINKER` / `CAD_RRB` vs `USD_ZCIS` / `EUR_ZCIS` / `GBP_ZCIS`), and a bond-implied breakeven is structurally a different object from a swap-implied breakeven. Each is its own desk concept and gets its own agent, prompt, and tool catalogue.

### What this ADR does NOT do

- Does not add the inflation data layer — the playbooks and ingested curve families are already on `build`.
- Does not author parity fixtures for the 15 primitives (PR15 debt — tracked separately) or `manifesto/` manifest entries (tracked separately).
- Does not change any existing domain, primitive, operator, or shared-substrate behaviour. The reconciliation is purely additive; the one shared-helper change (`fetch_single_tenor` gaining an optional `instrument_type` filter alongside build's existing optional `contract_code`) preserves every pre-existing caller byte-for-byte.
- Does not extend any other closed family — artifact types, workflow archetypes, slot types and the metadata enums are untouched.

## Alternatives considered

**Record the extension in the PR description only, no ADR.** Rejected — P11 is explicit that a new domain requires an ADR, and P8's closed-family-extension review surface needs a durable, citable record, not a PR comment that is hard to find later.

**A single combined `INFLATION` domain.** Rejected — linker bonds and ZCIS are distinct instrument families with disjoint curve families, distinct desk owners, and structurally distinct measures (bond-implied vs swap-implied inflation compensation). One agent straddling both would dilute the single-domain expertise P11 exists to protect and force one system prompt to cover two instrument families.

**Fold the inflation tools into the existing `sovereign_bonds` domain.** Rejected — real yields are not nominal yields. The linker tools carry an `instrument_type='inflation_linker'` DB-filter guard precisely to keep linker rows from being served under a nominal label; P11 isolation requires a separate tool catalogue and MCP subprocess, not a shared one.

## Design

The extension lands at exactly these sites (all additive):

- `orchestrator/contracts.py` — two new `Domain` enum members.
- `orchestrator/config.py` — two new `MCP_SERVERS` entries (`inflation_indexed_bonds_agent`, `inflation_swaps_agent`) and two new `DOMAIN_MCP_SERVERS` entries, each pointing at its own subprocess.
- `orchestrator/prompts.py` — two new child system-prompt constants (`INFLATION_INDEXED_BONDS_SYSTEM_PROMPT`, `INFLATION_SWAPS_SYSTEM_PROMPT`) plus the AVAILABLE DOMAINS and DOMAIN SIGNALS paragraphs in the supervisor router prompt.
- `orchestrator/session.py` — two `_DOMAIN_PROMPTS` entries and two `_build_domain_boundaries` labels.
- `rates_agent/inflation_indexed_bonds/` and `rates_agent/inflation_swaps/` — the two sub-agent packages (each: `mcp_server.py` + `tools/` with the per-tool four-file primitive shape).
- `rates_agent/workflows/__init__.py` — 15 `PrimitiveSpec` registrations in the rates workflow resolver.

`tests/test_orchestrator_domains.py` pins the three-surface contract: every `Domain` member must resolve to a non-empty MCP-server config and a registered system prompt, and the supervisor prompt must advertise each domain. A future half-wired domain therefore fails loudly at test time rather than being silently skipped at session-open.

## Consequences

**Positive:**
- 15 desk-useful inflation primitives become routable on `build`.
- `Domain` now has four members, each wired through all three coupled surfaces; the closed family stays coherent.
- `test_orchestrator_domains.py` makes the wiring contract enforceable — a partially-wired domain can no longer slip through.
- Each inflation domain is hard-isolated in its own MCP subprocess (P11).

**Negative / known trade-offs:**
- The closed family grew from two members to four. Every consumer that enumerates `Domain` now handles four — verified at reconciliation time to be only `orchestrator/config.py`, `orchestrator/session.py`, and `tests/test_orchestrator_domains.py`.
- The 15 primitives ship without parity fixtures (PR15 debt) and without `manifesto/` manifest entries — both tracked as separate follow-ups, not blockers for this extension.

## Rollout plan

1. **This ADR** — lands in the inflation-primitive reconciliation PR alongside the change it records.
2. **The reconciliation PR** — the 15 primitives, the two MCP servers, the orchestrator wiring, and the additive `fetch_single_tenor` dual-filter merge.
3. **Follow-ups (separate PRs):** parity fixtures for the 15 primitives (PR15); `manifesto/` manifest entries for the inflation tools.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-21 | Initial decision. Accepted. |
