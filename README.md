# Macro Copilot

An LLM-orchestrated research platform for macro funds, built on a typed substrate of validated quantitative primitives. Plain-English questions in, deterministic and reproducible analyses out.

> **This branch is the integrated rates + FX build**: both domains run from a single
> orchestrator, over one data substrate. See [Authorship](#authorship) for the domain split.

---

## What we are building

Discretionary macro trading desks sit on top of institutional-grade data and significant talent. The structural bottleneck is not data quality or analytical skill — it is **speed to insight**. The repetitive workflow between a PM's question and a quantitative answer today requires an analyst to open a terminal, pull data, write or run a script, format the output, and relay it back. Each pod rebuilds the same scripts and dashboards from scratch.

Macro Copilot collapses that loop. The analyst defines the analytical building blocks once, in code with version control. The PM types a question in plain English; the system picks a pre-validated workflow, runs it deterministically against a multi-asset time-series database, and streams the answer back with the full computation behind it. Follow-ups extend the same work instead of starting over.

The LLM does zero math. The compute layer is named, versioned Python tools backed by TimescaleDB. Every number traces to a tool, exact parameters, data source, timestamp, and methodology card.

---

## Design properties

| Property | What it means |
|---|---|
| **Composable** | Cross-asset by construction. New instruments and primitives slot into the registry without changing the orchestrator or the UI. |
| **Iterative** | Follow-ups extend the prior DAG. Turn three uses turn two's result. The conversation is the unit of analysis. |
| **Reproducible** | Every artifact is content-addressed. Same data plus same methodology plus same parameters yields a byte-identical result, today or six months from now. |
| **Auditable** | Every number traces to a named tool, exact parameters, data source, timestamp, and methodology card. There are no black boxes. |

---

## Current status

| Layer | Status |
|---|---|
| Data substrate — TimescaleDB + Bloomberg ingestion | LIVE |
| Primitives — rates: sovereign bonds, OIS, inflation-linked, inflation swaps, policy futures, bond futures | LIVE — 58 tools across 6 domains |
| Primitives — FX: spot, forwards, NDF, ATM vol, vol smile, macro indices | LIVE — 30 tools, 9 ingestion playbooks |
| Operators — finance-blind structural transforms | LIVE — 12 operators |
| Workflow templates — desk archetypes | LIVE — 2 of 4 (event_study, regime_conditioned_relationship) |
| Conversational orchestration — supervisor + seven domain agents over MCP | LIVE |
| Frontend — Monitor, Library, Ask, Workspace, FX surfaces | LIVE |
| **Persistent state, working set, replayable workspaces** | **In progress (Phase 0)** |
| Backtest archetype | Scoping |

Validation: a 30-prompt rates-agent gauntlet has run at 29/29 correct routings, 29/29 correct tool selections, and 59/59 mathematically accurate outputs against SQL ground-truth values.

---

## Repository layout

```
.
├── Macro_Copilot/                  # The active product. All work happens here.
│   ├── api/                        # FastAPI server (REST + WebSocket)
│   ├── orchestrator/               # LangGraph supervisor, per-domain agents, routing
│   ├── rates_agent/                # Rates primitives across six domains, workflow templates, MCP servers
│   ├── fx_agent/                   # FX primitives (spot, forwards, NDF, vol), ingestion playbooks, MCP server
│   ├── shared/                     # Typed substrate: artifacts, operators, workflow executor, bridge
│   ├── ingestion/                  # Parquet → TimescaleDB ingestion
│   ├── database/                   # Schema + connection helpers
│   ├── UI/                         # React + Vite frontend (macro-copilot-dashboard-polished)
│   ├── docs/                       # Architecture, methodology source registry, technical debt
│   ├── manifesto/                  # Per-instrument scoping docs + tool manifest YAMLs
│   ├── tests/                      # pytest suite (substrate, primitives, workflows, gauntlets)
│   └── environment.yml             # Conda env definition (Python 3.12, blpapi via conda)
│
├── QuantFinanceProject/            # Separate, older project. Not part of Macro Copilot.
│
├── pyproject.toml                  # Project metadata + ruff configuration
├── pytest.ini                      # Project-wide pytest configuration
├── .github/workflows/ci.yml        # CI: ruff lint + pytest substrate subset
└── README.md                       # This file
```

---

## Architecture (one-paragraph)

Five strict layers separate concerns. **L1 — Data substrate** stores one row per (date, instrument, field) with a full ingestion audit trail. **L2 — Primitives** turn data into finance-aware market quantities (yields, spreads, betas) via single-purpose typed tools whose methodology is locked in version-controlled YAML. **L3 — Operators** are finance-blind structural transforms over typed artifacts (alignment, arithmetic, thresholding, event windowing, ranking) — they know about units, frequency, and missingness; they know nothing about yields or basis points. **L4 — Workflow substrate** is a typed DAG validator + executor; every workflow is type-, unit-, and shape-checked before any compute runs. **L5 — Workflow templates** are named desk-shaped archetypes (event study, regime-conditioned relationship, attribution, cross-sectional screen) with locked topology and slot-fillable parameters. The LLM sits above L5: it chooses a template and binds slots, or — in design — emits a fixed-primitive operator DAG that the validator admits before execution. **The LLM never invents math.**

The primitive → operator handoff goes through a single bridge that lifts JSON-shaped primitive outputs into typed artifacts with structured lineage. Every artifact is content-addressed; lineage chains thread through every step; reproducibility is automatic by hash comparison. See `Macro_Copilot/docs/architecture/` for the full specification.

---

## Documentation

Authoritative references inside the repo:

| Topic | Document |
|---|---|
| Primitive (tool) architecture | `Macro_Copilot/docs/architecture/tool_architecture.md` |
| Operator architecture | `Macro_Copilot/docs/architecture/operator_architecture.md` |
| Workflow template architecture | `Macro_Copilot/docs/architecture/workflow_architecture.md` |
| Primitive → operator bridge | `Macro_Copilot/docs/architecture/bridge.md` |
| Methodology source-tag registry | `Macro_Copilot/docs/architecture/methodology_sources.md` |
| Bloomberg accuracy boundary | `Macro_Copilot/docs/core_principles.md` |
| Technical debt register | `Macro_Copilot/docs/technical_debt.md` |
| Product overview decks | `Macro_Copilot/docs/PDFs/` |

---

## Development

### Running the project locally

The runtime stack is conda-managed (because the Bloomberg API `xbbg` / `blpapi` is not PyPI-installable):

```bash
cd Macro_Copilot
conda env create -f environment.yml
conda activate macro-env

# Bring up Postgres + TimescaleDB + Prefect + API server
docker compose up -d

# Run the test suite
pytest
```

### Tooling

Lint and dev tooling are pip-installable into the conda env via the `[dev]` extra declared in `pyproject.toml`:

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

CI runs `ruff check` and a substrate-level pytest subset on every PR. See `.github/workflows/ci.yml`.

---

## Authorship

Macro Copilot is a joint Applied Project (Imperial College London), built by two contributors
with a strict domain split.

| Domain | Author |
|---|---|
| **Rates** — sovereign bonds, OIS, inflation-linked, inflation swaps, policy futures and bond futures primitives; workflow templates; platform architecture (L1–L5); orchestration; frontend platform | **Sreeram Andra** ([@Sreeram1503](https://github.com/Sreeram1503)) |
| **Foreign exchange** — 6 data substrates (spot, forwards, NDF, ATM vol, vol smile, macro indices; 695 instruments); 30 FX tools covering forward-implied carry, CIP and cross-currency basis, the volatility surface and cross-sectional scanners; 9 declarative ingestion playbooks; the FX dashboard surfaces; integration into the shared substrate | **Sacha Mimoun** ([@sacha-mimoun](https://github.com/sacha-mimoun)) |

The FX domain was developed as a stacked PR series
([#178 → #244](https://github.com/QuantiFi-Copilot/Macro_Copilot/pulls?q=is%3Apr+author%3Asacha-mimoun))
and is merged into the rates mainline on this branch. The PR and commit history carries the
full record of each author's work.

---

© 2026 Macro Copilot. Private internal repository.
