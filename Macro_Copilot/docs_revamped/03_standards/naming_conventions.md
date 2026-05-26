# Naming Conventions

> The only place where naming rules live. Every module, file, folder, class, function, convention key, test file, and error class follows the patterns here.

**Version:** v1
**Last reviewed:** 2026-05-18

## 1. Universal Rule

| Kind | Convention | Example |
|---|---|---|
| Modules, files, folders | `snake_case` | `curve_spread.py`, `align_series/`, `rates_agent/` |
| Functions, methods, parameters | `snake_case` | `def calculate_curve_spread(...)`, `params=`, `tool_config_hash=` |
| Classes | `CamelCase` | `WorkflowTemplate`, `OperatorSpec`, `Series`, `PrimitiveStep` |
| Constants, module-level singletons | `SCREAMING_SNAKE_CASE` | `OPERATOR_REGISTRY`, `ARTIFACT_TYPE_NAMES`, `CONFIG_PATH` |
| Private symbols (file-local) | leading `_` | `_compute_step_hash`, `_NAME_TO_CLASS` |
| MCP tool names | `<verb>_<noun>_tool` | `calculate_curve_spread_tool`, `compute_financing_rate_tool` |
| Lineage step classes | `<Kind>Step` | `PrimitiveStep`, `OperatorStep`, `AdapterStep`, `FetchStep` |
| Component error classes | `<Component>Error` | `CurveSpreadError`, `AlignSeriesError`, `WorkflowExecutionError` |
| Primitive input/output schemas | `<Primitive>Input`, `<Primitive>Output` | `CurveSpreadInput`, `CurveSpreadOutput` |
| Operator parameter schemas | `<Operator>Params` | `AlignSeriesParams`, `ThresholdEventsParams` |
| Operator names (registry keys + folder + function) | `snake_case` verb-phrase | `align_series`, `threshold_events`, `series_arithmetic` |
| Workflow archetype values | `snake_case` noun-phrase | `event_study`, `regime_conditioned_relationship`, `backtest` |
| Template IDs | `snake_case` noun-phrase | `event_study`, `backtest`, `breakeven_event_study` |
| Slot names in `template.yaml` | `snake_case`, structural (not asset-class-specific) | `signal_tool_name`, `target_curve_family`, `threshold` |
| `config.yaml` `defaults` keys | `snake_case`, **cross-component-comparable** | `ffill_limit_days` (same key everywhere it appears) |
| Convention `source` tags | `snake_case`, descriptive, registered | `industry_standard_252_business_days`, `methodology_judgement_pending_review` |
| Test files | `tests/test_<thing>.py` (flat, at the top of `tests/`) | `test_curve_spread_compute.py`, `test_workflow_event_study.py` |
| Per-primitive test triplet | `test_<tool>_compute.py`, `test_<tool>_wiring.py`, `test_<tool>_sql_validation.py` | `test_curve_spread_compute.py` + `..._wiring.py` + `..._sql_validation.py` |
| Per-operator test pair | `test_<operator>.py` + a workflow-integration test naming the operator | `test_align_series.py`, `test_conditional_aggregate.py` |
| Per-template test file | `tests/test_workflow_<template_id>.py` (or `..._e2e.py`, `..._synthetic.py`, `..._parity.py` for split coverage) | `test_workflow_event_study.py`, `test_workflow_backtest_e2e.py` |

## 2. Why This Exists

- **Predictable navigation.** A reader looking for the operator `align_series` finds it at `shared/operators/align_series/align_series.py` with `class AlignSeriesParams(BaseModel)` and tests at `tests/test_align_series.py`. No guessing.
- **Cross-component comparability.** When `ffill_limit_days` appears in two primitives' `config.yaml`, the substrate-wide lint compares them by exact key match. Naming the same convention differently in two places (`ffill_limit_days` vs `ffill_limit`) silently breaks the lint's drift detection.
- **Closed-family clarity.** A tool whose registry key, file name, function name, and `*Input` class name all share the same root makes the closed family auditable by `grep`. Different roots fragment the audit surface.

## 3. Applies To

Every component layer: primitives, operators, artifacts, workflow templates, playbooks, workspace, orchestration. Every standards document. Every test. Every commit message that cites a principle by ID.

## 4. Component Manifestations

| Component | Naming-specific principle |
|---|---|
| Primitive | [PR3](../02_components/primitive/README.md), folder shape — file names mirror tool name |
| Operator | [OPR3](../02_components/operator/README.md), folder shape — function name = folder name |
| Artifact | [ART2](../02_components/artifact/README.md), class name names the *shape* (`Series`, `EventSet`), not the use case (`YieldSeries`, `RegimeEvents`) |
| Workflow template | [WT3](../02_components/workflow_template/README.md), slot + node names are structural, not asset-class-specific |
| Playbook | naming convention in the per-playbook `README.md` (component contract) |

## 5. Anti-Patterns

- A tool whose folder is `curve_spread/` but whose function is `calculate_curve_spread` and whose registry key is `compute_curve_spread`. Pick one root.
- A class named `YieldSeries` when the artifact type is `Series` and the payload happens to be yields. Asset-class-blind types — name the shape.
- A primitive that calls its forward-fill cap `ffill_limit_days` while the corresponding operator calls it `ffill_limit`. The lint cannot detect drift.
- A test file at `shared/operators/align_series/test_align_series.py` instead of `tests/test_align_series.py`. The platform's test discovery convention is flat — components do not co-locate tests.
- A source-tag value `"default"` / `"standard"` / `"convention"` / `"tbd"` / `"bloomberg"` (vague vendor name only). Vague tags fail the methodology-disclosure registry check (see [`methodology_disclosure.md`](methodology_disclosure.md)).
- Slot names that embed asset-class concepts (`rates_curve_family`, `fx_pair`). Slot names are structural; asset-class specificity lives in the bound *value*, not the slot name.
- An operator named `align_rates_series`. Operators are finance-blind ([OPR6](../02_components/operator/README.md)); the right name is `align_series`.
- A template ID like `general_analysis` or `flexible_backtest`. Template IDs name one canonical analysis ([WT1](../02_components/workflow_template/README.md)).

## 6. Reviewer Checks

When reviewing any PR:

- [ ] Module/file/folder names are `snake_case`.
- [ ] Class names are `CamelCase`; constants are `SCREAMING_SNAKE_CASE`.
- [ ] If the PR adds an MCP tool, its name ends in `_tool` and the folder/function/registry-key roots match.
- [ ] If the PR adds an error class, it follows `<Component>Error` and subclasses the right base (per [`error_handling.md`](error_handling.md)).
- [ ] If the PR adds a `config.yaml` `defaults` key, the key matches the cross-component convention (run `python -m shared.config.lint`).
- [ ] If the PR adds a slot or operator name, it is structural, not asset-class-specific.
- [ ] If the PR adds a test file, it is at `tests/test_<thing>.py`, not inside the component folder.

## 7. Links

- [P3 (consistency by contract)](../00_thesis/01_non_negotiables.md)
- [`file_and_folder_layout.md`](file_and_folder_layout.md) — where each name's owning file lives
- [`error_handling.md`](error_handling.md) — error-class naming + base-class rules
- Component contracts: [`primitive/README.md`](../02_components/primitive/README.md), [`operator/README.md`](../02_components/operator/README.md), [`artifact/README.md`](../02_components/artifact/README.md), [`workflow_template/README.md`](../02_components/workflow_template/README.md)
