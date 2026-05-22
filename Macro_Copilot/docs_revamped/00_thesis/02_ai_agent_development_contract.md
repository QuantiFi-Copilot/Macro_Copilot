# AI Agent Development Contract

> The operating contract every AI agent (Claude Code, Codex, or any other) follows when writing or modifying code in this repository. This is the agent-side standardisation layer.

**Version:** v1.1
**Last reviewed:** 2026-05-16
**Audience:** AI agents writing or modifying code (primary). Humans collaborating with such agents (secondary).
**Status:** load-bearing. Changes require an ADR in [`../05_decisions/`](../05_decisions/).

---

## What this file is

This is the per-task operating procedure that turns the platform's principles ([`01_non_negotiables.md`](01_non_negotiables.md)) into action for an AI agent. The principles state *what* the rules are; this file states *how to consume the rules when writing code*.

The contract has eight binding rules (AC1–AC8). Each rule is short, falsifiable, and citable by ID. The same citation discipline that applies to the P-numbers applies to AC-numbers: cite by ID in commit messages and PR comments; never paraphrase the rule.

This file is the destination of the planned CLAUDE.md hook (see [`01_non_negotiables.md`](01_non_negotiables.md), "How these principles bind"). Until that hook is active, agents are routed here by explicit prompt or by the contributing human reminding the agent at session start.

## What this contract is NOT

This contract is a **floor**, not a ceiling. It marks the discipline below which work is not acceptable; it does not enumerate every action an agent should take and is not a substitute for judgment.

Two interpretive defaults that override any apparent over-reach in the rules below:

1. **When it is unclear whether a rule binds in a specific situation:** decide, act, and surface the decision in the PR description. Do not ask for permission on judgment calls — that is exactly the productivity tax this contract is designed to avoid.
2. **When it is unclear whether an AC8 escalation trigger applies:** ask. The AC8 triggers are the small set of decisions worth interrupting a human for; everything outside that set, decide.

The rules are not ceremony. If running the full checklist on a one-line typo fix feels excessive, it is — that work is *trivial* per the *Proportional rigor* section below, and the contract scales accordingly. The contract binds where the cost of getting it wrong is high (new components, methodology changes, closed-family extensions, citation discipline) and relaxes where the cost is low (typos, formatting, comment updates, small fixes inside an established pattern).

## Why this contract exists

AI agents have three specific failure modes that human contributors do not have to the same degree:

1. **Context loss across long tasks.** An agent that read the contract twenty turns ago may have forgotten the rule by the time it writes the relevant line. This contract is designed to be re-readable in two minutes at any point in a task.
2. **Plausible-but-invented patterns.** When a canonical pattern is not immediately visible in code, agents invent one that "looks right." The cost is silent inconsistency — every invented pattern dilutes the standardisation that lets the platform scale.
3. **Tone and shortcut drift.** Agents default to marketing language, defensive `try/except`, "let me also add…" surplus changes, and quick fixes when explicit pressure is not stated each turn.

Each AC rule below addresses one of these failure modes specifically. The rules are not preferences; they are non-optional, and any deviation is reportable in PR review.

## How to use this contract

- **At session start (cold):** read this file once, plus the items under *Pre-session reading*. ~5 minutes.
- **Before each task:** classify the change against *Proportional rigor* below, then read the items in *Per-task reading* that match.
- **During work:** when uncertain about a rule, search for the AC-number that applies; do not rely on memory of the spirit of the rule.
- **Before declaring done:** run the *Self-check before declaring done* checklist at the depth your change class requires.
- **When an AC8 trigger applies:** ask. Otherwise decide and surface in the PR.

## Proportional rigor

The contract scales to the change. Three classes:

| Class | What it looks like | Rules that apply with full force | Rules that scale down |
|---|---|---|---|
| **Trivial** | Typo, formatting, comment update, doc tweak, single-line fix that does not change behaviour, renaming a local variable, fixing a flaky test setup. | AC2 (cite when you cite), AC4 (graphify when navigating), AC7 (do not invent patterns) | AC1 no per-task reading required (the change does not depend on a contract); AC5 one-line sanity check is enough; AC6 trailer optional; AC8 triggers rarely apply |
| **Standard** | Modifying an existing component's method body, adding a test, adding an optional field to an existing schema, small refactor inside an established pattern, fixing a behavioural bug in a primitive / operator / template that does not change a methodology default. | All ACs, in full | Per-task reading limited to the touched component's contract + the relevant principle(s); self-check runs the items that apply to the change (not all 10); AC6 trailer mandatory |
| **Load-bearing** | New primitive, new operator, new workflow template, new playbook, new domain agent, methodology default change on a shipped tool, any closed-family extension (new artifact type / archetype / source-tag), changes to any file in [`00_thesis/`](.) or `01_architecture/` or `02_components/*/contract.md`. | All ACs, in full, with no scaling down | None |

A few practical implications:

- **Do not auto-promote.** If a change starts as trivial and grows during the work, re-classify and re-read. Do not finish a load-bearing change under trivial rules because that is how the work started.
- **When in doubt about the class, default to *Standard*.** Standard is the right middle for any non-obvious case; load-bearing reserves itself for the genuinely high-stakes changes named above.
- **Trivial does not mean "skip thinking."** It means the contract's reading and checklist overhead is not adding signal for this change. The principles (P1–P12) still apply to every line written; the *ceremony around verifying them* is what scales.

## Pre-session reading (cold start)

In this order:

1. [`../../CLAUDE.md`](../../CLAUDE.md) — repository-wide agent instructions (currently graphify; CLAUDE.md hook for this file is planned).
2. [`../README.md`](../README.md) — docs tree map and the contribution rules.
3. [`00_what_we_build.md`](00_what_we_build.md) — the product thesis. Read for orientation; do not re-read per task.
4. [`01_non_negotiables.md`](01_non_negotiables.md) — Quick Index plus full text on first read. Re-read the Quick Index only on subsequent sessions.
5. **This file** — the AC rules and the self-check.
6. `graphify-out/GRAPH_REPORT.md` — the codebase navigation map. Built by the `/graphify` command; agents consult it before any grep or open-file exploration.

Per [`../../CLAUDE.md`](../../CLAUDE.md), the graphify graph is the agent's primary map of the codebase. Use it for *"where is X defined"* and *"what depends on Y"* questions before falling back to file exploration.

## Per-task reading (before any code change)

The reading list depends on the task type. Per-component files (both `README.md` for the contract and `runbook.md` for the procedure + PR review checklist) live under [`../02_components/`](../02_components/); cross-cutting rules live under [`../03_standards/`](../03_standards/). Many of these are still forthcoming for components other than `playbook` and `primitive`. If the relevant contract has not been drafted, see **AC3**.

| Task type | Class | Read before writing any code |
|---|---|---|
| Typo / formatting / comment / single-line fix that does not change behaviour | Trivial | Nothing required. Cite the file in the commit; no trailer required. |
| Add a test for an existing component | Standard | The component's `README.md` (contract) and the relevant principle (typically P2 + P6) |
| Modify an existing primitive's method body without changing a methodology default | Standard | `02_components/primitive/README.md`, plus the principle the change addresses |
| Modify an existing operator's method body | Standard | `02_components/operator/README.md` (forthcoming), plus the principle the change addresses |
| Add an optional field to an existing schema | Standard | The component's `README.md` + `03_standards/error_handling.md` (forthcoming) |
| Bug fix in an existing primitive or operator (no methodology change) | Standard | The component's `README.md`, plus the principle the bug violates (typical: P2 + P3 + P5 + P6) |
| Add a new primitive | Load-bearing | `02_components/primitive/README.md` (contract) + `02_components/primitive/runbook.md` (procedure + PR review checklist) + `03_standards/test_patterns.md` (forthcoming) |
| Add a new operator | Load-bearing | `02_components/operator/README.md` + `02_components/operator/runbook.md` + `03_standards/file_and_folder_layout.md` — all forthcoming |
| Add a new workflow template | Load-bearing | `02_components/workflow_template/README.md` + `02_components/workflow_template/runbook.md` — both forthcoming |
| Add a new playbook | Load-bearing | `02_components/playbook/README.md` + `02_components/playbook/runbook.md` + `01_architecture/01_l1_data_substrate.md` (forthcoming) |
| Modify a methodology default on a shipped primitive | Load-bearing | `03_standards/methodology_disclosure.md` (forthcoming) + the methodology-change section of the component's `runbook.md` + AC8 escalation before any code |
| Add or remove a closed-family member (artifact type, archetype, source-tag) | Load-bearing | The relevant component's `README.md` closed-family policy section + its `runbook.md` extension PR review checklist + `05_decisions/` (file an ADR first) |
| Add a new domain agent (e.g., FX) | Load-bearing | The cross-component agent-bootstrapping runbook (forthcoming; currently distributed across the per-component runbooks); plus all four component contracts |
| Documentation update inside `docs_revamped/` | Depends — Trivial for tweaks, Load-bearing for changes to any file in `00_thesis/` `01_architecture/` `02_components/*/contract.md` | [`../README.md`](../README.md) (contribution rules), this file, the file being changed |

## Quick index — the AC rules

| ID | Rule | One-line summary |
|---|---|---|
| **AC1** | Read before writing | Read the per-task list that matches the change class. Trivial requires no reading. |
| **AC2** | Cite, never paraphrase | Cite rule IDs (P or AC numbers); never restate the rule in your own words. |
| **AC3** | Follow the canonical contract, or precedent + flag, or stop | Contract exists → follow it. Missing but ≥3-instance precedent in code → follow the precedent and flag the gap. Neither → stop and surface. |
| **AC4** | Use Graphify for navigation | Graphify first, grep second. Update the graph after every code change. |
| **AC5** | Self-check against the gate before done | Run the items in the matching component's PR review checklist (in `02_components/<component>/runbook.md`) that apply to the change. Trivial = one-line sanity check; Load-bearing = full gate. |
| **AC6** | Cite operationalised principles in commits | Commit trailer: `Operationalises: P<N>; AC<X>.` Mandatory on Standard and Load-bearing commits; optional on Trivial. |
| **AC7** | Refuse rather than invent | Missing pattern AND no precedent → refuse with reason. Do not invent precedent-setting patterns. |
| **AC8** | Ask only on the named triggers; otherwise decide | When a trigger applies, ask. When no trigger applies, decide and surface the decision in the PR — do not ask for permission on judgment calls. |

---

## The binding rules

Each rule follows the same skeleton: **Rule** (the imperative), **Why** (the reason it exists), **Specifically** (concrete actions and edge cases), **Failure mode** (what breaks if you skip it).

### AC1 — Read before writing

**Rule.** Before touching any code, read the matching items in *Per-task reading*. Do not skip the contract because the task "looks familiar."

**Why.** Inventing a pattern when one is documented is the single largest source of inconsistency. The contract exists so the agent does not have to re-derive the shape from code (which produces near-misses) or from prior sessions (which produces drift).

**Specifically.** If you cannot identify which contract applies, this is AC3 territory: stop and surface the gap rather than guessing. *"Looks familiar"* is not a substitute for reading.

**Failure mode.** A primitive added with three files instead of four; an operator with a domain-specific import; a workflow with bespoke loader logic. All three are P3 violations — and all three are prevented by AC1.

### AC2 — Cite, never paraphrase

**Rule.** When a rule applies, cite its ID (`P3`, `AC5`, …). Never restate the rule in your own words.

**Why.** Paraphrased rules drift; cited rules stay stable. Single source of truth (P10) is enforced through citation discipline. An audit trail of P-numbers in commit history and PR comments is dramatically more durable than the same content paraphrased.

**Specifically.** In commit messages, PR descriptions, code comments (rare), and refusal messages: cite by ID. Quoting the rule's verbatim text is fine; paraphrasing it is not. *"Per P3"* beats *"to keep this consistent with how other primitives are shaped"* in every context.

**Failure mode.** A commit message that says *"adding more thorough error handling"* instead of *"converts to typed `AlignSeriesError` per P6"*. The first decays into noise within a quarter; the second is forever auditable.

### AC3 — Follow the canonical contract, or follow precedent and flag, or stop

**Rule.** Three-tier fallback, in order:

1. **A canonical contract exists** (in [`../02_components/`](../02_components/)) → follow it strictly.
2. **No contract exists, but ≥3 existing instances in the codebase share a clear pattern** → follow that pattern, and **flag the gap in the PR description** (one sentence: *"this follows the precedent set by curve_spread, butterfly, pca_yield_curve; contract is not yet drafted at 02_components/primitive/contract.md"*). Propose drafting the contract as a follow-on.
3. **No contract AND no consistent precedent** → stop and surface. Do not invent.

**Why.** Inventing precedent silently is the most expensive kind of mistake — the next agent copies the invention. The cost of one invented pattern is multiplied across every future task that consults the precedent. *Following* existing precedent, by contrast, is a feature: it keeps the codebase consistent (P3) without waiting for the contract to be written.

**Specifically.**
- The "≥3 instances" threshold is meaningful — two could be coincidence, three is a pattern.
- "Flag the gap in the PR description" is the load-bearing piece. The human sees that a contract is needed and can choose to draft it, defer it, or accept the precedent as the de-facto contract.
- For tier 3 (no contract, no precedent), the refusal format is the same as AC7's refusal format: *"I cannot complete this task because [X]. The right next step is [draft the contract / file an ADR / consult the human about Y]."*
- *"Looks similar"* is not precedent. Precedent means three or more instances doing the same thing in the same way.

**Failure mode.** An agent invents a new shape because no contract exists, ships it, and the next five tasks in the same area inherit the invention. By the time the contract is drafted, four files need refactoring to align — and the human reviewing the contract has to choose between the invented pattern and the right one.

### AC4 — Use Graphify for navigation, not raw file traversal

**Rule.** For *"where is X defined"*, *"what depends on Y"*, *"what is the shape of Z"* questions, consult `graphify-out/GRAPH_REPORT.md` and the `/graphify query`, `/graphify path`, `/graphify explain` commands **before** resorting to grep or open-file exploration.

**Why.** Per [`../../CLAUDE.md`](../../CLAUDE.md), the graph is the codebase's primary navigation surface. It surfaces EXTRACTED and INFERRED edges that grep cannot see, and answers cross-module questions in one query that would otherwise cost 10–20 file reads.

**Specifically.** See the *Graphify usage* section below for the concrete command set. After every code-modifying task, run `graphify update .` (AST-only, no API cost) to keep the graph current.

**Failure mode.** Opening fifteen files via grep to answer a question the graph could have answered in one query, blowing the agent's context budget on navigation rather than thinking — and missing the cross-module relationships the graph would have surfaced.

### AC5 — Self-check against the gate before declaring done, proportional to change class

**Rule.** Before saying *"this task is complete"*, run the self-check at the depth your change class requires. Trivial = one-line sanity check. Standard = items in the matching component's PR review checklist (in `02_components/<component>/runbook.md`) that apply to the change. Load-bearing = full gate, every item.

**Why.** Declaring done before checking is the most common cause of PRs that ship contract violations. Once the agent has declared done, the cost of catching the violation moves entirely to human review — exactly what the gate is designed to prevent. The proportional split exists so the self-check has signal at every change class instead of becoming ceremony on the small ones.

**Specifically.**
- **Trivial:** confirm the change does what it claims and breaks no test. One line in the PR / commit is enough: *"renamed local var; tests green; no behavioural change."* No gate file to consult.
- **Standard:** open the relevant gate file, run the items that apply to the change, skip the items that don't (e.g., methodology-disclosure items do not apply to a test-only PR). State which items you ran and the result.
- **Load-bearing:** every item, every time. State each item explicitly: met / not met / N/A with reason. *No skipping.*
- The matching gate is forthcoming for most component types; when it does not yet exist, the Standard / Load-bearing self-check falls back to citing the principle IDs the change touches and confirming each is satisfied.

**Failure mode.** A primitive PR ships with the file shape right but the methodology disclosure missing (P5 violation), or the typed-exception class missing (P6 violation), because the agent saw tests green and declared done. The human spends review time on what the gate would have caught for free.

### AC6 — Cite the principles you operationalised in the commit message

**Rule.** Standard and Load-bearing commits end with the IDs of the principles and AC rules the change operationalises. Trivial commits do not require the trailer. Format when used: `Operationalises: P3, P5; AC2, AC5.`

**Why.** Citation discipline (AC2) at commit time creates an audit trail that survives the conversation in which the work happened. Six months later, a reader of `git log` sees which principles each Standard / Load-bearing commit was answering. Trivial commits do not benefit from this — the diff is self-evident, and forcing a trailer on a typo fix produces noise that dilutes the signal on the commits where it matters.

**Specifically.** Commit message structure (Standard and Load-bearing):

```
<type>(<scope>): <summary, imperative mood, ≤60 chars>

<optional body — why, not what. The diff shows what.>

Operationalises: P3, P5; AC2, AC5.
```

`<type>` is one of `feat`, `fix`, `refactor`, `test`, `docs`, `chore`. `<scope>` is the component or path touched. The trailer is mandatory on Standard and Load-bearing commits; the body is optional.

Trivial commits use the same `<type>(<scope>): summary` header with no body and no trailer. *"fix(docs): correct typo in 01_non_negotiables P3"* is complete on its own.

**Failure mode.** A Standard or Load-bearing commit titled *"fix curve_spread"* with no body and no trailer — informative for the moment, opaque six months later when the audit asks *"why was this change made and which rule was being addressed?"* The fix is to write the trailer at commit time, not to retro-add it.

### AC7 — Refuse with reason rather than inventing patterns

**Rule.** When a canonical pattern, contract, or example does not exist for the work you have been asked to do, **refuse with a specific reason** that names the missing artefact. Do not invent.

**Why.** This is P6 applied at the agent layer. An agent that invents a pattern is the agent equivalent of a silent fallback — it teaches the next agent that the invented pattern is correct, and the cost compounds across every subsequent task in the same area.

**Specifically.** Refusal format:

> *"I cannot complete this task because [the contract for component type X does not yet exist at `02_components/X/contract.md`]. The right next step is [draft the contract first / file an ADR / consult the human about Y]."*

Refusal with a named missing artefact is always preferred to a "best-effort" implementation. The next agent has to be able to see *what was missing*, not just *that something was assumed*.

**Failure mode.** A pattern almost-fits the codebase, ships in a PR, gets approved on the green tests, and becomes the precedent for the next five components in the same area. Undoing it touches six files instead of one.

### AC8 — Ask only on the named triggers; otherwise decide and surface in the PR

**Rule.** When one of the named triggers below applies, ask the human before proceeding. When no trigger applies, decide based on the principles and the contract, then surface the decision in the PR description. **Do not ask for permission on judgment calls.**

**Why.** Asking is expensive. Every interruption costs the human attention, and a contract that defaults to asking trains the agent to ping for everything — which is the productivity tax this contract is designed to prevent. The triggers below are the small set of decisions that are *genuinely* worth interrupting for: high cost-of-undo, or already gate-protected by a principle. Outside that set, the agent is expected to exercise judgment and document it.

**Specifically — the triggers.** Ask if any of these apply:

1. The task implies adding a new closed-family member (artifact type, archetype, methodology source-tag). Per P8 this needs an ADR before any code lands.
2. The task involves changing a methodology default already shipped in a tool's `config.yaml`. Per P5 plus the methodology-change gate, this needs explicit sign-off.
3. The task could be done as a primitive *or* an operator and the choice changes cross-asset portability (P9). Note: if the choice is obvious from the codebase precedent, this is not a real trigger — decide and proceed.
4. The task crosses domain agents and the orchestration pattern is not documented (P11).
5. A principle (P-number) appears to conflict with another principle in this specific task.
6. The user's request, as stated, appears to violate a principle.

**When the trigger does not apply (the common case).** Make the call, write the code, surface the decision in the PR description in one or two sentences:

> *"Chose to put `compute_breakeven` as a sovereign-domain primitive rather than a shared analytics function because both legs are sovereign instruments (per existing precedent: swap_spread is OIS-owned because OIS conventionally owns the asset-swap concept). Open to revisiting if the cross-domain breakeven case becomes real."*

That sentence is the audit trail. The PR reviewer sees the call and the reasoning; they can accept or push back. No round-trip required.

**Failure mode (in either direction).**
- *Under-asking:* an agent silently extends a closed family or changes a methodology default; the PR ships and the gap is caught weeks later in production output.
- *Over-asking:* an agent pings the human on every architectural judgment call ("should I name this `EventSet` or `EventCollection`?") — the human stops engaging, the contract becomes noise, the agent gets less useful direction over time.

The bias is toward making the right call, not toward asking.

---

## Self-check before declaring done

Run the items that apply to your change class. Each item is tagged with the minimum class at which it activates: **[all]** = run for every change including Trivial; **[standard+]** = Standard and Load-bearing; **[load-bearing]** = Load-bearing only.

- [ ] **[all] Does the change do what it claims and break no test?** The minimum bar for any commit.
- [ ] **[all] AC2.** Did I cite rule IDs (P / AC numbers) instead of paraphrasing in commit message and PR description?
- [ ] **[standard+] AC1.** Did I read the matching items in *Per-task reading* for the change class before writing?
- [ ] **[standard+] AC3.** For the component I touched: does a canonical contract exist? If yes, did I follow it? If no, did I follow ≥3-instance precedent and flag the gap? If neither, did I stop and surface (rather than invent)?
- [ ] **[standard+] P-checks.** For each principle the change touches: is the change compliant? Cite by ID, not by paraphrase.
- [ ] **[standard+] Tests reflect the contract** (P2). Tests assert what the *contract* requires, not what the implementation currently produces. Failure cases are tested (P6). Tolerance is from the contract, not chosen post-hoc.
- [ ] **[standard+] No invented patterns.** Every file shape, name, and contract field matches the canonical contract or the precedent — not "almost," exactly.
- [ ] **[standard+] AC6.** Commit message ends with the `Operationalises: …` trailer listing the relevant IDs.
- [ ] **[load-bearing] AC5.** Did I run through the matching PR review checklist in the component's `runbook.md`, item by item, with no skipping?
- [ ] **[load-bearing] Methodology disclosure** (P5). Methodology cards updated for any convention change. Every `Convention.source` tag is non-empty and from the taxonomy.
- [ ] **[load-bearing] Determinism** (P4). Hash-stability test still passes. No wall-clock time, no unfixed random seeds, no `dict` order assumed in serialisation.
- [ ] **[load-bearing] Single source of truth** (P10). I did not duplicate a fact across files. If I needed the same fact in two places, one is the source and the other links.
- [ ] **[load-bearing] AC8 triggers.** Did I check whether any of the six escalation triggers applied? If yes, did I ask? If no, did I document the decision in the PR description?

If any item is unclear, return to the matching read, then re-run the checklist at the right depth. Do not declare done with an unchecked item that applies to your change class.

## When to stop and ask the human

Always escalate, never decide silently, when:

1. **A closed-family extension is implied.** New artifact type, new archetype, new source-tag → ADR first (P8 + AC8).
2. **A methodology default would change for a shipped primitive.** Even a "small" tweak to `z_score_window_days` is a methodology change under P5 — the gate requires sign-off.
3. **A principle appears to conflict with another.** Two P-rules in tension on the same task → the human picks which dominates, with reasoning recorded.
4. **The user's request appears to violate a P-rule.** Restate the relevant principle in your own response, cite the ID, and ask for explicit confirmation rather than complying silently.
5. **The canonical contract for the work does not exist yet** (AC3 + AC7). Stop; surface the gap; do not best-guess.
6. **The task crosses agent boundaries** (P11) and the orchestration pattern is not yet documented.
7. **A test reveals an inconsistency between code and contract.** The contract wins by default (P10), but the resolution may require an ADR (if the contract is wrong) or a code fix (if the implementation is wrong).

Bias toward asking. The cost of an unnecessary question is one short exchange; the cost of an undone decision is a multi-file refactor.

## Anti-patterns specific to AI agents

Auto-reject in PR review when seen:

- **Marketing language** in code, docs, or commit messages: *"robust"*, *"powerful"*, *"elegant"*, *"intuitive"*, *"comprehensive"*. Remove. State the technical fact instead.
- **Emojis** in any artifact (commit, code, docs) unless the user explicitly asked for one.
- **Pleasantries** in code comments or doc prose. *"Let me also add…"* / *"For your convenience…"* — delete; state directly.
- **"Helpful" surplus changes** during feature work. *"I noticed this could be cleaner, so I also refactored…"* — no. The task is the task. Surface the noticed issue separately, do not bundle.
- **Invented base classes and abstractions.** *"I created a `BasePrimitive` to reduce duplication"* — unless the canonical contract specifies inheritance, do not introduce it.
- **Defensive `try/except`** when the contract specifies typed exceptions. P6 violation.
- **TODO comments without a clear scope or owner.** A TODO is acceptable if it states *what* must be done, *when* (e.g., before a named PR), or *who* picks it up. A bare `# TODO: improve this` is a P1 violation; a `# TODO: add SCD2 history before futures tools ship — see tech_debt_register.md #11` is fine.
- **Paraphrased principles.** *"We want this to be reproducible"* instead of *"per P4"*. AC2 violation.
- **New files outside the canonical shape inside an established component folder.** `helpers.py`, `utils.py`, `service.py` inside a primitive or operator folder when the four-file (or three-file) contract specifies otherwise — P3 violation. New files elsewhere (a new module under `shared/analytics/`, a new test file, a new doc) are fine when they belong; this anti-pattern is about contract-shaped folders specifically.
- **Plausible-but-fake citations.** Citing a file path, function name, line number, or principle ID that does not exist. *Verify before citing.* P10 violation (the cited source is the single source of truth — it must exist).
- **Invented principle IDs.** Only humans create new P-numbers, and only via ADR. An agent that writes `P11.5` or `P11a` is wrong; the principle either exists or it does not.
- **Tests that mirror the implementation.** A test that asserts what the implementation currently produces, rather than what the contract requires it to produce, is a snapshot — not a test. P2 violation.
- **Trusting prior conversation context over the doc.** When in doubt, re-read the contract. The conversation is not authoritative; the doc is.
- **Declaring done without the self-check.** AC5 violation.
- **Speeding past AC8 triggers.** *"I'll just pick one"* on a load-bearing ambiguity. Ask instead.

## Graphify usage

Per [`../../CLAUDE.md`](../../CLAUDE.md): the graphify graph at `graphify-out/` is the codebase's primary navigation surface. This section is the operational reference for AC4.

**When to use graphify:**
- *"Where is X defined?"* → `/graphify explain X`
- *"What depends on X?"* → `/graphify query "what depends on X"`
- *"How does X relate to Y?"* → `/graphify path X Y` (or `/graphify query "how does X relate to Y"`)
- *"What is the architecture around X?"* → consult the relevant community in `graphify-out/GRAPH_REPORT.md`.
- Cross-module questions in general → graphify first, grep second.

**When to fall back to file reading:**
- The graph returns insufficient detail for the specific question.
- The change is local to a single file (no cross-module analysis needed).
- The change is to documentation (the graph indexes code primarily).

**After every code-modifying task:** run `graphify update .` (AST-only, no API cost) to keep the graph current for the next session.

**Do not use graphify for:**
- Questions about content the agent has already loaded into context this turn (just refer to what you read).
- Questions about files outside the repo (graphify only indexes what's in the corpus).

## Changing this contract

Same discipline as the principles and the thesis:

1. **Open an ADR** in [`../05_decisions/`](../05_decisions/) describing the proposed change and the agent-behaviour consequences.
2. **Land the ADR and the contract change in the same PR.**
3. **Bump the version** of this file. The version log records every change.

This contract is intentionally stable. AI-agent behaviour relies on the rules not shifting under it; if AC4 means one thing today and another thing next week, agents cannot internalise the rule and the contract becomes noise.

## Version log

| Version | Date | Change | ADR |
|---|---|---|---|
| v1.1 | 2026-05-16 | Balance pass — contract was too restrictive in three specific places and would have blocked or over-taxed routine work. Edits, in order: (a) added **"What this contract is NOT"** preamble (floor not ceiling; default to decide-and-surface, ask only on AC8 triggers). (b) Added **"Proportional rigor"** section defining three change classes (Trivial / Standard / Load-bearing) — the binding rules scale by class. (c) Restructured the per-task reading table to include Trivial and Standard-tweak rows so common changes are not gated on the full reading list. (d) **AC3** rewritten as a three-tier fallback (canonical contract → ≥3-instance code precedent + flag the gap → stop and surface) — unblocks routine work without inviting invented patterns. (e) **AC5** scaled by change class (Trivial = one-line sanity check; Standard = apply relevant gate items; Load-bearing = full gate). (f) **AC6** trailer mandatory only on Standard / Load-bearing commits; optional on Trivial. (g) **AC8** removed "bias toward asking"; replaced with "ask only when a trigger applies; otherwise decide and surface in the PR." (h) Self-check checklist tagged each item by minimum change class so agents stop running irrelevant items. (i) Tightened two anti-patterns (TODOs without *scope or owner*; new files inside *established component folders* only). Quick Index updated for AC3 / AC5 / AC6 / AC8 to match. | (pending — `0003-ai-agent-development-contract.md`) |
| v1 | 2026-05-16 | Initial AI-agent development contract. Replaced by v1.1 the same day after a balance review. | — |
