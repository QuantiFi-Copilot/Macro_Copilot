# Single Review Round Policy

> **This factory uses ONE review round per tool.** Build → review → optional one-shot fix → commit. **No re-review after the fix.** This document codifies the explicit user constraint that distinguishes the frontend factory from the primitive factory (which allows up to 4 rounds with adjudication).

**Version:** v1
**Last reviewed:** 2026-05-29
**Status:** load-bearing operational policy. Applies to every dispatch decision the orchestrator makes.

---

## 1. The pipeline

For each tool in the catalog, the orchestrator dispatches at most TWO and at most THREE workers:

```
DISPATCH 1: Builder (run_claude_builder.sh)
    │
    ├─ produces all 8 frontend artifact slots per BUILD_GUIDE.md Stages 4–6:
    │    module.ts, THESIS.md, surfaces/BuildExtended.tsx, surfaces/BuildCompact.tsx,
    │    surfaces/<tool_slug>Shared.ts, surfaces/monitor/<Widget>.tsx,
    │    mockups/{Compact,Extended}.png are already present (input),
    │    __tests__/module.spec.ts, plus loader entry, plus typed-detail route +
    │    service helper + frontend type if Stage 5 was not pre-built.
    │
    ▼
DISPATCH 2: Reviewer (run_claude_reviewer.sh)
    │
    ├─ produces exactly one heading:
    │    APPROVED              → run pre-commit gate, commit, advance to next tool
    │    CHANGES REQUIRED      → see Dispatch 3
    │    DEFER / DO NOT BUILD  → mark blocked, advance to next tool
    │
    ▼
DISPATCH 3 (OPTIONAL — fix pass): Builder again, fresh context, given the reviewer findings
    │
    ├─ applies the reviewer's findings verbatim
    │
    ├─ run pre-commit gate (`npm run test:modules` + `npm run test:build` + `npm run typecheck`)
    │     gate green → commit, advance to next tool
    │     gate red   → mark human_required (DO NOT loop back)
```

**Maximum dispatches per tool: 3.** Always.

There is no `Dispatch 4: re-review`. There is no `Dispatch 5: fix-the-fix`. Once Dispatch 3 (the fix) commits or surfaces a human-required failure, the tool's pipeline is complete.

## 2. Why one round (the explicit trade-off)

The primitive factory allows up to 4 review rounds because:
- Backend math has subtle invariants a one-round bot would miss
- Methodology honesty often requires iterative push-back
- DB-backed validation surfaces new issues the offline tests miss

The frontend factory accepts a tighter loop because:
- Frontend work is **bounded assembly** — TSX from a mockup against a fixed FM contract, not open-ended math
- The mockup PNGs are a visual source-of-truth the reviewer can compare against in one pass
- `BUILD_GUIDE.md` Stages 4–6 enumerate every required slot — there's a discrete checklist the reviewer scores against
- The pre-commit gate (test + build + typecheck) catches the class of regressions a re-review would catch anyway
- A multi-round loop would burn 2-4x the quota for marginal additional quality

**The accepted risk:** the fix-pass builder could introduce a NEW bug in the same file it just fixed. This is mitigated by:
1. The pre-commit gate refuses to commit if `npm run test:modules` / `npm run test:build` / `npm run typecheck` fail.
2. The fix is targeted at the reviewer's exact findings — not a freeform rewrite — so the surface area for new bugs is small.
3. Each tool's diff is small (~8 files in the module folder + 2-3 shared-helper updates). A reviewer-introduced regression is unlikely to escape the test suite.

If the user later observes the bot committing buggy fixes that escape the pre-commit gate, the policy escalates to per-tool re-review, but only after that pattern is observed empirically — not preemptively.

## 3. When `CHANGES REQUIRED` becomes `human_required` (no Dispatch 3)

The orchestrator MUST mark the tool `human_required` and skip Dispatch 3 when ANY of:

- The reviewer's findings are vague ("the design could be better", "consider refactoring") — the builder cannot act on them without speculation.
- The reviewer's findings are STRUCTURAL: the entire module doesn't match the mockup; the wrong tier set was chosen; the tool's typed-detail endpoint contract is wrong. These need a human design call, not a per-finding fix.
- The reviewer's findings reference data or contracts that don't exist (the reviewer hallucinated a doc).
- The reviewer's findings would require touching more than 4 files outside the module folder (large blast radius).
- The reviewer's findings contradict the committed mockup (the builder must not be told to override the mockup).
- The reviewer emits `CHANGES REQUIRED` but lists no concrete findings.

When the orchestrator marks `human_required` for any of the above, it writes a `human_required` block to `frontend_runtime_state.yaml` summarising the reviewer's findings + which dismissal criterion applied + the prompt files + log files. The next cron wake skips the tool until the human acts.

## 4. What goes into Dispatch 3's prompt

When the orchestrator dispatches the fix pass, it composes a NEW prompt that contains:

1. The full `CLAUDE_BUILDER_PROMPT.md` (re-read every time).
2. A `## Reviewer findings to apply` section containing the reviewer's `CHANGES REQUIRED` block verbatim (the orchestrator extracts only the actionable findings — no dismissable nitpicks).
3. A `## Apply these findings; do NOT redesign` instruction — the builder is told to make targeted edits, not rewrite the module.
4. The same backend-tool context the original builder dispatch had (catalog entry + mockup paths + reference module + pre-flight audit output).

The builder applies the findings and commits via the pre-commit gate. The orchestrator does NOT dispatch the reviewer again.

## 5. Pre-commit gate (the only gate after Dispatch 3)

Before any commit (APPROVED or APPROVED-AFTER-FIX), the orchestrator MUST run from the repo root:

```sh
cd UI/macro-copilot-dashboard-polished
npm run test:modules
npm run test:build
npm run typecheck
```

ALL THREE must exit 0. Any failure → DO NOT COMMIT.

For the APPROVED path: a gate failure is rare but signals the builder shipped a regression the reviewer missed; mark `human_required` with the failure log.

For the APPROVED-AFTER-FIX path: a gate failure means the fix introduced or failed to address a regression; mark `human_required` with the failure log.

The gate is the contract. No commit without it.

## 6. What the orchestrator writes per tool to runtime state

After every dispatch and every gate run, append-only updates to `frontend_runtime_state.yaml`:

```yaml
current_tool: <verb>_<tool_slug>_tool
current_step: builder_dispatched | reviewer_dispatched |
              fix_dispatched | gate_running | committed | blocked | human_required
dispatch_round: 1 | 2 | 3        # 1 = builder, 2 = reviewer, 3 = fix
last_dispatch_at: <iso>
last_reviewer_heading: APPROVED | CHANGES REQUIRED | DEFER / DO NOT BUILD | null
last_gate_result: pass | fail | not_run
last_stop_reason: ...
status_notes:
  - at: <iso>
    kind: <reviewer_changes_required | fix_applied | gate_failed | ...>
    text: |
      <full reviewer findings, full gate log excerpt, etc.>
```

The `dispatch_round` field is the audit trail that proves the cap (≤3) was respected.

## 7. Anti-patterns this policy forbids

- Dispatch 4 to re-review a fix.
- Dispatch 3 when the reviewer findings are vague or structural (mark `human_required` instead).
- Committing without the pre-commit gate.
- Dispatching the builder a fourth time with "try again" or "improve the previous attempt" — that's a multi-round loop.
- The orchestrator silently downgrading a `CHANGES REQUIRED` to `APPROVED` because Dispatch 3 was attempted (the heading on record is the LAST reviewer heading; a fix that commits is approved-after-fix, NOT approved).
- Adjudicating reviewer findings (the primitive factory's "mandatory-fix vs dismissable" sorting) — here the rule is simpler: actionable findings → fix; vague/structural findings → human.

## 8. Reviewer prompt expectations under this policy

`CLAUDE_REVIEWER_PROMPT.md` MUST instruct the reviewer to:

- Emit ONE heading on a line by itself: `APPROVED` / `CHANGES REQUIRED` / `DEFER / DO NOT BUILD`.
- Under `CHANGES REQUIRED`, list each finding as a numbered, actionable item with a specific file path + line range and an exact fix description ("change `X` to `Y` on `surfaces/BuildExtended.tsx:42`").
- AVOID vague findings ("could be cleaner", "consider refactoring"). The reviewer's prompt warns that vague findings will be classified as `human_required` and skip the fix dispatch.
- AVOID structural findings without flagging them as `STRUCTURAL:` (the orchestrator looks for that prefix to route to `human_required` immediately).
- AVOID re-litigating the mockup (the mockup IS the design; the reviewer enforces FM contract + dual-view + bridge contract conformance, NOT design opinions).

## 9. Builder prompt expectations under this policy

`CLAUDE_BUILDER_PROMPT.md` MUST instruct the builder to:

- On Dispatch 1: build the entire module from BUILD_GUIDE Stages 4–6 + the mockups.
- On Dispatch 3 (if invoked): apply ONLY the reviewer findings listed in the prompt's `Reviewer findings to apply` section. Do NOT redesign. Do NOT touch files outside the listed paths unless the finding explicitly requires it.

## 10. Links

- [`README.md`](README.md) §"Intended loop" — the 11-step orchestrator loop that embeds this policy in steps 6–9.
- [`ORCHESTRATOR_PROMPT.md`](ORCHESTRATOR_PROMPT.md) §"Reviewer result rule" + §"Fix pass rule" — the operational embodiment of this policy.
- [`CLAUDE_REVIEWER_PROMPT.md`](CLAUDE_REVIEWER_PROMPT.md) §"Finding hygiene" — the contract the reviewer must obey for findings to be Dispatch-3 eligible.
- [`CLAUDE_BUILDER_PROMPT.md`](CLAUDE_BUILDER_PROMPT.md) §"Fix pass mode" — the instruction set the builder reads when Dispatch 3 is invoked.

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-29 | Initial policy. Defines the 3-dispatch cap, the APPROVED / CHANGES REQUIRED → fix / DEFER trichotomy, the `human_required` escape hatches for vague/structural findings, and the pre-commit gate as the sole post-fix safety net. Codifies the explicit user constraint distinguishing this factory from the primitive factory. |
