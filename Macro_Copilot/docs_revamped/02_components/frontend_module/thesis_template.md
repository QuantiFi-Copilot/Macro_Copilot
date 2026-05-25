# Frontend Module — THESIS Template

> The required template every module's `THESIS.md` copies and fills in. The THESIS is a designed artefact, not a generic README — it forces UX decisions into a citable document.

**Version:** v1
**Last reviewed:** 2026-05-25
**Status:** procedural template. Update when [FM10](README.md#fm10--thesis-discipline) changes.
**See also:** [`README.md`](README.md) — module contract (FM10); [`runbook.md`](runbook.md) Step 2 — where THESIS is written.

---

## How to use this template

1. Copy the entire **Template** section below into `THESIS.md` in the module folder.
2. Replace placeholders (`<TOOL_NAME>`, `<one-sentence ...>`, etc.) with module-specific content.
3. Answer all five questions in order. Empty answers, "TODO", and "N/A" (except where explicitly allowed for `deferred` modules) are CI failures via the round-trip test.
4. THESIS is reviewed in the PR. The reviewer's job is to push back on shallow answers; the THESIS exists BECAUSE the design discipline needs a documented artefact, not just code.

---

## What each question is asking

### Question 1 — What surfaces does this module ship?

Enumerate every tier claimed in `MODULE.tiers`. For each, write one sentence describing the surface.

**Example answers (good):**
> *Generic-runnable Build canvas via `GenericPrimitiveBuilder`.*
> *Monitor bento card showing the most recent CPI release inline with the rolling z-score band.*
> *Ask result card placing the surprise + z-score inline with the release date, distinct from the generic AssistantResearchCard which would render this as a plain time-series KPI.*

**Anti-patterns:**
- Listing surfaces the module does NOT claim ("we might add a Monitor widget someday").
- Listing surfaces in different language than the tier names (use the tier names verbatim).

### Question 2 — What does the user read off each surface?

For each surface, write one paragraph naming the specific decisions a PM makes from it. Be concrete — *which numbers does the PM look at first, second, third?*

**Example answer (good) for an event-signal module's Monitor widget:**
> *The Monitor widget shows the latest CPI release for the chosen country: actual YoY, consensus YoY, surprise in percentage points, and the rolling 24-release z-score of the surprise. A PM glances at this in the morning: was last month's print a surprise relative to the desk's expectations? Is the surprise running consistently in one direction (a z-score band drift over the strip)? The widget is the trigger for a deeper look in Build when the surprise is more than 1.5σ from the rolling mean.*

**Anti-patterns:**
- Generic answers ("the user sees the result of the tool").
- Answers that describe layout instead of decisions.

### Question 3 — Why these surfaces and not others?

For each surface claimed, explain why the generic alternative is inadequate. For each surface NOT claimed (but plausibly relevant), explain why not.

**Example answer (good):**
> *Bespoke Ask card: the generic AssistantResearchCard renders this tool's result as a KPI strip + sparkline, which loses the per-release framing the PM expects (release date inline, surprise + z-score colour-coded against the rolling band). A bespoke card costs one component file; the generic surface would force the PM to open Build to see the release breakdown they could have seen in chat. Monitor widget: the desk reads CPI releases at a glance; this is the canonical Monitor pattern. Custom Build surface NOT claimed: the generic builder + AutoRenderer renders the time-series + per-release table adequately; a bespoke Build canvas would duplicate the Monitor + Ask cards.*

**Anti-patterns:**
- "Because it looks better." Looks-only justifications belong in the design system, not in module-level THESIS.
- Justifications that don't compare to the generic alternative.

### Question 4 — What would change the design?

List the specific shifts in user need or backend output that would force a tier-set change. Concrete triggers; not generic "if requirements change."

**Example answer (good):**
> *If the backend ships intraday CPI surprise data (per-release timestamp at the minute granularity), the Monitor widget would need a real-time refresh affordance and the Ask card would gain a 'time since release' field — both would require the module to claim a polling hook that today's daily-cadence design doesn't need. If the desk's CPI workflow shifts from per-country to cross-country (comparing US vs EUR vs UK CPI surprises in one view), a new `custom_build_surface` is needed for the cross-country layout; today the per-country generic builder is sufficient. If the backend changes the surprise convention (signed actual−consensus → unsigned distance from a rolling mean), every surface's colour-coding and z-score interpretation has to be re-thought — the THESIS Question 2 paragraph would have to be rewritten.*

**Anti-patterns:**
- "N/A" or "nothing would change the design" — every design has triggers; surfacing them is the point.
- Generic answers ("if the team decides to redesign").

### Question 5 — Which backend doctrine does this module operationalise?

Cite the relevant P-numbers (platform doctrine), PR-numbers (backend primitive doctrine), ADRs, TD entries, and any other backend-side documentation the module's surfaces depend on. The citation is what makes the module's UX claims defensible.

**Example answer (good):**
> *Operationalises P5 (honest disclosure — the surprise convention is surfaced explicitly on the card; nothing is computed client-side). Operationalises FP9 (no client-side compute — the rolling z-score is the backend's, not recomputed in the widget). Operationalises FP7 (honest tier disclosure — this module is `generic_runnable`; the runtime-status tier is the canonical fully-runnable state, not the workflow-incompatible / paused workaround). Depends on backend's PR10 (provenance reachability — the methodology card surfaces the consensus source, the lookback window, the z-score window). Depends on ADR 0008 (event-playbook contract — the actual/consensus/surprise/release_time triple comes from the playbook the backend declared). Depends on ADR 0012 (event-primitive placement — `cpi_surprise` lives in `rates_agent/inflation_swaps/tools/` per the placement rationale).*

**Anti-patterns:**
- Empty or vague ("backend doctrine"; "various principles"). Cite by ID.
- Citations to non-existent or misnumbered principles. Verify against [`../../00_thesis/01_non_negotiables.md`](../../00_thesis/01_non_negotiables.md), [`../primitive/README.md`](../primitive/README.md), [`../../00_thesis/03_frontend_thesis.md`](../../00_thesis/03_frontend_thesis.md), and the ADRs.

---

## Template

Copy everything below verbatim into `THESIS.md` and fill in.

```markdown
# THESIS — `<TOOL_NAME>`

> One-paragraph summary of what this module surfaces and why it exists. (NOT a copy of the backend `methodology.what_it_does`; this is the UX-side framing.)

**Version:** v1
**Last reviewed:** YYYY-MM-DD
**Module spec:** [`module.ts`](module.ts)
**Backend artifact:** `<tool_name>` in `_PRIMITIVE_SPECS` / `WORKFLOW_INCOMPATIBLE_TOOLS` / reserved.
**Tier set:** `[generic_runnable, monitor_surface, ...]`

---

## 1. What surfaces does this module ship?

- **`<tier_name>`** — one sentence describing the surface.
- **`<tier_name>`** — one sentence.
- (one bullet per claimed tier)

## 2. What does the user read off each surface?

### `<tier_name>` surface

One paragraph naming the specific decisions a PM makes from this surface. Be concrete.

### `<tier_name>` surface

One paragraph.

## 3. Why these surfaces and not others?

For each surface claimed, explain why the generic alternative is inadequate.
For each surface NOT claimed (but plausibly relevant), explain why not.

## 4. What would change the design?

Specific triggers in user need or backend output that would force a tier-set change or surface rewrite. Concrete; not generic.

## 5. Which backend doctrine does this module operationalise?

Cite by ID:
- P-numbers from [`../../00_thesis/01_non_negotiables.md`](../../00_thesis/01_non_negotiables.md).
- PR-numbers from [`../primitive/README.md`](../primitive/README.md).
- FP-numbers from [`../../00_thesis/03_frontend_thesis.md`](../../00_thesis/03_frontend_thesis.md).
- FM-numbers from [`README.md`](README.md).
- ADRs from [`../../05_decisions/`](../../05_decisions/).
- TD entries from `docs/technical_debt.md` if relevant.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | YYYY-MM-DD | Initial THESIS for `<tool_name>`. |
```

---

## Minimal template for `deferred` modules

A `deferred` module has no surfaces; THESIS is correspondingly minimal:

```markdown
# THESIS — `<NAME>` (deferred reservation)

> This name is reserved for a planned backend feature. No frontend surfaces exist today.

**Version:** v1
**Last reviewed:** YYYY-MM-DD
**Module spec:** [`module.ts`](module.ts)
**Backend reservation:** `<rationale + link to ADR or TD entry>`
**Tier set:** `[deferred]`

---

## 1. What surfaces does this module ship?

None — `deferred` reservation.

## 2. What does the user read off each surface?

N/A — no surfaces.

## 3. Why these surfaces and not others?

N/A — no surfaces. This reservation exists to claim the folder name so future implementation does not encounter a naming conflict.

## 4. What would change the design?

The reservation lifts when the backend feature ships. At that point this THESIS is rewritten in full.

## 5. Which backend doctrine does this module operationalise?

- Reservation rationale: `<cite ADR / TD / archetype-enum slot>`.

---

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | YYYY-MM-DD | Reservation. |
```

## Version log

| Version | Date | Change |
|---|---|---|
| v1 | 2026-05-25 | Initial THESIS template. Five-question structure, examples of good / anti-pattern answers, minimal `deferred` variant. |
