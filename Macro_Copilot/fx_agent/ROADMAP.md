# FX Agent — Universe Extension Roadmap

**Living document.** Updated as phases complete. Single source of truth for the FX universe extension work happening on stacked branches above [PR #178](https://github.com/QuantiFi-Copilot/QFin/pull/178) (the FX integration PR).

> If you're picking up this work mid-flight (new contributor, new session, returning after a break), **read this file first** before anything else in `fx_agent/`.

---

## Current status

**Date:** 2026-05-21

**Branch:** `codex/fx-data-universe-extension` — stacked on `codex/fx-on-latest-build` (PR #178, draft).

**Phase in flight:** **Phase A — Cash forwards depth — ✅ COMPLETE (8/8 steps shipped).** Branch pushed on `codex/fx-data-universe-extension` as a draft stacked PR above #178.

**Immediate next action:** PR hygiene + manual `/fx` smoke + Phase B planning (per Codex's step-8 review). Specifically:

1. Open or refresh the stacked PR with `Depends on #178` in the body, request review **after** CI + manual smoke pass.
2. Manual `/fx` smoke in a real browser (not the headless preview that CORS-blocks `:5180`): configure `fx_carry` (tenor / rank_by / top_n / lookback) and `fx_forward_curve` (pair / lookback), verify reset-to-default, verify legacy localStorage layouts (with empty params) still render via widget-side defaults.
3. Phase B planning — start with EM spot/crosses only, verify Bloomberg conventions, update ROADMAP, then ingest. Discipline reminder: data acquisition ≠ product phase; do not ingest NDF / vol smile / CIP into canonical data yet.

**Steps 1–8 ✅ complete.** Wave 1 production ingestion landed cleanly:

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
3. ✅ **Run extraction → ingestion → readiness gate strict mode.** Done 2026-05-22. Extraction ran in ~7 min from university BBG terminal; ingestion landed 548,609 rows across 4 playbooks (loads 17-20, all SUCCESS); readiness gate strict mode passes 21/0/0; 30 forward tickers / 9 spot / 11 crosses / 30 vol present in DB with uniform 2000-01-03 → 2026-05-22 coverage.
4. ✅ **Audit `calculate_fx_carry` across tenors.** Done 2026-05-22 (commit `293c031`). Bug fixed: 12M tenor was silently falling back to 21-day tenor_days → ~12× inflated annualisation. Added explicit ValueError on unsupported tenors and tightened the Pydantic schema to `Literal["1W","1M","3M","6M","12M"]`.
5. ✅ **Add `get_fx_forward_curve` primitive.** Done 2026-05-22 (commit `e241f94`). For one G10 pair, returns one row per tenor with raw forward points, spot-unit forward points, outright, carry bps + ann%, z-score / percentile / range on `forward_points_spot_units`. Cross-tool consistency verified vs `calculate_fx_carry`.
6. ✅ **Carry scanner — extension of `calculate_fx_carry`, NOT a duplicate `scan_fx_carry`.** Done 2026-05-23 (commit `43a9632`). Per Codex's discipline ("one tool, not a duplicate"), `calculate_fx_carry` gained `rank_by` (carry_signed / abs_carry / abs_z_score) + `top_n` + `lookback_days` + per-pair carry-series z-score / percentile / range. Historical carry is built from per-date joined spot + forward (no lookahead); current snapshot uses the last common spot+forward date.
7. ✅ **Manifests + wiring (7a) + targeted tests (7b).** Done 2026-05-24 (commits `e35fb77` + `8a52959`). MCP server registers all 4 FX tools; API exposes `/forward-curve`; manifest entries updated with `pm_overridable` reflecting the new scanner params. 22 targeted assertions across 3 standalone test runners — 12M not inflated, rank_by sort, top_n truncation, latest-common-date alignment, JPY divisor handling on USDJPY, outright consistency, cross-tool numerical consistency, unknown-pair fail-loud, lookback_days bounds.
8. ✅ **Parameterized FX Carry widget + new FX Forward Curve widget.** Done 2026-05-24 (commit `378f233`). Per Codex's discipline ("one widget, not a `FXCarryScannerWidget` duplicate"), `fx_carry` widget upgraded to parameterized (tenor / rank_by / top_n / lookback); new `fx_forward_curve` parameterized widget for the term-structure view. Both self-fetching via per-widget hooks (no longer depend on `FXDataProvider` context); widget-side defaults preserve backward compatibility for legacy localStorage layouts with empty params.

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
