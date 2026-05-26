# ADR 0012 — Event-primitive placement: distribute by conventional desk ownership, not by event-type sibling agent

**Status:** Accepted
**Date:** 2026-05-25
**Builds on:** ADR 0004 (event-calendar substrate — the `macro_data.event_calendar` table + `--mode event-calendar` extractor that ingests events independent of any consuming sub-agent's `instrument_master` keying), ADR 0006 (inflation-domain agents — the precedent for "open a new sub-agent" being an ADR-gated extension of the `Domain` closed family), ADR 0008 (event-playbook contract — defines the per-event metadata that every event primitive reads), ADR 0009 (WIRP time-series — substrate read-only precedent for `wirp_meeting_pricing`'s OIS placement).
**Operationalises principles:** PR3 (primitive residence by conventional ownership — the primitive-layer rule this ADR formalises at the *event-primitive* layer), P11 (domain isolation — each existing sub-agent's tool catalogue stays single-domain; no event-bearing instrument types leak across desk boundaries), P3 (consistency by contract — every event primitive follows the same placement rule), P8 (closed-family discipline — the `Domain` enum is the closed family this ADR explicitly does NOT extend), P10 (single source of truth — turns a folklore decision into a citable, durable record).
**Scope:** Records the placement rule applied to the four event primitives that landed in the 8-tool chat (PR #187 / #188 / #190 / #192) and forecloses the two alternative architectures (sibling `events_agent/`, scoped `rates_agent/macro_calendar/`) that future event-primitive PRs would otherwise have to re-evaluate. This is **not** a closed-family extension — it pins a rule that keeps the `Domain` enum *unchanged* and routes every new event primitive into one of the existing sub-agents.

---

## Context

The 8-tool primitive-automation batch (May 2026) shipped four event-derived primitives. Each had a placement question — events are conceptually their own data shape (release_date / actual / consensus / surprise per ADR 0004, or implied_rate / probability per ADR 0009), and a naive read of "event = its own thing" would have argued for either a sibling `events_agent/` package (mirroring how `inflation_indexed_bonds` and `inflation_swaps` got their own packages per ADR 0006) or a new `rates_agent/macro_calendar/` sub-agent (one rates-internal home for everything event-shaped).

Neither path was taken. The actual placements were:

| Primitive | Placement | PR | Desk-ownership argument |
|---|---|---|---|
| `calculate_cpi_surprise` | `rates_agent/inflation_swaps/` | [PR #187](https://github.com/QuantiFi-Copilot/Macro_Copilot/pull/187) | CPI surprises drive the inflation-swap and breakeven complex. The ZCIS / linker desk is the conventional consumer of CPI prints. |
| `calculate_nfp_surprise` | `rates_agent/sovereign_bonds/` | [PR #188](https://github.com/QuantiFi-Copilot/Macro_Copilot/pull/188) | NFP surprises drive the front-end of the nominal yield curve (2Y / 5Y). The sovereign desk is the conventional consumer of payrolls prints. |
| `calculate_wirp_meeting_pricing` | `rates_agent/ois/` | [PR #190](https://github.com/QuantiFi-Copilot/Macro_Copilot/pull/190) | WIRP per-meeting policy-rate pricing is an OIS-desk read by construction (the value IS the OIS-implied policy-rate path). |
| `fomc_surprise_label` *(deferred — TD #31)* | planned `rates_agent/ois/` | [PR #192](https://github.com/QuantiFi-Copilot/Macro_Copilot/pull/192) | FOMC hawkishness signals trade through the OIS curve (front-end repricing of the policy-rate path). |

Each placement was decided per PR. Without an ADR the rule is folklore: the *next* event primitive (`auction_tail` blocked on TD #28, the three D-bloomberg-forward-curve INGEST primitives blocked on TD #34, eventual ECB / BOE meeting-by-meeting trackers, eventual claims / retail-sales / PMI surprises) would have to re-derive the placement reasoning from scratch, with the very real risk that one PR resolves it differently and the catalogue fragments.

The PR3 rule already exists at the primitive contract layer: *"a primitive lives in the agent and sub-agent that conventionally owns its concept on a real institutional desk."* This ADR is that rule explicitly applied to events.

## Decision

### 1. Distribute event primitives across the *existing* rates sub-agents per PR3 conventional-ownership.

For every event primitive in the rates domain, ask: *which existing rates desk consumes this event most directly to update its trading view?* That desk's sub-agent is the residence. The event's *data shape* is irrelevant; only the *consuming-desk identity* matters.

The four precedents above instantiate the rule:

- CPI prints → inflation-swap / breakeven desk → `inflation_swaps/`.
- NFP prints → nominal sovereign front-end → `sovereign_bonds/`.
- WIRP per-meeting pricing → OIS desk by construction → `ois/`.
- FOMC hawk/dove labels → OIS desk (front-end repricing of policy path) → `ois/` (deferred per TD #31 / fomc_surprise_label gap — placement decision stands; build deferred).

For event primitives whose conventional desk-owner is not in scope today (e.g. ECB-statement surprises, retail-sales / PMI surprises, sovereign auction tails), the residence is decided by the same rule applied to the *eventual* sub-agent — and the primitive is deferred (per PR6) until that sub-agent exists if the answer is a sub-agent the platform has not yet opened.

### 2. The `Domain` closed family stays unchanged.

This ADR explicitly does NOT extend the `Domain` closed family in `orchestrator/contracts.py`. Adding a new domain would be a separate ADR per P8 + P11, on the precedent of ADR 0006. The four members today (`SOVEREIGN_BONDS`, `OIS`, `INFLATION_INDEXED_BONDS`, `INFLATION_SWAPS`) are sufficient: every event primitive scheduled for Phase 3 has a conventional-desk owner already in that set.

If a future event primitive's conventional desk-owner is *not* one of the four current sub-agents (a plausible example: cross-asset event-study primitives whose owner is a not-yet-built `cross_asset/` sub-agent), the right action is to open the sub-agent via a P11-gated ADR first, then place the primitive — never to slot the primitive into the wrong existing sub-agent "for now."

### 3. The event-calendar *substrate* is shared and lives at the substrate layer, not under any agent.

This separation matters: the `macro_data.event_calendar` table + the `--mode event-calendar` extractor + the `ingestion/event_calendar.py` shared module (all from ADR 0004) are **shared substrate**, not domain-owned. Every event primitive — regardless of which sub-agent it lives in — reads from the same `event_calendar` rows via the standard L1 access path. The substrate is asset-class-blind per P9; the primitives that read it are asset-class-aware and placed per this ADR.

This mirrors the existing substrate pattern: `macro_data.market_data_daily` is shared substrate; `calculate_curve_spread` (sovereign) and `calculate_ois_curve_spread` (OIS) both read it from their respective sub-agents.

### 4. Cross-domain event primitives still follow PR3; the read path is `shared/analytics/*`, never a cross-agent import.

When an event primitive *reads* data that lives in another sub-agent's domain (e.g. an inflation event primitive that wants to overlay an OIS series for context), the fetch goes through a `shared/analytics/*` helper, never via `from rates_agent.<other_sub_agent>` import. This already binds at the primitive contract layer (PR3 verify-step: *"a `grep` for `from <other_agent>` inside this primitive's `compute.py` is empty"*); we are re-stating it here because event primitives' cross-domain reads are *especially* tempting.

## Alternatives considered

### (A) Open a sibling `events_agent/` package per P11

**Rejected.** This would treat "event-derived" as its own domain, parallel to `rates_agent/`, `fx_agent/`, etc. The argument for it: event primitives share a data shape (release_date / actual / consensus / surprise) and a substrate (`event_calendar`), so a single agent could specialise in event-driven analyses.

The argument against it (and why it loses):

- **Violates P11's tool-selection-clarity rationale.** P11 says *each agent becomes a single-domain expert*. An `events_agent` straddling CPI events + payroll events + central-bank events + auction events would have a system prompt and tool catalogue that span multiple desk domains — the very thing P11 exists to prevent. A CPI-event question routed to `events_agent` would still need cross-agent composition with `inflation_swaps` to read the ZCIS leg; routing it to `inflation_swaps` (which already owns the consuming-desk catalogue) skips the round-trip entirely.
- **Treats data shape as the residence rule, which contradicts PR3.** PR3's rule is about *conventional desk ownership*, not about *what the data looks like*. A CPI release and a ZCIS rate are both "things the inflation-swap desk watches today"; partitioning by data-shape would split that view across two agents.
- **Forces a `Domain` closed-family extension** (per P8 + P11, opening a new agent requires an ADR — ADR 0006 is the precedent). Every new event sub-agent (auction-events, central-bank-decision-events, etc.) would compound the extension load. The current path requires zero `Domain` extensions for Phase 3's event primitives.
- **Breaks the existing four placements.** Adopting `events_agent` would require moving cpi_surprise out of `inflation_swaps/`, nfp_surprise out of `sovereign_bonds/`, and wirp_meeting_pricing out of `ois/` — a net-negative refactor with no analytical benefit.

### (B) Scope a `rates_agent/macro_calendar/` sub-agent (rates-local equivalent of A)

**Rejected** for the same reasons as (A), with one specific addition: this would still require a `Domain` extension (the `Domain` enum lives at `orchestrator/contracts.py` and is the supervisor's routing key, irrespective of whether the new sub-agent sits under `rates_agent/` or as a sibling). The P11 + P8 cost is identical; only the file-system hierarchy differs. The analytical and routing-clarity arguments against (A) carry over verbatim.

A weaker version — *"a `rates_agent/macro_calendar/` package that holds shared event helpers but no MCP server, no `Domain` member, no separate prompt"* — was also considered. That would be a `shared/`-style utility package mis-named under `rates_agent/`. The right home for that kind of helper is `shared/analytics/event_helpers.py` (or wherever the substrate naturally lives), not under any sub-agent. If a helper is genuinely needed it should land there, not as a phantom sub-agent.

### (C) Place every event primitive in `sovereign_bonds/` "by default" (cluster around the most-consumed event-driven series)

**Rejected.** The sovereign desk is *one* event consumer among several; placing CPI surprises there would deny the inflation-swap desk's conventional ownership of CPI prints, force the inflation-swap MCP server to import cross-agent (P11 violation), and concentrate unrelated event types under one agent's prompt (P11 single-domain-expert violation). The `inflation_swaps` placement of `cpi_surprise` was the correct call precisely because it matches conventional desk ownership.

### (D) Defer every event-primitive placement to per-PR judgment (the status-quo before this ADR)

**Rejected** because the status-quo *is* the cost this ADR is meant to remove. Per-PR judgment got the four precedents above right, but it is unstable: the next PR is one reviewer-swap or one new contributor away from reaching a different verdict. P10 (single source of truth) requires the rule to live in *one* citable place, not be re-derived per PR.

## Consequences

**Positive.**
- Every future event primitive has a citable placement rule. The next PR description either invokes ADR 0012 + names the consuming desk, or invokes AC8 (the placement is unclear → ask, surfacing a contract gap if PR3 + this ADR don't resolve it).
- The `Domain` closed family stays at four members. Phase 3 ships event primitives without triggering P8 extensions.
- The four existing placements (cpi_surprise / nfp_surprise / wirp_meeting_pricing / fomc_surprise_label) are now ADR-anchored; reviewers can cite ADR 0012 if any future PR proposes moving them.
- The `shared/analytics/*` substrate-layer rule for cross-domain event reads is restated explicitly, reducing the chance of a future event primitive cutting a cross-agent import shortcut.

**Negative / accepted.**
- Each event primitive's PR description must justify its placement under PR3 + ADR 0012. This is one extra sentence per new-primitive PR, paid once per primitive.
- Event primitives whose consuming desk does not yet have a sub-agent (e.g. a cross-asset event primitive) are *deferred* under this rule rather than placed somewhere convenient. That is the correct enforcement of P11; it is recorded as "negative" only because it means some primitives stay unbuilt until their proper home opens.
- This ADR does not resolve the orthogonal question of where the `event_calendar` *substrate* extensions live — that remains shared per ADR 0004. (Stated here only so a future reader does not mistake this ADR for a substrate-placement decision; it is purely a primitive-placement decision.)

## Verification

After this ADR lands:

1. The four precedent primitives' compute.py paths match the placements declared in §Context:
   - `rates_agent/inflation_swaps/tools/cpi_surprise/compute.py` exists.
   - `rates_agent/sovereign_bonds/tools/nfp_surprise/compute.py` exists.
   - `rates_agent/ois/tools/wirp_meeting_pricing/compute.py` exists.
   - `rates_agent/ois/tools/fomc_surprise_label/` does NOT exist (deferred per TD #31; planned residence is `ois/` per this ADR).
2. A grep for `from <other_agent>` inside each event primitive's `compute.py` returns zero matches (PR3 verify-step, restated by Decision 4 above).
3. `Domain` (in `orchestrator/contracts.py`) still has exactly four members (`SOVEREIGN_BONDS`, `OIS`, `INFLATION_INDEXED_BONDS`, `INFLATION_SWAPS`). No `EVENTS` or `MACRO_CALENDAR` member exists.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial decision. Accepted — formalises the placement rule applied to the four event primitives in PR #187 / #188 / #190 / #192. |
