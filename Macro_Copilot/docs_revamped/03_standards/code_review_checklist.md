# Code Review Checklist

> The universal PR gate. Routes to the component runbook for component-specific items; carries the cross-cutting items inline. **This is the single file every reviewer runs against every PR.**

**Version:** v1
**Last reviewed:** 2026-05-18

---

## Section 0 — Route by component

For every PR, identify which component layers it touches and run the matching runbook's PR review checklist (it lives at the bottom of each runbook):

| If the PR touches… | Run this runbook checklist | Cite by |
|---|---|---|
| A primitive (`<agent>/<domain>/tools/<tool>/`) | [`02_components/primitive/runbook.md`](../02_components/primitive/runbook.md) | PR1–PR16 |
| An operator (`shared/operators/<op>/`) | [`02_components/operator/runbook.md`](../02_components/operator/runbook.md) | OPR1–OPR16 |
| An artifact type / metadata enum / codec | [`02_components/artifact/runbook.md`](../02_components/artifact/runbook.md) | ART1–ART16 |
| A workflow template | [`02_components/workflow_template/runbook.md`](../02_components/workflow_template/runbook.md) | WT1–WT16 |
| A playbook | [`02_components/playbook/runbook.md`](../02_components/playbook/runbook.md) | per-playbook contract |
| A closed-family extension (new archetype, new artifact type, new slot type, new metadata enum value, hash recipe change) | Both the component runbook **and** [`closed_family_discipline.md`](closed_family_discipline.md) | P8 + component principles |
| Lineage / hash behaviour | [`hash_determinism.md`](hash_determinism.md) + [ART10](../02_components/artifact/README.md) | P4, ART10 |
| API surface (`api/routes/...`) | (no dedicated runbook yet) + [`error_handling.md`](error_handling.md) | P6 |
| Standards files in this folder | ADR in [`05_decisions/`](../05_decisions/) + code-owner sign-off | per [root README](../README.md) contribution rules |

If a PR touches multiple layers (common — a new primitive often touches the operator registry indirectly through a new operator, or a template touches the closed family if it terminates at a new artifact type), run *every* matching runbook.

If the PR touches no component layer (a substrate-internal change, a test-only change), Section 0 is satisfied; proceed to the universal items below.

---

## Section 1 — Universal items (every PR, regardless of layer)

### 1A. Naming + layout

- [ ] All new modules / files / folders are `snake_case`; classes are `CamelCase`; constants are `SCREAMING_SNAKE_CASE`. (See [`naming_conventions.md`](naming_conventions.md).)
- [ ] New code lives at the right address (primitive under agent, operator under `shared/`, artifact under `shared/artifacts/`, template under `<agent>/workflows/`, tests at `tests/`). (See [`file_and_folder_layout.md`](file_and_folder_layout.md).)
- [ ] No cross-agent imports (`grep -r "from <other_agent>" <agent>/` is empty). [P11](../00_thesis/01_non_negotiables.md).
- [ ] No new top-level folders. (If a folder is genuinely needed, the PR carries an ADR.)
- [ ] If the PR adds an MCP tool / operator / error class / params class, names follow the conventions table in [`naming_conventions.md`](naming_conventions.md).

### 1B. Typed boundaries

- [ ] Every new `BaseModel` has `model_config = ConfigDict(frozen=True, extra="forbid", ...)`. (See [`typed_boundary_discipline.md`](typed_boundary_discipline.md).)
- [ ] `arbitrary_types_allowed=True` appears only when the model carries pandas / numpy / other non-Pydantic-validatable payload.
- [ ] Cross-field invariants live in `@model_validator(mode="after")`, not in caller code.
- [ ] No `Dict[str, Any]` or `**kwargs` carrying contracted data between layers (primitive `compute.py` returning a dict is the documented exception — the bridge lifts it).
- [ ] No `Optional[T]` with `None` default for a field that must be set in practice.

### 1C. Error handling

- [ ] New error classes subclass the layer-appropriate base — `Exception` (loader/config/registry), `ValueError` (validation, component-internal), `RuntimeError` (executor). (See [`error_handling.md`](error_handling.md).)
- [ ] No bare `except:` and no `except Exception:` outside the top-level API surface.
- [ ] Error messages name the specific invariant ("`Series` payload index must be sorted ascending", not "invalid input").
- [ ] Operators raise typed errors; they do not return `{"error": ...}` envelopes (OPR13).
- [ ] Re-raises preserve cause via `raise ... from e`.

### 1D. Tests

- [ ] Tests live at `tests/test_<thing>.py` (flat, not co-located).
- [ ] Determinism: fixed random seeds, frozen dates, no `datetime.now()` in test code.
- [ ] Real validators run (no Pydantic mocking).
- [ ] Negative tests use specific exception class + `match="<fragment>"`.
- [ ] Component-specific test pattern (per runbook) is satisfied — triplet for primitives, unit + integration for operators, four layers for artifacts, five layers for templates. (See [`test_patterns.md`](test_patterns.md) for the cross-cutting discipline.)

### 1E. Methodology disclosure

- [ ] Every new methodology choice is in `config.yaml` `defaults` (primitive/operator) or `slot_schema` (template), not as a code constant.
- [ ] Every `defaults` entry has a `source` from the canonical tag registry. (See [`methodology_disclosure.md`](methodology_disclosure.md).)
- [ ] No vague tags (`"default"`, `"standard"`, `"convention"`, `"tbd"`, `"bloomberg"` alone).
- [ ] `python -m shared.config.lint` passes (cross-component convention-drift check).

### 1F. Hash / lineage (when applicable)

- [ ] If the PR adds a new producer (primitive / operator / adapter), the step is constructed via `<Step>.build()` (not direct construction with explicit `hash=`).
- [ ] Every content-defining choice is folded into the step's `params` (per [`hash_determinism.md`](hash_determinism.md)).
- [ ] If the PR changes the hash recipe (rare), an ADR exists and `tests/state/test_hash_stability.py` is regenerated with documented migration.

### 1G. Closed-family (when applicable)

- [ ] If the PR adds an entry to a closed family (`ARTIFACT_TYPE_NAMES`, `WORKFLOW_ARCHETYPES`, `SlotDeclaration.type` Literal, `SlotConstraint` union, `TimeSeriesUnits`, `MissingnessPolicy`, hash recipe), an ADR exists and *every* downstream consumer is updated in the same PR. (See [`closed_family_discipline.md`](closed_family_discipline.md).)
- [ ] No parallel enum invented (e.g. `class PanelUnits` next to `TimeSeriesUnits`).

### 1H. Code hygiene

- [ ] No commented-out code blocks. Either delete or pull into a doc comment with rationale.
- [ ] No new comments that paraphrase the code ("# Increment counter" above `counter += 1`). Comments earn their place by explaining *why* something non-obvious is true; otherwise omit. (Per the [thesis on comments in CLAUDE.md](../../CLAUDE.md).)
- [ ] No new `# TODO` / `# FIXME` without a linked ADR or issue and a target resolution.
- [ ] No new dependencies in `pyproject.toml` / `requirements*.txt` without a one-line justification in the PR description.

---

## Section 2 — Commit message + PR description discipline

### 2A. Commit messages

Every commit follows this shape:

```
<verb>(<scope>): <one-line summary in present-tense imperative>

<optional body — what changed, why, and any non-obvious decisions>

Operationalises: <P/PR/OPR/ART/WT/AC IDs the change manifests, comma-separated>
Co-Authored-By: <agent or co-author per CLAUDE.md>
```

- **Scope** is the component (`primitive`, `operator`, `artifact`, `workflow`, `substrate`, `api`, `tests`, `docs`).
- **Summary** is ≤72 chars, imperative ("add", "fix", "rename"), no trailing period.
- **`Operationalises:` trailer** cites by ID — never paraphrase ([AC2](../00_thesis/02_ai_agent_development_contract.md), [AC6](../00_thesis/02_ai_agent_development_contract.md)). Example: `Operationalises: P3, P9; OPR1, OPR4, OPR6; AC1, AC3, AC6.`
- **Co-Authored-By** trailer is present for any AI-agent-authored commit.

A commit with no `Operationalises:` trailer either (a) does not change a contracted surface, in which case it's fine to omit, or (b) does change one but the author hasn't decided which principles apply — in which case the commit is not ready.

### 2B. PR descriptions

The PR description must answer four questions:

1. **What changed?** One short paragraph.
2. **Which contracts does it operationalise?** List of P / PR / OPR / ART / WT / AC IDs.
3. **Which runbook checklists apply?** A list pointing at the matching `02_components/<component>/runbook.md` checklists.
4. **Test plan.** Bulleted markdown checklist of what was run (`pytest tests/test_<thing>.py`, `python -m shared.config.lint`, etc.).

For a **load-bearing** change ([AC class per AC contract](../00_thesis/02_ai_agent_development_contract.md)) — adding a primitive, operator, artifact type, workflow template, or extending a closed family — the description additionally:

- Cites the pre-flight check answers from the component's runbook.
- Names the reviewers (closed-family extension requires multi-reviewer sign-off).
- Documents any ADRs filed in `05_decisions/` for this change.

### 2C. Branch naming

- AI-agent branches: `<agent>/<short-task-name>` (e.g. `claude/<task>`, `codex/<task>`).
- Human branches: `<author-handle>/<short-task-name>`.
- Worktrees (for parallel agent work) follow `claude/<worktree-name>` per `.claude/worktrees/`.

### 2D. When to ask for human review before drafting code

Per [AC8](../00_thesis/02_ai_agent_development_contract.md), stop and ask the human if any of the following holds:

- The pre-flight check (per the relevant runbook) has an uncertain answer.
- The change is a closed-family extension (new archetype, new artifact type, new slot type, new metadata enum, hash recipe change).
- The change crosses an agent boundary or proposes a new top-level folder.
- The change requires an ADR but the framing has not been ratified.
- A test pattern (per component runbook) cannot be satisfied with the current code shape — meaning either the test pattern needs to change (ADR) or the code needs to be refactored.

---

## Section 3 — Reviewer's flow

1. Read the PR description; confirm sections 2B.1 – 2B.4 are present.
2. Identify which component runbooks apply (Section 0).
3. Run each matching runbook's PR review checklist.
4. Run Section 1 (universal items) against the diff.
5. Run Section 2 (commit message + PR description) against the commit history.
6. For load-bearing changes, confirm Section 2D was respected (pre-flight check + ADR + reviewer list).
7. Sign off, request changes, or escalate.

The reviewer cites by principle ID in comments. Sample comment: *"This violates [WT7](../02_components/workflow_template/README.md) — `operator_name` cannot be slot-substituted."*

---

## Section 4 — When this checklist is wrong

If a check here drifts from a component contract, the **component contract wins** and this file is updated. Standards are derived from cross-component patterns; they do not override component principles.

If a check here is missing for a real cross-cutting concern, file an ADR + add the check + bump this file's version.

If the routing in Section 0 misses a layer, add the row + link.

## Links

- All standards files: [`naming_conventions.md`](naming_conventions.md), [`file_and_folder_layout.md`](file_and_folder_layout.md), [`typed_boundary_discipline.md`](typed_boundary_discipline.md), [`error_handling.md`](error_handling.md), [`test_patterns.md`](test_patterns.md), [`methodology_disclosure.md`](methodology_disclosure.md), [`hash_determinism.md`](hash_determinism.md), [`closed_family_discipline.md`](closed_family_discipline.md)
- Component runbooks: [`primitive/runbook.md`](../02_components/primitive/runbook.md), [`operator/runbook.md`](../02_components/operator/runbook.md), [`artifact/runbook.md`](../02_components/artifact/runbook.md), [`workflow_template/runbook.md`](../02_components/workflow_template/runbook.md), [`playbook/runbook.md`](../02_components/playbook/runbook.md)
- Thesis: [`P-numbers`](../00_thesis/01_non_negotiables.md), [`AC-numbers`](../00_thesis/02_ai_agent_development_contract.md)
