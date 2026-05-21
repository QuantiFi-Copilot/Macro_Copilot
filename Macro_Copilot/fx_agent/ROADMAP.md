# FX Agent — Universe Extension Roadmap

**Living document.** Updated as phases complete. Single source of truth for the FX universe extension work happening on stacked branches above [PR #178](https://github.com/QuantiFi-Copilot/QFin/pull/178) (the FX integration PR).

> If you're picking up this work mid-flight (new contributor, new session, returning after a break), **read this file first** before anything else in `fx_agent/`.

---

## Current status

**Date:** 2026-05-21

**Branch:** `codex/fx-data-universe-extension` — stacked on `codex/fx-on-latest-build` (PR #178, draft).

**Phase in flight:** **Phase A — Cash forwards depth** (step 3 of 8 — Bloomberg extraction COMPLETE for Wave 1, ingestion to Postgres pending on Mac).

**Immediate next action:** Sacha goes home, runs `ingest_parquet.py` via Docker with `GCP_BUCKET_NAME=quantifi-fx-data-sacha`, then `refresh_instrument_metadata.py` per playbook, then `tests/test_fx_data_readiness.py --strict-metadata` to validate the new universe.

**Steps 1 and 2 ✅ complete** (Bloomberg ticker verification + canonical playbook in repo). **Step 3 partially complete:** Wave 1 Bloomberg extraction ran in ~7 minutes from the university terminal (much faster than the 30-40 min estimate — batched upsert PRs landing in the rebase paid off). 548,609 rows across 4 playbooks landed in `gs://quantifi-fx-data-sacha`. Postgres ingestion pending.

| Playbook | Rows extracted |
|---|---|
| `fx_forwards` | 206,399 |
| `fx_vol` | 204,533 |
| `fx_crosses` | 75,713 |
| `spot_fx` | 61,964 |

**Wave 2 discovery findings recorded below** ([Wave 2 — Bloomberg discovery findings](#wave-2--bloomberg-discovery-findings-not-yet-ingested)) — not yet ingested, but the conventions are now documented for Phase B/C/D/E planning.

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
3. **Run extraction → ingestion → readiness gate strict mode.** Must pass clean. Expected: 21 → 30 forward tickers present, no `warn`, no `fail`.
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

**Outstanding items for future verification sessions:**
- USDCNY NDF correct ticker format (not `USDCNH+1M`)
- BRL NDF aliasing (`BCN+` vs `BRL+`)
- CIP basis ticker formats post-2020 (BBG taxonomy may have changed)
- Whether AUD basis swap has a different ticker convention (perhaps `ADBS<n>`)

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

---

## Open questions / blockers

### 🟡 BLOCKER: Bloomberg extraction for canonical `fx_forwards` v2.0 (Phase A step 3)

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
