# FX Merge Playbook — 2026-06-10 (retargeted: `revamp` is the mainline)

**Supersedes** `MERGE_PLAYBOOK_2026-05-28.md` (which targeted `build`). On 2026-06-10 Sacha confirmed: **`revamp` is the new mainline.** `build` has been frozen since 2026-05-26; everything below is re-measured against `origin/revamp`.

Still a **planning doc, not an instruction to merge now.** Hard rules from the 05-28 playbook carry over unchanged: no merge without a coordinated window, no live rebase/force-push of any stack branch until ready to cascade the whole chain in one session, `revamp`/`build` never touched.

**REVAMP IS READ-ONLY (Sacha rule, 2026-06-10).** `revamp` is Sreeram's branch: we never push, force-push, or merge INTO it. Allowed: fetching and reading it (`git log/diff/show/ls-tree origin/revamp`) to keep this playbook fresh, and rebasing OUR branches onto it (rewrites our branches only). The final "merge into revamp" step in §4 is **executed by Sreeram** — our job ends at "stack rebased clean, tests green, PRs ready".

---

## 1. What changed on the mainline (verified 2026-06-10)

- `origin/revamp`: **+195 commits over `build`**, **+266 over our stack base** (#178's branch point). Active daily.
- **Frontend automation factory**: per-tool UI modules generated under `UI/.../src/modules/primitives/<tool_name>/` (THESIS.md · module.ts · surfaces/BuildCompact.tsx + BuildExtended.tsx + monitor/<X>Widget.tsx · mockups/ · __tests__/module.spec.ts), driven by `docs_revamped/02_components/{primitive,operator}/BUILD_GUIDE.md`, run by an automation loop (branch `frontend_automation`, merged in batches — ~28 rates tools done).
- **Operator layer expanded BY SREERAM** on revamp: `rolling_zscore`, `percentile_rank`, `correlation`, `rolling_correlation`, `cointegration`, `rolling_statistic`, `convert_units` — all **Series→Series** per "OPR9: one Series in, one Series out (closed family)". (`construct/evaluate/summarize_trades` no longer present in operators/.)
- **`fx_agent/` does not exist on revamp** → our backend territory is untouched.

## 2. The (surprisingly good) conflict picture

Conflict surface of `codex/fx-on-latest-build` (#178) vs **revamp** = **the SAME 8 files** as vs build:

`orchestrator/{config,contracts,prompts,session}.py` · `UI/.../monitor/registry.ts` · `UI/.../monitor/WidgetRenderer.tsx` · `UI/.../types/library.ts` · `tests/conftest.py`

Revamp's 370 UI file changes are **new files** (factory modules) — zero new overlap with our stack. Reproduce:
```bash
git fetch origin
base=$(git merge-base origin/revamp origin/codex/fx-on-latest-build)
comm -12 <(git diff --name-only "$base" origin/revamp | sort) \
         <(git diff --name-only "$base" origin/codex/fx-on-latest-build | sort) | grep -v graphify-out
```
The cascade-rebase plan from 05-28 therefore stands, with `origin/revamp` substituted for `origin/build` everywhere. More commits to replay through (266), same 8-file resolution surface.

## 3. Per-PR verdicts under the new mainline

| PRs | Verdict |
|---|---|
| **#178 → #240** (stack: data + 30 tools) | **Fully valid.** `fx_agent/` is new-file territory; rebase target becomes revamp. |
| **#241 `rolling_zscore_panel`** | **Likely obsolete.** Sreeram's `rolling_zscore` (Series→Series) is canonical on revamp; a panel variant is reproducible by composition (map over columns) → fails his promotion rule. Recommend: close in favor of his, after his confirmation. |
| **#242 `cross_sectional_rank`** | **Different operation, undecided.** His `percentile_rank` ranks a value within its own trailing history (time axis); ours ranks ACROSS columns at each date (cross-section). Not a duplicate — but our Panel→Panel shape may not fit his OPR9 Series-closed-family rules. His call: keep/reshape (SeriesSet input?)/absorb into his factory. |
| **#243 (UI polish) / #244 (Wave 2 widgets)** | **Pattern question.** Monitor widget core (registry/renderer) is unchanged on revamp, so they still apply technically — but his factory now generates per-tool modules including monitor widgets. FX UI should probably be REDONE through his factory post-merge (BUILD_GUIDE stages) rather than merged as hand-built widgets. Keep both PRs as reference/spec; expect to supersede. |
| Planning docs (INVENTORY, PLAYBOOKs, BACKLOG) | Valid; this doc supersedes the 05-28 playbook. |

## 4. Recommended sequence when the window opens

1. **Align with Sreeram first** (see ping below): confirm rebase target = revamp; fate of #241/#242; FX frontend via his factory.
2. **Throwaway dry-run**: rebase a copy of #178's branch onto `origin/revamp`, document the 8 resolutions, delete the branch. No force-push.
3. **Cascade-rebase the stack bottom-up in ONE session** (#178 → … → #240 (+#243/#244 if kept)), force-with-lease each, re-run the FX suites.
4. **Merge bottom-up** into revamp — **performed by Sreeram** (revamp is read-only for us; we deliver a clean, green, rebased stack and he merges). #241/#242: closed 2026-06-10 instead of merging (see §7).
5. **Post-merge**: re-ingest FX data in the target env (the 695 instruments / 9.15M rows live in the local dev DB; OIS 1.14M rows were local-only), run his frontend factory over the FX tools, then resume the tools backlog (`FX_TOOLS_BACKLOG_2026-05-28.md` — fully valid, fx_agent untouched).

## 5. Sreeram ping (3 questions, evidence-backed)

> 1. **Mainline**: confirming we rebase the FX stack (#178→#244) onto `revamp` — measured the conflict surface, it's the same 8 files as vs build (orchestrator ×4, monitor registry/renderer, library types, conftest); fx_agent is all new files.
> 2. **Operators**: you shipped `rolling_zscore` + `percentile_rank` (Series→Series, OPR9) on revamp — I close our #241 `rolling_zscore_panel` in favor of yours? And #242 `cross_sectional_rank` (ranks across columns per date — different op from percentile_rank): want it reshaped to your OPR rules, or absorbed into your roadmap?
> 3. **FX frontend**: should FX UI go through your frontend_automation factory (BUILD_GUIDE modules) post-merge instead of our hand-built monitor widgets (#243/#244)? Happy to treat ours as spec/reference for the factory run.

## 6. Refresh
Re-run §2's command + re-check `git rev-list --count origin/codex/fx-on-latest-build..origin/revamp`; update verdicts; bump filename date.

## 7. Status update — 2026-06-10: Sreeram signed off

Ping sent; answer: **"OK I trust you."** → all 3 questions green-lit, operator fate delegated to us. Executed same day:

- **#241 closed** (superseded by his `rolling_zscore`; branch kept on origin for reference).
- **#242 closed** (different operation — cross-sectional vs time-rank — but Panel→Panel shape doesn't fit revamp's OPR9 Series-closed-family rules; re-propose post-merge in revamp-conform shape, e.g. `cross_sectional_rank(SeriesSet)` per the architecture doc's own Pass example).
- **Rebase target = revamp** confirmed. **FX frontend via his factory** post-merge confirmed; #243/#244 remain open as spec/reference for the factory run.

Remaining gate: **the merge window is Sacha's timing call.** When opened, run §4 steps 2-5 (throwaway dry-run → one-session cascade rebase → merge bottom-up → re-ingest → factory → backlog).

## 8. Post-merge alignment backlog (audit vs revamp standards, 2026-06-10)

Full comparative audit of FX vs Sreeram's revamp standards (docs_revamped: P1–P12, PR1–PR16 + 8-stage BUILD_GUIDE, OPR/ART v2 per ADR 0016, tool_lifecycle 7 axes, methodology_exposure, ADR 0014/0015). **Already compliant, do not redo**: data substrate + playbooks, 4-file shape + compute signature, conventions value/source/rationale, bucket taxonomy, MCP wrapper pattern, finance-blind discipline, cross-domain read pattern (`fetch_cross_market_pair` survives on revamp), P7/P11.

### Bloc 1 — merge-window conditions
- [ ] Cascade-rebase onto revamp (§4; 8 known conflict files).
- [ ] Re-wire FX into the orchestrator's **dynamic Domain enum** (PR-10F — members built from sub-package dirs; replaces #178's hardcoded wiring).
- [ ] Re-test F1 panels against **ART v2** hardened validators (strict index, ±Inf forbidden, lineage integrity).
- [ ] **Renumber our FX ADR**: our "ADR 0007" collides with revamp's `0007-otr-resolver` → becomes 0017+.

### Bloc 2 — per-tool backend conformity (×30, mechanical, batchable)
- [ ] `exposure:` block on every convention + `_conventions_from_config` resolver (pilot pattern: `breakeven_inflation_simple`).
- [ ] Per-tool `README.md` + `LIFECYCLE_CHECKLIST.md` (tool folder becomes 6 files).
- [ ] Pytest-ify standalone-runner tests; complete the F1 seven (panels + scanners): wiring + sql_validation + parity fixtures.
- [ ] Split `01_fx_manifest.yml` into per-sub-agent manifests (spot / forwards / ndf / vol) with revamp-style headers.
- [ ] Create `manifesto/01_instruments/fx_agent/` (universe map + 6 substrate docs — content exists in FX_INVENTORY, it's reformatting).

### Bloc 3 — platform integration
- [ ] `tool_metadata` (ADR 0015): add FX domains to the `CHECK (domain IN ...)` constraint (**coordinate with Sreeram — shared schema**), extend `populate_tool_metadata.py`, then author theoretical_reference / known_limitations / desk_narrative ×30 (the real intellectual work).
- [ ] Register the 30 tools in `_PRIMITIVE_SPECS` with `output_field_units` (+ `output_artifact_type="Panel"` for the 3 panels) → FX becomes DAG-composable/selectable.
- [ ] Validate existing workflow templates (event_study / regime / backtest) with FX slots (WT15 instrument-agnostic claim) before authoring any FX-specific template.
- [ ] Optional: `fx_agent/storage/views/` (fx_instruments_view).
- [ ] Re-propose `cross_sectional_rank` per OPR v2 (ADR 0016 explicitly plans a `cross_sectional` family).

### Bloc 4 — frontend via HIS factory (not by hand)
- [ ] Enter the 30 FX tools into the frontend_automation catalog → modules generated in batches (THESIS, dual Build views, Monitor surface, mockups, module.spec). #243/#244 = spec/reference, then superseded.
- [ ] `surface_contract.md` rows for every FX tool (PR gate).

Effort: Blocs 1+2 ≈ 2-3 sessions (mostly mechanical); Bloc 3 ≈ 2 sessions (7-axes authoring dominates); Bloc 4 ≈ catalog entries, his factory does the rest. Strategic read: FX is at parity on substrate + backend core, behind on conformity/metadata/frontend — same position as his own tools 29-58, which his factory is still retrofitting.
