# Non-Negotiables

> Ten principles that bind every line of code, every documentation page, and every PR in this repository.

**Version:** v1.3
**Last reviewed:** 2026-05-16
**Status:** load-bearing — any change after first-ADR adoption requires an ADR in [`../07_decisions/`](../07_decisions/). Pre-canonical revisions are recorded in the version log at the bottom of this file.

---

## What this file is

This is the small, fixed set of rules that every other document in [`docs_revamped/`](..) inherits from. Component contracts, runbooks, standards, gates, and ADRs all derive from the principles stated here. If those downstream files conflict with this one, this one wins.

It is intentionally short. Ten principles, each on one page, each falsifiable. If a principle here cannot be checked by a reviewer in under sixty seconds, it is too vague — open a PR to tighten it.

## How to read this file

For your first read: scan the **Quick index** below, then read every principle's **Rule** line. That alone gives you the whole spec. The **Rationale**, **Verify**, and **Anti-patterns** sections are reference material for when a question or a PR turns on a specific principle.

For ongoing work: do not re-read the file from top to bottom. Look up the specific principle by ID when it comes up.

## How to cite a principle

Every principle has a stable ID (`P1`–`P10`). Cite the ID, not the text:

- In a commit message: `fix: align curve_spread test to P3 — 4-file shape`
- In a PR comment: `This change violates P9 (operator imports from rates_agent/)`
- In a code comment (rare; usually unnecessary): `# P5: source must be explicit, not inferred`
- In a runbook step: `Before merging, verify P2 and P6 with the admission checklist.`

IDs never change meaning. If a principle is superseded, the ID stays archived and a new ID is assigned to the replacement — same convention as ADRs. See **Changing a principle** at the bottom of this file.

## Quick index

| ID | Principle | One-line rule |
|---|---|---|
| **P1** | Future-proofed design | Every component is built as a permanent piece of the product. No placeholders, no shortcuts, no MVPs that bypass the canonical contract. |
| **P2** | Accuracy is non-negotiable | Every component meets its accuracy bar before it merges. If accuracy cannot be met, ingest the value from a source of record; if neither is possible, refuse. |
| **P3** | Consistency by contract | Every instance of a repeatable component type follows the same contract. There are no special instances. |
| **P4** | Determinism and replayability | Every artifact is content-addressed and reproducible. Same inputs produce byte-identical outputs across machines, Python versions, and time. |
| **P5** | Honest disclosure | Every methodology choice, convention default, and known limitation is visible on the user-facing surface. Hiding limitations is a defect. |
| **P6** | No silent failure | Compute layer raises typed exceptions; optional lookups are explicitly typed; transport boundaries convert to envelope contracts. Never a fabricated answer. |
| **P7** | Source-of-record boundary and vendor SDK isolation | Vendor SDKs and vendor-shaped logic live only in L1 adapters. Net-new L2–L5 code reads from the L1 contract, not from vendor-shaped surfaces. |
| **P8** | Closed-family discipline | Small canonical enumerations (archetypes, artifact types, source tags) are closed. Extensions are deliberate, ADR-recorded decisions. |
| **P9** | Finance-blind operator boundary | Operators contain no domain knowledge. Domain lives in primitives and playbooks; the boundary is absolute. |
| **P10** | Single source of truth | Every fact is defined in exactly one place. Every other reference is a link, not a copy. |
| **P11** | Domain isolation | Each domain agent has access only to its own tools and data. Cross-domain queries are orchestrated by the supervisor, which composes structured outputs from multiple agents. |
| **P12** | Bloomberg Accuracy Boundary | Never recompute what the source of record provides better than we can. Ingest, compute to indistinguishable accuracy, or refuse — never ship an inferior approximation. |

## How these principles bind

Stating a rule does not enforce it. Three lightweight mechanisms are planned to make the principles bind in practice. Two are documented here; one requires a follow-on PR.

1. **CLAUDE.md hook *(planned — not yet active)*.** The repo's [`CLAUDE.md`](../../CLAUDE.md) currently contains only graphify instructions. A follow-on PR adds a section instructing any AI agent to consult this file at session start and cite the relevant principle ID in commit messages. Until that PR lands, agent-side enforcement is by explicit prompt only.
2. **Per-component contracts inherit by ID.** Every `02_components/*/contract.md` opens with an explicit list of the principles it operationalises for that component type. The contract is the principle's local expression. Read the principle once, then the contract — do not re-derive.
3. **Review gates cite IDs.** Every checklist in [`../05_review_gates/`](../05_review_gates/) has lines tagged with the principle ID being verified. A reviewer's eye is trained to scan for P-numbers.

No tooling. No CI hook on this file. The enforcement is cultural and procedural. That is enough provided everyone — human and agent — uses the IDs consistently. The CLAUDE.md hook PR is tracked as an open follow-on; until it lands, this file is bound by human discipline alone.

---

## The principles

### P1 — Future-proofed design

**Rule.** Every component is built as a permanent piece of the product, not a placeholder, not an MVP, not a shortcut. If a code path is not yet ready, it raises `NotImplementedError` with an explicit, tested, contract-conformant refusal — it does not pretend to work. The work is either done to the standard of the canonical contract or it is not merged.

**Rationale.** Hacks compound. A "placeholder" never gets replaced; it gets extended by another placeholder. The expansion claim (200+ tools, multiple agents, multiple asset classes) collapses the moment a single component is built as a shortcut, because every later component built against it inherits the shortcut. The codebase carries the cost of every shortcut forever.

There is a deliberate distinction between **shortcuts** (forbidden) and **documented refusal surfaces** (required). A primitive that supports `method=A` and explicitly raises `NotImplementedError` for `method=B` — with `B` listed under `planned_extensions` in its `config.yaml` and a test asserting the refusal — is correct. A primitive that *pretends* to support `B` via an inferior proxy and does not say so is a P1 (and P5) violation. Refusal is honesty; pretence is debt.

**Verify.**
- Would this code make sense if it were the *only* implementation of this purpose we ever wrote?
- Does the file shape match the canonical contract for the component type? (Primitive: 4-file shape. Operator: 3-file shape. Workflow: `template.yaml`. See [`02_components/`](../02_components/).)
- Are TODOs that will never be addressed deleted, not added?
- Is the test coverage matched to the production bar, not just to "make CI green"?
- Is the file named after its purpose, not its temporal state?
- Is every `NotImplementedError` accompanied by: (a) a `planned_extensions` entry in the relevant `config.yaml`, (b) a test that asserts the refusal, and (c) a methodology-card disclosure (per P5)?

**Anti-patterns (auto-reject).**
- Files named `*_v2.py`, `*_temp.py`, `*_old.py`, `*_new.py`, `*_wip.py`, `*_quick.py`
- `# TODO: refactor`, `# TODO: phase 2`, `# TODO: cleanup later` without a linked ADR or roadmap item
- "MVP" implementations that skip the canonical contract (e.g., a primitive without `config.yaml` because "we'll add it later")
- Inline magic constants where a `Convention` belongs
- "It works in the demo" as merge justification
- Disabling a test with `@pytest.mark.skip("flaky")` instead of fixing the flake or the test
- Bare `raise NotImplementedError` without a message, without a `planned_extensions` reference, and without a test

**Exceptions.** None for *shortcuts*. *Documented refusal surfaces* (`NotImplementedError` with the three preconditions above) are not exceptions — they are the canonical way to ship an incomplete contract honestly.

**Relates to.** P3 (consistency by contract is the enforcement mechanism), P10 (no parallel "v2" copies).

**Operational application.** [`02_components/*/contract.md`](../02_components/), [`04_standards/file_and_folder_layout.md`](../04_standards/), [`05_review_gates/`](../05_review_gates/)

---

### P2 — Accuracy is non-negotiable

**Rule.** Every component, regardless of size, meets its accuracy bar before it merges. The bar is stated in the component's contract and tested in [`06_quality_and_evals/`](../06_quality_and_evals/). When a value cannot be computed to acceptable accuracy, the answer is to *ingest* the value from a source of record. When neither computation nor ingestion is possible, the answer is to *refuse* (per P6). It is never to guess, approximate silently, or ship a half-correct calculation.

**Rationale.** A wrong primitive contaminates every workflow that uses it. The cost of a wrong number compounds; the cost of an extra test does not. The "lineage is the product" claim under P4 is empty if the values in the lineage are themselves wrong. This principle is what makes the platform's audit story defensible.

**Verify.**
- The 3-test pattern (compute + wiring + parity) passes on every primitive. See [`04_standards/test_patterns.md`](../04_standards/).
- Parity is tested against the source-of-record data, with tolerance stated in the test docstring — not chosen post-hoc to make the test pass.
- Tolerance for derived calculations is stated in the component's `contract.md`, not invented per-test.
- No `# TODO: add test` or `# TODO: improve accuracy` in a merged PR.
- Coverage thresholds in [`04_standards/test_patterns.md`](../04_standards/) are met.

**Anti-patterns (auto-reject).**
- A primitive without a parity fixture against the source of record
- A tolerance that is not stated in the component's `contract.md` (tolerance must be justified by the accuracy requirement, not chosen during test-writing to make the assertion pass)
- A test suite missing failure-case assertions — every parity test must include at least one input that should fail validation, and the failure mode must be the typed exception the contract specifies
- Edge cases (empty inputs, single-row inputs, dtype boundary, NaN handling) absent from the test file
- "It worked on one example I tried" as merge justification
- Wrapping a known-incorrect calculation in a comment that says "approximate" or "good enough"
- Skipping a parity test because "the data is hard to mock" — the data is the point of the test
- Shipping a proxy that diverges materially from the source of record. The bar is *indistinguishable to a PM* per P12: a single-digit-bp difference on a 5Y5Y forward is acceptable; a 15-percentage-point WIRP divergence is not.
- Using `pytest.approx` with a tolerance not derived from the contract's stated bar

**Exceptions.** None. If accuracy genuinely cannot be met, refuse (P6) — do not ship.

**Relates to.** P5 (any approximation must be disclosed), P6 (the alternative to inaccuracy is refusal, not guesswork), P4 (accuracy without determinism is unverifiable).

**Operational application.** [`04_standards/test_patterns.md`](../04_standards/), [`06_quality_and_evals/`](../06_quality_and_evals/), per-component admission checklists in [`05_review_gates/`](../05_review_gates/)

---

### P3 — Consistency by contract

**Rule.** Every instance of a repeatable component type follows the same contract. If primitive X has four files, primitive Y has four files. If operator A raises a typed exception, operator B raises a typed exception. There are no "special" cases, no "this one's different," no per-instance bespoke shapes. The contract is the shape.

**Rationale.** The platform's claim to scale (200+ primitives, dozens of operators, multiple agents) depends entirely on every component looking like every other component of its type. Inconsistency means every component is a snowflake that must be understood individually — which kills any LLM router, embedding retrieval, scaffolder CLI, or human reviewer. Consistency is not aesthetic; it is the precondition for scale and for AI-orchestrated code generation.

**Verify.**
- File layout matches the canonical contract: primitive = `config.yaml` + `schemas.py` + `compute.py` + `__init__.py`; operator = `config.yaml` + `schemas.py` + `operator.py`.
- Names follow [`04_standards/naming_conventions.md`](../04_standards/).
- All mandatory fields in the component's `contract.md` are present.
- The component passes the matching admission checklist in [`05_review_gates/`](../05_review_gates/).
- If a developer's instinct is "this case is different," the default conclusion is *they are wrong*. Either the contract is wrong (open a contract-revision PR with an ADR) or the implementation is wrong. Both are valid; an in-place exception is not.

**Anti-patterns (auto-reject).**
- A primitive missing one of the four canonical files
- An operator that imports from a domain-specific path (violates P9 too)
- A workflow template with bespoke loader logic instead of using `shared/workflow/template_loader.py`
- Naming variants like `util.py`, `helper.py`, `service.py` that obscure the component type
- "This is just a quick test of a different shape" — there is no test shape; there is the shape
- Inline overrides of contract fields ("for this tool, the config also has X")

**Exceptions.** None. If the contract genuinely needs to evolve, change the contract (with ADR) — do not write an exception.

**Relates to.** P1 (consistency is how future-proofing is enforced in practice), P8 (closed families are a specific kind of contract), P10 (the contract is the single source of truth for the shape).

**Operational application.** [`02_components/*/contract.md`](../02_components/), [`02_components/*/file_layout.md`](../02_components/), [`04_standards/naming_conventions.md`](../04_standards/), [`04_standards/file_and_folder_layout.md`](../04_standards/), [`05_review_gates/`](../05_review_gates/)

---

### P4 — Determinism and replayability

**Rule.** Every artifact produced by the platform is content-addressed by a deterministic hash recipe that is stable across machines, Python versions, library versions, and operating systems. Identity is driven by content, not by registry IDs. The substrate is content-pinned today (Phase 0); the full byte-identical re-execution loop closes in Phase 1+.

The four pins of replay are explicit. They are not interchangeable.

| Pin | What it is | Where it lives | In the hash? |
|---|---|---|---|
| `head_hash` | SHA-256 of the canonical-JSON-serialised lineage chain. The artifact's identity. | Computed from `PrimitiveStep` + `OperatorStep` chain. | **Yes** — this *is* the hash. |
| `tool_config_hash` | SHA-256 of the canonicalised YAML *content* of the tool's `config.yaml` at construction time. | Stored inside each `PrimitiveStep`. | **Yes** — folded into `head_hash`. |
| `methodology_version_id` | Registry row id pointing at the canonicalised YAML content. | Stored on `PrimitiveStep` as `Optional[int]`. | **No** — metadata only. Auto-increment ids are not stable across deploys; folding them in would break the pinned-hash test. The hash-stability invariant is documented in `01_architecture/07_state_and_persistence.md` (forthcoming). |
| `application_version_id` | Registry row id for the code revision (git commit). | Stored on `artifact_metadata`. | **No** — metadata only. |

**Rationale.** The product's audit promise — *"open a six-month-old workspace, see the same numbers"* — depends entirely on this. Without determinism, lineage is decorative. With determinism, lineage becomes a falsifiable claim: any artifact's correctness is verifiable by recomputing it from its pinned inputs. P4 is the substrate-level reason any other principle in this list has weight.

**Verify (current substrate — Phase 0).**
- Every artifact carries a `head_hash` computed from canonical-JSON over the lineage chain (sorted keys, deterministic float serialisation). The full replay model is documented in `01_architecture/08_replay_and_versioning.md` (forthcoming).
- `PrimitiveStep.tool_config_hash` is the content hash of the canonicalised YAML (not the raw bytes — whitespace and key order do not change identity).
- `tests/state/test_hash_stability.py` (the CI gate) is green: pinned canonical hashes for representative artifacts match across runs.
- Restart resilience test passes: persist artifact + DAG + workspace + working-set, `engine.dispose()` + clear all in-process caches, build a fresh engine, re-fetch by slug, assert byte-identical artifact hashes (`tests/integration/test_workspace_replay.py`, `tests/integration/test_phase0_demo.py`).
- Divergence detection works: `mode=original` reconstructs the pinned `ToolConfig` from the registry; `mode=current` re-reads disk and emits `methodology_diffs` when content changed.
- No CSV-based float hashing anywhere (closes tech-debt #20).

**Verify (target acceptance bar — Phase 1+).**
- Re-execution under `mode=original` rebuilds the `PrimitiveStep` from the reconstructed `ToolConfig` and produces an artifact whose `head_hash` equals the original. This assertion is the Phase 1 hand-off; the Phase 0 substrate is what makes it meaningful.
- Cross-version test: the same primitive on Python 3.11 and 3.12 produces identical lineage hashes (a target to harden; current pinned-hash test is single-version).

**Anti-patterns (auto-reject).**
- Hashing via `str(df)` or CSV-formatted floats
- Adding `methodology_version_id` or `application_version_id` to the canonical hash recipe (these are metadata pointers, not identity — see `state_schema.md` Hash-stability invariant)
- Storing methodology by file *path* rather than by *content hash*
- Reading the current YAML on disk during `mode=original` replay (must read the registry-pinned content)
- Wall-clock time anywhere in a primitive's compute path
- Unfixed random seeds in any pipeline node
- File paths or hostnames in lineage that change between machines
- Relying on `dict` traversal order in serialisation (use sorted keys)

**Exceptions.** None. Replay determinism is the audit story; the audit story is the product.

**Relates to.** P2 (accuracy is only meaningful if it is repeatable), P5 (replayability requires explicit methodology pinning, which requires disclosure), P10 (one canonical hash per artifact).

**Operational application.** [`01_architecture/06_bridge.md`](../01_architecture/), [`01_architecture/07_state_and_persistence.md`](../01_architecture/), [`01_architecture/08_replay_and_versioning.md`](../01_architecture/), [`04_standards/hash_determinism.md`](../04_standards/), [`06_quality_and_evals/replay_acceptance.md`](../06_quality_and_evals/) — all forthcoming.

---

### P5 — Honest disclosure

**Rule.** Every methodology choice, convention default, scope limitation, active proxy, and known deviation from a source of record is visible on the user-facing surface — methodology card, lineage card, workspace node, or response. Hiding a limitation is a defect. There is no such thing as a "minor undisclosed assumption."

**Rationale.** Macro Copilot's trust model is *"show your work, then earn the user's trust."* A PM comparing this product to a known data terminal has no patience for hidden assumptions; if an output uses an OIS proxy for repo, the PM must see that on the card, not discover it weeks later. Hidden conventions destroy trust on first contact and never recover. Disclosure is what makes P2 (accuracy) sufficient — a disclosed approximation is honest; an undisclosed one is fraud.

**Verify.**
- Every `Convention` in every tool's `config.yaml` carries a non-empty `source` tag drawn from the methodology-source taxonomy. No `"default"`, no `"standard"`, no `"tbd"`, no empty string. The taxonomy is documented in `04_standards/methodology_disclosure.md` (forthcoming).
- Methodology cards display the central knob, every consequential convention, every active proxy, and a "what this does NOT do" block.
- Workflow templates carry a "what this does NOT do" disclosure (e.g., for `backtest`: frictionless, mid-price, no transaction costs, OIS proxy for repo).
- Refusals under P6 state the specific missing capability ("no rolling-correlation operator exists"), not a generic "I can't do this."
- Every active proxy is named on the methodology card in plain language, not buried in code comments.

**Anti-patterns (auto-reject).**
- Convention `source` tagged as `"default"`, `"standard"`, `"convention"`, `"bloomberg"` (vague — name the specific field convention, not the vendor), or empty — per the taxonomy in `04_standards/methodology_disclosure.md` (forthcoming)
- Burying a proxy in a code comment instead of the methodology card
- "We'll add the disclosure later" as merge justification
- Returning a number without showing the conventions that produced it
- A backtest result without a methodology card listing all financing and pricing assumptions
- A response that says "approximately" without naming what is being approximated

**Exceptions.** None. If a methodology is too embarrassing to disclose, do not ship it — fix it first.

**Relates to.** P2 (accuracy is verifiable only when assumptions are stated), P6 (a refusal under P6 is itself a disclosure), P7 (vendor identity is one of the things that must be transparently disclosed when relevant).

**Operational application.** [`02_components/primitive/conventions.md`](../02_components/primitive/), [`04_standards/methodology_disclosure.md`](../04_standards/) — both forthcoming.

---

### P6 — No silent failure

**Rule.** Three distinct layers, three distinct rules:

1. **Compute layer** (primitives, operators, workflow executor, bridge, internal helpers). Failures **must** raise typed exceptions specific to the failing component (e.g., `AlignSeriesError`, `ToolConfigError`, `EvaluateTradesError`). No silent defaults, no implicit fallbacks, no fabricated answers.
2. **Optional-typed lookups** (cache reads, get-by-hash, get-by-id, registry lookups). Returning `None` is allowed **iff** the function's signature is explicitly typed `-> Optional[X]` and the contract states that `None` means *"not present, by design."* Callers must handle the `None` branch; the absence is information, not failure.
3. **Transport boundaries** (MCP servers, HTTP API endpoints, WebSocket handlers — the surfaces facing the LLM and the UI). These convert internal exceptions into typed error envelopes (e.g., `{"error": "..."}`) per their published contract. The contract states what error types are surfaced and how. Envelopes at this layer are the *correct* shape; they are not a violation.

Across all three layers: refusal is always preferred to hallucinated competence. *"No `rolling_correlation` operator exists; this analysis requires it"* is correct. *"Here is an approximate result"* — when "approximate" hides "we cannot do this" — is a defect.

**Rationale.** A silent fallback is worse than a crash. A crash gets noticed and fixed; a fallback contaminates every downstream artifact until weeks later when someone notices the numbers are wrong. For an AI-orchestrated system, this is doubly true: a silently-failing tool teaches the orchestrating LLM that the tool *"works"* when it doesn't, leading to compounded misuse across many subsequent prompts. The layer split exists because the cost-of-fallback is asymmetric: invisible in compute, visible (and required) at transport boundaries.

**Verify.**
- Compute-layer primitives and operators raise typed exceptions; no `except Exception: pass`, no `except` without re-raise or narrowing.
- Functions that may legitimately return absence are declared `-> Optional[X]` with a docstring stating what `None` means (e.g., *"cache miss"*, *"hash not in registry"*).
- MCP/API handlers convert exceptions to typed envelopes whose schema is part of the route's contract; the envelope shape is documented, not invented per-call.
- LLM-facing routers emit explicit refusals naming the missing capability — never generic *"sorry, I can't do this."*
- Catching an exception narrows the type (`except FileNotFoundError`, `except IntegrityError`), never blanket — and the catch handler either re-raises, converts to a typed envelope at the right boundary, or returns a contract-typed `None`. Nothing else.

**Anti-patterns (auto-reject).**
- `try: compute() except Exception: return 0.0` (compute layer)
- A primitive's `compute()` returning `None` when the return type is *not* `Optional` — change the signature or raise
- `return {"error": "..."}` from a primitive or operator (transport-shape leaking into compute)
- "I'll fill in NaN if the data is missing" without disclosing it (also a P5 violation)
- Returning the last-known-good value when fresh data is unavailable
- Catching the wrong exception class to mask an unrelated error
- A primitive that silently filters out rows it cannot process — silent row drop is a P5 violation too
- "Best-effort" defaults applied without telling the caller
- Wrapping a real error in a generic `ValueError("something went wrong")`
- A transport handler that re-raises into the response stream instead of converting to its envelope contract

**Exceptions.** None for compute-layer silent failure. *Documented* fallbacks (e.g., `ffill` up to N days, then raise) are allowed only when named in the component's `config.yaml`, surfaced on the methodology card per P5, and asserted by a test. Optional returns are not exceptions — they are a contract decision that must appear in the type signature.

**Relates to.** P2 (the alternative to inaccuracy is refusal, not silent error), P5 (refusal must be specific to be honest disclosure), P3 (the same exception type / envelope shape across a component family is itself a contract).

**Operational application.** [`04_standards/error_handling.md`](../04_standards/), [`02_components/*/contract.md`](../02_components/), [`02_components/orchestration/`](../02_components/orchestration/) (the transport-boundary contracts)

---

### P7 — Source-of-record boundary and vendor SDK isolation

**Rule.** The platform exposes a single L1 read contract for primitives to consume. Vendor SDKs, vendor authentication, vendor pagination, and vendor-shaped field decoding are isolated to the L1 adapter inside [`../../ingestion/`](../../ingestion/) and [`../../database/`](../../database/). New code in the L2–L5 layers — primitives, operators, workflows, artifacts, orchestrator, API, UI — is written against the L1 contract. Vendor-shaped names that already exist above L1 in the current implementation are treated as legacy and migrated incrementally; **net-new code does not extend that surface.**

**Rationale.** Today, the platform is Bloomberg-first by design. The product's relationship to that source is stated plainly in the thesis (see [`00_what_we_build.md`](00_what_we_build.md)): *Bloomberg is the pricing engine; we are the intelligence layer.* That is the product's current commitment and not a defect. The long-term value is the platform's portability into any institutional data environment — a market-data terminal, an internal data lake, a vendor API, a hedge fund's cleaned warehouse — *without* rewriting L2–L5. P7 is the principle that protects the path from "Bloomberg today" to "any source-of-record tomorrow." The boundary is what enables the option, not a rejection of the present.

The day a second L1 adapter is needed, the cost of every L2–L5 coupling becomes visible. P7 is paid in small increments now so it is not paid as a rewrite later.

**Verify.**
- New L2–L5 code reads only from the L1 read contract (the SQL views / Python access layer documented in `01_architecture/01_l1_data_substrate.md`, forthcoming). It does not import vendor SDKs or hard-code vendor field mnemonics.
- Vendor SDK imports (`blpapi`, etc.) appear only inside [`../../ingestion/`](../../ingestion/). Grep for them above L1 is empty for *new* code.
- Vendor authentication and pagination logic lives only in the adapter layer.
- A new L1 adapter (for any second source of record) implements the same contract — verifiable by a contract-test suite reused across adapters.
- Vendor-specific parity tests live in [`06_quality_and_evals/`](../06_quality_and_evals/), keyed by adapter name.
- Migration register: any pre-existing L2–L5 file that references a vendor by name is logged in `08_roadmap/tech_debt_register.md` with a planned migration; **net-new files do not add to the register.**

**Anti-patterns (auto-reject for new code).**
- A new primitive that imports a vendor SDK
- A new operator that names a vendor field mnemonic (`PX_LAST`, `YLD_YTM_MID`, etc.) — operators consume typed artifacts (P9), not vendor-shaped rows
- A new workflow whose template assumes a specific vendor's data shape
- A new docstring that describes the *platform* in vendor terms, versus describing the *current deployment*
- Vendor authentication or pagination logic introduced above L1 for the first time
- "Quick" workaround that bakes a vendor-specific transform into a non-ingestion module

**Acceptable (existing reality, not violations).**
- Vendor-named columns in `instrument_master` and `time_series` populated by the current adapter
- UI labels and methodology cards that name the current vendor when describing what data was used (this is *disclosure* under P5, not coupling)
- Documentation that describes Bloomberg-first defaults of the current deployment (this is disclosure under P5, not coupling)

**Exceptions.** L1 adapter implementations themselves use vendor-specific code by definition; nothing else may, in new code.

**Relates to.** P5 (when a deployment is bound to a specific vendor, the user-facing disclosure names the vendor — this is not a P7 violation), P9 (the operator layer is doubly insulated — finance-blind *and* free of vendor field shapes), P1 (no new code that takes a vendor-shaped shortcut "for now").

**Operational application.** `01_architecture/01_l1_data_substrate.md` (forthcoming), [`02_components/playbook/contract.md`](../02_components/playbook/), [`06_quality_and_evals/`](../06_quality_and_evals/), [`08_roadmap/tech_debt_register.md`](../08_roadmap/)

---

### P8 — Closed-family discipline

**Rule.** Small canonical enumerations — workflow archetypes, artifact types, operator vocabulary, methodology-source tags — are closed. Each is defined as a `Literal[...]` type or an enum in code, and the set of valid values is exhaustive. Extending one is a deliberate, ADR-recorded decision that touches the entire stack: router, validator, UI, gauntlet, and contracts. Casual additions are forbidden.

**Rationale.** Closed families are what let the platform scale. If `WorkflowArchetype` becomes any-string, every router, validator, UI surface, and gauntlet has to handle arbitrary input — and the substrate stops being a substrate. If `ArtifactType` grows by accident, the operator algebra fragments. Constraint is the source of leverage; loosening constraint is the most expensive change in this codebase. Closed families are deliberately uncomfortable to extend, because the discomfort is what protects them.

**Verify.**
- Workflow archetypes are a `Literal[...]` type (`shared/archetypes.py` or equivalent), not a free string.
- Artifact types are a `Literal[...]` discriminator on the artifact base class.
- Methodology source tags resolve to an enum (or are migrating toward one — see `04_standards/methodology_disclosure.md`, forthcoming).
- New entries to a closed family are added only via PRs that include: an ADR in [`../07_decisions/`](../07_decisions/), a closed-family-extension gate from [`../05_review_gates/`](../05_review_gates/), and corresponding updates to router / validator / UI / gauntlet.
- The set of closed families is listed in `01_architecture/00_overview.md` (forthcoming).

**Anti-patterns (auto-reject).**
- Adding a new value to a `Literal[...]` in a single PR without an ADR
- Creating a "custom" artifact type to handle one tool's odd output
- Adding a new methodology source tag as a one-off
- "Open for extension" reasoning when the cost is centralised in a router or validator
- A workflow template with bespoke DAG-loading logic instead of using a registered archetype
- Two slightly different artifact-type names in different files for "essentially the same thing"

**Exceptions.** None. Extensions are welcome; informality is not.

**Relates to.** P3 (the closed family is the contract that consistency enforces), P10 (the closed family is the single source of truth for the valid set).

**Operational application.** [`02_components/workflow_template/archetype_extension_policy.md`](../02_components/workflow_template/), [`02_components/artifact/closed_family_policy.md`](../02_components/artifact/), [`05_review_gates/archetype_closed_family_extension_gate.md`](../05_review_gates/), [`05_review_gates/artifact_type_closed_family_extension_gate.md`](../05_review_gates/)

---

### P9 — Finance-blind operator boundary

**Rule.** Operators (the L3 layer) contain no finance domain knowledge. They are structural transforms over typed artifacts. Domain knowledge — what an instrument is, what a tenor means, what a curve family represents, what a basis point is — lives in primitives (L2) and playbooks (L1). The boundary is absolute. An operator that knows what "OIS" means is not an operator; it is a primitive that should be relocated.

**Rationale.** This boundary is the precondition for cross-asset portability. The substrate works for rates today, FX tomorrow, equities later — because the same operators apply across all of them. The moment one operator imports a `curve_family` enum or an `instrument_type` discriminator, the substrate is rates-only and the cross-asset claim is dead. The boundary also enforces clean composition: if `align_series` knew about "sovereign vs. OIS," then every new instrument type would require changes to `align_series`. With the boundary, new instruments require zero operator changes — verified by the Phase 4 instrument-agnostic acceptance test.

**Verify.**
- No operator imports from `rates_agent/`, `fx_agent/`, or any domain-specific module.
- Operator I/O types reference only closed-family artifact types (`Series`, `Panel`, `EventSet`, `SeriesSet`, `WindowedPanel`, `ScalarMetric`, `RankedResult`, `TradeSet`) — never `SovereignSeries`, `OISCurve`, or any domain-shaped wrapper.
- Operator config files have no instrument, tenor, or asset-class fields.
- Adding a new agent (e.g., FX) requires zero changes to any existing operator. Verified by the instrument-agnostic test required of every operator and every workflow template — documented in `01_architecture/03_l3_operators.md` (forthcoming).
- Operator tests run against synthetic non-rates inputs and pass.

**Anti-patterns (auto-reject).**
- An operator that branches on `curve_family`, `tenor`, `asset_class`, or `instrument_type`
- An operator param named `sovereign_threshold` (use a generic name; let the caller bind a sovereign-derived value)
- "I'll just put this convention in the operator for now" — never
- An operator with branching logic per asset class
- Inheriting an operator from a domain-specific base class
- A docstring that describes the operator in finance terms ("aligns yield series across sovereigns") when generic terms suffice ("aligns series by index")

**Exceptions.** None. Domain-aware logic belongs in primitives. If an operator is tempted to know domain facts, promote the would-be operator to a primitive (with an ADR) or restructure so the primitive emits the domain-shaped result and the operator consumes the generic shape.

**Relates to.** P3 (the boundary is a contract; the contract is consistency), P7 (operators are doubly insulated — both finance-blind and vendor-blind), P8 (the artifact types operators consume are themselves a closed family).

**Operational application.** [`02_components/operator/contract.md`](../02_components/operator/), [`01_architecture/03_l3_operators.md`](../01_architecture/), [`04_standards/file_and_folder_layout.md`](../04_standards/) — all forthcoming.

---

### P10 — Single source of truth

**Rule.** Every fact in this codebase and its documentation is defined in exactly one place. Every other reference to that fact is a link, not a copy. Duplicates are bugs. Synchronisation by hand is not a strategy.

**Rationale.** Duplicated facts drift. A methodology stated in three places becomes three slightly different methodologies; a convention listed in two YAMLs becomes the wrong one in one of them. The maintenance cost of duplication exceeds the cost of every PR forever. Cross-references are not optional; they are the only mechanism by which a multi-author, multi-agent codebase stays coherent. P10 is the principle that protects every other principle from rot.

**Verify.**
- Component contracts in [`02_components/*/contract.md`](../02_components/) are the source for what makes a valid component; other docs link to them, never restate them.
- Convention defaults live in `config.yaml`, not in Python module constants. (Closing the door on duplication.)
- The five-layer architecture is described in exactly one place (`01_architecture/00_overview.md`, forthcoming); all other architecture docs link to it.
- ADRs are immutable; if an ADR is superseded, a new ADR explicitly supersedes it and the old one stays in place with a forwarding pointer.
- Glossary definitions are linked, never retyped.
- If you find yourself writing the same paragraph twice, you have a P10 violation. Extract one place, link from the other.

**Anti-patterns (auto-reject).**
- Copy-pasting a methodology paragraph from one tool's docstring to another's
- Two different docs each claiming to be authoritative for the same thing
- A field documented in code AND in a contract file with the two definitions slightly different
- "I'll keep this in sync manually" — you will not
- A YAML value that duplicates a Python constant
- Definitions inside a runbook that should live in a contract
- Re-explaining a principle in a runbook instead of citing its P-number

**Exceptions.** *Trivial canonical phrases* (e.g., the project's one-line description) may appear in a small number of indexable surfaces — package docstrings, the top-level [`README.md`](../README.md), an `__init__` file. In every such case, exactly one location is authoritative and the others are explicitly *copies of* it, with the canonical location named.

During the initial population of this tree, some sections are simply *not yet written* — that is not duplication; it is absence. Sections marked ⏳ in [`../README.md`](../README.md) "Status of this tree" do not have a parallel-authoritative source to reconcile against; the right action when content is needed before it is drafted is to draft it (per the contribution rules in [`../README.md`](../README.md)), not to fork from another location.

**Relates to.** P3 (the contract is the single source of truth for the shape), P5 (one methodology card per artifact, never duplicated), P8 (the closed family is one set, defined once).

**Operational application.** All cross-document linking. [`04_standards/code_review_checklist.md`](../04_standards/). `glossary.md` (forthcoming).

---

### P11 — Domain isolation

**Rule.** Each domain agent (rates, FX, equities, and any future addition) has hard-wired access only to its own tools, primitives, and data. A domain agent never invokes another domain's tools directly. Cross-domain queries are orchestrated at the supervisor layer (in [`../../orchestrator/`](../../orchestrator/)), which routes to multiple domain agents in sequence or parallel and composes their structured outputs into a single answer.

**Rationale.** Two effects compound:

1. **Tool-selection ambiguity collapses.** When the Rates agent's tool catalogue contains only rates tools, its router has a strictly smaller, disjoint search space — accuracy of tool selection rises sharply and the LLM is far less likely to misroute. If the Rates agent could see FX tools, every rates prompt would carry implicit ambiguity about whether the right tool was FX-shaped.
2. **Each agent becomes a single-domain expert.** Each agent's system prompt, registry, retrieval embeddings, and few-shot examples are tuned for one domain. Mixing tools dilutes that expertise, and the agent stops being good at any one thing. The platform's scale claim ("100+ instruments, 10+ agents") requires that adding a new agent does not degrade the existing ones.

The supervisor is the *only* component that knows about multiple agents. A cross-asset query (*"FX-hedged UST yield"*) becomes: supervisor → Rates agent (UST yield) → FX agent (forward points + cross-currency basis) → supervisor composes from each agent's `ChildResponse.structured_facts`. The composition is over structured outputs, never prose.

**Verify.**
- Each domain agent's package ([`../../rates_agent/`](../../rates_agent/), future `fx_agent/`, etc.) declares its tool registry locally. The MCP server for that agent registers only that agent's tools.
- The supervisor (in [`../../orchestrator/`](../../orchestrator/)) is the only component holding a multi-domain view or routing across agents.
- Cross-domain composition reads `ChildResponse.structured_facts` from each child agent and assembles the answer structurally; the supervisor does not perform prose-level synthesis across child answers.
- A grep for `from fx_agent` inside [`../../rates_agent/`](../../rates_agent/) returns zero matches (and vice versa for every pair of domain agents).
- New domain agents are added by mirroring the existing pattern (sibling package), never by extending or inheriting an existing agent's class.

**Anti-patterns (auto-reject).**
- A primitive in `rates_agent/` that imports from `fx_agent/` or any other domain agent
- A domain agent's router prompt enumerating tools from other domains
- "Convenience" cross-domain calls inside a single agent's compute path
- The supervisor synthesising cross-asset answers by stitching prose from multiple `ChildResponse`s instead of composing structured facts
- A workflow template that hard-codes calls across two domain agents — cross-domain workflows live at the orchestrator level, not the template level
- A new domain agent built by inheriting from an existing agent — agents are siblings, not a hierarchy
- Tool descriptions in one agent's registry that reference another agent's tools by name

**Exceptions.** Shared building blocks — operators in [`../../shared/operators/`](../../shared/operators/), artifact types in [`../../shared/artifacts/`](../../shared/artifacts/), the workflow substrate in [`../../shared/workflow/`](../../shared/workflow/) — are explicitly shared across all agents. They are domain-blind by P9 and have no concept of which agent invoked them. Sharing them is not a P11 violation; it is the substrate working as designed.

**Relates to.** P3 (every agent follows the same agent contract — file shape, MCP server pattern, registry shape), P8 (the set of agents is a closed family; new domains require an ADR), P9 (operators are finance-blind and therefore safe to share across agents).

**Operational application.** `01_architecture/09_orchestration.md` (forthcoming), `02_components/orchestration/contract.md` (forthcoming), `02_components/orchestration/routing_contract.md` (forthcoming).

---

### P12 — Bloomberg Accuracy Boundary

**Rule.** Never recompute what the connected source-of-record data layer already provides, unless our recomputation is *indistinguishable* from the source-of-record output by the standards of macro trading workflows. *"Indistinguishable"* means: a PM comparing our output to the equivalent source-of-record screen would not find a discrepancy that changes their analytical conclusion or trading decision.

The boundary resolves to one of four outcomes for every new primitive:

| If… | Then… |
|---|---|
| The source of record computes the value better than we can match | **Ingest** the source-of-record output as a data field. Do not ship a recomputation. |
| We can match source-of-record accuracy to "indistinguishable" tolerance | **Compute** it ourselves; ship the primitive. |
| The source of record does not compute the value at all | **Compute** it ourselves; this is the platform's genuine value-add (cross-instrument scans, regime classification, multi-tool orchestration, custom RV). |
| We cannot match source-of-record accuracy AND cannot ingest | **Do not ship.** Refuse per P6 with a specific reason, or expose behind an explicit `analyst_override` knob with full P5 disclosure (the single narrow exception below). |

The current source of record is Bloomberg. The product's stated relationship to that source is in [`00_what_we_build.md`](00_what_we_build.md): *Bloomberg is the pricing engine; we are the intelligence layer.* The principle is named after the current source for clarity; when the platform connects to a second source-of-record adapter (an internal data lake, a vendor API, a hedge fund warehouse), the same boundary applies relative to whichever source is connected. The underlying logic generalises; the title follows current scope.

**Rationale.** The end user is a discretionary macro PM with the source-of-record terminal open on the next screen. If our tool gives them a number that visibly contradicts what they see there, they will not investigate why — they will stop using our tool. Trust is binary: one wrong WIRP number kills adoption of the entire copilot. The platform's value is not in replicating a pricing engine; it is in being the intelligence layer composed *over* the source-of-record data.

This principle is also the scope-discipline rule for every new-primitive PR: it forces the author to defend, before writing code, whether the primitive belongs to category (a), (b), (c), or (d) above.

**Verify (the three-question decision framework — required on every new-primitive PR).**

| # | Question | Required answer in the PR description |
|---|---|---|
| 1 | Does the source of record already compute this? | Yes / No (with a citation to the relevant screen, function code, or field mnemonic) |
| 2 | If yes — can we recompute it with indistinguishable accuracy? | Yes (with parity-fixture results attached) / No (justifies the ingestion path) |
| 3 | If neither (1) nor (2) — does the source of record *not* compute this at all? | Yes (this is genuine value-add — explain how) |

In practice this resolves to:
- **Ingest (do not recompute):** meeting-by-meeting rate expectations (WIRP), bootstrapped forward curves at specific tenors (FWCV), carry-and-roll-down analytics (FWCV / YAS), OAS / ASW spreads, anything requiring a full multi-curve bootstrap or options pricing model.
- **Compute (genuine value-add):** rolling z-scores and percentile ranks across the full instrument universe, cross-instrument scanners ranked by statistical extremes, curve regime classification (bull steepener, bear flattener, etc.), multi-tool orchestration chains (scan → filter → compare → synthesise), custom cross-market RV with historical context.
- **Compute (where accuracy is achievable):** two-point curve spreads, cross-market yield/rate differentials, butterfly spreads, period changes in bps, simple ≤1Y forward rates.

**Anti-patterns (auto-reject).**
- A primitive that recomputes a source-of-record field when the field is directly ingestible
- A primitive that ships a "best-effort" proxy — e.g., linear interpolation of par OIS rates for per-meeting pricing when the source-of-record screen shows a step-shaped path. The project's doctrine on this is absolute: *we do not, under any circumstances, build something half-assed under deterministic mode.* If accuracy cannot be matched, ingest from the source of record or refuse.
- A new-primitive PR description that does not answer the three-question framework
- "We'll improve the accuracy in v2" as merge justification
- Inventing a new methodology for a quantity the source of record already publishes well
- Pricing-engine work (curve bootstrapping, OAS, OAS-equivalent yield) that duplicates the source-of-record output
- A parity tolerance chosen to make the test pass rather than chosen from the "indistinguishable to a PM" bar (also a P2 anti-pattern)

**Exceptions.** One narrow allowance: when a computation is genuinely necessary but cannot match source-of-record accuracy, it may be shipped behind an explicit `analyst_override` knob that requires the user to opt in *and* discloses on the methodology card exactly what the deviation from the source of record is (P5). This is the *only* place an opinionated computation is acceptable, and only when wrapped in explicit user consent and explicit disclosure.

**Relates to.** P2 (P2's accuracy bar — "indistinguishable from source of record" — is defined by P12 in scope-setting terms), P5 (any computation that approaches but does not equal the source of record must disclose the methodology and the analyst-override mode), P6 (when neither ingestion nor matching computation is possible, the platform refuses), P7 (the source of record is whatever L1 adapter is connected; the boundary travels with the adapter).

**Operational application.** This principle is the canonical source for the scope-discipline rule (the three-question framework). Downstream: [`02_components/primitive/admission_checklist.md`](../02_components/primitive/) (the three-question framework is item 1 of the new-primitive checklist), [`05_review_gates/primitive_admission_gate.md`](../05_review_gates/) — both forthcoming.

---

## Changing a principle

Principles in this file are stable on purpose. Changes are slow on purpose. The procedure for changing one:

1. **Open an ADR** in [`../07_decisions/`](../07_decisions/) describing the proposed change, the reason, the impact across `02_components/`, `04_standards/`, and `05_review_gates/`, and any code-side mechanical changes required.
2. **Get review** from at least one code owner. Principle changes are higher-stakes than feature changes; they affect every future PR.
3. **Land the ADR and the principle change in the same PR** — never separately. The ADR is the immutable record; the principle is the operational rule. They must agree.
4. **Bump the version** of this file (`v1` → `v2`) and update `Last reviewed`. If the change supersedes a principle entirely, the old `P<N>` is marked superseded in place — its ID is *not reused*; the replacement gets a new ID (`P11`, `P12`, …).
5. **Update downstream files** in the same PR: any contract, runbook, or gate that cites the changed principle is updated, not left to drift.

The version log at the bottom of this file records every change.

## Citation cheat sheet

| Use | Pattern |
|---|---|
| In a commit message | `fix(curve_spread): tag conventions per P5 (methodology_source not "default")` |
| In a PR review comment | `Suggest revising — this returns 0.0 on missing data, which violates P6.` |
| In a code comment (rare) | `# P5: source must be explicit, not inferred at runtime` |
| In a runbook step | `Step 4 — verify P2 (parity test) and P3 (four-file shape) using the admission checklist.` |
| In an ADR | `This decision supersedes the application of P8 in the artifact-types closed family.` |
| In a refusal message to the user | `(internal: refusal under P2 + P6 — no source-of-record path available for this metric)` |

Citation discipline is what makes the IDs durable. Cite by ID; never paraphrase the rule.

---

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.3 | 2026-05-16 | Stand-alone-tree pass: removed every cross-link out of this docs tree into the prior project documentation (the prior tree is being deleted at cleanup; this tree is the single source from day one). Quotes previously attributed to prior docs are now stated inline as the platform's own doctrine. Forward references to forthcoming files in [`01_architecture/`](../01_architecture/), [`04_standards/`](../04_standards/), [`05_review_gates/`](../05_review_gates/), [`06_quality_and_evals/`](../06_quality_and_evals/) replace prior cross-links. P10 migration-window exception removed (the migration model no longer applies — the tree is canonical, not migrated). | (pending) |
| v1.2 | 2026-05-16 | Added two principles, append-only (IDs never move): (a) **P11 — Domain isolation.** Each domain agent has hard-wired access only to its own tools and data; cross-domain queries are orchestrated at the supervisor layer, which composes structured outputs from multiple single-domain agents. Eliminates tool-selection ambiguity and forces single-domain expertise. Sibling-pattern agents, never inheritance; shared substrate (`shared/operators/`, `shared/artifacts/`, `shared/workflow/`) is the explicit exception per P9. (b) **P12 — Bloomberg Accuracy Boundary.** Codifies the scope-discipline rule: never recompute what the source of record provides better; ingest, compute to indistinguishable accuracy, or refuse. Embeds the three-question decision framework as a required answer on every new-primitive PR. Principle name follows current source-of-record (Bloomberg); underlying logic generalises with whichever L1 adapter is connected. Quick Index updated. | (pending) |
| v1.1 | 2026-05-16 | Pre-canonical revisions before first ADR: (a) P1 — explicit allowance for documented refusal surfaces (`NotImplementedError` with `planned_extensions` + test); (b) P2 — anti-patterns replaced with reviewer-enforceable criteria (parity fixture, tolerance from contract, failure-case assertions); (c) P4 — restructured around the exact four-pin model (`head_hash`, `tool_config_hash`, `methodology_version_id` *metadata-only*, `application_version_id` *metadata-only*), split current Phase-0 substrate from Phase-1+ target acceptance bar; (d) P6 — three-layer split (compute / optional lookups / transport boundary), `return None` allowed iff typed `Optional`, MCP/API envelopes are the *correct* shape at transport; (e) P7 — reframed as source-of-record boundary + vendor SDK isolation; acknowledges current Bloomberg-first reality, scoped to net-new code; (f) P10 — migration-window exception added (later removed in v1.3); (g) "How these principles bind" — CLAUDE.md hook flagged as planned, not yet active. No ADR exists yet; first ADR will retroactively record this set. | (pending — `0001-initial-non-negotiables.md`) |
| v1   | 2026-05-16 | Initial principle set (P1–P10) — superseded same day by v1.1 review. | — |
