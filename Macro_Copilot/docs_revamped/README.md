# Macro Copilot — Engineering Documentation

> Institutional documentation for building, reviewing, and extending the Macro Copilot platform.

## What this tree is

This is the contract between the codebase and everyone who writes code against it — human contributors, AI agents writing code, and reviewers approving it. It is organised as **specifications + recipes + gates**, not essays. Every page is meant to be navigated for reference, not read top-to-bottom.

Two non-negotiables for any document that lives here:

1. **A spec, not a story.** Every contract page uses `MUST` / `SHOULD` / `MUST NOT` language, ends with a checklist, and links to a canonical example in the code. If a page reads like an essay, it belongs in a design doc, not here.
2. **One source of truth.** Each fact is defined once, in the folder that owns it, and referenced from elsewhere. If you find a definition repeated, the duplicate is wrong by default.

If you are looking for product mockups, marketing material, or freeform brainstorms, those do not live here. Adjacent product / domain content lives in the [`../manifesto/`](../manifesto/) folder.

## What Macro Copilot is

Macro Copilot is a typed-DAG composition platform for macro trading research, built on a Bloomberg-grounded data substrate. Every prompt produces a content-addressed graph of finance-aware **primitives** and finance-blind **operators**, composed into either fixed-topology **workflow templates** or LLM-composed open graphs, and persisted as a **workspace** that can be replayed byte-identically months later.

The product thesis, the non-negotiable principles, and the design philosophy live in [`00_thesis/`](00_thesis/). All other folders are derivatives of what is stated there.

## How to navigate

These docs are indexed by **what kind of question** you have, not by what layer of the system you are touching.

| If you are asking… | Look in |
|---|---|
| Why do we build this? What are the non-negotiables? | [`00_thesis/`](00_thesis/) |
| What is the architecture? How are the layers organised? | [`01_architecture/`](01_architecture/) |
| What is a *primitive / operator / workflow / playbook / artifact / workspace / orchestrator*, and what must it satisfy? | [`02_components/<component>/README.md`](02_components/) (each component folder owns its contract) |
| How do I add a new one? Is this PR ready to merge? | [`02_components/<component>/runbook.md`](02_components/) — each component's runbook carries the procedure + the PR review checklist |
| What naming, testing, error-handling, and disclosure rules must all code follow? | [`03_standards/`](03_standards/) |
| What is the accuracy / replay / parity bar? | [`04_quality_and_evals/`](04_quality_and_evals/) |
| Why did we decide X? | [`05_decisions/`](05_decisions/) |
| What is planned, what has shipped, what is deferred? | [`06_roadmap/`](06_roadmap/) |
| What does this term mean? | `glossary.md` (forthcoming, at the root of this tree) |

## Reading paths

### Path A — New human contributor:

The order to read:

1. `00_thesis/00_what_we_build.md` — the product thesis
2. `00_thesis/01_non_negotiables.md` — the hard rules everything else inherits
3. `01_architecture/00_internal_architecture.md` — backend flow; `01_external_architecture.md` for the UI surface
4. The `README.md` of whichever component you are touching first, in [`02_components/`](02_components/) — the component-specific contract
5. The matching `runbook.md` in the same component folder — the procedure plus the PR review checklist
6. `03_standards/code_review_checklist.md` — the universal PR gate (forthcoming)

### Path B — AI agent writing code

An agent does not need persuasion; it needs the contract. Read, in order, only what is relevant to the change being made:

1. The component-specific `README.md` (contract) under [`02_components/`](02_components/)
2. The matching `runbook.md` in the same component folder — procedure + PR review checklist (the per-component runbook fold-in eliminates the previous separate runbooks and review-gates folders)
3. The applicable files in [`03_standards/`](03_standards/) (naming, tests, error handling, methodology disclosure, hash determinism)
4. Self-check against the runbook's PR review checklist before declaring the change done

Agents should not consult [`00_thesis/`](00_thesis/) for routine work. Read it once at session start; do not re-read it per task.

### Path C — Reviewer

1. The matching `runbook.md`'s PR review checklist in [`02_components/<component>/`](02_components/) for the component being changed
2. `03_standards/code_review_checklist.md` — the universal items (forthcoming)
3. If the PR introduces a new archetype, new artifact type, or changes a methodology default: the corresponding closed-family-extension section of the component's `README.md` plus the methodology-change section of its `runbook.md` are mandatory, not optional

### Path D — PM, external practitioner, or stakeholder

1. `00_thesis/00_what_we_build.md`
2. `06_roadmap/README.md` — phase sequence and what is shipped (forthcoming)
3. [`04_quality_and_evals/`](04_quality_and_evals/) — the bars we hold ourselves to, including parity tolerance against the connected data source

## Directory structure

```
docs_revamped/
├── README.md                       # this file
├── glossary.md                     # every term defined once (forthcoming)
│
├── 00_thesis/                      # WHY — the defensible doctrine
│                                   # Product thesis, non-negotiables, AI-agent contract.
│                                   # Short, load-bearing, rarely changes.
│
├── 01_architecture/                # WHAT — the substrate layers
│                                   # Internal (orchestrator + agents + tools/operators
│                                   # + workflow DAGs) and external (UI surfaces).
│
├── 02_components/                  # CONTRACTS + PROCEDURES — one folder per component type
│   │                               # Each folder holds two files:
│   │                               #   README.md  — the contract (what it is, the principles
│   │                               #                that bind it, anti-patterns, examples)
│   │                               #   runbook.md — the procedure to add one + the PR review
│   │                               #                checklist (the gate sits next to the
│   │                               #                procedure it gates)
│   ├── primitive/                  #   Finance-aware deterministic tools (curve_spread, …)
│   ├── operator/                   #   Finance-blind structural transforms (align_series, …)
│   ├── workflow_template/          #   Closed-family DAG archetypes (event_study, backtest, …)
│   ├── artifact/                   #   Closed-family typed outputs (Series, Panel, EventSet, …)
│   ├── playbook/                   #   Ingestion specs that populate the data substrate
│   ├── workspace/                  #   URL-addressable handles for built analyses
│   └── orchestration/              #   Supervisor, sessions, turns, MCP servers
│
├── 03_standards/                   # MUST-follow rules that cross components
│                                   # Naming, file layout, test patterns, error handling,
│                                   # methodology disclosure, hash determinism, the
│                                   # universal code-review checklist.
│
├── 04_quality_and_evals/           # QUALITY BARS
│                                   # Primitive gauntlet, workflow gauntlet, Tier-2 capacity
│                                   # report, replay acceptance test, source-of-record parity bar.
│
├── 05_decisions/                   # ADRs — the immutable "why" log
│                                   # One file per major architectural decision, append-only.
│                                   # Never edit a merged ADR; supersede it.
│
└── 06_roadmap/                     # WHAT IS SHIPPED, WHAT IS NEXT
                                    # Phase-by-phase plan, tech-debt register,
                                    # acceptance criteria per phase.
```

Folder structure is **final**. File contents inside each folder are being written component-by-component. The earlier-planned `03_runbooks/` and `05_review_gates/` folders were folded into each component's folder as `runbook.md` (procedure + PR review checklist living next to the contract).

## Status of this tree

This documentation tree is in the process of being populated. The shell is in place; the contents are being written one folder at a time, alongside the human design conversations that surface what the contracts must say.

| Folder / File | Status |
|---|---|
| `README.md` (this file) | ✅ written |
| `glossary.md` | ⏳ pending |
| `00_thesis/00_what_we_build.md` | ✅ written |
| `00_thesis/01_non_negotiables.md` | ✅ written (P1–P12) |
| `00_thesis/02_ai_agent_development_contract.md` | ✅ written (AC1–AC8) |
| `01_architecture/00_internal_architecture.md` | ✅ written |
| `01_architecture/01_external_architecture.md` | ✅ written |
| `02_components/playbook/` (README + runbook) | ✅ written |
| `02_components/primitive/` (README + runbook) | ✅ written (PR1–PR16) |
| `02_components/operator/` (README + runbook) | ✅ written (OPR1–OPR16) |
| `02_components/artifact/` (README + runbook) | ✅ written (ART1–ART16) |
| `02_components/workflow_template/` (README + runbook) | ✅ written (WT1–WT16) |
| `02_components/workspace/` | ⏳ pending |
| `02_components/orchestration/` | ⏳ pending |
| `03_standards/` (README + 9 lateral files) | ✅ written (naming_conventions, file_and_folder_layout, typed_boundary_discipline, error_handling, test_patterns, methodology_disclosure, hash_determinism, closed_family_discipline, code_review_checklist) |
| `04_quality_and_evals/` | ⏳ pending |
| `05_decisions/` | ⏳ pending |
| `06_roadmap/` | ⏳ pending |

This tree is **the canonical source from day one for whatever sections are written**. Pending sections are simply not yet written — there is no parallel-authoritative source to consult. When a section is needed before it has been drafted, the right move is to draft it (per the contribution rules below), not to invent a contract or assume one from code alone.

Once this tree is fully populated, the prior project documentation will be removed in a single cleanup PR and this tree will become the only documentation. Until then, prior project documentation may still exist on disk as drafting reference for the authors writing this tree, but it is no longer cited from inside this tree.

## How to contribute to these docs

1. **Open a folder, not the whole tree.** Work on one component or one standard at a time. Folder-by-folder PRs are easier to review than tree-wide ones.
2. **Quote the code, do not paraphrase it.** If a contract states a field, link to the file and line in the codebase. If the code is the source of truth, the doc must mirror it exactly. If they drift, the doc is wrong — fix the doc.
3. **Every `MUST` needs a reason.** State why the rule exists in one sentence. Rules without rationale rot first.
4. **Decisions are immutable.** If a decision in [`05_decisions/`](05_decisions/) needs to change, write a new ADR that supersedes the old one. Never edit a merged ADR in place.
5. **Versioned contracts.** Each `contract.md` carries a `Version: vN` header. A new MUST or a removed SHOULD bumps the version and triggers a corresponding gate update.
6. **Standards changes are PR-gated.** Any change to a file in [`03_standards/`](03_standards/) requires an ADR in [`05_decisions/`](05_decisions/) and explicit sign-off from a code owner. Standards are not a place for unilateral edits.
7. **No cross-links into the prior project documentation.** This tree is canonical. References to the prior `../docs/`, `../WORKFLOW_GAUNTLET.md`, `../tmp/`, or any other pre-revamp documentation file are prohibited — those files will be removed when this revamp is complete, and any link out of this tree becomes a broken pointer the moment cleanup happens. When you need to cite a concept that lived in the prior docs, restate it inside this tree as the platform's own doctrine, or forward-point to the file in this tree where it will live (marked `(forthcoming)` if not yet written). Cross-links to [`../manifesto/`](../manifesto/) and [`../CLAUDE.md`](../CLAUDE.md) are allowed because both survive cleanup.

## Out of scope for this tree

- **Product surface mockups, UI design, copy decisions.** These live in product/design tooling.
- **Domain content** (instrument families, market conventions per asset class). This lives in [`../manifesto/`](../manifesto/) and is referenced from here, not duplicated.
- **Brainstorm / exploration docs.** These live outside this tree until they harden into a decision (at which point an ADR is filed in [`05_decisions/`](05_decisions/) and the brainstorm is archived under [`06_roadmap/`](06_roadmap/) if it relates to a phase, or absorbed into the relevant component contract).
- **Agent operating instructions.** These live in [`../CLAUDE.md`](../CLAUDE.md). The agent reads that file plus the relevant component contract.

---

*This README is the only file in this tree that points to every other folder. If you are editing this file, you are changing the navigation contract — get a review.*
