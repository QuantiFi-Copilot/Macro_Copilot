# Macro Copilot

A research platform for macro desks. You ask a question in plain English; the system answers
it with a calculation you can inspect, reproduce and argue with.

The platform covers the **rates complex** and **foreign exchange**, on top of a
Bloomberg-sourced TimescaleDB warehouse.

---

## The idea

Macro research is rarely held up by the difficulty of a single calculation. It is held up by
the work of assembling one: picking instruments, pulling history, handling day-count and
calendar conventions, aligning series, and only then computing something.

Natural language is an appealing way to shorten that path, and it carries an obvious risk. A
model can follow the wording of a question perfectly, misread the calculation behind it, and
answer with confidence anyway.

So the architecture separates the two problems:

- **The language model interprets and routes.** It reads the question, decides which existing
  components should run, and explains the result. It never produces a number itself.
- **Deterministic code computes.** Tools own their market conventions explicitly. Given the
  same inputs, they return the same output every time.
- **Workflows are validated before they run**, then carry a content-addressed record of how
  they were built, so a result can be reproduced later.
- **When the catalogue cannot express what was asked, the system says so** instead of
  substituting the nearest-sounding calculation.

## How it is organised

```
Language model      interprets the question, chooses a route
Templates           fixed analytical structures with open parameters
Workflow substrate  typed graphs, validated before execution
Operators           finance-blind statistical transforms
Tools               individual market calculations, each owning its conventions
Data                Bloomberg observations in TimescaleDB
```

Tools are split across market-specific domains — sovereign bonds, OIS, inflation-linked
bonds, inflation swaps, policy futures, bond futures and foreign exchange. Each domain runs
as its own MCP subprocess with access only to its own toolset, so a request routed to one
domain cannot reach another's tools. This is a routing and reliability boundary, not a
security or entitlement boundary.

Interfaces on top: **Monitor** (recurring views), **Ask** (a conversational research agent),
**Build** (persistent workflows) and **Library** (the catalogue of what the system can
actually compute).

## Repository layout

```
Macro_Copilot/
  orchestrator/     routing, domain registry, MCP client wiring
  rates_agent/      rates domains, one MCP server each
  fx_agent/         foreign exchange
  shared/           finance-blind operators, workflow engine, schemas
  manifesto/        tool manifests (the Library reads these)
  ingestion/        declarative Bloomberg ingestion playbooks
  database/         TimescaleDB schema and migrations
  api/              FastAPI service
  UI/               React + TypeScript dashboard
  tests/            fixtures and differential tests
  docs/             engineering documentation

QuantFinanceProject/   earlier, unrelated Indian-equity research work
```

## This branch

`sacha-fx-integration` is a snapshot of the foreign-exchange integration as it stood in
**May 2026**: FX spot and forward curves, forward-implied carry and carry decay, realised
volatility and volatility risk premium, correlation and beta, and cross-sectional scanners,
wired into the shared orchestrator and workflow substrate.

FX work continued after this snapshot — non-deliverable forwards, cross-currency basis,
volatility smiles and term structure, and the Monitor widgets. The most recent state lives on
[`codex/fx-ui-wave2-widgets`](../../tree/codex/fx-ui-wave2-widgets).

## Authorship

Macro Copilot is a two-person project.

- **Rates complex** (sovereign bonds, OIS, inflation-linked bonds, inflation swaps, policy
  futures, bond futures) and the initial platform architecture: **Sreeram Andra**
- **Foreign exchange**, its integration into the shared substrate, and the FX interface work:
  **Sacha Mimoun**

The orchestration layer, the typed workflow substrate and the shared operator library were
developed jointly. `git log` and the branch history record who wrote what.

## Status

Research software under active development, not a production system. The two halves are
developed on separate branches and integrated on demand, so any given branch exposes a subset
of the full catalogue. Transaction costs are not modelled, and coverage is limited to rates
and foreign exchange.
