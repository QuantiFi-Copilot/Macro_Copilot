# FX Merge Playbook — 2026-05-28

**Purpose.** The exact, ordered path to eventually merge the FX universe work into Sreeram's `build`, plus the current drift/conflict reality. This is a **planning doc, not an instruction to merge now.** As of this snapshot we are deliberately **NOT merging into `build`** and **NOT rebasing the live stack**.

**Companion docs:** `fx_agent/ROADMAP.md` (living roadmap), `fx_agent/FX_INVENTORY_2026-05-28.md` (data/tools/PR snapshot).

---

## 0. Hard rules (do not violate without a real merge window)

- **No merge into `build`** until a coordinated window with Sreeram.
- **No live rebase / force-push of `codex/fx-on-latest-build` (#178) or any stack branch** until we are ready to cascade-rebase the WHOLE chain bottom-up in one session. Force-pushing #178 alone leaves the 15+ child PRs pointing at a rewritten base → "weird state".
- Rebasing our own branch onto `origin/build` does **not** modify Sreeram's `build` (it only rewrites our branch) — but it DOES force the cascade above, so it is gated by the same rule.

---

## 1. Current state (verified 2026-05-28)

- **19-PR FX stack**, linear, base = `build`: #178 → #184 → #189 → #191 → #198 → #203 → #207 → #228 → #230 → #232 → #233 → #234 → #235 → #236 → #237 → #238 → #239 → #240 → #243.
- **2 independent operators** (base `build` directly): #241 `rolling_zscore_panel`, #242 `cross_sectional_rank`.
- Ready-for-review: #178, #184, #189, #239. Draft: the other 15 (+ #241/#242/#243 draft).

### Mergeable state (GitHub-computed)
| PR | base | mergeable_state |
|---|---|---|
| #178 | `build` | **dirty (CONFLICTS)** |
| #239 | stack parent | clean |
| #240 | #239 | clean |
| #243 | #240 | clean |
| #241 | `build` | **clean** |
| #242 | `build` | **clean** |

`build` has advanced **71 commits** since the stack branched from it.

---

## 2. The blocker: #178 conflicts with current `build`

The base of the entire stack (`codex/fx-on-latest-build`, #178) no longer merges cleanly into `build`. **Good news: the conflict surface is only 8 files**, and the rest of the stack adds NEW `fx_agent/...` files that don't conflict.

### The 8 conflict files (both sides changed since divergence)
| File | Why | Expected difficulty |
|---|---|---|
| `orchestrator/session.py` | FX integration wired the orchestrator; build evolved it | medium |
| `orchestrator/config.py` | same | medium |
| `orchestrator/contracts.py` | same | medium |
| `orchestrator/prompts.py` | FX prompt additions vs build prompt changes | medium |
| `UI/.../monitor/registry.ts` | FX added widgets; build evolved the registry | low (additive entries) |
| `UI/.../monitor/WidgetRenderer.tsx` | same | low |
| `UI/.../types/library.ts` | library types extended both sides | low |
| `tests/conftest.py` | fixtures added both sides | low |

Reproduce the list:
```bash
git fetch origin build
base=$(git merge-base origin/build origin/codex/fx-on-latest-build)
comm -12 \
  <(git diff --name-only "$base" origin/build | sort) \
  <(git diff --name-only "$base" origin/codex/fx-on-latest-build | sort)
```

---

## 3. Two merge tracks

### Track A — Operators (#241, #242) — UNBLOCKED
Both branch directly off `build` and are `clean`. Pure additive (new `shared/operators/<name>/` folders + tests, zero edits to existing files). **Lowest-risk first merges** whenever a window opens — they don't depend on the FX stack at all.

### Track B — FX stack (#178 → #243) — BLOCKED on #178 rebase
Needs a bottom-up rebase onto current `build` before anything merges (see §4).

---

## 4. Track B rebase plan (the real work — DO NOT start without a window)

1. Rebase `codex/fx-on-latest-build` (#178) onto `origin/build`; resolve the 8 files in §2.
2. **Cascade**: rebase each child branch onto its newly-rebased parent, in stack order (#184 onto rebased #178, #189 onto rebased #184, …, #243 onto rebased #240). Force-push each, in order.
3. Re-run the FX test suites (35 Phase F1 checks + the per-phase compute/wiring/sql_validation triplets + parity fixtures).
4. Re-verify `mergeable_state` → `clean` bottom-up.

**This must be done in ONE focused session** (all 19 branches), not piecemeal — otherwise child PRs dangle on a rewritten base.

### Recommended de-risking BEFORE the real rebase: a throwaway dry-run
Measure + document the exact 8 resolutions WITHOUT touching the official stack:
```bash
git fetch origin build
git checkout -b codex/fx-rebase-dry-run-2026-05-28 origin/codex/fx-on-latest-build
git rebase origin/build          # resolve the 8 conflicts
# ... resolve, build/test locally ...
# DO NOT force-push #178. Capture the resolutions into this playbook, then delete the branch.
git checkout -  &&  git branch -D codex/fx-rebase-dry-run-2026-05-28   # when done
```
The dry-run yields the precise resolution recipe; the real cascade then becomes mechanical.

---

## 5. Merge order (once rebased + reviewed) — bottom-up
`#178 → #184 → #189 → #191 → #198 → #203 → #207 → #228 → #230 → #232 → #233 → #234 → #235 → #236 → #237 → #238 → #239 → #240 → #243`.
GitHub auto-retargets the next PR's base on each merge. Operators **#241 / #242** merge anytime (independent).

---

## 6. Pre-merge checklist (per PR)
- [ ] `mergeable_state == clean`
- [ ] FX tests pass on the rebased branch
- [ ] draft → ready-for-review
- [ ] Sreeram review approved
- [ ] merged → confirm next PR auto-retargets

---

## 7. Data note (do not forget)
The ingestion **playbooks merge as code**, but the **695 FX instruments / 9.15M rows live in the local dev DB** (`macro_data`). They must be **re-ingested in the target environment** (or already present in the shared TimescaleDB). The Phase C OIS data (1.14M rows) was a **local-only** ingestion from Sreeram-provided parquet.

---

## 8. UI coverage gap (post-merge work, not a blocker)
FX backend is complete; UI surfaces are Rates-first:
- **Library**: 30/30 FX tools (full parity — reads manifest).
- **Monitor → FX Agent**: 4/30 widgets wired (+ Forward Curve chart + sidebar sub-sections from #243).
- **Build (interactive workspace)**: **0/30** — `paramSpecs.ts` / context decoder are Rates-only; "Open in Build" on an FX tool fails to decode. Wiring FX into Build = a Wave 2 frontend chunk (paramSpecs + decoder + FX primitive views).

---

## 9. Honest cost + recommendation
- The rebase cost **grows** every day `build` advances (today: 71 commits / 8 files).
- "Not merging yet" is valid, but **not free** — the longer the wait, the larger the eventual rebase.
- **Recommended sequence when a window opens**: (1) merge operators #241/#242 first (zero-risk warm-up), (2) do the throwaway dry-run to lock the 8 resolutions, (3) cascade-rebase the stack bottom-up in one session, (4) review + merge bottom-up.

---

## 10. How to refresh this doc
Re-run the §1 mergeable-state queries + the §2 conflict-file command, update the tables, bump the filename date. Stale once `build` advances further.
