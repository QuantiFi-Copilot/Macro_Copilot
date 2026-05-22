# Standards

> Cross-cutting rules that apply regardless of which component you're touching. Lateral, not vertical.

**Version:** v1
**Last reviewed:** 2026-05-18
**Status:** load-bearing. Standards changes require an ADR in [`../05_decisions/`](../05_decisions/) and explicit code-owner sign-off (per the root [`README.md`](../README.md) contribution rules).

---

## How standards relate to component contracts

[`../02_components/`](../02_components/) is organised **vertically**: each folder owns one component type (primitive, operator, artifact, workflow_template, playbook, workspace, orchestration). Each component's `README.md` carries that component's principles (`PR1–PR16`, `OPR1–OPR16`, `ART1–ART16`, `WT1–WT16`, …); each `runbook.md` carries that component's procedure + PR review checklist.

This folder is organised **laterally**: each file owns one concern that recurs across components (naming, file layout, testing, error handling, methodology disclosure, hash determinism, closed-family discipline). A standards file does *not* re-state per-component principles — it articulates the universal rule and links to the per-component manifestations.

The acid test: if a standards file's main content is "see [component]/runbook.md", delete it. If three component contracts state the same rule three different ways, move the rule here and have the component contracts link back.

## Files

| File | Concern | Cross-references |
|---|---|---|
| [`naming_conventions.md`](naming_conventions.md) | Module / file / function / class / convention-key / test-file naming | P3 |
| [`file_and_folder_layout.md`](file_and_folder_layout.md) | Where each component lives; agent isolation; substrate vs agent vs shared | P3, P11 |
| [`typed_boundary_discipline.md`](typed_boundary_discipline.md) | Frozen Pydantic with `extra="forbid"` at every typed boundary; no loose dicts crossing layers | PR8, OPR8, ART7 |
| [`error_handling.md`](error_handling.md) | Per-layer error conventions; no silent fallback; raise vs envelope per boundary | P6, PR11, OPR13, WT12 |
| [`test_patterns.md`](test_patterns.md) | Determinism, fixtures, file naming, parity tests where structurally possible | P3, P12, PR16, OPR16, ART13, WT15 |
| [`methodology_disclosure.md`](methodology_disclosure.md) | Methodology visible at every layer; source-tag registry | P5, PR7, OPR7, WT8, WT13 |
| [`hash_determinism.md`](hash_determinism.md) | Lineage step hash recipe; what's in vs out; cross-deploy stability | P4, ART10 |
| [`closed_family_discipline.md`](closed_family_discipline.md) | Closed enums + ADR-gated extension; "open catalogue within closed family" pattern | P8, PR8, OPR3, ART2, ART4, ART12, WT2, WT4, WT9 |
| [`code_review_checklist.md`](code_review_checklist.md) | **Central artifact.** Universal PR gate + commit-message + PR-description discipline. Routes to component runbooks for component-specific items. | P12, AC2, AC6, AC8 |

## How to read this folder

For day-to-day work: you don't. Use [`code_review_checklist.md`](code_review_checklist.md) as the entry point — it routes you to the per-concern file when a question turns on it.

For first-time onboarding: read [`code_review_checklist.md`](code_review_checklist.md) once, then skim each per-concern file. Total reading time should be under 30 minutes.

For changing a standard: open an ADR in [`../05_decisions/`](../05_decisions/), bump the file's version, get code-owner sign-off.

## What is *not* here

- **Component-specific principles** (PR/OPR/ART/WT numbers). Those live in [`../02_components/`](../02_components/).
- **Why** rationale that belongs to the platform thesis. That lives in [`../00_thesis/`](../00_thesis/).
- **Architecture decisions.** Those live in [`../01_architecture/`](../01_architecture/) and [`../05_decisions/`](../05_decisions/).
- **Quality bars + evaluation thresholds.** Those live in [`../04_quality_and_evals/`](../04_quality_and_evals/) (forthcoming).
- **Step-by-step procedures for adding components.** Those live in each component's `runbook.md`.

If you find yourself wanting to add a file here that mostly restates content from elsewhere, don't. Improve the link from there to here, or delete the would-be file.
