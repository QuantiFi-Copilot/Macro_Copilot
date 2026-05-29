# Runbook — How to add a new primitive

> Step-by-step procedure for adding a new primitive. The most load-bearing component runbook in the platform: primitives are where domain knowledge lands, and a wrongly-shaped primitive compounds across every workflow that consumes it.

**Version:** v1.1
**Last reviewed:** 2026-05-17
**Audience:** any contributor (human or AI agent) introducing a new primitive.
**Prerequisite reading:** [`README.md`](README.md) in this folder — the principles every primitive honours (PR1–PR16). Read it once before starting; refer back when a step says *"per principle PR<N>."*
**Operationalises principles:** P1 (built right, not as a placeholder), P3 (every primitive follows the same shape), P11 (each agent owns its own primitives), and primitive-specific PR1–PR16 throughout.
**AC class:** Adding a new primitive is **Load-bearing** per [`../../00_thesis/02_ai_agent_development_contract.md`](../../00_thesis/02_ai_agent_development_contract.md). Run the full self-check; do not skip the gate.

> **Scope note.** This runbook covers the **BACKEND slice only** — the four-file tool folder + MCP wrapper + manifest entry + Stage-2 test triplet (Steps 1–11 below). For the full end-to-end procedure across all 8 lifecycle stages (Backend → DB curated migration → Frontend module → Frontend bridge endpoint → Frontend dual-view + Monitor + Mockups → Mirrors + per-tool README → Closeout sign-off), read [`BUILD_GUIDE.md`](BUILD_GUIDE.md) instead. BUILD_GUIDE is the single front-door manual and references this runbook for the backend stages.

---

## When to use this runbook

- **Introducing a new desk concept** that does not already exist in any agent's tool catalog (e.g., a new financing-rate methodology, a new attribution decomposition, a new vol-surface primitive when FX ships).
- **Splitting an over-broad existing primitive** into two when it has accidentally accreted multiple concepts and now violates PR2.
- **Promoting a research tool to a standard primitive** once it has matured to satisfy PR7–PR11.

## When NOT to use this runbook

- **Adding new tickers to an existing playbook** — that is a playbook edit ([`../playbook/runbook.md`](../playbook/runbook.md)), not a new primitive. Per PR5, a new universe member is not a new primitive.
- **Adding a new curve family / region / country** that an existing primitive already parameterises — extend the existing primitive's allowed input values, do not add a sibling. Also PR5.
- **Tweaking a methodology default on an existing primitive** — that is an in-place YAML edit with a regenerated parity fixture and a PR rationale (PR15), not a new primitive PR.
- **Composing existing primitives into a new analytical workflow** — that is a workflow template ([`../workflow_template/`](../workflow_template/), forthcoming), not a primitive. Per PR4, the default action when a desk concept maps to a chain is *compose*, not *build a new primitive*.

The dividing line: a new primitive introduces a *concept* the platform has not seen before. An in-place edit, a coverage extension, or a workflow does not.

## Pre-flight check — seven decisions BEFORE writing any code

A new primitive PR is hard to undo (audit history, parity fixtures, downstream workflow registrations all key on the primitive's name). Seven decisions to make and document in the PR description before any code is drafted. **If any of these is uncertain, stop and ask the human (AC8).**

### 1. Concept novelty (PR5) — is this actually a new primitive?

Write one sentence naming the specific desk concept this primitive introduces. Run it past the existing catalog:

- Does any existing primitive's `tool.name + one_liner` plausibly cover this use case under a different input value?
- Is the "new" concept just "the existing concept but for `<new family / region / instrument>`"?

If the answer to either is *yes*, stop. The right path is an extension of the existing primitive, not a new one.

### 2. Parsimony (PR4) — is this actually worth a new primitive?

Can this output be produced by composing existing primitives in the same sub-agent with the existing operator catalog? If yes, write out the composition explicitly (`primitive_A → operator_X → primitive_B → operator_Y → result`), then defend the new primitive against the composition under at least one of the five criteria:

| Criterion | Argument shape |
|---|---|
| **Accuracy** | The composition accumulates error that direct computation avoids; the direct result is bit-stable in a way the composition is not. *Quantify the error if possible.* |
| **Efficiency** | The composition would require N round-trips / intermediate persistence / repeated rolling-window calculations; the direct primitive is materially faster in a way the desk would notice at typical query rates. |
| **Interpretability** | The composition produces an artifact whose *shape* the desk doesn't recognise; the direct primitive produces a single desk-recognized number with a desk-recognized name. |
| **Provenance** | The composition loses upstream methodology context across the artifact boundary; the direct primitive carries explicit provenance about every choice. |
| **LLM tool-selection clarity** | The composition would force the LLM router to assemble a multi-step DAG correctly *every time* the desk asks for what is conceptually one thing. The router's accuracy degrades as the assembly burden grows. A single primitive named after the desk concept removes the assembly burden. |

If none of the five apply defensibly, the new primitive should not be built — compose instead.

**Non-overlap check.** Run the proposed `tool.name` and `methodology.what_it_does` past every existing primitive's `name + one_liner`. Is there an existing primitive whose scope could plausibly cover this use case? If yes, the right answer is usually to *extend the existing primitive* (new central knob value, new universe coverage) rather than add a sibling. The LLM router cannot reliably distinguish between primitives whose one-liners are near-paraphrases of each other.

### 3. Metadata sufficiency (PR6) — is the data there?

List every metadata field the real desk-recognized definition requires (vendor field name, derived field, reference field). For each, confirm:

- The field is already present in `instrument_master` or `market_data_daily`, or
- It is ingested by an existing playbook with the right values, or
- A playbook extension landing in the same PR (or a prerequisite PR) makes it available.

If any required metadata is missing and cannot land alongside the primitive, the PR is closed and the primitive is added to the planned-but-blocked register with the data dependency named. **No proxies. Per PR6 and P12.**

### 4. Domain residence (PR3) — where does it live?

Name the desk role that would ask for this primitive by name. The primitive lives in that agent / sub-agent's `tools/` folder, even if it reads input data from other domains.

- For an existing agent (rates today): `<rates_agent>/<sub_agent>/tools/<tool_name>/`.
- For a new agent (FX, credit, etc., forthcoming): the agent's package must already exist (per P11, agents are sibling packages, never inherited from each other); the agent's `__init__.py`, MCP server, and `tools/` directory should land in an earlier PR or the same PR.

Cross-domain data fetching goes through `shared/analytics/*` helpers, never via a direct `from <other_agent>` import.

### 5. Concept cohesion (PR2) — is this actually one primitive?

Read your one-sentence concept description aloud. Does it use the word "or"? Does it describe two things ("either snapshot, or comparison, or panel")? Does it have an internal mode switch where the modes return *different shapes of output*?

If yes, you are looking at two (or three) primitives wearing one trench-coat. Split before drafting.

A `method: Literal[...]` parameter is fine *only if* the methods are different methodologies for **the same concept** (per PR11). A method parameter that switches between different concepts is a PR2 violation.

### 6. Central knob (PR8) — what is the single methodological choice the LLM controls?

Name the central knob. It is the parameter that *defines what the primitive IS*. Every other methodology choice — windows, ddof, fill limits, rounding, default field names, thresholds — goes in YAML, not in `<Tool>Input`.

If you find yourself wanting to expose two methodology knobs, you may have two primitives. Re-check PR2.

If the central knob is genuinely "rounding precision" or "field name override," the primitive does not have a central methodology choice and is probably a Bucket 1A level/spread primitive whose central knob is `lookback_days` (a display window, not a methodology window).

### 7. Standardness honesty (PR7–PR11) — can this primitive be standard?

Walk through PR7–PR11 once:

- **PR7** Every methodology default in YAML?
- **PR8** Exactly one central knob?
- **PR9** If this is a composition primitive, can B expose every methodology choice A makes (strict)?
- **PR10** Can the output echo enough provenance to reproduce the methodology?
- **PR11** If there are multiple methods, do unbuilt ones raise `NotImplementedError` cleanly?

If you cannot answer *yes* to all five, the primitive may genuinely require the (forthcoming) non-standard category. Surface the case to the human (AC8) rather than ship a non-standard primitive under a standard label. **Until the non-standard category opens, the answer is to defer.**

---

## Step 1 — Write the parsimony argument in the PR description

Before drafting any code, the PR description must explicitly answer:

1. The one-sentence concept being introduced.
2. The composability check (which composition was considered; why it's materially worse against at least one of the five criteria).
3. The non-overlap check (the closest existing primitive; why this one is genuinely distinct).
4. The metadata audit (every required field; where each one lives).
5. The domain residence rationale (which desk role asks for this).
6. The central knob.
7. The standardness self-check (PR7–PR11 each addressed).

This block is the reviewer's first read. If the answers are missing or hand-waved, the PR bounces before any code is reviewed. **Per AC2 (cite, never paraphrase) and AC6 (cite operationalised principles in commits), cite by PR-number throughout.**

## Step 2 — Place the four-file folder

```
<agent>/<sub_agent>/tools/<tool_name>/
  __init__.py
  config.yaml
  schemas.py
  compute.py
```

Naming conventions:

- **Folder name** (`<tool_name>`) is the *concept slug*: lowercase snake_case, names the desk concept the primitive owns. Examples in current code: `curve_spread`, `butterfly`, `rate_level`, `swap_spread`, `pca_yield_curve`, `financing_rate`. The folder name is **not** mechanically derived from the MCP tool name — `rate_level/` houses the function called `get_ois_rate_level` (the MCP wrapper adds the `_tool` suffix in the manifest).
- **MCP tool name** (in `config.yaml`'s `tool.name`) typically follows `<verb>_<concept>_tool` where the verb is one of:
  - `calculate_` — most common; for primitives that compute a derived metric (`calculate_curve_spread`, `calculate_butterfly`, `calculate_pca_yield_curve`, `calculate_swap_spread`).
  - `get_` — for primitives that retrieve a fundamental level or rate (`get_yield_levels`, `get_ois_rate_level`).
  - `build_` — for primitives that assemble a multi-instrument container (`build_sovereign_yield_panel`).
  - `compute_` — for primitives where `calculate_` would read as off-pattern given the result type (`compute_financing_rate`).
  - `classify_` — for categorical-output primitives (`classify_curve_move`).
- **Python compute function name** matches the verb pattern of the MCP tool name without the `_tool` suffix (the MCP wrapper adds `_tool`).
- The folder lives under the sub-agent that conventionally owns the concept (PR3).

The folder name and the MCP tool name are *related* but not mechanically derived from each other: folder = concept slug; MCP name = `<verb>_<concept>_tool` where the verb is chosen for natural readability.

## Step 3 — Draft `config.yaml`

Three top-level blocks, in this conventional order:

```yaml
tool:
  name: <calculate_<tool_name>_tool>   # the MCP-facing name
  domain: <sub_agent>                  # e.g., sovereign_bonds, ois
  description: |
    <one paragraph human description — what this computes and for whom>
  category: <desk_invariant_primitive | quant_standard_analytic>

conventions:
  <every methodology default>:
    value: <scalar>
    source: <registered tag from the methodology source registry>
    rationale: <one sentence explaining why this value>
    valid_range: [lo, hi]              # for numerics

methodology:
  what_it_does: |
    <one paragraph; drives the methodology card>
  assumptions:
    - <each assumption as a short string>
  citations:
    - <each citation>
  planned_extensions: |
    <if applicable: what's pinned in V1 and what the path to configurability is>
```

**Per PR7**: every methodology default goes here. *Verify*: read the YAML; can you list every methodology choice the primitive makes without reading `compute.py`? If not, methodology is hidden in code.

**Per PR12**: every `source` value references a registered tag. No vague tags (`default`, `standard`, `bloomberg`, `tbd`).

**Per PR13**: shared convention keys (`z_score_window_days`, `ffill_limit_days`, `default_field_name`, etc.) take the same value as in every other tool that uses them. Run `python -m shared.config.lint` after drafting; it must exit clean.

## Step 4 — Draft `schemas.py`

```python
class <Tool>Input(BaseModel):
    # Per-query parameters: instrument selectors + the central knob
    # Optional field_name override defaults to None (sentinel for "use YAML default")
    # Cross-field invariants live here as @model_validator(mode="after")

class <Tool>CurrentMetrics(BaseModel):
    # The snapshot block returned in the output

class <Tool>TimeSeriesRow(BaseModel):
    # One row of the optional time series

class <Tool>Output(BaseModel):
    # Top-level response; reflects the canonical wire format
    # Includes the provenance block per PR10
```

**Per PR8**: `<Tool>Input` contains the instrument selectors plus at most one methodological knob. No more.

**Per PR7**: cross-field invariants (`short_tenor != long_tenor`, `end_date > start_date`) live in code as Pydantic validators, not in YAML.

**Per PR10**: `<Tool>Output` includes a methodology/provenance block surfaced in the output fields, not just in the lineage chain.

**Per PR14**: if any output field name embeds a methodologically-load-bearing parameter (`high_252d_bps`), guard the underlying convention with `NotImplementedError` so the field name cannot lie.

## Step 5 — Draft `compute.py`

```python
from pathlib import Path
from shared.config import load_tool_config, ToolConfig

CONFIG_PATH = Path(__file__).parent / "config.yaml"

# Verb choice: calculate_ (derived metric, most common), get_ (fundamental
# level / rate), build_ (multi-instrument container), compute_, classify_.
# Pick the verb that reads naturally for the concept.
def <verb>_<tool_name>(
    engine,
    params: <Tool>Input,
    config: ToolConfig | None = None,
) -> dict:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # Read every convention explicitly
    z_window = config.convention_value("z_score_window_days")
    ffill_limit = config.convention_value("ffill_limit_days")
    # ... etc

    # Resolve field_name sentinel (None → YAML default)
    field_name = params.field_name or config.convention_value("default_field_name")

    # Fetch via shared.analytics.* helpers; pass conventions as explicit kwargs
    raw_df = fetch_<...>(engine=engine, ..., field_name=field_name)

    # Math, using shared.analytics.* primitives with conventions as kwargs
    # ...

    return {
        "current_metrics": {...},
        "time_series": {...},
        # provenance block per PR10 — required for composition primitives,
        # pasted-input primitives, statistical-fit primitives, and any
        # primitive using a proxy/approximation; optional for simple
        # Bucket 1A primitives where every choice is YAML-locked and
        # the config+lineage suffice.
    }
```

**Per PR7**: every numeric/string constant in `compute()` is either a mathematical truth or read via `config.convention_value(...)`. No `_Z_SCORE_WINDOW_DAYS = 252` at module level.

**Per PR11** (if applicable): if `<Tool>Input.method` includes values not implemented in V1, raise `NotImplementedError("<method> is not yet supported; see methodology.planned_extensions")` immediately on compute entry for those values.

**Per PR9** (if applicable): if this primitive composes another (calls `another_primitive.compute(...)` at runtime), pass the upstream config explicitly with the conventions surfaced in this primitive's input — do not rely on the upstream's auto-load.

## Step 6 — Draft `__init__.py`

```python
from .compute import CONFIG_PATH, calculate_<tool_name>
from .schemas import (
    <Tool>Input,
    <Tool>CurrentMetrics,
    <Tool>TimeSeriesRow,
    <Tool>Output,
)

__all__ = [
    "CONFIG_PATH",
    "calculate_<tool_name>",
    "<Tool>Input",
    "<Tool>CurrentMetrics",
    "<Tool>TimeSeriesRow",
    "<Tool>Output",
]
```

Stable re-exports so the rest of the repo can `from <agent>.<sub_agent>.tools.<tool_name> import calculate_<tool_name>` without reaching into submodules.

## Step 7 — Wire downstream integration points

A primitive in isolation does nothing. Confirm at least the following touchpoints land in the same PR (or are explicitly tracked as prerequisites):

| Touchpoint | What goes in |
|---|---|
| `<agent>/<sub_agent>/mcp_server.py` | MCP tool registration wrapping `calculate_<tool_name>`. The wrapper loads the bundled config explicitly (PR7), passes `config=` explicitly to `compute()`, converts internal exceptions to the MCP error envelope shape per the transport-boundary contract (P6's transport layer). |
| `<agent>/<sub_agent>/tools/schemas/__init__.py` | Re-export `<Tool>Input` / `<Tool>Output` if the schemas sub-package is used. |
| `<agent>/workflows/__init__.py` | If this primitive should be available to the workflow template layer, register the `PrimitiveSpec` with the correct `output_field_units`. |
| The relevant manifest YAML in `manifesto/03_tool_manifest/<agent>/` | A new entry with `name`, `domain`, `sub_agent`, `bucket`, `category`, `status: built`, `implementation:` block, `one_liner`, `bucket_rationale`, `pm_overridable`, `related_tools`, `workflows`, `references`, `built_date`. |
| `tests/conftest.py` | If the SQL validation runner follows the non-pytest-collection pattern, conftest may need to know about the new file. |

If any of these is skipped, the primitive may compile but it will not be reachable from the agent's MCP server, the workflow registry, or the Library page UI — and the catalog will silently lose it.

## Step 8 — Run the test triplet (PR16)

Three test files, each exercising a different layer:

```
tests/test_<tool_name>_compute.py
tests/test_<tool_name>_wiring.py
tests/test_<tool_name>_sql_validation.py
```

### `test_<tool_name>_compute.py`

- Bundled config loads cleanly via `load_tool_config(CONFIG_PATH)`.
- All declared conventions are present and have non-empty `source` and `rationale`.
- Synthetic input produces the expected synthetic output.
- Explicit-config-vs-auto-load parity: calling with `config=load_tool_config(CONFIG_PATH)` returns the same output as calling with `config=None`.
- Convention overrides change behaviour deterministically: bump `z_score_window_days` in a custom `ToolConfig` and confirm the z-score output changes.
- If applicable: each placeholder guard (`NotImplementedError` for pinned structural choices) fires correctly.

### `test_<tool_name>_wiring.py`

- `field_name=None` resolves to the YAML default; `field_name=""` is treated identically (the empty-string sentinel pattern); `field_name="EXPLICIT"` is honored.
- Controlled error envelopes preserve the expected shape at the MCP boundary.
- Cross-field invariants in Pydantic fire before `compute()` runs (e.g., `short_tenor == long_tenor` raises a clear validation error).

### `test_<tool_name>_sql_validation.py`

- Connects to the real DB via the container/dev stack with **read-only `SELECT` queries only**.
- Independently reproduces the core math against real data; this is not a smoke test, it is a parity check.
- Asserts the Python primitive's output matches the SQL baseline within the tolerance declared in `TOLERANCE_BY_FIELD` (rounding and floating-point noise only).

Per PR16: **all three layers are required before merge.** Offline tests alone do not satisfy the contract.

## Step 9 — Run CI lint (PR13)

```bash
python -m shared.config.lint
```

The lint must exit clean. If it flags a divergence (your new tool's `z_score_window_days` differs from the catalog's standard value of 252), the right action is to align — either by using the catalog standard, or by changing the catalog (which is a much larger PR and requires explicit rationale).

## Step 10 — Capture the parity fixture (PR15)

Run the parity-capture script against the live DB. The script captures:

- The raw rows that flowed into `compute()` for known inputs.
- The frozen `as_of_date` used at capture time.
- The full expected output as a JSON.
- A `capture` provenance block (captured_at, database_name, as_of_date, raw_rows_count, raw_rows_sha256).

The fixture lives at `tests/fixtures/<tool_name>_v1/`. The parity test (typically inside `test_<tool_name>_compute.py` or as a sibling `test_<tool_name>_parity.py`) replays the captured rows through a mocked fetcher, verifies the raw-rows SHA256 matches (tamper detection), and asserts byte-equal output within a `1e-9` absolute tolerance.

**Per PR15**: any subsequent change to a `conventions:` value requires this fixture to be regenerated *in the same PR* as the convention change, with rationale in the PR description.

## Step 11 — Verify the LLM tool-selection clarity (PR4)

Before declaring the primitive done, sanity-check the routing:

- Read your new primitive's `tool.name` and `one_liner` (in the manifest YAML entry).
- Read each related primitive's `tool.name` and `one_liner`.
- Could the LLM router confuse the two? Could a desk user reading both descriptions plausibly invoke the wrong one?

If yes, either tighten the new primitive's `one_liner` to disambiguate, or reconsider whether it should exist (PR4 + PR5).

## Automation scope — what the build-bot is and is not allowed to do

The platform ships an automated coding agent (a Claude builder + Codex reviewer loop) that can build primitives end-to-end without human implementation. Its scope is deliberately narrow. **The bot is only authorised to build primitives that match all of the following criteria.** A new-primitive PR that satisfies these can be assigned to the bot; one that does not requires human implementation.

**The bot is in scope for:**

- **Bucket 1A primitives** — strict deterministic arithmetic on stored market data, no model fitting, no user-facing model state. (1B fit-based primitives like PCA, rolling-regression, half-life are *out* of scope.)
- **Single-archetype level / spread shapes** — primitives whose output is a snapshot + `TimeSeries` (the Archetype A and Archetype B patterns named in the worked examples). Panel-producing primitives (Archetype E), categorical primitives (Archetype F), and composition primitives (Archetype D) are *out* of scope.
- **In-domain primitives** — primitives that read data from the same sub-agent they live under. Cross-domain primitives like `swap_spread` are *out* of scope.
- **Single-method primitives** — no `method: Literal[...]` enum dispatch with NotImplementedError surfaces. Multi-method primitives like `financing_rate` are *out* of scope.
- **Primitives with no missing-data blocker** — every required metadata field must already be available in an existing playbook. The bot will defer (refuse to build) rather than ship under a proxy.
- **Primitives whose `tool.category` is `desk_invariant_primitive`** — the bot does not handle `quant_standard_analytic` primitives, which require a methodology preface that the bot is not equipped to write.

**The bot is out of scope for** (these require human implementation):

- Composition primitives (PR9 considerations).
- Statistical-fit primitives (Bucket 1B — model state, central knob = model spec).
- Cross-domain primitives (PR3 cross-domain variant).
- Multi-method primitives (PR11 enum dispatch).
- Panel-producing primitives (different output shape, different downstream consumption).
- Categorical-output primitives (different output shape, not bridge-composable).
- Any primitive that requires new playbook data to be ingested first (PR6 — the bot defers on data-blocker).
- Any primitive that requires `quant_standard_analytic` methodology preface.

**Scope-expansion path.** The bot's templates can be extended to cover additional archetypes over time. Each extension is a separate PR that (a) adds template scaffolding for the new archetype, (b) demonstrates the template on a real new-primitive build, (c) updates this "Automation scope" section to reflect the new coverage. Expanding the bot's scope is a deliberate, documented step — not an automatic one.

**Operational pointer.** The bot's full doctrine, prompts, and runtime catalog live at `tmp/automation/primitive_automation/` in the repo (during the docs-revamp migration window). When the docs-revamp lands, those operational files will move to a permanent location — this section will be updated when that happens.

## Common pitfalls

Things reviewers see repeatedly:

- **Methodology constants in `compute.py`.** `_Z_WINDOW = 252` at module level. PR7 violation. Move to YAML.
- **A second methodology knob in `<Tool>Input`.** PR8 violation. Move to YAML, or split into two primitives if both knobs are genuinely central.
- **Composition primitive whose config is silent on upstream methodology.** PR9 violation. Surface every upstream convention, or pin with rationale.
- **Output that returns a number with no methodology trail.** PR10 violation. Add a provenance block.
- **`Convention.source: "default"`.** PR12 violation. Use a registered tag.
- **Skipping the SQL validation test.** PR16 violation. Offline tests alone are insufficient.
- **No parity fixture.** PR15 violation.
- **A `tool.name` that overlaps an existing primitive's `one_liner`.** PR4 violation (LLM-routing overlap).
- **A primitive that imports from another agent's package.** P11 + PR3 violation. Use `shared/analytics/` helpers instead.
- **An unbuilt method that silently returns a default rather than raising `NotImplementedError`.** PR11 violation.
- **A new primitive built when an existing one could be extended.** PR4 + PR5 violation.
- **Manifest YAML entry forgotten.** The primitive ships but doesn't appear in the Library page UI; the catalog silently loses it.

## PR review checklist

The reviewer signs off when each item is met. Cite the matching PR-number; do not paraphrase (AC2).

- [ ] **Pre-flight check** answered explicitly in the PR description (PR4, PR5, PR6, PR3, PR2, PR8, PR7–PR11 each addressed).
- [ ] **PR1.** `tool.name` is concept-named, not instrument-named. `<Tool>Input` exposes instrument selectors; no hardcoded instrument behaviour in `compute()`.
- [ ] **PR2.** The `methodology.what_it_does` describes one concept; no `method` parameter switches between different concepts.
- [ ] **PR3.** The primitive lives in the sub-agent that conventionally owns its concept. `grep` for `from <other_agent>` inside `compute.py` is empty.
- [ ] **PR4.** Parsimony argument explicit in PR description (the composition that was considered + why this primitive is materially better against ≥1 of the five criteria). LLM-routing overlap check done.
- [ ] **PR5.** Genuinely a new concept, not a coverage extension of an existing primitive.
- [ ] **PR6.** Every required metadata field is in the substrate already, or lands in the same PR. No proxies.
- [ ] **PR7.** Every methodology default is in `config.yaml`. No methodology constants in `compute.py`. Cross-field invariants in Pydantic, not YAML.
- [ ] **PR8.** Exactly one central knob in `<Tool>Input` (beyond instrument selectors).
- [ ] **PR9** *(if composition primitive)*. Every upstream methodology choice is surfaced or pinned with rationale in this primitive's config. Output provenance echoes upstream methodology.
- [ ] **PR10.** Output includes a methodology / provenance block surfaced in output fields.
- [ ] **PR11** *(if multi-method)*. Unbuilt methods raise `NotImplementedError` with `methodology.planned_extensions` reference. Pydantic validator enforces method-specific parameter requirements.
- [ ] **PR12.** Every `Convention.source` value is a registered tag. No vague tags.
- [ ] **PR13.** `python -m shared.config.lint` exits clean.
- [ ] **PR14.** Methodology-encoded field names guarded by `NotImplementedError` on convention changes.
- [ ] **PR15.** Parity fixture in `tests/fixtures/<tool_name>_v1/`. Reviewer can see the captured `as_of_date`, raw rows count, and expected output.
- [ ] **PR16.** All three test files present (`compute`, `wiring`, `sql_validation`). SQL validation uses only `SELECT` queries and independently reproduces the core math.
- [ ] **Manifest YAML entry** added with all required fields.
- [ ] **MCP server wiring** registers the tool.
- [ ] **Workflow registry** updated if the primitive should be reachable from templates.
- [ ] **AC6.** Commit message ends with the `Operationalises:` trailer listing the relevant IDs — e.g., `Operationalises: P1, P3, P5, P11; PR1, PR2, PR4, PR7, PR8, PR16; AC1, AC3, AC5, AC6.`

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.1 | 2026-05-17 | Pre-canonical factual corrections aligned with the README v1.1 revisions: (a) **Step 2 naming** — replaced the single `calculate_<tool>` template with the actual verb diversity used across the catalog (`calculate_`, `get_`, `build_`, `compute_`, `classify_`); clarified that folder name is the *concept slug* and the MCP tool name is `<verb>_<concept>_tool` chosen for natural readability, not mechanically derived. (b) **Step 5 compute template** — verb is now templated (`<verb>_<tool_name>`); provenance block in output marked as required for composition / pasted-input / statistical-fit / non-obvious-methodology cases per the revised PR10, optional for simple Bucket 1A primitives. (c) **Automation scope** — new section added that names exactly which primitives the build-bot is in scope for (Bucket 1A, level / spread shapes, in-domain, single-method, no missing-data blockers, `desk_invariant_primitive`) and which require human implementation (composition, statistical-fit, cross-domain, multi-method, panel-producing, categorical, data-blocked, `quant_standard_analytic`). Documents the scope-expansion path. | (pending) |
| v1 | 2026-05-17 | Initial runbook for adding a new primitive. Replaced by v1.1 the same day after a factual-review pass aligned with the README's corrections. | — |
