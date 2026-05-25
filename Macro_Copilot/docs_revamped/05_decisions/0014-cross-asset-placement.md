# ADR 0014 — Cross-asset placement: defer until a desk-demanded cross-asset workflow is queued

**Status:** Accepted
**Date:** 2026-05-25
**Builds on:** ADR 0006 (inflation-domain agents — the canonical "open a sibling sub-agent via `Domain` closed-family extension" precedent the rejected Option B would invoke), ADR 0012 (event-primitive placement — distribute by conventional desk ownership per PR3, the most recent ADR pinning a placement rule and the format precedent for this file). Inherits the platform thesis in [`00_thesis/01_non_negotiables.md`](../00_thesis/01_non_negotiables.md) (P11 domain isolation) and the per-component contract for playbooks in [`02_components/playbook/README.md`](../02_components/playbook/README.md).
**Operationalises principles:** P8 (closed-family discipline — the `Domain` enum stays at four members; no `CROSS_ASSET` admission), P10 (single source of truth — pins the cross-asset placement decision in one citable record so future PRs do not re-derive it), P11 (domain isolation — each agent's tool catalogue stays single-domain; cross-asset signals are not colonised into `rates_agent/playbooks/`), P5 (honest disclosure — the deferral itself, its revisit triggers, and its scope are surfaced rather than left as folklore).
**Scope:** Records the verdict on Phase 3 / Stage 8's open question — how (if at all) the platform ingests cross-asset signals (VIX, MOVE, DXY, oil, gold, equity index levels) and where their primitives live. **No `Domain` extension; no new agent package; no new sub-agent; no new playbook.** This ADR forecloses the three concrete paths that future PRs would otherwise have to re-litigate.

---

## Context

Phase 3 / Stage 8 ([`tmp/primitive_expansion/phase3.md`](../../tmp/primitive_expansion/phase3.md)) raises the cross-asset placement question explicitly: when (and how) does the rates platform ingest cross-asset signals — equity-vol indices (VIX), rates-vol indices (MOVE), USD strength (DXY), oil, gold, equity index levels (SPX) — that desk PMs read alongside rates positions?

Three paths were proposed:

1. **Open a sibling `cross_asset_agent/` package** per P11, mirroring how `inflation_indexed_bonds_agent` and `inflation_swaps_agent` were opened in ADR 0006. Costs: a `Domain` closed-family extension (P8 + P11 ADR), MCP subprocess wiring (`MCP_SERVERS` + `DOMAIN_MCP_SERVERS`), supervisor router prompt update (`SUPERVISOR_SYSTEM_PROMPT`'s `AVAILABLE DOMAINS` + `DOMAIN SIGNALS`), system-prompt constant, `_build_domain_boundaries` label, and per-domain test surface — the full ADR 0006 substrate.
2. **Scope a `rates_agent/cross_asset/` sub-agent** — same as (1) but as a sub-agent inside `rates_agent/` rather than a sibling. Same `Domain` closed-family extension cost (the enum lives at `orchestrator/contracts.py` regardless of where the package sits on the filesystem); only the directory hierarchy differs.
3. **Defer entirely until a real cross-asset analysis is demanded.** No new agent, no new sub-agent, no new playbook. Cross-asset substrate stays out of `rates_agent/playbooks/`.

A prior reviewer correction — referenced in the work order driving this ADR — said *"do NOT colonise `rates_agent/playbooks/` with non-rates instruments without an ADR."* That correction is precisely the forklore this ADR makes durable: silently dropping a `cross_asset_signals.yml` into the rates playbook tree (or adding a `time_series_vix` field next to a sovereign yield primitive's `time_series` field) would violate P11 on first read and accumulate P11 debt with every subsequent edit.

### What "cross-asset signals" means here

The signals in scope of this question are equity-vol indices (VIX, V2X), rates-vol indices (MOVE), USD strength indices (DXY), commodities (front-month crude, gold), and equity index levels (SPX, NDX, SX5E). They are *price data the rates desk reads as context*, not data the rates desk owns by convention. A rates PM looking at a steepener trade may glance at VIX or DXY for risk-off context, but the rates desk does not maintain VIX or DXY conventions, does not have primitive-level expertise in vol surfaces or FX index construction, and does not need cross-asset substrate to answer any of the desk-useful questions Phase 1–3's primitives target.

The question this ADR answers is *not* "should the rates platform ever cover cross-asset workflows?" — that question's answer is plausibly yes, eventually. The question this ADR answers is *"should Round 3 / Stage 4 open the cross-asset substrate without a concrete demanded workflow on the queue?"* — and the answer is no.

## Decision

### 1. Defer. No cross-asset agent, no cross-asset sub-agent, no cross-asset playbook in Round 3.

The platform's V1 catalogue does not include a cross-asset analysis. No primitive in the V1 catalogue requires VIX / MOVE / DXY / oil / gold / SPX as an input. No workflow template in the V1 catalogue terminates in a cross-asset artifact. Round 3 / Stage 8's question is therefore *premature* under the platform's existing discipline:

- **P11 (domain isolation)** — opening a new agent should be tied to a real desk-demanded workflow, not to a speculative future workflow. ADR 0006 was *demanded* by 15 inflation primitives + their two distinct curve-family universes already shipped on `primitive_automation`. A cross-asset agent today has zero such pull.
- **P8 (closed-family discipline)** — extending `Domain` is the heaviest cross-cutting change in the platform's component system (per ADR 0006 itself: the extension touches the enum, the MCP-server map, the system-prompt map, the supervisor router prompt, the boundary-label builder, and the per-domain test gauntlet). Doing it speculatively would invert the discipline.
- **P5 (honest disclosure)** — silently colonising `rates_agent/playbooks/` with cross-asset signals would hide a cross-domain dependency in a rates-shaped surface. The deferral makes the absence of cross-asset substrate explicit.

`Domain` (in `orchestrator/contracts.py`) stays at four members: `SOVEREIGN_BONDS`, `OIS`, `INFLATION_INDEXED_BONDS`, `INFLATION_SWAPS`. No `CROSS_ASSET`, no `EQUITY`, no `FX`, no `COMMODITY` member is added.

### 2. Cross-asset signals do not land in `rates_agent/playbooks/`.

The `rates_agent/playbooks/` directory holds playbooks for instruments the rates desk conventionally owns: sovereign bonds, OIS, inflation-linkers, ZCIS, policy futures, bond futures (forthcoming), repo (forthcoming, gated on TD #29 / #30). Cross-asset signals are explicitly out of scope for this directory.

A future PR that wants to ingest a cross-asset signal *must* re-open this ADR (per "Revisit triggers" below). It must not silently add a `cross_asset.yml` playbook, must not graft a `time_series_vix` field onto an existing rates primitive, and must not add a cross-asset `curve_family` value to any existing sovereign / OIS playbook.

### 3. The event-calendar substrate's domain-blindness is unaffected.

ADR 0004's `macro_data.event_calendar` table + `--mode event-calendar` extractor + `ingestion/event_calendar.py` shared module is asset-class-blind by P9 — events like FOMC / ECB / BOE meetings and CPI / NFP / claims releases are *not* cross-asset signals in this ADR's sense. Event primitives consuming those rows are placed per ADR 0012 (PR3 conventional desk ownership), entirely within the existing four sub-agents. This ADR does not constrain the event substrate.

The distinction: a *macro event* (a CPI release, a FOMC decision) is consumed by the desk that conventionally owns the instrument family that prices the event — already placed correctly per ADR 0012. A *cross-asset signal* (a VIX level, a DXY index) is not consumed by any rates desk by convention; it is read for context. The two have different placement rules.

### 4. The deferral is not a permanent prohibition.

This ADR records the *current* state of cross-asset substrate as "deferred, no concrete workflow queued." It does not foreclose the path. "Revisit triggers" below name the concrete conditions under which this ADR is re-opened.

The deferral pattern follows ADR 0006's logic in reverse: ADR 0006 opened two sibling sub-agents *because* 15 primitives were ready to ship and demanded their domain residences. This ADR keeps a cross-asset sub-agent closed *because* zero primitives demand it.

## Alternatives considered

### (B) Open a sibling `cross_asset_agent/` package per P11 + ADR 0006

**Rejected for V1.** This is the canonical "open a new agent" path; the substrate exists (ADR 0006 demonstrates exactly how it works). The blocker is not architectural — it is the absence of demand.

Triggering this path requires:
- An ADR extending `Domain` with a `CROSS_ASSET` member, modelled on ADR 0006.
- A first cross-asset primitive (or a first cross-asset workflow template) landing in the same PR or in an explicit prerequisite chain — per ADR 0006's discipline ("the extension is admitted only when there is a primitive that emits the new type AND a consumer that uses it" applied at the agent level).
- The full ADR 0006 wiring: `MCP_SERVERS` entry, `DOMAIN_MCP_SERVERS` entry, `_DOMAIN_PROMPTS` entry, supervisor router prompt's `AVAILABLE DOMAINS` + `DOMAIN SIGNALS` paragraphs, `_build_domain_boundaries` label, system-prompt constant.
- Per-domain primitive resolver in `cross_asset_agent/workflows/__init__.py`.
- `tests/test_orchestrator_domains.py` updates so the test asserts the new member is wired through all three coupled surfaces.

None of that is justified by an empty cross-asset catalogue. Opening the agent speculatively would also dilute the supervisor's single-domain-expert claim (P11's rationale 2): adding `CROSS_ASSET` to the router's `AVAILABLE DOMAINS` paragraph would teach the LLM to consider cross-asset routing for every rates-shaped prompt, which is exactly the misrouting P11 exists to prevent.

### (C) Scope a `rates_agent/cross_asset/` sub-agent (rates-local equivalent of B)

**Rejected** for the same reasons as (B), plus the explicit overlap with ADR 0012's "Alternative B" rejection: *"This would still require a `Domain` extension (the `Domain` enum lives at `orchestrator/contracts.py` and is the supervisor's routing key, irrespective of whether the new sub-agent sits under `rates_agent/` or as a sibling). The P11 + P8 cost is identical; only the file-system hierarchy differs."* (ADR 0012 §"Alternative B".)

A `rates_agent/cross_asset/` sub-agent would also be a deeper P11 violation than (B): a sub-agent under `rates_agent/` implicitly claims that cross-asset signals are *rates-owned*, which is exactly the conventional-desk-ownership rule ADR 0012's PR3 forecloses. Cross-asset signals are not rates-owned; placing them under `rates_agent/` (even as a sub-agent) would invert that.

### (D) Colonise `rates_agent/playbooks/` with cross-asset signals "as a transitional step"

**Rejected.** This is the explicit prior-reviewer correction this ADR is making durable. A `cross_asset.yml` playbook under `rates_agent/playbooks/` would:
- Violate P11 by routing cross-asset signals through the rates agent's MCP subprocess + system prompt.
- Violate ADR 0012's restatement of PR3 (Decision 4 — "the read path for cross-domain primitives is `shared/analytics/*`, never a cross-agent import").
- Make the deferral invisible to a future reader, who would assume the cross-asset surface is rates-owned and continue the pattern.

The substrate's existing `macro_data.market_data_daily` table is asset-class-blind — it would happily accept a VIX row tomorrow. That capability does not justify the placement. *Schema permits, governance forbids.* This ADR keeps the governance explicit.

### (E) Add a single cross-asset primitive into an existing rates sub-agent (e.g. a `vix_level` primitive under `sovereign_bonds/`)

**Rejected.** A cross-asset primitive in a rates sub-agent would force the rates sub-agent's MCP server to register tools whose `Domain` membership is wrong on first read. The sub-agent's system prompt would also describe a tool the rates desk does not own. This is the primitive-level analogue of (D).

## Consequences

**Positive.**
- The `Domain` closed family stays at four members. P8 is preserved at full strength.
- `rates_agent/playbooks/` stays single-domain. P11 is preserved.
- A future cross-asset workflow has a clear gating procedure (this ADR's "Revisit triggers") rather than a per-PR judgment call.
- The supervisor's tool-selection accuracy claim (P11's rationale 1) is not diluted by a speculative cross-asset `DOMAIN SIGNALS` paragraph.
- The prior reviewer correction is now ADR-anchored and citable.

**Negative / accepted.**
- A future user prompt that genuinely wants a cross-asset analysis ("show me the 10Y UST yield rolling correlation to VIX") will hit a refusal until this ADR is re-opened with a concrete first workflow. That refusal is correct per P6 — refusal beats silent under-coverage — and is the cost the deferral discipline pays.
- The platform's scope claim ("multi-asset eventually") is unaffected; this ADR records that V1 does not yet operationalise it.
- Some cross-asset signals (VIX especially) are routinely referenced in rates desk commentary. The deferral means those references stay verbal until the substrate opens; that is a documented P5 limitation, not a P12 violation (we are not approximating VIX from a proxy; we are simply not ingesting it yet).

## Revisit triggers (the conditions under which this ADR is re-opened)

This ADR is re-opened — via a follow-on ADR that supersedes the relevant sections — when any of the following lands:

1. **A desk-demanded cross-asset workflow is queued** for the V1 catalogue. Examples that would qualify: an FX-hedged-UST yield workflow (rates × FX); a 10Y UST / VIX correlation regime study (rates × equity vol); a USD-DXY-conditioned sovereign-spread screen (rates × FX). Each demands a cross-asset substrate the deferral excludes today.
2. **A second source-of-record adapter** is connected (per P7's portability claim) and its native data shape covers cross-asset signals as a first-class surface. Re-opening at that point matches the deferral's "no demand today" framing — if a new adapter brings cross-asset data for free, the cost calculation changes.
3. **A formal cross-asset substrate plan** is drafted (analogous to Phase 1 / Phase 2 / Phase 3 plans in [`tmp/primitive_expansion/`](../../tmp/primitive_expansion/)) with concrete first primitives, first workflows, and first agent residence. The plan would be the prerequisite to a `Domain` extension ADR — not its replacement.

Conditions that do *not* re-open the ADR:
- A user prompt asking for cross-asset analysis ad-hoc. The right answer is a refusal naming the missing substrate, per P6.
- A reviewer wanting "to set things up for cross-asset later." Speculative substrate is exactly what P11 + P8 reject.

## Verification

After this ADR lands:

1. The file `docs_revamped/05_decisions/0014-cross-asset-placement.md` exists and is referenced by future cross-asset-shaped PRs.
2. `orchestrator/contracts.py`'s `Domain` enum has exactly four members — unchanged from the post-ADR-0006 state.
3. `rates_agent/playbooks/` contains no cross-asset playbook (no `vix.yml`, `move.yml`, `dxy.yml`, `oil.yml`, `gold.yml`, `spx.yml`, `cross_asset_signals.yml`).
4. No primitive under `rates_agent/*/tools/` references a cross-asset `curve_family` value (e.g. `VIX_INDEX`, `DXY_INDEX`, `SPX_LEVEL`) in its `*Input` schema's allowed-values set.
5. A grep for `cross_asset` inside `rates_agent/` returns zero matches (this ADR's filename is the only place that string appears in the docs-revamped tree).

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial decision. Accepted — defers cross-asset substrate until a desk-demanded workflow is queued. Rejects sibling-agent / sub-agent / playbook-colonisation paths for V1. Names the three concrete revisit triggers under which the deferral is re-opened. |
