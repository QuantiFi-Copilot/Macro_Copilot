# ADR 0007 — FX domain agent: extending the `Domain` closed family with `fx`

**Status:** Accepted
**Date:** 2026-05-21
**Builds on:** ADR 0006 (closed-family extension precedent for inflation domains). The data substrate this agent reads is already on `build`, populated by `fx_agent/playbooks/spot_fx.yml` and `fx_agent/playbooks/fx_forwards.yml`. The UI, API routes, catalogue manifest, and data-readiness gate were landed earlier on this branch as a sequence of additive commits.
**Operationalises principles:** P8 (closed-family discipline — the `Domain` enum is a closed family; this records its extension), P11 (domain isolation — each agent owns only its tools and gets its own MCP subprocess; P11 explicitly requires an ADR for a new domain), P3 (consistency by contract — the FX agent follows the same sub-agent shape as `sovereign_bonds` / `ois` / inflation domains).
**Scope:** Records the `Domain` closed-family extension that makes the FX agent routable from the supervisor. The three FX primitives — `get_fx_spot_level`, `scan_fx_spot`, `calculate_fx_carry` — already ship as a working MCP server in `fx_agent/mcp_server.py`; what was missing was the orchestrator wiring that exposes them to the supervisor's routing layer. This ADR records the closed-family decision; the wiring lands in the same PR.

---

## Context

`orchestrator.contracts.Domain` is a closed enumeration — the set of domain specialists the supervisor can route to. After ADR 0006 it has four members: `SOVEREIGN_BONDS`, `OIS`, `INFLATION_INDEXED_BONDS`, `INFLATION_SWAPS`. Under P8 it is a closed family; under P11 the set of domain agents is itself a closed family and *"new domains require an ADR."*

The FX agent has been integrated across every surface except the orchestrator:

- **Data substrate** — `spot_fx.yml` and `fx_forwards.yml` populate G10 spot and 1M forward universes; the readiness gate `tests/test_fx_data_readiness.py` passes in `--strict-metadata` mode (21/0/0).
- **API routes** — `api/routes/fx/cards.py` exposes `/api/v1/fx/{spot-level,scanner,carry}` over the same Engine dependency as Rates.
- **Catalogue manifest** — `manifesto/03_tool_manifest/fx_agent/01_fx_manifest.yml` registers the three primitives with the same schema as the rates manifests.
- **UI** — `FXAgentPage` consumes the shared widget engine (`useWidgetLayout('fx', ...)`); three FX widgets sit alongside the rates widgets in `WidgetRenderer`; the sidebar surfaces a live FX scope via `FXDataProvider`.
- **MCP server** — `fx_agent/mcp_server.py` registers `get_fx_spot_level_tool`, `scan_fx_spot_tool`, and `calculate_fx_carry_tool` over stdio, ready to spawn as a child subprocess.

What was missing: `Domain.FX`, `DOMAIN_MCP_SERVERS[Domain.FX]`, `_DOMAIN_PROMPTS[Domain.FX]`, and the supervisor prompt's awareness of the FX domain. Without those four sites, the supervisor cannot route any FX query — every observable FX surface (the widget, the sidebar Today panel, the API endpoint) renders data, but the **Copilot itself** has no FX domain to dispatch to. The integration is structurally incomplete.

This ADR brings the orchestrator into alignment.

## Decision

1. **Extend the `Domain` enum** with one new member:
   - `FX = "fx"`

2. **Wire the FX domain through the three coupled surfaces** the supervisor/child architecture requires — and that `tests/test_orchestrator_domains.py` pins:
   - `orchestrator.contracts.Domain` — the routing key.
   - `orchestrator.config.DOMAIN_MCP_SERVERS` — the per-domain MCP subprocess map (`Domain.FX` → `{"fx": MCP_SERVERS["fx_agent"]}`).
   - `orchestrator.session._DOMAIN_PROMPTS` — the per-domain system-prompt map (`Domain.FX` → `FX_SYSTEM_PROMPT`).

   Plus the supervisor router prompt (`SUPERVISOR_SYSTEM_PROMPT` — AVAILABLE DOMAINS + DOMAIN SIGNALS sections) and the multi-domain boundary labels in `_build_domain_boundaries`.

3. **The FX domain gets its own MCP subprocess** — `fx_agent` is a distinct entry in `MCP_SERVERS` (formerly a commented-out "Future agents" placeholder), spawning its own `fx_agent/mcp_server.py`. This is P11 hard isolation at the client level: a rates child physically cannot see FX tools, and vice versa.

4. **One domain in V1, not several.** Sreeram's inflation reconciliation (ADR 0006) split inflation into two domains (`INFLATION_INDEXED_BONDS` and `INFLATION_SWAPS`) because the 15 tools already shipped against two structurally distinct instrument families with disjoint curve families and distinct desk owners. The FX agent in V1 ships **three tools, all on the same family** — G10 cash spot and 1M-tenor forwards (carry). A single `FX` domain is the correct grain for V1; splitting now would create empty-or-nearly-empty sibling domains that violate P1 (built right, no placeholder wiring).

   The future split path is documented under "Rollout plan" below.

### What this ADR does NOT do

- Does not add the FX data layer — playbooks, ingestion, and the live DB rows are already on `build` (the data was extended and metadata-refreshed in earlier commits on this branch).
- Does not author parity fixtures or backtests for the three FX primitives — tracked separately.
- Does not change any existing domain, primitive, operator, or shared-substrate behaviour. The extension is purely additive: contracts.py, config.py, prompts.py, session.py, and the test file all gain entries without any existing entry being modified.
- Does not extend any other closed family — artifact types, workflow archetypes, slot types, and metadata enums are untouched.
- Does not wire FX into the `rates_agent/workflows/__init__.py` `PrimitiveSpec` resolver. The FX agent has no workflows in V1; if and when FX workflows ship, they will get their own resolver registry following the rates pattern.

## Alternatives considered

**Record the extension in the PR description only, no ADR.** Rejected — P11 is explicit that a new domain requires an ADR, and P8's closed-family-extension review surface needs a durable, citable record. ADR 0006 set the precedent for inflation; the FX extension follows the same discipline.

**Split FX immediately into `FX_CASH` / `FX_VOL` / `FX_NDFS` (mirror inflation's 2-domain pattern).** Rejected for V1 — there is no FX vol or NDF tool catalogue yet, and creating sibling domains that hold zero tools would be the kind of placeholder wiring P1 forbids. The trajectory is to **start mono-domain and split when each sibling has earned its own tool catalogue** — the same path Rates took (started as a single agent, then split into `SOVEREIGN_BONDS` and `OIS` once each had a real catalogue, then extended again into the two inflation domains).

**Fold the FX tools into an existing domain (e.g. `OIS` for the carry leg, since both involve interest-rate differentials).** Rejected — FX spot and FX carry are structurally distinct from any rates instrument. FX carry is a forward-points-implied differential between two currencies' funding rates, not an outright swap rate; routing it under `OIS` would force the OIS specialist to handle a different instrument family with disjoint curve families (FX pairs vs OIS curve families). P11 isolation requires a separate tool catalogue and MCP subprocess.

## Design

The extension lands at exactly these sites (all additive):

- `orchestrator/contracts.py` — one new `Domain` enum member (`FX = "fx"`).
- `orchestrator/config.py` — one new `MCP_SERVERS` entry (`fx_agent`, previously commented-out) and one new `DOMAIN_MCP_SERVERS` entry pointing at it.
- `orchestrator/prompts.py` — one new child system-prompt constant (`FX_SYSTEM_PROMPT`) plus extensions to the AVAILABLE DOMAINS and DOMAIN SIGNALS paragraphs of `SUPERVISOR_SYSTEM_PROMPT`.
- `orchestrator/session.py` — one `_DOMAIN_PROMPTS` entry and one `_build_domain_boundaries` label.
- `fx_agent/mcp_server.py` — already shipped; no change.
- `tests/test_orchestrator_domains.py` — additional assertions covering the new domain on all surfaces (enum membership, dedicated subprocess, prompt scope, boundary label, supervisor advertisement, and session construction).

`tests/test_orchestrator_domains.py` pins the three-surface contract for `Domain.FX` the same way ADR 0006 pinned it for the inflation domains: every `Domain` member must resolve to a non-empty MCP-server config and a registered system prompt, and the supervisor prompt must advertise each domain.

## Consequences

**Positive:**
- The three FX primitives (`get_fx_spot_level`, `scan_fx_spot`, `calculate_fx_carry`) become routable from the supervisor. The widget surface, the API routes, and the catalogue manifest now have a Copilot-facing endpoint behind them.
- `Domain` now has five members, each wired through all three coupled surfaces; the closed family stays coherent.
- `test_orchestrator_domains.py` makes the FX wiring contract enforceable — a partially-wired FX domain can no longer slip through.
- The FX domain is hard-isolated in its own MCP subprocess (P11).

**Negative / known trade-offs:**
- The closed family grew from four members to five. Every consumer that enumerates `Domain` now handles five — verified at extension time to be `orchestrator/config.py`, `orchestrator/session.py`, and `tests/test_orchestrator_domains.py` (the same three sites ADR 0006 verified).
- V1 is mono-domain `FX`. When the universe extends to vol surfaces or NDFs (see rollout plan), the closed family will grow again and the mono-domain decision will need to be revisited.

## Rollout plan

1. **This ADR** — lands in the FX-domain wiring PR alongside the change it records.

2. **The wiring PR (this commit)** — `FX` enum member, `MCP_SERVERS["fx_agent"]`, `DOMAIN_MCP_SERVERS[Domain.FX]`, `FX_SYSTEM_PROMPT`, supervisor-prompt extensions, `_build_domain_boundaries` label, and the test additions that pin the contract.

3. **Future FX universe extension (separate PRs):**
   - More tenors for forwards (1W, 3M, 6M, 1Y) and the `forward_curve` tool implementation (currently scaffolded as a `NotImplementedError` stub).
   - Realized-vol tool implementation (currently scaffolded as a stub).
   - EM / NDF universe (USDCNH, USDINR, USDBRL, USDKRW, etc.).
   - FX option-implied vol surface (ATM, risk reversals, butterflies).
   - CIP / cross-currency basis tools (depending on OIS substrate readiness).

4. **Future closed-family re-extension (separate ADR, if needed):** when the FX tool catalogue grows past G10 cash and crosses into instrument families with structurally distinct measures and disjoint substrates — typically vol options (different desk, implied not realized) and NDFs (non-deliverable, EM substrate) — the mono-`FX` domain will be split. Likely shape: `FX_CASH` (spot + forwards + carry), `FX_VOL` (option-implied surface), `FX_NDF` (EM non-deliverable). That split requires its own ADR; this one only records the V1 mono-domain decision.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-21 | Initial decision. Accepted. |
