# What Macro Copilot Is

> The product thesis. Read this once at the start of any work on the platform; cite it by section when a decision turns on what is in scope.

**Version:** v1.2
**Last reviewed:** 2026-05-16
**Status:** load-bearing. Changes require an ADR in [`../05_decisions/`](../05_decisions/) and a coordinated bump to any dependent file in [`01_non_negotiables.md`](01_non_negotiables.md).

---

## In one sentence

For institutional macro portfolio managers, Macro Copilot turns the multi-step research a desk runs by hand every day — cross-curve scans, event studies, regime comparisons, attribution decompositions, quick backtests — into a few natural-language prompts, returning each answer with its methodology and data sources visible on the page, and saving every analysis as a workspace the desk can revisit later.

> *Internal/engineering view of the same product (for technical readers only):* a typed-DAG composition platform that sits above an institutional source-of-record data layer, composing finance-aware primitives and finance-blind operators into auditable graphs. The PM-facing description above is the one to use in any external or quick-pitch context.

## The thesis

A senior macro portfolio manager spends thirty to sixty minutes of a typical workday on tasks that are mechanical at their core: scanning a curve universe for statistical extremes, comparing two regimes' behaviour around a policy event, decomposing a yield move into curve components, sketching a back-of-envelope backtest. None of this work *requires* a portfolio manager; it requires primitives, operators, and a router that knows which to call.

The market does not lack data — institutional desks already have a market-data terminal, an internal data lake, or both. What is missing is the layer between *data* and *answer*: a substrate where each computation is a typed node, each composition is a recoverable graph, and each result can be audited, replayed, overridden, and extended. Macro Copilot is that layer.

The platform's leverage comes from three commitments that compound:

- **Composability.** Every workflow is a DAG over the same small set of primitives and operators. Adding one primitive expands the space of expressible analyses far more than linearly.
- **Auditability.** Every artifact carries a content-addressed lineage chain. There are no black-box answers; the DAG is the answer.
- **Replayability.** Every analysis is an addressable workspace. The substrate ships in phases — Phase 0 (today) delivers deterministic hashing, methodology pinning, restart resilience, and divergence detection; Phase 1+ closes the byte-identical re-execution loop on top of it. The phasing is documented in P4 of [`01_non_negotiables.md`](01_non_negotiables.md); the commitment is non-negotiable, the closure is sequenced.

The current implementation is built against Bloomberg as the source-of-record data layer. The platform's relationship to that layer is stated plainly: *Bloomberg is the pricing engine; we are the intelligence layer.* The architecture is deliberately split so that the *vendor* lives below L1 and *the platform* lives above L1; a second source-of-record adapter — an internal data lake, a vendor API, a hedge fund's cleaned warehouse — can populate the same substrate without rewriting the intelligence layer. P7 in [`01_non_negotiables.md`](01_non_negotiables.md) is what defends that seam.

## Who this product is for

The target user is a **discretionary macro portfolio manager at an institutional hedge fund**, working primarily in rates today and extending into FX and other asset classes over time. The PM:

- Has at least one market-data terminal open already, and another two screens of charts and notes;
- Already knows the analytical patterns (event studies, regime-conditioned relationships, attribution decompositions, cross-sectional screens, backtests) by hand;
- Is impatient with anything that wastes a discretionary thinker's time on mechanical data assembly;
- Will not tolerate a hidden assumption — a single wrong number from the tool kills trust permanently;
- Cares about a small number of high-stakes decisions, not high-throughput automation.

The product is *not* aimed at:

- Junior analysts learning the discipline (different tool, different shape — they need explanation, not composition);
- Quant systematic teams running a vectorised research stack (their pipeline does not need an LLM router);
- Retail traders or any audience without institutional source-of-record access (the L1 substrate is the floor; the product collapses without it).

## What makes the platform genuinely valuable

Macro Copilot exists because of five capabilities that are absent — separately or in combination — from any single existing tool a macro PM uses today:

1. **Natural-language access over a structured analytical universe.** A PM types *"BTP-Bund 2σ event study at the 5Y tenor"*; the platform recognises the archetype, binds the slots, executes the DAG, returns a typed terminal artifact, and renders it on a workspace with every methodology visible. No keystroke navigation, no per-screen lookups.
2. **Cross-instrument scanning.** Existing terminals are designed one screen at a time. The platform scans full curve universes — every tenor, every country, every benchmark — in a single primitive call, ranks by statistical extremes, and surfaces the outliers.
3. **Automated regime detection from declared rules.** Manual regime labelling is tedious and inconsistent. The platform produces regime classifications from declared rules (curve direction, volatility band, policy event) and uses them as inputs to downstream operators — a closed loop instead of a manual one.
4. **Multi-step analytical reasoning composed into a single graph.** Today, via five closed-family workflow archetypes (`event_study`, `regime_conditioned_relationship`, `attribution_decomposition`, `cross_sectional_screen`, `backtest`), the platform composes multi-step DAGs in one prompt with one lineage chain. Phase 2+ extends this to open DAGs the LLM composes from the operator catalogue, with the same audit trail.
5. **Auditable, replayable workspaces.** Every analysis is a content-addressed graph plus a workspace URL. The PM revisits the workspace, overrides a convention, sees the variant side-by-side, promotes the variant if they like it, and the URL is share-able across the desk. No re-running from scratch; no losing the prior result.

Each of these is technically reachable today on a Bloomberg terminal with enough manual labour. The platform's value is that the five compound: a session that takes a PM thirty minutes by hand condenses to a few prompts on the platform, and the audit trail is *better* than the manual version because every step is recorded structurally rather than by note-taking.

The longer-horizon value is the **substrate's portability**. The same primitives, operators, and workflow templates that run on rates today will run on FX and equities tomorrow because the operator layer is finance-blind (P9) and the data substrate is isolated below L1 (P7). The compound moat is not *"we do rates well"*; it is *"we do every macro asset class on one substrate the user already trusts."*

## The substrate in one paragraph

Five architectural layers, named in the project's own vocabulary:

- **L1 — Data substrate.** Playbook-declared ingestion populates `instrument_master` + `time_series`. The boundary above which no new code knows the source-of-record vendor.
- **L2 — Primitives.** Finance-aware deterministic compute, one folder per primitive under [`../../rates_agent/`](../../rates_agent/), four-file shape (`config.yaml` + `schemas.py` + `compute.py` + `__init__.py`), methodology pinned in `config.yaml`.
- **L3 — Operators.** Finance-blind structural transforms over typed artifacts, in [`../../shared/operators/`](../../shared/operators/), three-file shape, promoted to the shared catalogue only when used across ≥3 archetypes.
- **L4 — Workflow substrate.** DAG model, executor, registry, validator, in [`../../shared/workflow/`](../../shared/workflow/).
- **L5 — Workflow templates.** Closed-family DAG archetypes in [`../../rates_agent/workflows/`](../../rates_agent/workflows/), topology locked, slot-driven.

Each domain is implemented as its own agent — a self-contained package (`rates_agent/`, future `fx_agent/`, etc.) that owns its primitives and presents them to the orchestrator through an MCP server. A domain agent's registry only contains that domain's tools; cross-domain composition happens at the supervisor layer in [`../../orchestrator/`](../../orchestrator/), which routes to multiple agents and composes their structured outputs. This is P11 in [`01_non_negotiables.md`](01_non_negotiables.md), and the reason a "FX-hedged UST yield" answer cleanly combines a Rates-agent result and an FX-agent result without either agent knowing about the other.

Two construction modes share the substrate:
- **Conversational** — the Ask page; the LLM router emits either a template binding (Tier 1) or an open DAG (Tier 2, Phase 2+); the executor runs it.
- **Visual** — the Build page; the user composes primitives and operators directly into a workspace, overriding conventions and forking variants in place.

Both modes produce the same typed artifacts with the same lineage. The full architectural reference lives in [`../01_architecture/`](../01_architecture/) — this paragraph is the orientation, not the spec.

## What we are not building

Scope discipline is a thesis-level commitment. The platform exists *because* it refuses to do several things that would dilute its identity, accuracy, trust, or portability. Each refusal is deliberate; each one preserves a specific property of the product.

| Out of scope | Why |
|---|---|
| **A pricing engine.** We do not bootstrap curves, price options, or compute OAS from raw cashflows. | The source of record already does this to a bar we cannot match. Recomputing creates parity risk that destroys trust on first divergence. The operative rule is the Bloomberg Accuracy Boundary (P12): *ingest, do not recompute.* |
| **A retail or junior-analyst product.** | The data-substrate floor (institutional source-of-record access) makes this physically impossible. The product would degrade into a worse market-data terminal without one, and the value proposition collapses. |
| **A general-purpose LLM chat tool.** | The platform's value is computational — typed DAGs over a registry of building blocks. Knowledge retrieval and prose synthesis are adjacent capabilities; they are routed through the orchestrator's top-level intent classifier (computational vs. retrieval vs. interpretive), not through the DAG path. The orchestration contract is documented in `02_components/orchestration/` (forthcoming). |
| **A trading bot.** | The platform surfaces analyses; it does not place orders, route trades, or interact with execution venues. Phase-1 backtests are frictionless (mid-price, no transaction costs, OIS proxy for repo) and disclose every assumption on the methodology card under P5. |
| **A multi-tenant SaaS product (yet).** | Until institutional persistence, isolation, audit, and compliance gates are designed end-to-end, multi-tenancy is a future concern. The current substrate is single-user / single-org by deliberate scope. |
| **Half-correct calculations.** | If a value cannot be computed to source-of-record-grade accuracy, the answer is to *ingest* the source-of-record value. If neither is possible, the answer is to *refuse* (P6) with a specific reason. No proxies that pretend to be the real thing. This is the operative anti-pattern under the Bloomberg Accuracy Boundary (P12). |
| **A vendor-locked product.** | The L1 read contract is the boundary. New code is written against the contract, not against a specific vendor's surface (P7). The platform's long-term value is its portability into any institutional data environment. |
| **Manual data pipelines.** | Playbooks declare the ingestion contract. New instruments are added by editing a YAML universe block, not by writing bespoke Python. Mechanical ingestion is the floor; per-instrument hand-coding is a P1 violation. |
| **A black-box recommender.** | The DAG *is* the answer. We do not surface conclusions whose derivation is hidden. Methodology cards (P5) make every assumption visible on the surface, not behind a click. |
| **Open-ended LLM code generation against the codebase.** | New primitives, operators, and templates pass through the canonical contract, the relevant runbook, and the matching gate. Agent-generated artifacts that bypass the contract are P3 violations regardless of how clean the diff looks. |

Each item above is a strategic refusal, not a deferred backlog item. Re-categorising one of these as in-scope is a thesis change requiring an ADR.

## The commitments behind this thesis

The thesis above is enforced operationally by the principles in [`01_non_negotiables.md`](01_non_negotiables.md). Each commitment the platform makes to the user maps to one or more principle IDs:

| Commitment to the user | Enforced by |
|---|---|
| "Every number you see comes from a lineage chain you can trace." | P4 (determinism), P5 (disclosure), P10 (single source of truth) |
| "Every methodology is on the card, not hidden in code." | P5 |
| "Every analysis you build today opens to the same numbers months from now." | P4 (commitment) — Phase-0 substrate today, Phase-1+ closes the byte-identical re-execution loop |
| "When the platform cannot do what you asked, it says so specifically." | P6 |
| "Every number the platform produces either matches source-of-record accuracy when we compute it, or comes from the source of record directly — we do not ship inferior approximations." | P12 + P2 + P6 |
| "Every primitive looks like every primitive; every operator looks like every operator." | P3 |
| "Each agent the platform uses is a single-domain expert — the Rates agent only sees rates tools, the FX agent only sees FX tools, and cross-asset queries are composed at the orchestrator layer." | P11 |
| "The substrate that runs rates today will run FX and equities tomorrow without a rewrite." | P7, P9, P11 |
| "The platform plugs into whichever institutional data substrate you already have." | P7 (the L1 contract is the boundary; current adapter is Bloomberg, second adapter is the portability proof) |
| "Extending the platform happens through documented, reviewed decisions — never casual edits." | P1, P8, ADR discipline |
| "An answer the platform gives in May is the same answer in November." | P2, P4, P5 together |

The principles inherit the thesis; they do not replace it. Read this file once; cite the principles per the cheat sheet in [`01_non_negotiables.md`](01_non_negotiables.md).

## How this thesis changes

The contents of this file are thesis-level commitments. They change slowly, in coordinated PRs, with explicit decision records. The procedure:

1. **Open an ADR** in [`../05_decisions/`](../05_decisions/) describing the proposed change and its consequences for principles, contracts, runbooks, and roadmap. Thesis changes have the largest downstream footprint in this docs tree; budget the review accordingly.
2. **Land the ADR and the thesis change in the same PR** — never separately. The ADR records the *why*; the thesis records the *what*. They must agree.
3. **Coordinate updates** to any principle (`01_non_negotiables.md`), contract (`02_components/*/contract.md`), or roadmap entry (`06_roadmap/`) that depends on the thesis-level claim. Stale downstream files after a thesis change are P10 violations.
4. **Bump the version** of this file (v1 → v2) and update `Last reviewed`. The version log at the bottom of this file records every change.

Thesis stability is a feature, not a bug. Slow change is what makes the principles trustworthy and what lets downstream contracts compound on a stable foundation.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.2 | 2026-05-16 | Stand-alone-tree pass: removed every cross-link out of this docs tree into the prior project documentation. Quotes previously attributed to prior docs are now stated inline as the platform's own doctrine. References to prior design notes (Tier-1/Tier-2 split etc.) replaced with forward pointers to forthcoming files in this tree. | (pending — `0002-product-thesis.md`) |
| v1.1 | 2026-05-16 | (a) Replaced the one-sentence pitch with PM-language (dropped "typed-DAG composition platform" and other technical jargon; technical paraphrase kept as a parenthetical for engineering readers). (b) Added domain-agent + supervisor description in the substrate paragraph, citing P11. (c) Added commitments rows for P11 (single-domain agents) and P12 (Bloomberg Accuracy Boundary); restructured the prior "computes to the source-of-record bar or refuses" row to cite the full P12 + P2 + P6 trio. | — |
| v1 | 2026-05-16 | Initial product thesis. | — |
