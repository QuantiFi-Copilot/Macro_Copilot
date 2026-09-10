# FX Agent — Universe Extension Roadmap

**Living document.** Updated as phases complete. Single source of truth for the FX universe extension work happening on stacked branches above [PR #178](https://github.com/QuantiFi-Copilot/QFin/pull/178) (the FX integration PR).

> If you're picking up this work mid-flight (new contributor, new session, returning after a break), **read this file first** before anything else in `fx_agent/`.

---

## Current status

**Date:** 2026-05-27

**Phase F1 ✅ SHIPPED on `codex/fx-phase-f1-fx-panels-scanners` (PR #240 draft, stacked on #239).** 7 cross-sectional FX tools landed: 3 substrate panels (`calculate_fx_vol_panel`, `calculate_fx_forwards_panel`, `calculate_fx_basis_panel`) + 4 composition scanners (`scan_fx_cross_currency_basis`, `scan_fx_implied_yield_differential`, `scan_fx_calendar_spread`, `scan_fx_vol_skew`). FX manifest: 23 → 30 tools. All 35 Phase F1 tests PASS live-DB. Asset-agnostic discipline (G2) preserved: panels emit typed `Panel`; scanners are pure composition; cross-sectional math (correlation / PCA / factor loadings) deliberately NOT duplicated FX-side (consumes `Panel` in shared/operators).

**Phase C ✅ SHIPPED on `codex/fx-phase-c-cip-basis` (PR #239 VALIDATED+FROZEN).** First FX→rates cross-domain primitive (`get_fx_cross_currency_basis`). Sign convention HARD-LOCKED Bloomberg BCRX-style validated via four independent corroborations (Sreeram via Bloomberg ref, Codex algebraic, Claude concrete EURUSD/-30bp, empirical end-to-end PASS on Sreeram-provided OIS data). 1.14M OIS rows ingested locally via Sreeram's official helpers (load_audit IDs 53 + 54).

**Branch (previous status):** `codex/fx-tradability-warehouse-seed` — stacked on `codex/fx-data-universe-bbg-warehouse-seed` (PR #207 visibility-only). Full stack: PR #178 → #184 → #189 → #191 → #198 → #203 → #207 → tradability → #235 (E2) → #236 (E3) → #237 (B+) → #238 (helper) → #239 (Phase C) → #240 (Phase F1).

**Phase in flight:** **FX TRADABILITY layer ✅ COMPLETE.** All substrate seeding done. Bid/ask layer added to spot, forwards, NDFs, ATM vol (std tenors), smile (std tenors). Cross G10 vol ATM seeded. Static convention matrix (NAME / SECURITY_DES / MARKET_SECTOR_DES) ingested for all 638 prior instruments. **695 FX instruments in DB across 6 instrument_types** (was 638 pre-tradability). 32 distinct (instrument_type, field_name) tuples across PX_LAST + PX_BID + PX_ASK.

  - `fx_spot` (38 = 9 G10 + 11 EM core/top-up + 6 EM warehouse-seed + 11 G10_CROSSES handled in fx_crosses, plus 1 EM_SPOT_REFERENCE — but DB total counted as 38)
  - `fx_forward` (82 = 42 G10 with 2Y/5Y + 30 EM deliverable + 10 SGD/THB)
  - `fx_ndf` (30 = 5 NDF families × 5 tenors + USDTWD NTN+ × 5)
  - `fx_vol` ATM (200 = 6 G10 + 11 EM + 6 EM warehouse × 5 standard tenors + 17 majors × 5 extended tenors VON/V2W/V2M/V9M/V2Y)
  - `fx_vol_smile` (284 = 13 pairs × 4 deltas × 3 standard tenors [156] + USDCNH/USDINR × 4 × 3 [24] + 13 pairs × 4 × 2 extended tenors 1W/6M [104])
  - `fx_macro_index` (4 = DXY, BBDXY, JPMVXYG7, JPMVXYEM)

`calculate_fx_carry` supports `market_scope` ∈ {G10/EM/ALL} (Codex Option B). All stacked PRs (#178 → #184 → #189 → #191 → #198 → #203) Open/Ready/mergeable=clean; warehouse-seed branch about to open as draft PR #204+.

**Maximaliste target locked:** Tier 1 + Tier 2 = **~28 tools across Phases A → E + G + H**. **Substrate-wise all Tier 1 phases now have data in DB.** Phase F (macro indices) is also data-ready as a Tier 3 byproduct. Tier 3 phases I (CFTC positioning) and J (PPP/REER) deferred — both archive-only after Phase 1 discovery failed all candidate ticker formats; awaiting a future BBG session that uncovers a working format.

**Immediate next decision:** With all substrates seeded, the next code work is pure tool-building — no Bloomberg trip needed. Recommended Tier 1 order: Phase C (CIP/xccy basis, reads OIS + forwards substrate, derived-only) → Phase D (NDF tools, now that USDTWD spot+NDF are both in DB) → Phase E (vol ATM + smile tools, full 200-row ATM + 284-row smile universe). Phase G (workflows) and Phase H (shared operators) are infrastructure and can be tackled in parallel. Phase B+ (implied yield diff + carry basket) is still pending too. All stacked PRs (#178 → #184 → #189 → #191 → #198 → #203 → warehouse-seed) Open/visibility-only/mergeable=clean; merge order matters when the integration window opens.

**Phase A ✅ SHIPPED on `codex/fx-data-universe-extension` (PR #184).** Wave 1 production ingestion landed cleanly:

| Playbook | Tickers | Rows ingested | Date range | Load ID |
|---|---|---|---|---|
| `fx_forwards` | 30 (6 G10 × 5 tenors) | 206,399 | 2000-01-03 → 2026-05-22 | 18 |
| `fx_vol` | 30 (6 G10 × 5 tenors, ATM) | 204,533 | 2000-01-03 → 2026-05-22 | 19 |
| `fx_crosses` | 11 G10 crosses | 75,713 | 2000-01-03 → 2026-05-22 | 17 |
| `spot_fx` | 9 G10 majors | 61,964 | 2000-01-03 → 2026-05-22 | 20 |
| **Total** | **80 instruments** | **548,609 rows** | **2000-01-03 → 2026-05-22** | |

Readiness gate `--strict-metadata` passes 21/0/0. Three env-level fixes required during Wave 1 ingestion are documented in the "Local env fixes" section below — most importantly, `max_locks_per_transaction=16384` is now baked into `docker-compose.yml` so future ingestions don't hit the same OOM.

**Wave 2 discovery findings** ([Wave 2 — Bloomberg discovery findings](#wave-2--bloomberg-discovery-findings-not-yet-ingested)) — not yet ingested, but the conventions are now documented for Phase B/C/D/E planning. In particular: NDFs are quoted as outright (not points), vol smile (RR/BF at 25-delta and 10-delta) is available for G10 pairs, and CIP basis should be derived from OIS+forwards rather than from the fragile direct basis-swap tickers.

---

## Architectural disciplines

These rules apply to **every** PR on this branch and its descendants. Codex and Sreeram have validated each.

### 1. Purely additive on stacked branches

This branch is stacked on PR #178. We do NOT touch files in scope of #178 unless absolutely necessary:

- ❌ Do not touch `orchestrator/{contracts,config,prompts,session}.py`
- ❌ Do not touch `docs_revamped/05_decisions/0007-fx-domain-agent.md`
- ❌ Do not touch UI widget engine wiring (`registry.ts`, `defaults.ts`, `WidgetRenderer.tsx`, `AppShell.tsx`)
- ❌ Do not touch `tests/test_orchestrator_domains.py`
- ❌ Do not touch `tests/test_fx_data_readiness.py` core (only safe addition: new ticker entries in playbooks the readiness gate reads)

- ✅ Add new playbooks under `fx_agent/playbooks/`
- ✅ Add new tools under `fx_agent/{spot,forwards,vol}/tools/`
- ✅ Add new manifest entries under `manifesto/03_tool_manifest/fx_agent/`
- ✅ Add new API routes (`api/routes/fx/detail.py`, etc.) when widgets demand them
- ✅ Add new UI widgets — but **only after the tools they consume are stable**

### 2. Primitives vs operators — strict separation

| Layer | Lives in | Role |
|---|---|---|
| **Operators** | `shared/operators/` | Generic Series / Panel / EventSet transformations. Asset-agnostic. |
| **Analytics** | `shared/analytics/` | Asset-aware loaders from substrate (e.g. `rates_fetch.py`, future `fx_fetch.py`). |
| **Primitives** | `<agent>/<subdomain>/tools/<tool>/` | Compose loader + operator(s) to produce a domain-specific artifact. |

**Examples of mistakes to avoid:**

- ❌ `zscore_custom_fx` — z-score is a generic operator, not an FX primitive. If `summarize_series` doesn't already expose z-score, create/extend the **operator**, not an FX-specific tool.
- ❌ `calculate_fx_correlation_matrix` — correlation on a Panel is a generic operator. Build it once, reuse cross-asset.
- ✅ `calculate_fx_panel` — legitimate primitive: loads FX rows from substrate, returns an FX-typed Panel object.
- ✅ `get_fx_forward_curve` — legitimate primitive: queries forward observations by tenor for a pair, returns a curve object.

**Rule of thumb:** if the transformation could be useful for Credit, Equity, Commodities — it's an operator, not an FX primitive.

### 3. No premature ADR

ADR = **durable architectural decision** (e.g. extending `Domain` closed family — see ADR 0007).

Catalogue extensions (more tenors, more crosses, more tools) are **not** architectural decisions. Manifest entries + commit messages suffice.

An ADR 0008 will be needed **only when** we split `Domain.FX` into `FX_CASH` / `FX_VOL` / `FX_NDF` — most likely during Phase E (vol).

### 4. Macro goal, not encyclopedia

The objective is a **complete FX universe to build real macro workflows**, not an exhaustive primitives library. Every new tool must answer: "what macro workflow does this enable that doesn't work today?"

### 5. Codex stacked-PR guardrails (G1, G2, G3)

Locked by Codex review on 2026-05-24 — apply to every contribution on this branch and descendants.

**G1 — Stability of in-review PRs.** Do NOT push commits to a branch whose PR is in draft-stable / review-pending state. Any update must land on a separate stacked branch.
- PR #178 (`codex/fx-on-latest-build`) — review-pending. **Frozen.**
- PR #184 (`codex/fx-data-universe-extension`) — review-pending. **Frozen.**
- All Phase B+ work happens on `codex/fx-data-universe-phase-b` and its descendants.

**G2 — Operators discipline (strict).** When adding to `shared/operators/`, ZERO asset-class references in the operator code. No `fx_pair` parameter, no hidden FX logic, no instrument-master attribute reads. If a transform is generic, it lives in shared and serves rates / FX / credit / equity identically. Asset-aware glue belongs in `shared/analytics/` loaders or in the per-domain primitive that *uses* the operator.

**G3 — ADR timing.** ADR 0008 (split `Domain.FX` → `FX_CASH` / `FX_VOL` / `FX_NDF`) only when vol AND/OR NDF have **substantial built tools**, not preemptively. Mirror Sreeram's inflation split timing — ADR 0006 landed with 15 inflation tools already built, not with the first scaffold. Premature ADR = wasted churn.

---

## Phase plan (ordered low risk → high risk)

```
Phase A — cash forwards depth (G10)   [✅ SHIPPED, PR #184 draft]   Tier 1
Phase B — spot EM + FX panel          [IN FLIGHT, this branch]      Tier 1
Phase C — CIP / cross-currency basis  [planned, reads OIS]          Tier 1
Phase D — NDFs                        [planned]                     Tier 1
Phase E — FX vol (ATM + smile)        [planned, ADR 0008 here]      Tier 1
Phase F — macro indices (DXY, etc.)   [optional]                    Tier 3
Phase G — workflows substrate         [planned]                     Tier 2
Phase H — shared operators extension  [planned]                     Tier 2
Phase I — CFTC positioning            [optional]                    Tier 3
Phase J — PPP / REER deviations       [optional]                    Tier 3
```

**Target = Tier 1 + Tier 2 = Phases A → E + G + H = ~28 tools, 8-10 dev sessions.** See [Maximaliste vision](#maximaliste-vision--tier-12--28-tools-across-phases-a-e--g--h) for the full substrate map, tools catalogue, and Tier rationale.

### Maximaliste vision — Tier 1+2 = ~28 tools across Phases A-E + G + H

Locked by Codex review on 2026-05-24. This section is the **strategic answer** to the question "what would a serious macro-hedge-fund FX system look like in this architecture?"

**Quality framing — not tool count:** "Serious FX" = correct conventions + deterministic primitives + asset-agnostic operators + reusable DAG workflows + compute/wiring/SQL tests + ADR only for real architectural extension. Tool count is an outcome of covering real macro workflows, not a target in itself.

#### Substrate map (~150-200 tickers cible)

| Sub-domain | Coverage | Approx tickers | Phase |
|---|---|---|---|
| G10 spot | 9 majors + 11 crosses | 20 | A ✅ |
| G10 forwards | 6 G10 pairs × 5 tenors (1W/1M/3M/6M/12M) | 30 | A ✅ |
| G10 vol ATM | 6 G10 pairs × 5 tenors (1W/1M/3M/6M/1Y) | 30 | E |
| G10 vol smile | 6 G10 pairs × ~3 tenors × {RR25, BF25, RR10, BF10} | ~72 | E |
| EM spot | 9 EM pairs (MXN, ZAR, TRY, BRL, PLN, HUF, KRW, IDR, PHP) | 9 | **B (this branch)** |
| EM forwards (deliverable) | MXN, ZAR, TRY, BRL, PLN, HUF × 5 tenors | 30 | B extension or sub-phase |
| NDFs (outright) | CCN+, IRN+, BCN+, KWN+, IHN+ × 5 tenors | 25 | D |
| CIP basis / xccy proxy | G10 × 3 tenors (1M / 3M / 12M) | derived, no new tickers | C |
| Macro proxies (DXY, BBDXY, JPMEMCI, …) | 5-10 indices | 10 | F (Tier 3) |
| Central bank rates substrate | reads existing `rates_agent` OIS — cross-domain | 0 new | C |
| CFTC positioning | Non-commercial net spec for 6 G10 | 6 | I (Tier 3) |
| PPP / REER | BIS REER monthly | 5-10 | J (Tier 3) |

#### 32 tools catalogue by sub-domain

Numbers in parentheses = count per sub-domain.

**Spot (8)**
- `get_fx_spot_level` ✅ (A)
- `scan_fx_spot` ✅ (A) — market_scope-aware (G10 / EM / etc.) via SQL filter, no code change needed for B
- `calculate_fx_panel` — Shape C, returns typed `Panel` artifact via `shared.artifacts.types.Panel` (**B-core, this PR**)
- `get_fx_returns_series` ✅ (B follow-up — shipped on `codex/fx-data-universe-phase-b-followup`, daily/weekly/monthly horizons)
- `calculate_fx_drawdown` ✅ (B follow-up — shipped, running DD + peak/trough/recovery)
- `get_fx_realized_vol` ✅ (B follow-up — shipped, rolling std × sqrt(252), default window 30d)
- `get_fx_z_score_panel` — uses shared operator (H)
- `get_fx_correlation_matrix` — uses shared operator (H)

**Forwards (5)**
- `calculate_fx_carry` ✅ (A, extended to scanner with rank_by / top_n / lookback / field_name)
- `get_fx_forward_curve` ✅ (A)
- `calculate_fx_implied_yield_differential` — `y_quote − y_base` from forward points (B+ — depends on EM forwards substrate)
- `scan_fx_carry_basket` — top-K basket constructor across G10 + EM (B+ — depends on EM forwards substrate)
- `calculate_fx_roll_yield` — forward → spot decay (C)

**Vol (8)**
- `get_fx_atm_vol_level` (E)
- `get_fx_vol_term_structure` — ATM full curve for one pair (E)
- `scan_fx_vol` — cross-pair ATM ranking (E)
- `get_fx_vol_z_score` (E)
- `get_fx_risk_reversal` — RR25 / RR10 skew metric (E)
- `get_fx_butterfly` — BF25 / BF10 kurtosis metric (E)
- `calculate_fx_vol_smile` — full {ATM, RR25, BF25, RR10, BF10} for one pair × one tenor (E)
- `calculate_fx_vol_carry` — implied vs realized spread (E)

**Derived (3)**
- `calculate_fx_cip_basis` — reads OIS via `shared.analytics.rates_fetch` (C)
- `scan_fx_cip_dislocations` (C)
- `calculate_fx_cross_currency_basis` — xccy proxy (C)

**NDFs (3)**
- `get_fx_ndf_outright` — different convention from G10 forwards (outright not points) (D)
- `calculate_fx_ndf_implied_carry` (D)
- `scan_fx_ndf_carry` (D)

**Macro (3, Tier 3)**
- `get_fx_index_level` — DXY, BBDXY, JPMEMCI (F)
- `calculate_fx_reer_deviation` — vs BIS REER (J)
- `calculate_fx_ppp_deviation` — vs OECD PPP (J)

**Positioning (2, Tier 3)**
- `get_cftc_fx_positioning` — non-commercial net spec (I)
- `scan_fx_positioning_extremes` (I)

#### Effort estimates per phase

| Phase | Scope | Tools | Effort | Tier |
|---|---|---|---|---|
| A | G10 forwards depth + scanner + curve + UI | 4 | ✅ shipped | 1 |
| B-core | EM spot universe (9 pairs) + `calculate_fx_panel` typed Panel primitive | 1 | ✅ shipped | 1 |
| B follow-up | `get_fx_returns_series` + `calculate_fx_drawdown` + `get_fx_realized_vol` (separate stacked branch on B-core) | 3 | ✅ shipped | 1 |
| B+ | `calculate_fx_implied_yield_differential` + `scan_fx_carry_basket` (after EM forwards substrate extracted) | 2 | 0.5-1 session | 1 |
| C | CIP / xccy basis (reads OIS substrate) + roll yield | 3 | 1 session | 1 |
| D | NDFs (5 pairs × 5 tenors, outright convention) | 3 | 1-2 sessions | 1 |
| E | Vol ATM term structure + RR/BF smile + vol carry | 8 | 2 sessions | 1 |
| F | Macro indices (DXY, BBDXY, JPMEMCI) | 1 | 0.5 session | 3 |
| G | Workflows substrate (`fx_agent/workflows/` mirroring `rates_agent/workflows/`) | infra | 0.5 session | 2 |
| H | Shared operators extension (correlation_matrix, pca, regime_classifier, factor_decomposition) — strictly asset-agnostic per G2 | 4 | 1 session | 2 |
| I | CFTC positioning | 2 | 0.5 session | 3 |
| J | PPP / REER deviations | 2 | 0.5 session | 3 |

**Tier 1 (must-have)** = A + B + C + D + E = **24 tools**
**Tier 2 (substrate maturity)** = G + H = **+4 tools** (infra + 4 operators)
**Tier 1 + Tier 2 = ~28 tools, 8-10 sessions.** ← validated target.
**Tier 3 (nice-to-have)** = F + I + J = +5 tools, built only on concrete demand.

#### 4 cross-cutting infrastructure items

1. **Operators extension** (`shared/operators/`) — Phase H
   - `correlation_matrix` (rolling, configurable window)
   - `pca` (eigendecomp on returns matrix)
   - `regime_classifier` (vol-state HMM or threshold-based)
   - `factor_decomposition` (carry / value / momentum factor exposures)
   - **G2 discipline (strict):** ZERO asset-class references. No `fx_pair` param. Works on any `Panel`.

2. **Workflows substrate** (`fx_agent/workflows/`) — Phase G
   - Mirror `rates_agent/workflows/` pattern.
   - Composable DAG workflows: `fx_carry_screen.yml`, `fx_vol_regime_scan.yml`, `cip_dislocation_alert.yml`, `em_spot_drawdown_report.yml`.

3. **UI surfaces extension**
   - `FXVolAgentPage` (E) — separate route if vol becomes substantial.
   - `FXCorrelationMatrixWidget` (H)
   - `FXCarryBasketWidget` (B)
   - `FXNDFScannerWidget` (D)
   - Existing `FXAgentPage` extended with vol / CIP panels.

4. **Orchestrator extensions**
   - **ADR 0008 (G3 timing):** split `Domain.FX` → `Domain.FX_CASH` + `Domain.FX_VOL` + `Domain.FX_NDF` only when each has substantial built tools. Mirror Sreeram's inflation split timing.
   - When fired: update `contracts.py`, `config.py`, agent prompts, `session.py` routing.
   - Per-domain orchestrator test in `test_orchestrator_domains.py`.

#### Final decision rationale

**Build Tier 1 + Tier 2 = 28 tools across Phases A → E + G + H.**

- Covers every major FX research workflow: spot regime, carry, CIP, NDFs, vol surface, basket construction, factor analysis.
- Stays within Sreeram's architectural patterns (primitives vs operators, per-tool 4-file, manifest discipline, ADR per closed-family extension).
- Provides ~3× the tool count of current Rates agent (~10 tools) — appropriate since FX has more sub-domains (cash vs vol vs NDF) than rates.
- Skips Tier 3 unless concrete demand emerges. No speculative builds on CFTC / PPP / REER.
- 8-10 dev sessions total.

#### Per-phase compliance check discipline (~10 min before coding any new phase)

1. Does Sreeram have an analogous tool / concept in `rates_agent/`? Read his code.
2. Does the proposed addition violate any of his disciplines (primitives vs operators, per-tool 4-file layout, naming conventions, cross-domain reads)?
3. Does this need a new ADR? (Use G3 timing.)
4. Does this need new tests in his pattern (compute + wiring + sql_validation)?
5. Document the compliance answer in the matching "Phase X" section below before starting.

#### 3 open questions for Sreeram (raise at PR #178 / #184 review)

1. **Cross-domain reads** — when an FX tool reads OIS substrate (Phase C CIP), preferred pattern: shared helper in `shared.analytics.rates_fetch`, or a FX-side wrapper that imports from it?
2. **Workflows factoring** — when creating `fx_agent/workflows/` (Phase G), what stays agent-local vs. goes to `shared/workflow/`?
3. **ADR 0008 timing** — wait until FX_VOL + FX_NDF have concrete tools (per G3), or prepare the split scaffold earlier so the rest of Phase E lands inside it?

### Wave 1 — Opportunistic data acquisition

Independent of the phase plan above. Wave 1 = **batch all the low-risk Bloomberg extractions while the terminal is accessible**, even for phases whose tools/widgets we won't build yet. Data sits in DB ready, tool/UI work follows the phase order.

**Strict discipline:** Wave 1 is **data acquisition only**. Ingesting ATM vol data does NOT mean we implement FX vol tools, build a vol UI, or extend `Domain` enum to `FX_VOL`. The phase plan dictates when tools land; Wave 1 just ensures their substrate is ready.

**Scope locked for Wave 1:**

| Playbook | Wave 1 action | Final ticker count | Phase that builds tools on top |
|---|---|---|---|
| `fx_forwards.yml` | Canonical v2.0 (G10 × 5 tenors, start 2000) | 30 | Phase A |
| `spot_fx.yml` | v2.0: add NZDUSD, USDNOK, USDSEK (G10 majors complete) | 9 | Phase A scanners benefit; Phase B builds the panel |
| `fx_crosses.yml` | v2.0: add NZD-side and EUR-side G10 crosses | 11 | Phase B |
| `fx_vol.yml` | v2.0: extend ATM 1M → full tenor strip (1W/1M/3M/6M/12M) with `smile_point: "ATM"` metadata | 30 | Phase E (after ADR 0008 domain split) |

**Total: 80 instruments in DB after Wave 1.**

**Explicitly excluded from Wave 1** (need decisions or BBG verification before extraction):
- NDFs (Bloomberg has multiple conventions; ticker verification needed)
- FX vol smile (25-delta RR/BF; deltas + tenors scope decision needed)
- CIP / cross-currency basis (direct ticker vs derived-from-OIS decision needed)
- EM broad universe (which subset of EM? MXN, ZAR, TRY, KRW, BRL, …?)

These land in their respective phases (B/C/D/E) with full architectural review.

### Phase A — Cash forwards depth — LOCKED scope

Goal: extend forwards from 1M-only to the standard tenor strip (1W, 1M, 3M, 6M, 1Y) across G10 majors, and ship the first two parameterized FX primitives.

**8-step plan (in order):**

1. ✅ **Confirm Bloomberg tickers.** Done 2026-05-21. Long format `<PAIR><TENOR> Curncy` valid for all 30 combinations, history from 2000-01-03. Canonical 1Y is `12M`.
2. 🟡 **Extend `fx_forwards.yml`** for all G10 forward tenors. **Done in repo (canonical v2.0, 30 tickers, start 2000-01-01); legacy `fx_forwards_curve.yml` deleted.** Pending: actual Bloomberg extraction from the playbook.
3. ✅ **Run extraction → ingestion → readiness gate strict mode.** Done 2026-05-22. Extraction ran in ~7 min from university BBG terminal; ingestion landed 548,609 rows across 4 playbooks (loads 17-20, all SUCCESS); readiness gate strict mode passes 21/0/0; 30 forward tickers / 9 spot / 11 crosses / 30 vol present in DB with uniform 2000-01-03 → 2026-05-22 coverage.
4. ✅ **Audit `calculate_fx_carry` across tenors.** Done 2026-05-22 (commit `293c031`). Bug fixed: 12M tenor was silently falling back to 21-day tenor_days → ~12× inflated annualisation. Added explicit ValueError on unsupported tenors and tightened the Pydantic schema to `Literal["1W","1M","3M","6M","12M"]`.
5. ✅ **Add `get_fx_forward_curve` primitive.** Done 2026-05-22 (commit `e241f94`). For one G10 pair, returns one row per tenor with raw forward points, spot-unit forward points, outright, carry bps + ann%, z-score / percentile / range on `forward_points_spot_units`. Cross-tool consistency verified vs `calculate_fx_carry`.
6. ✅ **Carry scanner — extension of `calculate_fx_carry`, NOT a duplicate `scan_fx_carry`.** Done 2026-05-23 (commit `43a9632`). Per Codex's discipline ("one tool, not a duplicate"), `calculate_fx_carry` gained `rank_by` (carry_signed / abs_carry / abs_z_score) + `top_n` + `lookback_days` + per-pair carry-series z-score / percentile / range. Historical carry is built from per-date joined spot + forward (no lookahead); current snapshot uses the last common spot+forward date.
7. ✅ **Manifests + wiring (7a) + targeted tests (7b).** Done 2026-05-24 (commits `e35fb77` + `8a52959`). MCP server registers all 4 FX tools; API exposes `/forward-curve`; manifest entries updated with `pm_overridable` reflecting the new scanner params. 22 targeted assertions across 3 standalone test runners — 12M not inflated, rank_by sort, top_n truncation, latest-common-date alignment, JPY divisor handling on USDJPY, outright consistency, cross-tool numerical consistency, unknown-pair fail-loud, lookback_days bounds.
8. ✅ **Parameterized FX Carry widget + new FX Forward Curve widget.** Done 2026-05-24 (commit `378f233`). Per Codex's discipline ("one widget, not a `FXCarryScannerWidget` duplicate"), `fx_carry` widget upgraded to parameterized (tenor / rank_by / top_n / lookback); new `fx_forward_curve` parameterized widget for the term-structure view. Both self-fetching via per-widget hooks (no longer depend on `FXDataProvider` context); widget-side defaults preserve backward compatibility for legacy localStorage layouts with empty params.

**Explicitly OUT of Phase A:**

- `calculate_fx_carry_curve` — likely derivable from `get_fx_forward_curve` + a generic carry operator; drop until we can prove non-redundancy.
- `FXCarryCurveWidget` — same reasoning.

### Phase B — Spot EM + FX panel primitive — IN FLIGHT (this branch)

**Goal:** extend spot coverage to 9 EM majors and introduce the cross-sectional FX panel primitive — the foundation for every later cross-sectional FX workflow (carry basket, correlation matrix, factor decomposition, regime classification).

**Branch:** `codex/fx-data-universe-phase-b` — stacked on `codex/fx-data-universe-extension` (PR #184), itself stacked on `codex/fx-on-latest-build` (PR #178).

#### 5 architectural Qs locked by Codex (2026-05-24)

1. **Single playbook:** extend `spot_fx.yml` to include EM. Do NOT create a separate `spot_fx_em.yml`. Rationale: G10 + EM share schema, family field already segregates them.
2. **`calculate_fx_panel` shape:** Shape C — returns a real typed `Panel` artifact via `shared.artifacts.types.Panel` (cross-sectional, time-indexed). Not a dict, not a DataFrame, not an ad-hoc namedtuple.
3. **`scan_fx_spot` market_scope:** already market_scope-aware via SQL filter on `instrument_master.attributes`. **Zero code change** needed — adding EM rows to the universe is enough for it to surface them under `market_scope='EM'`.
4. **Tests:** extend `tests/test_fx_data_readiness.py` with EM assertions. Do NOT create a new test file.
5. **UI deferred:** ship data + scanner + panel primitive first. UI for EM scanner / FX panel lands later (sub-phase or Phase E grouping). Phase B closes when the API + MCP layer is clean.

#### Locked decisions (BBG verification 2026-05-25)

- **EM universe = 9 pairs:** USDMXN, USDZAR, USDTRY, USDBRL, USDPLN, USDHUF, USDKRW, USDIDR, USDPHP. No drop, no add.
- **Uniform start_date = 2000-01-01** for all 9 EM pairs. No per-instrument override (locked out).
- **USDTRY redenomination resolution:** Scenario A — Bloomberg has a clean back-adjusted series across the 1 Jan 2005 redenomination. 31/12/2004 = 1.3435, 03/01/2005 = 1.3475, 28/02/2005 = 1.2830. No 1,000,000× discontinuity. TRY stays in V1.
- **Fail-loud ingestion:** if any of the 9 tickers has no PX_LAST at the retained start window, pipeline must crash, not silent-skip.

#### Phase B core + follow-ups

**Discipline (Codex):** this PR ships ONE new primitive (`calculate_fx_panel`) + the spot EM substrate that feeds it. The follow-ups land in their own stacked branches/PRs to keep this PR short, focused, and reviewable. **Do not let Phase B grow into a 6-tool PR.**

**Core (THIS PR — single new primitive):**
1. `calculate_fx_panel` — Shape C typed `Panel` of FX returns / levels across a universe via `shared.artifacts.types.Panel`. **Cornerstone primitive** — every later cross-sectional FX tool consumes it.

**Follow-ups after panel lands ✅ SHIPPED** on `codex/fx-data-universe-phase-b-followup` (stacked on `codex/fx-data-universe-phase-b`):
2. `get_fx_returns_series` ✅ — log-returns at configurable horizon (daily / weekly / monthly). Closed-Literal horizon, output unit RATIO. 9/9 targeted compute tests PASS.
3. `calculate_fx_drawdown` ✅ — running drawdown DD_t = (P_t / running_max) - 1 with snapshot (current/max DD + peak/trough/recovery dates + time-to-recovery). Output unit RATIO ≤ 0. 9/9 PASS.
4. `get_fx_realized_vol` ✅ — N-day rolling realized vol from daily log-returns, annualized via sqrt(252), expressed in PERCENT. Default window 30d. 9/9 PASS + math hand-verified against direct spot fetch.

**Later (depends on EM forwards substrate, then C/D — explicitly NOT in immediate Phase B):**
5. `calculate_fx_implied_yield_differential` — `y_quote − y_base` from forward points. Requires EM forwards extracted first (not in this PR). Prepares Phase C CIP.
6. `scan_fx_carry_basket` — top-K basket constructor across G10 + EM. Requires EM forwards + ideally CIP / NDF substrate to be meaningful.

#### Phase B execution sequence (core PR only — short and focused)

1. ✅ **First commit (THIS commit) — ROADMAP update.** Maximaliste plan ported from session memory; Phase B 5 Qs and BBG decision acted in repo.
2. **Extend `spot_fx.yml` v3.0** — add 9 EM pairs with `start_date: 2000-01-01`, `fx_family: "EM_SPOT"`, `market_scope: "EM"`, `region` per pair (LATAM / EMEA / APAC).
3. **Wave 1.5 Bloomberg extraction** — ~9 tickers × ~26 years = ~60k rows. Bucket: `gs://quantifi-fx-data-sacha`. Use the same canonical recipe as Phase A (see "Open questions / blockers" historical entry).
4. **Postgres ingestion** — extend `tests/test_fx_data_readiness.py` with EM assertions; readiness gate `--strict-metadata` must pass clean.
5. **Verify `scan_fx_spot(market_scope="EM")`** surfaces the 9 new pairs correctly — no code change expected (Codex locked), but a runtime smoke + a targeted test assertion in `test_fx_data_readiness.py` (or a new wiring assertion) to make the contract visible.
6. **Implement `calculate_fx_panel`** — compute + schemas + config + `__init__.py`. Uses `shared.artifacts.types.Panel`. Pydantic schema with `Literal["G10", "EM", "G10_CROSSES", "ALL"]` for `market_scope`.
7. **MCP wiring + API route + manifest entry + tests for `calculate_fx_panel` ONLY** (compute + wiring + sql_validation, mirroring Phase A pattern). Do NOT scope-creep into the follow-up tools (`returns_series`, `drawdown`, `realized_vol`) — they get their own stacked PR.
8. **Stacked draft PR** — `Depends on #184` and `Depends on #178` in body. Short, single-primitive, very reviewable.

**Follow-up PRs status:**
- **Phase B follow-up PR ✅ SHIPPED** on `codex/fx-data-universe-phase-b-followup` — 3 new derived primitives (`get_fx_returns_series` + `calculate_fx_drawdown` + `get_fx_realized_vol`) + `shared.analytics.fx_fetch.fetch_fx_spot_series` single-pair helper. 6 commits, 33 targeted-test assertions (9+9+9+6 wiring) PASS. Same architectural pattern as B-core, no extra substrate.
- **Phase B+ PR** — `calculate_fx_implied_yield_differential` + `scan_fx_carry_basket`. Requires EM forwards substrate to be extracted first (separate Wave). Lands after Phase C planning is clear.

#### Phase B compliance check (per §"Per-phase compliance check discipline")

1. **Sreeram analogue?** Rates has no `calculate_rates_panel` yet, but `shared.artifacts.types.Panel` exists from his core types. We re-use his abstraction, do not invent a parallel.
2. **Discipline violations?** None — adding tools to existing `fx_agent/spot/tools/` directory; no operator violations; no orchestrator touch.
3. **New ADR?** No (G3). Adding spot pairs + a panel primitive is catalogue extension, not architectural.
4. **New tests?** Yes — compute + wiring + sql_validation for each new tool (mirror Phase A step 7b pattern). EM ticker assertions added to `test_fx_data_readiness.py` (existing file, per locked Q4).
5. **Compliance answer documented** — this section.

#### Operator gap flagged for Phase H

If a generic `summarize_series` doesn't expose z-score on a `Panel`, that's an operator extension — done in `shared/operators/` (Phase H), not as an FX primitive. Phase B should NOT add `zscore_fx_panel` even if convenient; flag the gap and route through `shared/operators/` once Phase H lands.

### Phase C — CIP / cross-currency basis — planned

Goal: cross-currency basis swap levels and CIP deviation (forward-implied carry vs OIS differential).

**Scope:**

- New playbook `fx_xccy_basis.yml` (Bloomberg `<CCY>BS<tenor> Curncy`-style tickers OR derive from existing OIS + forwards substrate — decision pending data check)
- `get_xccy_basis_level` primitive
- `calculate_cip_deviation` primitive (reads BOTH FX forwards and Sreeram's OIS substrate)
- `scan_xccy_basis` primitive
- UI widgets

**Cross-substrate touch:** this is the first FX tool that consumes Sreeram's OIS substrate. Coordinate with him on `shared/analytics/rates_fetch.py` exports before adding `shared/analytics/fx_fetch.py`.

### Phase D — NDFs — planned

Goal: non-deliverable forwards for EM Asia / LatAm.

**Scope:**

- New playbook `fx_ndf.yml` (USDCNH NDF, USDINR NDF, USDBRL NDF, USDKRW NDF, USDIDR NDF, USDPHP NDF, USDTWD NDF)
- `get_ndf_outright` primitive (NDFs are non-deliverable — distinct quoting convention)
- `calculate_ndf_carry` primitive
- `calculate_ndf_vs_deliverable_basis` (parity NDF vs deliverable where both exist)
- UI widgets

**Convention engineering needed:** the current `"JPY" in pair` shortcut for points_divisor (see Conventions) is fragile for NDF / EM. Phase D must introduce **per-ticker convention metadata** via `instrument_master.attributes`. The loader reads attributes; the tool reads from the loader. No more substring matching.

**Architectural decision deferred:** keeping NDFs under `Domain.FX` for now. If Phase D ships 5+ NDF-specific tools that don't make sense under cash FX semantics, revisit. Don't write an ADR pre-emptively.

### Phase E — FX vol — planned

Goal: implied vol surface (ATM + smile) + realized vol.

**Scope:**

- The ATM vol data substrate **lands in Wave 1** (data acquisition only — see [Wave 1 — Opportunistic data acquisition](#wave-1--opportunistic-data-acquisition)). The canonical `fx_vol.yml` v2.0 already covers ATM across the full G10 tenor strip. Phase E does NOT touch the substrate, it builds tools on top of it.
- New playbook for smile data (RR + BF, e.g. `fx_vol_smile.yml`) — 25-delta and possibly 10-delta. Bloomberg ticker conventions need to be verified before this playbook is written.
- Implement `realized_vol/compute.py` (currently scaffolded stub — see ADR 0007 rollout plan)
- New primitives: `calculate_implied_vol_atm`, `calculate_risk_reversal`, `calculate_butterfly_skew`, `vol_smile_reconstruction`, `vol_regime_classifier`

**Architectural decision (ADR 0008 required here):** vol introduces a structurally distinct instrument family — option-implied surfaces, different desk owner, different curve families. The likely split: `FX_CASH` (spot + forwards + carry + NDFs) and `FX_VOL` (option-implied surface). Mirrors how Sreeram split inflation into `INFLATION_INDEXED_BONDS` (bonds) and `INFLATION_SWAPS` (ZCIS).

**ADR 0008 deliverables:**

- New `Domain.FX_VOL` (rename `Domain.FX` → `Domain.FX_CASH` is optional but cleaner)
- New `MCP_SERVERS["fx_vol_agent"]` pointing at `fx_agent/vol/mcp_server.py` (replaces the deleted stub — see PR #178 housekeeping)
- New `FX_VOL_SYSTEM_PROMPT`
- New `DOMAIN_MCP_SERVERS[Domain.FX_VOL]` mapping
- Supervisor prompt extensions (AVAILABLE DOMAINS + DOMAIN SIGNALS)
- `_build_domain_boundaries` label
- Test extensions in `test_orchestrator_domains.py`
- A new `FXVolAgentPage` UI surface

---

## Conventions / lessons learned

Accumulates as we go. New conventions get an entry here when we discover or decide them.

### JPY forward points divisor

**Location:** [fx_agent/forwards/tools/fx_carry/config.yaml](forwards/tools/fx_carry/config.yaml) — keys `jpy_forward_points_divisor` (100) and `default_forward_points_divisor` (10000).

**Current logic:** [compute.py:38-42](forwards/tools/fx_carry/compute.py) uses `"JPY" in pair` substring matching to pick the divisor.

**Phase A status:** works correctly for all G10 JPY pairs (USDJPY, EURJPY, GBPJPY, etc.).

**Future limitation:** the substring shortcut is fragile for EM / NDF (some have their own divisors). **Phase D must upgrade** to per-ticker convention metadata via `instrument_master.attributes` (the loader reads attributes; the tool reads from the loader).

### Playbook universe entry shape

Each universe entry should include (minimally) for FX:
- `ticker` (e.g. `EURUSD1M Curncy`)
- `instrument_type` (`fx_spot`, `fx_forward`, `fx_vol`, `fx_ndf`)
- `pair` (e.g. `EURUSD`)
- `tenor` (e.g. `1M` — for forwards / vol / NDFs only)
- `base_ccy`, `quote_ccy`
- `market_scope` (`G10`, `EM`, etc.)
- `fx_family` (logical grouping for the readiness gate)
- `region` (`Global`, `EMEA`, `APAC`, `LATAM`)

The readiness gate `tests/test_fx_data_readiness.py` validates these fields. New playbooks must follow the shape.

### Local env fixes for Wave 1 ingestion

While running Wave 1's first Postgres ingestion, three environmental issues surfaced. All three need to be present (or applied at fresh-clone time) for any large FX ingestion to complete cleanly. Documented here so future contributors don't re-discover them.

**Fix 1 — ADR 0003 schema migration on existing DB volumes.**

The `database/schema.sql` file is mounted to `/docker-entrypoint-initdb.d/` and only runs on the **first** Postgres cluster initialisation. The `cusip` and `isin` columns (ADR 0003) were added to `schema.sql` after the local volume was created, so existing volumes don't have them. Ingestion fails with `AttributeError: cusip` inside `upsert_instrument_master`.

Apply the delta migration once per existing volume:

```bash
docker exec -i macro-tsdb psql -U quantuser -d macrodata \
    < Macro_Copilot/database/migrations/2026-05-20_a3_b1_schema_sync.sql
```

The migration is idempotent (`IF NOT EXISTS` everywhere) so it's safe to re-run.

**Fix 2 — `max_locks_per_transaction` raised from 128 to 16384 (PERSISTENT via docker-compose).**

Large `market_data_daily` upserts run in a single atomic transaction by design (delete + every batch + audit flip — see `ingest_parquet.py`). A 206k-row payload exceeds Postgres's default lock-table footprint, surfacing as `out of shared memory — increase max_locks_per_transaction`.

This fix is now baked into `docker-compose.yml` via the tsdb service `command:` override:

```yaml
command:
  - postgres
  - -c
  - max_locks_per_transaction=16384
```

A fresh `docker compose up` will start Postgres with the bumped value, so this fix survives volume recreation. No manual action needed at clone time beyond `docker compose up`.

**Fix 3 — `GCP_BUCKET_NAME` env var for the FX bucket.**

While we maintain two GCS buckets (FX = `gs://quantifi-fx-data-sacha`, rates = `gs://macro-storage-bucket`), set the bucket env var explicitly when running FX extractions or ingestions. The default in all scripts is `macro-storage-bucket`. See "Wave 1 bucket override" section above for the full sequence and the future migration plan.

### Wave 2 — Bloomberg discovery findings (not yet ingested)

Discovery wave run from the university Bloomberg terminal on 2026-05-22 in parallel with the Wave 1 extraction. **No tickers from this section are in `instrument_master` yet** — these are convention notes to inform Phase B/C/D/E playbook design when those phases ship.

**EM spot — all valid, ready for Phase B ingestion when scope is decided:**

| Ticker | Status |
|---|---|
| `USDMXN Curncy` | ✅ |
| `USDZAR Curncy` | ✅ |
| `USDTRY Curncy` | ✅ |
| `USDBRL Curncy` | ✅ |
| `USDPLN Curncy` | ✅ |
| `USDHUF Curncy` | ✅ |
| `USDKRW Curncy` | ✅ (deliverable, not NDF) |
| `USDIDR Curncy` | ✅ |
| `USDPHP Curncy` | ✅ |

→ Phase B can extend `spot_fx_em.yml` or extend `spot_fx.yml` with EM market_scope, no further verification needed.

**NDFs — convention identified, partial scope:**

| Ticker | Status | Convention note |
|---|---|---|
| `IHN+1M Curncy` | ✅ | **IDR NDF outright 1M** — quoted as outright, not points |
| `IRN+1M Curncy` | ✅ | **INR NDF outright 1M** |
| `BCN+1M Curncy` | ✅ | **BRL NDF outright 1M** — use this, not `BRL+1M` |
| `BRL+1M Curncy` | ⚠️ Exists but no `PX_LAST` data in any window | Stale or alias — avoid |
| `KWN+1M Curncy` | ✅ | **KRW NDF outright 1M** |
| `IHN+3M Curncy` | ✅ | IDR NDF outright 3M (tenor extension works) |
| `IRN+3M Curncy` | ✅ | INR NDF outright 3M |
| `USDCNH+1M Curncy` | ❌ Invalid | USDCNH is the deliverable offshore yuan pair — NOT an NDF format. USDCNY NDF uses a different ticker (to verify later — possibly `CCN+1M`, `CNN+1M`, or similar). |

**Critical convention discovery:** NDFs are quoted as **outright forwards**, not as forward points (unlike G10 forwards where `EURUSD1M Curncy` returns small numbers like 17.09). When Phase D builds `calculate_ndf_carry`, the formula is `(outright_forward − spot) / spot` annualised, **not** `forward_points / divisor`. The G10 `calculate_fx_carry` tool will need an "ndf-aware" branch, or NDFs will need their own primitive.

**Vol smile (EURUSD test, 4/4 valid) — convention confirmed:**

| Ticker | Status |
|---|---|
| `EURUSD25R1M Curncy` | ✅ 25-delta Risk Reversal 1M |
| `EURUSD25B1M Curncy` | ✅ 25-delta Butterfly 1M |
| `EURUSD10R1M Curncy` | ✅ 10-delta Risk Reversal 1M |
| `EURUSD10B1M Curncy` | ✅ 10-delta Butterfly 1M |

→ Phase E vol smile playbook scope: both 25-delta and 10-delta points are available. Format is `<PAIR><DELTA><R|B><TENOR> Curncy`. Likely extends to all 6 G10 pairs and the same tenor strip as ATM. **30 ATM × (1 + 4 smile points) = 150 tickers if maximalist**, or just 25-delta only = 90 tickers.

**Cross-currency basis (CIP) — fragile direct path:**

| Ticker | Status |
|---|---|
| `EUBS3 Curncy` | ⚠️ Data in 2020, `#N/A` in 2010 AND 2026 windows |
| `BPBS3 Curncy` | ⚠️ Same pattern (mid-period data only) |
| `JYBS3 Curncy` | ⚠️ Same pattern |
| `EUBS12 Curncy` | ⚠️ Same pattern |
| `AUBS3 Curncy` | ❌ Invalid security |
| `EUBS24 Curncy` | ❌ Invalid (likely BBG uses `EUBS2Y` or `EUBS24M`) |

→ **The direct-ticker path for CIP basis is too fragile for production**. Phase C should derive CIP deviation from **OIS differential − forward-implied carry** using the OIS substrate (Sreeram's domain) and the forwards substrate (ours) — both already in DB. No new playbook needed for Phase C in this path. The basis-swap ticker formats can be re-verified in a future session if direct quotes are ever needed.

**Wave 2 follow-up (also 2026-05-22, same university session) — 3 of the 4 outstanding items resolved:**

| Item | Status | Finding |
|---|---|---|
| USDCNY NDF ticker | ✅ Resolved | `CCN+1M Curncy` (NAME = "CCY NDF OUTRIGHT 1MO"). Tested formats `CNN+1M`, `IRC+1M` are invalid. Clean history 2010-2026 (e.g. 6.78 in 2010, 6.83 in 2026). |
| AUD basis swap ticker | ✅ Resolved | `ADBS3 Curncy` (NAME = "AUD-USD BS 3M (BvL/B) 3Y"). `AUBSC` invalid. |
| CIP basis pricing source quirk | ❌ Not the issue | `EUBS3 BGN Curncy` has the same 2010+2026 `#N/A` pattern as plain `EUBS3`. Pricing source override doesn't help. `EUBS3 ICAP` is invalid. |

**Critical convention re-read on basis swap tickers:** the NAME of `EUBS3 BGN Curncy` is **"EURUSD BS (3M VS 3M) 3Y"** — decoded:
- `BS` = basis swap
- `3M VS 3M` = both legs reset at 3-month frequency (swap convention, fixed)
- **`3Y` = the SWAP MATURITY is 3 YEARS, not 3 months**

So the suffix `n` in `EUBSn Curncy` denotes maturity in YEARS, not tenor in months. `EUBS3` is a **3-year** basis swap, not a 3-month basis swap. `EUBS12` is 12-year (same data-coverage issue). `EUBS24` invalid = likely a 24-year maturity is not quoted. `ADBS3` follows the same "3-year maturity AUD basis swap" convention.

The 2010-and-2026 `#N/A` pattern is now explained: specific multi-year maturities aren't continuously quoted across all calendar dates; quotes appear when there's a primary book / dealer activity at that maturity.

**Updated implication for Phase C:** the **derived path is now even more clearly correct**. To get a clean CIP deviation series at standard short tenors (3M, 6M, 1Y), the answer is:

```
implied_carry = forward_points / divisor / spot * (365 / days_to_tenor)
ois_differential = ois_funding_ccy - ois_base_ccy
cip_deviation_bps = (implied_carry - ois_differential) * 10000
```

Both inputs are in DB after Wave 1 (forwards) and from Sreeram's OIS substrate (OIS curves). No new playbook, no new extraction.

**Still outstanding (very-future-work):**
- BRL NDF aliasing — `BCN+` works (confirmed earlier), `BRL+` is stale; document the convention. No action needed.
- Whether short-tenor CIP basis swap tickers exist (e.g. `EUBS3M Curncy` for a 3M-maturity basis swap rather than 3Y) — not needed for Phase C since the derived path works, but useful if a future tool wants the direct quote for comparison / validation.

### Wave 1 bucket override — temporary FX bucket via env var

**Background:** the FX agent's GCS extraction landed historically in a private bucket (`gs://quantifi-fx-data-sacha`, project `quantifi-fx-agent`), while Sreeram's rates substrate lands in `gs://macro-storage-bucket`. The four pipeline scripts had inconsistent bucket configuration:

- `historical_extractor.py` / `incremental_extractor.py` — already honoured the `GCP_BUCKET_NAME` env var (default `macro-storage-bucket`).
- `push_playbooks.py` / `ingest_parquet.py` — hardcoded `macro-storage-bucket` with no override.

**Wave 1 fix:** added the same `GCP_BUCKET_NAME` env-var fallback to `push_playbooks.py` and `ingest_parquet.py`. Default stays `macro-storage-bucket` so Sreeram's flow is unchanged; the FX flow sets `GCP_BUCKET_NAME=quantifi-fx-data-sacha` at runtime.

**To run FX extraction / ingestion against the FX bucket:**

```bash
# Extraction (on the Bloomberg-enabled machine):
export GCP_BUCKET_NAME=quantifi-fx-data-sacha
python utils/push_playbooks.py
python utils/historical_extractor.py --playbook fx_forwards --playbook spot_fx --playbook fx_crosses --playbook fx_vol

# Ingestion (Mac, in Docker):
docker compose exec rates-agent-dev micromamba run -n macro-env \
  env GCP_BUCKET_NAME=quantifi-fx-data-sacha python ingestion/ingest_parquet.py
```

**Migration plan (deferred):** consolidate FX and rates into a single shared `macro-storage-bucket` once IAM access is granted to the `fx-agent@quantifi-fx-agent.iam.gserviceaccount.com` service account. The env-var override layer will stay (it's also useful for testing / staging environments), but the default `macro-storage-bucket` will hold both rates and FX data going forward.

### Bloomberg 1Y tenor naming — forwards vs vol asymmetry

Bloomberg uses **inconsistent** ticker conventions for the 1-year tenor between forward points and ATM implied vol:

| Asset class | 1Y format | Invalid alternative |
|---|---|---|
| Forward points (`<PAIR><TENOR> Curncy`) | `EURUSD12M Curncy` | `EURUSD1Y Curncy` (invalid) |
| ATM implied vol (`<PAIR>V<TENOR> Curncy`) | `EURUSDV1Y Curncy` | `EURUSDV12M Curncy` (invalid) |

Both verified via direct `BDH` formula on the Bloomberg terminal (2026-05-21).

**Our playbook convention** in response: the `ticker` field carries the BBG quirk (so extraction works), but the `tenor` field stays uniform `"12M"` across asset classes (so cross-asset tools like "what's the carry vs vol at 1Y" can match on the same horizon regardless of asset). Tools that need to derive the BBG ticker from a tenor must therefore do so per-asset-class, not via a global mapping.

**For future asset classes** (NDF vol, FX options smile, swaption vol, etc.) — verify the 1Y format on BBG explicitly before writing the playbook. Don't assume.

### Legacy FX DB state (pre-Phase-A audit, 2026-05-21)

The FX data layer had been ingested in several waves before this branch existed, and the load_audit lineage is mixed. Concretely, before the canonical migration:

| Legacy dataset_name | Tickers | Notes |
|---|---|---|
| `spot_fx` | 6 G10 majors (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF) | Source: `spot_fx.yml` |
| `fx_forwards_1m` | 3 only (AUDUSD1M, USDCAD1M, USDCHF1M) | Partial — the other 3 majors at 1M lived in `fx_forwards_curve` |
| `fx_forwards_curve` | 8 (EURUSD 1W/1M/3M/6M, GBPUSD 1M/3M, USDJPY 1M/3M) | Partial scaffold — overlapping scope with `fx_forwards_1m` |
| `fx_vol` | 6 ATM-1M vol tickers | Scaffold for Phase E |
| `fx_crosses` | 6 G10 crosses (EURGBP, EURJPY, …) | Scaffold for Phase B |
| `macro_risk_proxies` | 6 risk indicators (SPX, VIX, MOVE, DXY, CL1, XAU) | Not strictly FX |

**The 1M forwards smoke test passed by accident** — the carry tool fetched its 6 tickers from `instrument_master` without caring about `load_audit.dataset_name`, so the 3-from-`fx_forwards_1m` + 3-from-`fx_forwards_curve` split was invisible to the tool.

**Phase A canonicalises this** by replacing `fx_forwards.yml` (`playbook_name: fx_forwards_1m`) and the partial `fx_forwards_curve.yml` with a single canonical `fx_forwards.yml` v2.0 (`playbook_name: fx_forwards`, 30 tickers, start 2000-01-03).

**Ingester behavior during the canonical migration (Codex correction):** the ingester's destructive section only deletes `market_data_daily` rows whose `load_audit.playbook_name` matches the *current* playbook_name. So:

- Old `load_audit` rows for `fx_forwards_1m` and `fx_forwards_curve` **stay** for audit history.
- Overlapping `market_data_daily` rows (e.g. EURUSD1M for dates already covered) get their `load_id` superseded by the new canonical load via the (instrument_id, trade_date, field_name) upsert.
- New rows (the 19 new tickers + the 2000-2005 backfill on existing tickers) are added.
- No orphan rows are expected because `fx_forwards_curve`'s entire universe is now absorbed by `fx_forwards` v2.0.

**Other scaffolds** (`fx_vol`, `fx_crosses`, `macro_risk_proxies`) are out of Phase A scope and will be audited / canonicalised in their respective phases (B, B, ?, E).

### Tickers without metadata in DB

If a playbook is updated with new metadata fields after data has already been ingested, run:

```bash
docker compose exec rates-agent-dev micromamba run -n macro-env \
  python utils/refresh_instrument_metadata.py --playbook <stem> --apply
```

This re-syncs `instrument_master.attributes` from the YAML without touching prices or re-running Bloomberg. Added as a utility in PR #178 (commit `ab5468a`).

---

## Decision log

Chronological history of decisions, so a returning contributor can see *why* things are the way they are.

| Date | Decision | Driver |
|---|---|---|
| 2026-05-20 | FX wired across data + UI + manifest layers as four commits | Initial Codex scaffolding on `codex/fx-on-latest-build` |
| 2026-05-21 | Refresh utility added (`utils/refresh_instrument_metadata.py`) to fix FX forward 1M metadata gaps without Bloomberg re-extraction | Readiness gate `--strict-metadata` warned; full re-extraction was overkill |
| 2026-05-21 | FX migrated to the shared widget engine; `FXAgentPage` mirrors `RatesAgentPage` | Sreeram's pattern for `/rates` should apply to `/fx` |
| 2026-05-21 | ADR 0007 written; `Domain.FX` added to the orchestrator closed family | P11 requires an ADR for new domains |
| 2026-05-21 | Phase A scope locked at 2 primitives (`get_fx_forward_curve`, `scan_fx_carry`) + 2 widgets, after dropping `calculate_fx_carry_curve` as likely redundant | Codex review — avoid encyclopedic accumulation |
| 2026-05-21 | This ROADMAP file created | Sreeram suggestion — survive context loss between sessions |
| 2026-05-21 | Phase A step 1 (Bloomberg ticker verification) complete. Long format `<PAIR><TENOR> Curncy` valid for all 30 (6 G10 pairs × 5 tenors). History from 2000-01-03 uniform. Canonical 1Y is `12M`, not `1Y`. | BBG verification by Sacha + ChatGPT-assisted BDH formula in Excel |
| 2026-05-21 | Canonical playbook migration: `fx_forwards.yml` (v1, `playbook_name: fx_forwards_1m`) + `fx_forwards_curve.yml` (partial scaffold) → consolidated into `fx_forwards.yml` v2.0 (`playbook_name: fx_forwards`, 30 tickers, start 2000-01-01). Convention now aligns with `runbook.md` rule "playbook_name must match filename stem". | Codex review — naming convention violation + duplicate scaffold |
| 2026-05-21 | `fx_forwards_curve.yml` deleted from repo (no other references found via `rg fx_forwards_curve`). Its 8 tickers are subsumed by the canonical v2.0 universe. | Discipline: single canonical source of truth per dataset |
| 2026-05-21 | Introduced "Wave 1" — opportunistic data-only batch extraction while Bloomberg is accessible. `spot_fx.yml` v2.0, `fx_crosses.yml` v2.0, `fx_vol.yml` v2.0 land alongside Phase A's forwards. Total Wave 1 footprint: 80 instruments. Strict discipline: data only, no tool/UI/domain work beyond Phase A. | Sacha at BBG terminal — exploit access window for data not yet decision-locked |
| 2026-05-21 | `fx_vol.yml` v2.0 keeps canonical filename (vs new `fx_vol_atm.yml`) per repo convention "playbook_name matches filename stem". Added `smile_point: "ATM"` metadata on every row so the future smile playbook (RR/BF, Phase E) can coexist cleanly. | Codex review |
| 2026-05-21 | Wave 1 ticker verification (32 new tickers) complete on BBG. All 32 valid from 2000-01-03. **Asymmetric 1Y naming discovered:** forward points use `12M`, ATM vol uses `V1Y`. Tickers in `fx_vol.yml` corrected from `V12M` → `V1Y`. Internal `tenor` field kept as `"12M"` for cross-asset tool consistency. | BBG `BDH` formula verification |
| 2026-05-22 | Bucket override: `push_playbooks.py` and `ingest_parquet.py` gained `GCP_BUCKET_NAME` env-var support (matching the extractors' existing convention). FX Wave 1 runs against `gs://quantifi-fx-data-sacha` via `export GCP_BUCKET_NAME=quantifi-fx-data-sacha`; rates / Sreeram's flow unchanged (default still `macro-storage-bucket`). Long-term plan: consolidate into one bucket once IAM is sorted. | Permission denied on `macro-storage-bucket` for `fx-agent@quantifi-fx-agent.iam` SA during Wave 1 prep |
| 2026-05-22 | Wave 1 Bloomberg extraction ✅ — 548,609 rows across 4 playbooks pushed to `gs://quantifi-fx-data-sacha` in ~7 minutes. Postgres ingestion pending. | Sacha at university Bloomberg terminal |
| 2026-05-22 | Wave 2 discovery complete — findings recorded under "Wave 2 — Bloomberg discovery findings". Key conventions: NDFs are quoted as outright (not forward points), vol smile (RR/BF, 25-delta and 10-delta) is available across G10 pairs, CIP basis swap direct tickers are fragile (stale/invalid for many pairs) — Phase C should derive CIP from OIS + forwards instead. | BBG verification in Excel from university terminal |
| 2026-05-22 | Wave 2 follow-up (same session) — USDCNY NDF ticker resolved (`CCN+1M Curncy`, outright, full 2010-2026 history). AUD basis ticker resolved (`ADBS3 Curncy`). **Bigger discovery: the `EUBSn` convention is maturity-in-YEARS, not tenor-in-months** — `EUBS3` is a 3-year basis swap, `EUBS12` is 12-year, etc. This re-explains the patchy data coverage and reinforces the derived-from-OIS approach for Phase C CIP. | Decoded NAME field `EURUSD BS (3M VS 3M) 3Y` of `EUBS3 BGN Curncy` |
| 2026-05-22 | Wave 1 Postgres ingestion ✅ — 548,609 rows ingested into `macro_data.market_data_daily` (loads 17–20). All 4 playbooks SUCCESS. Readiness gate `--strict-metadata` passes 21/0/0. DB sanity snapshot confirmed: 30 forwards / 9 spot / 11 crosses / 30 vol with uniform 2000-01-03 → 2026-05-22 coverage and full 5-tenor × 6-pair matrix on forwards and vol. | After fixing ADR 0003 schema delta, max_locks_per_transaction bump, and `GCP_BUCKET_NAME` env var |
| 2026-05-22 | `docker-compose.yml` tsdb service gained a `command:` override setting `max_locks_per_transaction=16384`. Persistent across volume recreation; future devs won't hit the "out of shared memory" OOM that blocked the first ingestion attempt. | Local fix during Wave 1 ingestion, now infrastructure |
| 2026-05-22 | Phase A step 4 — audit of `calculate_fx_carry` across all 5 tenors surfaced a silent-fallback regression on the 12M tenor (annualisation factor was 12× too large because the missing 12M entry in `_tenor_days_from_config` fell through to the 1M default). Fixed: added `tenor_12m_days: 252` convention, replaced the silent fallback with an explicit `ValueError`, and tightened the schema to `Literal["1W","1M","3M","6M","12M"]`. Verified EURUSD 12M annualised carry of +1.43% (was a ghost +17% before). | Codex review caught the silent fallback as a "fail loud" violation |
| 2026-05-22 | Phase A step 5 — `get_fx_forward_curve` primitive shipped. First FX primitive that consumes the full Wave 1 substrate (G10 forwards depth + spot) and delegates rolling z-score / percentile / range to `shared.analytics.levels.compute_level_metrics` — same generic operator the rates `yield_levels` tool uses. New helper module `fx_agent/forwards/_shared.py` carries the JPY-aware divisor and tenor-day lookup; `fx_carry` was refactored to import from it (zero behaviour change, single source of truth for conventions across forwards tools). | Codex "primitives vs operators" architectural discipline |
| 2026-05-23 | Phase A step 6 — `calculate_fx_carry` extended into a proper scanner instead of duplicating into a separate `scan_fx_carry`. New params: `rank_by`, `top_n`, `lookback_days`, `field_name`. Each output row now carries a rolling 252-day z-score / percentile / range on its OWN `carry_annualized_pct` historical series. Three Codex garde-fous enforced: historical carry joined per `trade_date` (no lookahead bias), current snapshot uses the LAST common spot+forward date, and `lookback_days` is the DB fetch window only (the z-score window stays in config). Z-score is on `carry_annualized_pct` (carry richness) rather than on `forward_points_spot_units` (curve stretchedness) — different question, documented in config.yaml. | Codex review: avoid duplicate tool surfaces; extend the existing primitive |
| 2026-05-24 | Phase A step 7a — wired `get_fx_forward_curve` everywhere (MCP server, FastAPI route, manifest entry), and updated the `calculate_fx_carry_tool` MCP signature + API endpoint to expose the new scanner params. Without this, the step-5 compute file was an orphan unreachable from the Copilot routing stack and the dashboard. Manifest descriptions rewritten for retrieval-quality matching. | Codex flag: an unwired compute file is not a primitive |
| 2026-05-24 | Phase A step 7b — three standalone-CLI test runners (same pattern as `tests/test_fx_data_readiness.py`) covering the regressions and contracts Codex's step-7 review demanded: 22 assertions across `test_fx_carry_compute.py` (9), `test_fx_forward_curve_compute.py` (8), `test_fx_tools_wiring.py` (5). All assertions pin failures we would have shipped if the bugs of steps 4 / 6 came back. | Codex review: tests must catch known-fixed bugs, not be aspirational |
| 2026-05-24 | Phase A step 8 — UI: parameterized `fx_carry` widget (tenor / rank_by / top_n / lookback) and new `fx_forward_curve` parameterized widget. Per Codex's UI discipline ("upgrade the widget id, do not duplicate"), kept widget id `fx_carry` so legacy localStorage layouts survive; widget-side defaults preserve pre-upgrade behaviour. Both widgets self-fetching via per-instance hooks (no longer rely on `FXDataProvider` context, which is now reserved for the page-level `fx_spot_snapshot` + `fx_scanner` that don't take params). | Codex discipline: one widget per primitive, no duplicates |
| 2026-05-24 | Phase A wrap — ROADMAP refresh: status line moved from "step 4 next" to "8/8 complete"; stale "pre-aggregated only in V1" copy in `defaults.ts` and `FXDataProvider.tsx` updated to reflect that fx_carry / fx_forward_curve are now parameterized. Branch ready for stacked PR (Depends on #178) and manual `/fx` smoke. | Codex independent validation: typecheck / build / test:build / 3 test runners / readiness gate / lint / live API smokes all green |
| 2026-05-24 | **Maximaliste vision locked** — Tier 1 + Tier 2 = ~28 tools across Phases A → E + G + H. Quality framing ("serious FX" = conventions + primitives + asset-agnostic operators + workflows + tests + ADR-when-real). Codex validated G1/G2/G3 guardrails: G1 freezes in-review PRs, G2 forbids asset-class refs in `shared/operators/`, G3 defers ADR 0008 until vol/NDF have substance. Tier 3 (F macro indices, I CFTC positioning, J PPP/REER) deferred — build on concrete demand only. | Sacha asked "what does serious FX look like in this architecture?" Codex synthesised the maximaliste target + the 3 guardrails to keep it disciplined. |
| 2026-05-24 | **Phase B architectural Qs locked** (5/5) — (1) extend `spot_fx.yml`, not separate `spot_fx_em.yml`; (2) `calculate_fx_panel` returns typed `Panel` via `shared.artifacts.types.Panel` (Shape C); (3) `scan_fx_spot` already market_scope-aware via SQL filter — zero code change; (4) extend `test_fx_data_readiness.py`, not new test file; (5) defer UI — ship data + scanner + panel primitive first. Per-instrument start_date also LOCKED OUT — uniform EM start_date, no exceptions. | Codex Q&A pass: each decision pre-empts a future architectural divergence between FX and rates. |
| 2026-05-25 | **EM Bloomberg verification done** at university terminal. USDTRY = Scenario A (clean back-adjusted across 1 Jan 2005 redenomination — 31/12/2004=1.3435, 03/01/2005=1.3475, 28/02/2005=1.2830 — no 1,000,000× discontinuity). All 9 EM pairs (MXN, ZAR, TRY, BRL, PLN, HUF, KRW, IDR, PHP) have PX_LAST from ≤ 2000-01-03. | ChatGPT-assisted BBG HP verification, structured prompt with explicit decision tree (3 scenarios → uniform decision). |
| 2026-05-25 | **EM start_date LOCKED = 2000-01-01** for all 9 pairs. Universe intact (no drop of TRY, no shift of all EM to 2005). Fail-loud ingestion required — pipeline crashes rather than silent-skip if any of the 9 tickers has no PX_LAST at the retained window. | Direct consequence of the BBG verification result. |
| 2026-05-25 | **Phase B branch created.** `codex/fx-data-universe-phase-b` stacked on `codex/fx-data-universe-extension` (PR #184 still in review-pending, G1 respected — no commits added to it). This ROADMAP commit is the first commit on the new branch, porting the maximaliste plan from session-only memory into the repo as durable documentation. | Codex G1 — separate stacked branch for every meaningful update once a PR is in review-pending state. |
| 2026-05-25 | **Phase B scope tightened to one primitive** during ROADMAP diff review. Codex flagged a scope-creep risk: the initial draft listed 6 tools (`calculate_fx_panel` + returns / drawdown / realized vol + implied yield diff + carry basket) all under Phase B. Re-split into B-core (THIS PR — `calculate_fx_panel` only), B-followup (returns / drawdown / realized vol, separate stacked PR — no extra substrate), B+ (implied yield diff + carry basket, deferred until EM forwards substrate is extracted). The 6 tools stay in the maximaliste plan, but only 1 ships in this PR. Also corrected `shared.types.Panel` → `shared.artifacts.types.Panel` (actual repo path). | Keep PRs short, single-primitive, easy to review — same discipline as Phase A step splits. |
| 2026-05-25 | **Phase B follow-up shipped** on `codex/fx-data-universe-phase-b-followup` (stacked on `codex/fx-data-universe-phase-b`). 3 single-pair derived primitives — `get_fx_returns_series` (log-returns, daily/weekly/monthly, RATIO), `calculate_fx_drawdown` (running DD + peak/trough/recovery, RATIO ≤ 0), `get_fx_realized_vol` (rolling std × sqrt(252), PERCENT). Architecture: extend `shared.analytics.fx_fetch.py` with `fetch_fx_spot_series` single-pair helper (mirror `rates_fetch.fetch_single_tenor`); each tool follows the 4-file pattern of `yield_levels` exactly (current_metrics + canonical `TimeSeries`); ZERO SQL ad-hoc per tool (all loading through `shared/analytics/fx_fetch.py` per Codex's panel-first discipline). MCP + API GET routes + manifest entries wired. 33 targeted-test assertions PASS (9+9+9 compute + 15 wiring + readiness gate 28/0/0 no regression). Doc nit on `fx_fetch.py` market_scope/fx_family clarification (Codex's #189 skim backlog) resolved in commit 1. | Phase B follow-up plan locked 2026-05-25 with Codex: panel-first discipline, no ad-hoc SQL per tool, mirror yield_levels shape. |
| 2026-05-25 | **TimeSeriesUnits.PRICE** added to the closed enum during Phase B core. Used by `calculate_fx_panel`'s typed Panel for FX spot levels (closed enum had only PERCENT/BPS/Z_SCORE/RATIO/PCT_RANK/FACTOR_LEVEL/COUNT — none fits cleanly for an absolute price level). Non-breaking extension (no exhaustive enumeration over the enum anywhere). Asset-agnostic — equally usable for equity / commodity prices. | Closed-enum extension is the explicit Sreeram pattern for adding a new unit; documented in the enum itself. |
| 2026-05-25 | **BBG batch (Wave 2) shipped** on `codex/fx-data-universe-bbg-batch` (stacked on PR #191). Discovery + extraction completed at the university Bloomberg terminal (ChatGPT-assisted, 6 substrates ~75 min total). 6 ingestion commits + 1 fix commit on the branch. Substrates ingested: (1) g10_forwards_long (12 G10 × {2Y, 5Y} extending fx_forwards.yml v3.0 → 42 instruments via Codex Option A combine-with-existing for sanity gate), (2) ndf (25 NDFs in new fx_ndf.yml, outright unit, BCN+1W flagged as low-liquidity), (3) em_forwards (30 EM deliverable in new fx_em_forwards.yml — MXN/ZAR/TRY/PLN/HUF/PHP), (4) em_vol (55 EM/NDF-currency ATM vol extending fx_vol.yml v3.0 → 85 total; CNH/INR flagged vol_only), (5+6) vol_smile (156 = 72 G10 + 84 EM smile in new fx_vol_smile.yml — single playbook per Codex, market_scope discriminator). New generic converter `utils/manual_bbg_xlsx_to_parquet.py` (substrate-parameterised, replaces per-substrate scripts). 1.86M total rows ingested. | One-shot BBG batch unlocks Phase B+ / D / E ATM / E smile substrates simultaneously — minimises Bloomberg terminal trips. |
| 2026-05-25 | **calculate_fx_carry market_scope param added** (Codex Option B) after substrate 3 (em_forwards) silently widened the tool from 6 to 12 rows. New closed Literal `FXCarryMarketScope = "G10" \| "EM" \| "ALL"` with default "G10" (Phase A behaviour preserved). SQL filter `attributes->>'fx_family' = ANY(:fx_families)` on the forwards side; closed map in compute.py mirrors the Pydantic Literal in schemas.py (fail-loud at both boundaries). NDFs (fx_family='EM_NDF') explicitly excluded — separate compute path coming Phase D. 4 new tests (default G10, EM scope, ALL scope, invalid Pydantic). MCP + API wrappers + manifest entry updated. | Closes Codex's "EM carry/carry basket as explicit B+ decision" rule in code, not just intent. |
| 2026-05-25 | **BBG top-up shipped** on `codex/fx-data-universe-bbg-topup` (stacked on PR #198 = bbg-batch). Mini ~5-min BBG session closing a Phase D gap discovered after PR #198: `calculate_ndf_implied_carry` needs spot ref for the underlying currency; we had NDFs (CCN+ for CNY, IRN+ for INR) but no spot for those underlyings. Substrate `spot_topup` (new in converter, combine-with-existing pattern to pass sanity gate) loads USDCNH (offshore tradable, spot_convention='offshore_tradable', from 2010-08-23), USDINR (composite reference, spot_convention='composite_reference', full 2000+), and USDCNY (onshore PBOC fix, NEW fx_family='EM_SPOT_REFERENCE', spot_convention='onshore_pboc_fix', full 2000+). USDCNY uses the distinct family to flag its non-tradable nature — downstream tools that need tradable-spot-only filter `fx_family != 'EM_SPOT_REFERENCE'`. Test gate `EM_SPOT_PAIRS` extended 9 → 11; per-pair overrides (`EM_PAIR_MIN_DATE_OVERRIDES`, `EM_PAIR_MIN_ROWS_OVERRIDES`) for USDCNH's later start. fx_vol cleanup: removed `vol_only: true` from CNH/INR ATM rows + cleaned DB attribute. load_id=28 SUCCESS, 215,929 rows. **Branch G1-frozen as PR #203** — visibility only, no Sreeram reviewer. | Codex nuance — close the Phase D substrate gap before the tool ships, even if it's a separate mini-session. |
| 2026-05-25 | **BBG WAREHOUSE SEEDING shipped** on `codex/fx-data-universe-bbg-warehouse-seed` (stacked on PR #203). Strategic pivot from "minimum viable substrate" → "BBG data warehouse seeding while terminal is available" — driven by Sacha's intent to build many downstream tools without future surprise data gaps. **Phase 1 discovery** at the terminal validated which BBG ticker formats actually serve data (vs the pre-flight guesses that turned out invalid). Results: ATM extended tenors (VON/V2W/V2M/V9M/V2Y), smile extended tenors (1W/6M), CNH/INR smile (1M/3M/1Y), macro indices (DXY/BBDXY/JPMVXYG7/JPMVXYEM), EM extension (SGD/TWD/THB spot+vol+forwards, CLP/COP/PEN spot+vol only, TWD NTN+ NDF) all confirmed valid. CFTC positioning (IMM/USCF/CFTC families) + BIS REER all formats invalid → archive-only, Phase I+J deferred. **Phase 2 extraction** produced 8 XLSX files (~268 net-new instruments). **Mac-side ingestion pipeline:** 9 commits — 1 converter extension (8 new SubstrateConfigs) + 8 substrate ingestion commits (load_ids 29-36). Each substrate uses the combine-with-existing pattern (Codex Option A) to pass the ingester's 80% sanity gate without per-substrate playbook fragmentation. Total: 638 FX instruments in DB after this session (was 370 pre-session). All 8 substrates verified with readiness gate strict 28/0/0 between commits. Test gate `EM_SPOT_PAIRS` extended 11 → 17 with the 6 new EM warehouse-seed pairs (USDSGD/TWD/THB/CLP/COP/PEN). | Sacha pivot: "je compte en produire des dizaines et des dizaines de tools, mais il me faut la data" — exploit terminal access window proactively for the full Tier-1-and-then-some substrate footprint, not just the next-phase minimum. |
| 2026-05-26 | **FX TRADABILITY warehouse seed shipped** on `codex/fx-tradability-warehouse-seed` (stacked on PR #207). Final BBG-only session adding the bid/ask tradability layer + static convention matrix to the existing 638-instrument FX universe. **Phase 1 discovery** validated PX_BID + PX_ASK availability across all FX families: all 9 verdicts (spot G10, spot EM, forwards G10, forwards EM, NDF, ATM G10, ATM EM, smile G10, smile EM) PASSED. PX_VOLUME returns #N/A for FX OTC across the board — archive-only verdict. G10 cross ATM vol discovery PASSED (5/5 samples). **Phase 2 extraction** produced 11 XLSX files (1 static matrix + 9 bid/ask + 1 cross vol, 871 sheets total). **Mac-side ingestion:** 5 commits — (1) converter refactor with multi-field support + multi-playbook-XLSX support (parses regex `field` group → BBG field_name), (2) 9 bid/ask SubstrateConfigs, (3) static matrix ingester utility, (4) sanity-gate fix (widen combine to all instrument_type), (5) cross_vol_atm + fx_crosses.yml v3.0 alignment + tests. **Critical lesson:** ingester `_delete_existing_playbook_scope` deletes rows from prior loads of the same playbook on the affected instruments. Loads 44+45 (both fx_vol) and 46+47 (both fx_vol_smile) caused atm_vol_em_bidask to wipe atm_vol_g10_bidask's data. Fix: consolidate same-playbook bid/ask into one ingest (load_ids 48+49 restored). Future protocol: multi-substrate bid/ask for the same playbook must be consolidated at parquet-build stage. **DB state after:** 695 FX instruments (was 638 + 57 new: 55 G10 cross vols + 2 G10 cross spots GBPCHF + CADJPY). Bid/ask coverage: 37 spot + 70 forward + 30 ndf + 115 vol (std tenors only) + 156 vol_smile (std tenors only) = 408 instruments with PX_BID + PX_ASK. Static matrix: 638 instruments enriched with name + security_des + market_sector_des. Full FX test suite green: readiness 28/0/0 + 10 test files all PASS. | Sacha's "build many downstream tools / backtests" intent — bid/ask makes backtests cost-aware, distinguishing research-grade from execution-realistic. |

---

## Open questions / blockers

### ✅ RESOLVED: Bloomberg extraction for canonical `fx_forwards` v2.0 (Phase A step 3)

Historical note retained for reference. The extraction sequence below ran successfully on 2026-05-22 in ~7 minutes; ingestion in ~5 minutes after two env fixes (the ADR 0003 schema delta and the `max_locks_per_transaction` bump, now persisted in `docker-compose.yml`). The same sequence is the canonical recipe for any future FX extraction wave.

**Owner:** Sacha (university terminal with Bloomberg + GCP access).

**Status:** the canonical playbook `fx_forwards.yml` v2.0 is committed on this branch. Bloomberg extraction has not yet been run for it.

**Sequence to execute from the university terminal:**

```bash
# 1. Republish the updated playbook to GCS (the extractor reads from GCS)
docker compose exec rates-agent-dev micromamba run -n macro-env \
  python utils/push_playbooks.py

# 2. Bloomberg → GCS parquet (full historical, 2000-01-03 onwards, 30 tickers)
docker compose exec rates-agent-dev micromamba run -n macro-env \
  python utils/historical_extractor.py --playbook fx_forwards

# 3. GCS parquet → Postgres (idempotent upsert)
docker compose exec rates-agent-dev micromamba run -n macro-env \
  python ingestion/ingest_parquet.py

# 4. Re-sync instrument_master.attributes from the playbook YAML
#    (in case some new tickers landed without all the metadata fields)
docker compose exec rates-agent-dev micromamba run -n macro-env \
  python utils/refresh_instrument_metadata.py --playbook fx_forwards --apply

# 5. Strict readiness gate — must pass clean
docker compose exec rates-agent-dev micromamba run -n macro-env \
  python tests/test_fx_data_readiness.py --strict-metadata
```

**Expected result of the readiness gate:** 30 forward tickers present (was 6/3 + 8 across two datasets pre-migration). All optional metadata fields match playbook. No `warn`, no `fail`.

**If anything in steps 2-3 fails:** investigate before re-running. Common pitfalls: Bloomberg session timeout, GCS auth, Postgres row count sanity gate (it refuses to proceed if incoming count is <80% of previous — irrelevant here since we're growing the universe).

**Past resolved blocker (ticker verification):** see [Decision log](#decision-log) entry "Phase A step 1 complete" — all 30 tickers validated on Bloomberg with uniform 2000-01-03 history, canonical 1Y = `12M`.
