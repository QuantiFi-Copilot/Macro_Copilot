# FX Agent — Universe Extension Roadmap

**Living document.** Updated as phases complete. Single source of truth for the FX universe extension work happening on stacked branches above [PR #178](https://github.com/QuantiFi-Copilot/QFin/pull/178) (the FX integration PR).

> If you're picking up this work mid-flight (new contributor, new session, returning after a break), **read this file first** before anything else in `fx_agent/`.

---

## Current status

**Date:** 2026-05-21

**Branch:** `codex/fx-data-universe-extension` — stacked on `codex/fx-on-latest-build` (PR #178, draft).

**Phase in flight:** **Phase A — Cash forwards depth** (step 1 of 8 — awaiting Bloomberg ticker verification).

**Immediate next action:** Sacha verifies Bloomberg ticker formats and historical depth from the university terminal. See [Open questions / blockers](#open-questions--blockers) for the exact template to return.

**Nothing should be coded until that verification is complete.**

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

---

## Phase plan (ordered low risk → high risk)

```
Phase A — cash forwards depth         [LOCKED, awaiting tickers]
Phase B — spot universe + FX panel    [planned]
Phase C — CIP / cross-currency basis  [planned, reads OIS substrate]
Phase D — NDFs                        [planned]
Phase E — FX vol                      [planned, will require ADR 0008]
```

### Phase A — Cash forwards depth — LOCKED scope

Goal: extend forwards from 1M-only to the standard tenor strip (1W, 1M, 3M, 6M, 1Y) across G10 majors, and ship the first two parameterized FX primitives.

**8-step plan (in order):**

1. **Confirm Bloomberg tickers.** Verify exact format (`12M` vs `1Y`) and historical depth for all 6 pairs × 5 tenors = 30 tickers. See [Open questions / blockers](#open-questions--blockers).
2. **Extend `fx_forwards.yml`** for all G10 forward tenors. From 6 tickers (1M-only) to ~30 tickers.
3. **Run extraction → ingestion → readiness gate strict mode.** Must pass clean.
4. **Audit `calculate_fx_carry`** for tenor conventions: annualisation by tenor, day-count basis (ACT/360 for USD-funding, ACT/365 for JPY/GBP), JPY divisor still correct (it is — see Conventions).
5. **Add `get_fx_forward_curve`** primitive: returns the forward curve (all available tenors) for a given pair.
6. **Add `scan_fx_carry`** primitive: cross-sectional carry ranking at a chosen tenor.
7. **Add manifests + tests** (compute / wiring / sql_validation per tool, following Sreeram's rates pattern).
8. **Add UI widgets** — `FXForwardCurveWidget` (parameterised by pair) and `FXCarryScannerWidget` (parameterised by tenor). Only after tools are stable.

**Explicitly OUT of Phase A:**

- `calculate_fx_carry_curve` — likely derivable from `get_fx_forward_curve` + a generic carry operator; drop until we can prove non-redundancy.
- `FXCarryCurveWidget` — same reasoning.

### Phase B — Spot universe + FX panel — planned

Goal: extend spot coverage from 6 G10 majors to G10 (including crosses) + EM majors; introduce the cross-sectional FX panel primitive.

**Scope:**

- New playbook `fx_spot_em.yml` (USDMXN, USDZAR, USDTRY, USDPLN, USDHUF, USDCZK, USDIDR, USDKRW, USDINR, USDBRL, USDCLP, etc.)
- Extend `spot_fx.yml` with missing G10 crosses (NZDUSD, EURGBP, EURJPY, GBPJPY, AUDJPY, AUDNZD)
- `calculate_fx_panel` primitive (loads FX spots into a typed Panel)
- Verify `scan_fx_spot` accepts `market_scope='EM'` / `'G10_CROSSES'` correctly
- 1–2 UI widgets (`FXPanelWidget`)

**Operator gap to flag (not in Phase B but to track):** if a generic `summarize_series` doesn't expose z-score on a Panel, that's an operator extension — done in `shared/operators/`, not as an FX primitive.

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

- New playbooks `fx_vol_atm.yml` (ATM straddle pricing) and `fx_vol_smile.yml` (25-delta RR + BF)
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

---

## Open questions / blockers

### 🟡 BLOCKER: Bloomberg ticker verification (Phase A step 1)

**Owner:** Sacha (university terminal access).

**What to verify:** for each of the 6 G10 pairs × 5 tenors = 30 candidate tickers, confirm:
1. The ticker exists on Bloomberg.
2. The earliest available history date for `PX_LAST`.
3. Whether `1Y` is the canonical format or `12M` (some pairs alias both).

**Tickers to check** (G10 majors):

```
EURUSD{1W,1M,3M,6M,12M-or-1Y} Curncy
GBPUSD{1W,1M,3M,6M,12M-or-1Y} Curncy
USDJPY{1W,1M,3M,6M,12M-or-1Y} Curncy
AUDUSD{1W,1M,3M,6M,12M-or-1Y} Curncy
USDCAD{1W,1M,3M,6M,12M-or-1Y} Curncy
USDCHF{1W,1M,3M,6M,12M-or-1Y} Curncy
```

**Return format:**

```
Field checked = PX_LAST
Historical range checked = 2005-01-01 to latest available

1Y format = [12M or 1Y]   ← the canonical one

1W   -> all 6 pairs OK [earliest start: YYYY-MM-DD]
1M   -> all 6 pairs OK [earliest start: YYYY-MM-DD]
3M   -> all 6 pairs OK [earliest start: YYYY-MM-DD]
6M   -> all 6 pairs OK [earliest start: YYYY-MM-DD]
[12M/1Y] -> all 6 pairs OK [earliest start: YYYY-MM-DD]

Missing/weird:
- [None, or list specific exceptions e.g. "USDCHF12M only from 2008"]
```

**Why this gate matters:** the alternative (guess the format, run extraction, discover mid-pipeline that 1Y doesn't exist for X pairs) wastes a full Bloomberg extraction cycle. Verifying first is 30 minutes and saves a day.
