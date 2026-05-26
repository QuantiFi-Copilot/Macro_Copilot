# File and Folder Layout

> Where every kind of code lives. The single source of truth for the platform's directory shape — what belongs under `shared/`, what belongs under an agent, what belongs under `tests/`, and what never crosses those boundaries.

**Version:** v1
**Last reviewed:** 2026-05-18

## 1. Universal Rule

Code is organised by **substrate vs domain**:

- **Substrate** (`shared/`, `state/`, `api/`, `tests/`): asset-class-blind code that any agent uses. Operators, artifact types, the workflow engine, persistence, the API surface, the cross-cutting test suite.
- **Domain** (`<agent>/`, e.g. `rates_agent/`): asset-class-aware code that lives under exactly one agent. Primitives, workflow templates, the primitive resolver, the agent's MCP server.

Agents are **sibling-isolated** ([P11](../00_thesis/01_non_negotiables.md)): no agent ever imports from another agent. The substrate is the only legal cross-agent dependency.

## 2. The Canonical Tree

```
Macro_Copilot/
├── shared/                                # Substrate: asset-class-blind
│   ├── analytics/                         # Domain-aware compute helpers reusable across
│   │                                      # primitives (curve_bootstrap, regression, …).
│   │                                      # Despite finance vocabulary, code is shared-
│   │                                      # callable; agent-specific compute belongs in
│   │                                      # <agent>/<domain>/tools/<tool>/compute.py.
│   ├── artifacts/                         # Closed-family typed artifact wrappers
│   │   ├── types.py                       # Series, SeriesSet, EventSet, Panel, WindowedPanel
│   │   ├── trades.py                      # TradeSet + LegSpec + Trade
│   │   ├── lineage.py                     # PrimitiveStep / OperatorStep / AdapterStep / FetchStep
│   │   ├── missingness.py                 # MissingnessPolicy discriminated union
│   │   ├── units.py                       # TimeSeriesUnits re-export
│   │   └── adapters/                      # Bridges: primitive output dict → typed artifact
│   │       ├── from_time_series.py        # tool_output_to_artifact_series / _panel
│   │       └── from_raw_dataframe.py      # raw_dataframe_to_artifact_series
│   ├── config/                            # Tool + operator config loaders, lints
│   │   ├── tool_config.py                 # primitive config.yaml schema + loader
│   │   ├── operator_config.py             # operator config.yaml schema + loader
│   │   └── lint.py                        # cross-component convention-drift lint
│   ├── operators/                         # Closed family of finance-blind operators
│   │   └── <operator_name>/               # four-file folder per OPR3
│   │       ├── __init__.py
│   │       ├── config.yaml
│   │       ├── schemas.py
│   │       └── operator.py
│   ├── schemas/                           # Cross-component Pydantic schemas (TimeSeries, …)
│   └── workflow/                          # Workflow substrate (engine, registry, validator)
│       ├── executor.py
│       ├── template.py                    # WorkflowTemplate + SlotDeclaration + constraints
│       ├── template_loader.py
│       ├── template_registry.py
│       ├── template_card.py
│       ├── registry.py                    # OPERATOR_REGISTRY + ARTIFACT_TYPE_NAMES
│       ├── result.py                      # WorkflowResult
│       ├── types.py                       # PrimitiveNode / OperatorNode / WorkflowEdge / Workflow
│       └── validate.py
│
├── state/                                 # Substrate: persistence
│   ├── artifact_store.py                  # put_artifact / get_artifact + per-type codecs
│   ├── schemas.py                         # DB row schemas + ArtifactTypeLiteral
│   ├── workspace_repo.py
│   ├── dag_repo.py
│   ├── methodology_versions.py
│   └── object_storage.py
│
├── api/                                   # Substrate: HTTP / WebSocket surface
│   ├── server.py
│   ├── routes/                            # FastAPI routes (chat, workflows, rates, ...)
│   └── dependencies.py
│
├── <agent>/                               # Domain: one agent per asset class
│   ├── <domain>/                          # e.g. sovereign_bonds, ois, fx, equities
│   │   ├── tools/                         # Primitives — one folder per tool
│   │   │   └── <tool_name>/               # four-file folder per PR3
│   │   │       ├── __init__.py
│   │   │       ├── config.yaml
│   │   │       ├── schemas.py
│   │   │       └── compute.py
│   │   ├── reference/                     # static domain reference data (optional)
│   │   └── mcp_server.py                  # MCP server exposing this domain's tools
│   ├── workflows/                         # Workflow templates owned by this agent
│   │   ├── <template_id>/                 # two-file folder per WT16
│   │   │   ├── __init__.py
│   │   │   └── template.yaml
│   │   ├── mcp_server.py                  # MCP server exposing templates (LLM-facing)
│   │   ├── _runner.py                     # CLI / runner shared by user-facing surfaces
│   │   ├── cli.py
│   │   └── __init__.py                    # Hosts <agent>_primitive_resolver
│   └── __init__.py
│
├── api/routes/workflows/catalogue.py      # Explicit imports of templates to expose via REST
│
└── tests/                                 # All tests, flat
    ├── conftest.py                        # Cross-cutting fixtures
    ├── _workflow_synthetic_fetchers.py    # Shared synthetic DB-fetcher patches
    ├── test_<tool>_compute.py             # Primitive compute-layer tests (PR16 triplet)
    ├── test_<tool>_wiring.py              # Primitive MCP wiring tests
    ├── test_<tool>_sql_validation.py      # Primitive SQL parity tests
    ├── test_<operator>.py                 # Operator unit tests (OPR16)
    ├── test_workflow_<template>.py        # Workflow template tests (WT15)
    ├── test_artifacts.py                  # Artifact contract tests (ART13)
    └── ...
```

## 3. Why This Exists

- **Sibling-isolated agents** ([P11](../00_thesis/01_non_negotiables.md)). Each agent is independently versioned, independently deployable, and never aware of any other agent. The only cross-agent dependency is the substrate.
- **Substrate stays finance-blind** ([P9](../00_thesis/01_non_negotiables.md)). The substrate is the operator + workflow engine + artifact types + persistence. None of it knows what asset class is being analyzed; that knowledge lives in the agent's primitives and the slot values bound at runtime.
- **Closed families live at one address**. `OPERATOR_REGISTRY`, `ARTIFACT_TYPE_NAMES`, `WORKFLOW_ARCHETYPES`, `_ARTIFACT_CLASSES` — each is the single source of truth, owned by `shared/`. Per-component contracts cite the exact address.
- **Tests are flat** so test discovery is uniform and so component folders stay focused on production code. A reviewer reading `shared/operators/align_series/` sees four files and knows the entire operator; the tests sit alongside every other test in `tests/`.

## 4. Component Manifestations

| Component | Where it lives | Why there |
|---|---|---|
| Primitive | `<agent>/<domain>/tools/<tool_name>/` ([PR3](../02_components/primitive/README.md)) | Asset-class-aware; sibling-isolated under the agent |
| Operator | `shared/operators/<operator_name>/` ([OPR3](../02_components/operator/README.md)) | Asset-class-blind; substrate-shared |
| Artifact wrapper class | `shared/artifacts/types.py` (or `trades.py` for compound shapes) ([ART2](../02_components/artifact/README.md)) | Closed family at one address |
| Artifact adapter (bridge) | `shared/artifacts/adapters/` ([ART14](../02_components/artifact/README.md)) | Substrate-shared; only legal path from primitive output → artifact |
| Artifact-store codec | `state/artifact_store.py` ([ART6](../02_components/artifact/README.md)) | Persistence is substrate |
| Workflow template | `<agent>/workflows/<template_id>/` ([WT2](../02_components/workflow_template/README.md)) | Asset-class-scoped by its agent's primitive resolver |
| Workflow substrate | `shared/workflow/` ([WT2](../02_components/workflow_template/README.md)) | Asset-class-blind engine + registry |
| Playbook | `<agent>/<domain>/playbooks/<playbook>.yaml` (per playbook contract) | Asset-class-aware ingestion spec |

## 5. Anti-Patterns

- **Primitive under `shared/`.** `shared/tools/curve_spread/` is wrong; the tool is asset-class-aware. Put it at `rates_agent/sovereign_bonds/tools/curve_spread/`.
- **Operator under an agent.** `rates_agent/operators/align_series/` is wrong; operators are finance-blind. Put it at `shared/operators/align_series/` (per OPR3).
- **Workflow template under `shared/workflows/`.** Templates bind specific primitives via the agent's resolver; they belong at `<agent>/workflows/`.
- **Cross-agent import.** `from fx_agent.primitives import ...` inside `rates_agent` (or vice versa) violates [P11](../00_thesis/01_non_negotiables.md). Agents communicate only through substrate registries.
- **Test inside a component folder.** `shared/operators/align_series/test_align_series.py` violates the flat-tests convention. Move to `tests/test_align_series.py`.
- **Compute helper duplicated across primitives.** A function copied from one primitive's `compute.py` into another's belongs in `shared/analytics/`, not in two `compute.py` files.
- **State / persistence logic inside a component.** Reading from the DB inside `compute.py` is fine for primitives; reading from the *artifact store* inside an operator is not. Persistence is `state/`, not `shared/operators/`.
- **A new top-level folder.** Anything new lives under `shared/`, an existing agent, `state/`, `api/`, or `tests/`. New top-level folders require an ADR.

## 6. Reviewer Checks

- [ ] Does the new code live in the right layer (substrate vs agent vs state vs API)?
- [ ] If a primitive, is it under `<agent>/<domain>/tools/<tool_name>/` with the four-file shape?
- [ ] If an operator, is it under `shared/operators/<operator_name>/` with the four-file shape?
- [ ] If a workflow template, is it under `<agent>/workflows/<template_id>/` with the two-file shape?
- [ ] No cross-agent imports (`grep -r "from <other_agent>" <agent>/`).
- [ ] No new top-level folders unless the PR carries an ADR.
- [ ] Tests live in `tests/test_<thing>.py`, not inside component folders.
- [ ] Shared compute helpers (used by ≥2 primitives) live in `shared/analytics/`, not duplicated in multiple `compute.py`.

## 7. Links

- [P3 (consistency by contract)](../00_thesis/01_non_negotiables.md), [P11 (sibling-isolated agents)](../00_thesis/01_non_negotiables.md)
- [`naming_conventions.md`](naming_conventions.md) — names within each folder
- Component contracts: [`primitive/README.md`](../02_components/primitive/README.md) (PR3), [`operator/README.md`](../02_components/operator/README.md) (OPR3), [`artifact/README.md`](../02_components/artifact/README.md) (ART2, ART6, ART14), [`workflow_template/README.md`](../02_components/workflow_template/README.md) (WT2, WT16)
