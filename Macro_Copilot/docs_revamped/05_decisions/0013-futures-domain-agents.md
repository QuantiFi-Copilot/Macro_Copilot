# ADR 0013 — Futures domain agents: extending the `Domain` closed family with `policy_futures` and `bond_futures`

**Status:** Accepted
**Date:** 2026-05-22
**Builds on:** ADR 0006 (inflation domain agents — first precedent for splitting an instrument family out of the rates default domains into its own sub-agent + MCP subprocess); ADR 0008 (event-playbook contract — independent of this ADR but co-shipping with the data-layer expansion that finally makes these primitives buildable).
**Operationalises principles:** P8 (closed-family discipline — the `Domain` enum extension is ADR-recorded), P11 (domain isolation — each agent owns only its tools and gets its own MCP subprocess; "new domains require an ADR"), P3 (consistency by contract — the two new agents follow the same sub-agent shape as `sovereign_bonds` / `ois` / `inflation_indexed_bonds` / `inflation_swaps`), P1 (built right — no placeholder wiring; the sub-agent skeletons and orchestrator wiring land complete in this PR).
**Scope:** Records the `Domain` closed-family extension carried by the primitive-automation relaunch PR. That PR lands two empty sub-agent skeletons (`rates_agent/policy_futures/` and `rates_agent/bond_futures/`), wires both through the orchestrator's three coupled surfaces, and adds 11 catalog entries for the OpenClaw factory to build into them. The ADR is the closed-family record; the implementation lands in the same PR.

---

## Context

`orchestrator.contracts.Domain` is a closed enumeration — the set of domain specialists the supervisor can route to. After ADR 0006 it has four members: `SOVEREIGN_BONDS`, `OIS`, `INFLATION_INDEXED_BONDS`, `INFLATION_SWAPS`. Under P8 it is a closed family; under P11 the set of domain agents is itself a closed family and *"new domains require an ADR."*

Two instrument families on the `build` substrate have no domain agent today:

- **Policy futures** — exchange-traded short-term-interest-rate futures (SOFR_FUT, EUR_SHORT_RATE_FUT, SONIA_FUT). Quoted in price (`= 100 − implied rate`); strip-position-keyed (SFR1…SFR8 etc., not tenor-keyed); 24 instruments live in `rates_agent/playbooks/policy_futures.yml`. The STIR desk reads the front in implied-rate space all day. Existing tools cannot serve this — `curve_spread` is tenor-keyed; sovereign primitives use yield space; OIS primitives use par-rate space. Implied rate, strip snapshot, calendar spread, simple butterfly, pack-average, cross-CB spread, volume/OI, morning scan are all desk-recognised concepts on the STIR desk that no current agent owns.

- **Bond futures** — exchange-traded sovereign-bond futures (TU1/FV1/TY1/UXY1/US1/WN1 on UST_FUT; RX1/DE_FUT analogues; JB1; etc.). Quoted in price; 19 instruments live in `rates_agent/playbooks/bond_futures.yml`. The headline reads are price and volume/OI — distinct from the cash-sovereign desk's yield-space tools. The full RV stack (CTD, basis, DV01-weighted spreads) is data-blocked and deferred per `docs/technical_debt.md` items 24/28; in this ADR's scope the `bond_futures` agent owns only the three monitors (`futures_price_level`, `futures_volume_oi`, `scan_bond_futures_extremes`).

The data substrate these agents read is already on `build`: both playbooks have been ingested, the rolling-contract metadata SCD2 (TD#2) is in `instrument_metadata_history`, and `bond_futures.yml`'s TY1/UXY1 + US1/WN1 dup-key concern (TD#11) is resolved via `contract_code`. The orchestrator wiring is the only missing piece, and recognising the two new domains is the `Domain` closed-family extension this ADR records.

## Decision

1. **Extend the `Domain` enum** with two members:
   - `POLICY_FUTURES = "policy_futures"`
   - `BOND_FUTURES = "bond_futures"`

2. **Wire each domain through the three coupled surfaces** (the contract `tests/test_orchestrator_domains.py` pins):
   - `orchestrator.contracts.Domain` — routing key.
   - `orchestrator.config.DOMAIN_MCP_SERVERS` — per-domain MCP subprocess map (new entries `policy_futures_agent`, `bond_futures_agent`).
   - `orchestrator.session._DOMAIN_PROMPTS` — per-domain system-prompt map.
   Plus the supervisor router prompt (`SUPERVISOR_SYSTEM_PROMPT` — AVAILABLE DOMAINS + DOMAIN SIGNALS), the multi-domain boundary labels in `_build_domain_boundaries`, and (when the factory builds primitives) the rates workflow resolver registry in `rates_agent/workflows/__init__.py`.

3. **Each domain gets its own MCP subprocess** — `policy_futures_agent` and `bond_futures_agent` are distinct entries in `MCP_SERVERS`, each spawning its own `mcp_server.py`. P11 hard isolation at the client level: a policy-futures child physically cannot see bond-futures tools, and vice versa. The STIR desk and the cash/futures bond desk are distinct constituencies; cross-leakage is undesirable in both directions.

4. **Two domains, not one.** Policy futures and bond futures are distinct instrument families on real desks. Policy futures are quoted in implied-rate space (strip positions; pack averages; cross-CB spreads against the central-bank pricing path); bond futures are quoted in price space and the canonical RV objects involve CTD-implied yields and DV01-weighted hedge ratios (deferred Phase 4 work). A single combined "futures" domain would dilute single-domain expertise (P11) and force one prompt to straddle two instrument families with disjoint conventions, units, and methodology disclosures.

### What this ADR does NOT do

- Does not build the primitives themselves — the OpenClaw primitive-automation factory builds them per the catalog (11 catalog entries: 8 policy_futures + 3 bond_futures monitors).
- Does not add the canonical bond-futures RV stack (CTD, basis, implied repo, DV01-weighted inter-commodity spread, cross-country DV01+FX spread). Those primitives are documented-deferred (`docs/technical_debt.md` 24/27; Phase 4 work) until D-repo + D-deliverable land.
- Does not extend any other closed family — artifact types, workflow archetypes, slot types and the methodology-source tag set are untouched.
- Does not modify any existing primitive in `sovereign_bonds` / `ois` / `inflation_indexed_bonds` / `inflation_swaps`. The two new sub-agents are sibling packages, never inheriting from or modifying existing ones.

## Alternatives considered

**Record the extension in the PR description only, no ADR.** Rejected — P11 is explicit that a new domain requires an ADR, and P8's closed-family-extension review surface needs a durable, citable record. ADR 0006 set this precedent; following it keeps the discipline.

**A single combined `FUTURES` domain.** Rejected — policy futures and bond futures have disjoint conventions (price vs implied rate as the primary read; strip-position vs maturity-month as the primary key; RFR-compounded vs term-Euribor vs cash-bond-CTD as the underlying), disjoint curve families, distinct desk owners (STIR desk vs cash/futures rates desk), and structurally distinct RV objects. One agent straddling both would dilute single-domain expertise per P11 and force one router prompt to disambiguate two universes whose typical PM questions look superficially similar but cash out very differently.

**Fold both into an existing agent.** Rejected for the same reasons as ADR 0006's analogous rejection: extending an existing sub-agent's tool catalogue with a different instrument_type / different fetch path / different unit conventions violates PR1's concept-ownership rule and forces the existing agent's prompt to cover two desk concepts at once. The factory then routes ambiguously.

## Design

The extension lands at exactly these sites (all additive):

- `orchestrator/contracts.py` — two new `Domain` enum members.
- `orchestrator/config.py` — two new `MCP_SERVERS` entries (`policy_futures_agent`, `bond_futures_agent`) and two new `DOMAIN_MCP_SERVERS` entries, each pointing at its own subprocess.
- `orchestrator/prompts.py` — two new child system-prompt constants (`POLICY_FUTURES_SYSTEM_PROMPT`, `BOND_FUTURES_SYSTEM_PROMPT`) plus the AVAILABLE DOMAINS and DOMAIN SIGNALS paragraphs in the supervisor router prompt.
- `orchestrator/session.py` — two `_DOMAIN_PROMPTS` entries and two `_build_domain_boundaries` labels.
- `rates_agent/policy_futures/` and `rates_agent/bond_futures/` — the two sub-agent packages (each: `__init__.py`, `mcp_server.py` skeleton, `tools/__init__.py`, `tools/schemas/__init__.py`). The factory fills in the per-tool `tools/<name>/` four-file folders.

`tests/test_orchestrator_domains.py` pins the three-surface contract. After this PR every `Domain` member must resolve to a non-empty MCP-server config and a registered system prompt, and the supervisor prompt must advertise each domain. A future half-wired domain therefore fails loudly at test time rather than being silently skipped at session-open.

## Consequences

**Positive:**
- 11 desk-useful futures primitives (8 policy + 3 bond monitors) become routable on `primitive_automation` once the factory builds them, and on `build` once reconciled — closing the largest data-unlocked gap in the catalogue.
- `Domain` now has six members, each wired through all three coupled surfaces; the closed family stays coherent.
- Each new agent is hard-isolated in its own MCP subprocess (P11): a STIR-desk question cannot fall through to a bond-futures tool by accident, and vice versa.
- The bond-futures monitors ship with explicit P5 disclosure (*"this is CTD-of-rolling-generic price; for CTD-implied yield see the Phase-4 stack"*) so the desk knows the monitor's epistemic limits.

**Negative / known trade-offs:**
- The closed family grew from four members to six. Every consumer that enumerates `Domain` now handles six — limited to `orchestrator/config.py`, `orchestrator/session.py`, and `tests/test_orchestrator_domains.py` (a P10 win — single enumeration surface).
- The bond-futures domain ships at V1 with only monitors; the canonical RV stack is documented-deferred. The P5 disclosure on the monitor primitives' methodology cards is what keeps this honest (per ADR 0006's same pattern of disclosing scope-limited V1 capability).
- `futures_pack_average_simple` (policy_futures) ships with a deliberate PR11 `NotImplementedError` for `curve_family = EUR_SHORT_RATE_FUT` because `policy_futures.yml` does not yet annotate the `delivery_month_type` (serial vs quarterly — Euribor strip mixes serials). SOFR + SONIA build cleanly. The refusal is the canonical "honest until data lands" pattern.

## Rollout plan

1. **This ADR** lands in the primitive-automation-relaunch PR alongside the change it records.
2. **The same PR** — base scaffolding (Domain enum + the 4 orchestrator files + the two empty sub-agent packages), the strip-aware and event_calendar shared fetchers, the hardened automation instruction set, and the new `primitive_catalog.yaml`.
3. **The OpenClaw factory** then builds the 11 futures primitives into the ready sub-agents (one primitive per build-review-fix-review cycle, per the catalog's `build_order`).
4. **Reconciliation back to `build`** — once the factory has produced and reviewed the futures primitives on `primitive_automation`, a separate reconciliation PR (analogous to the inflation reconciliation that brought ADR 0006's primitives onto `build`) ports them to `build`.
5. **Follow-ups (separate PRs):** parity fixtures (PR15 debt — the factory's PR15 rule requires fixtures from day one, but a backfill pass may still be needed for the inflation primitives that pre-dated PR15 enforcement); the bond-futures RV stack once D-repo + D-deliverable land (Phase 4).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-22 | Initial decision. Accepted. |
