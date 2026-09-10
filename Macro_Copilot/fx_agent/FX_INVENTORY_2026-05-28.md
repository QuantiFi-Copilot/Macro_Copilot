# FX Agent — Inventory snapshot (2026-05-28)

**Purpose.** A dated, reproducible snapshot of the FX agent state — data substrate, tools, playbooks, PR stack, gaps. Per Codex review (2026-05-28): a recap that doesn't include the queries that produced the numbers is "just a narrative photo"; with SQL inline it becomes verifiable.

**How to refresh.** Re-run every SQL block in §3. If a number diverges from this document, the number is current and this document is stale — bump the filename date and rewrite.

**Branch context at snapshot time.**
- Active branch: `codex/fx-phase-f1-fx-panels-scanners` (PR #240, draft, reviewable)
- Last commit on branch: `8d51a09` (ROADMAP doc update)
- Stack base: `codex/fx-phase-c-cip-basis` (PR #239 FROZEN, sign convention BCRX validated 4×)
- Working tree: clean except untracked `.claude/` (local agent state, gitignored upstream)

---

## 1. Architecture in one paragraph

Four sub-agents (`spot` / `forwards` / `ndf` / `vol`) under `fx_agent/` + a single manifest at `manifesto/03_tool_manifest/fx_agent/01_fx_manifest.yml`. Discipline **G2 asset-agnostic** (Codex review 2026-05-27): FX-specific math stays FX-side (BCRX sign convention, FX implied yield differential via spot+forward, smile substrate routing, points-to-spot JPY divisor). Cross-asset generic math (correlation, PCA, regression, rolling stats) consumes typed `Panel` artifacts in `shared/operators/` — currently UNBUILT and tracked as Phase F2 priority. The Phase F1 scanners contain ~50 LOC of rolling z-score + cross-sectional rank each, duplicated 4× → primary candidate for first shared operator extraction.

---

## 2. Stack PRs status

18 FX-related open PRs, all stacked linearly on `build` → `codex/fx-on-latest-build` (#178). No merge can begin until #178 lands on `build`.

```
build
└─ #178  FX integration: data, UI, orchestrator (ADR 0007)             (Open, NOT draft)
   └─ #184  Phase A — forwards depth G10                                (Open, NOT draft)
      └─ #189  Phase B core — EM spot + calculate_fx_panel              (Open, NOT draft)
         └─ #191  Phase B follow-up — 3 derived single-pair             (Draft)
            └─ #198  BBG Wave 2 — 6 new substrates + fx_carry scope     (Draft)
               └─ #203  BBG top-up — APAC spot references               (Draft)
                  └─ #207  Warehouse seeding — FX substrate expansion   (Draft)
                     └─ #228  FX Tradability — bid/ask + cross vols     (Draft)
                        └─ #230  Tradability cleanup                    (Draft)
                           └─ #232  Phase D — NDF tools (3)             (Draft)
                              └─ #233  Phase E1 — ATM vol (4 prim.)     (Draft)
                                 └─ #234  D/E1 compliance follow-up     (Draft)
                                    └─ #235  Phase E2 — smile (3)       (Draft)
                                       └─ #236  Phase E3 — vol carry(2) (Draft)
                                          └─ #237  Phase B+ — iyd+basket(Draft)
                                             └─ #238  helper long_pair  (Draft)
                                                └─ #239  Phase C — xccy basis  ✨FROZEN, NOT draft
                                                   └─ #240  Phase F1 — 3 panels + 4 scanners 🆕 (Draft, reviewable)
```

✨ = sign convention HARD-LOCKED + validated four independent ways · 🆕 = current head

---

## 3. Data substrate (DB)

**Top-level numbers** (as of snapshot, refresh via SQL §3.1):

- **695 FX instruments** in `macro_data.instrument_master`
- **9.15M rows** in `macro_data.market_data_daily`
- **3 fields**: PX_LAST (all 695 instruments) + PX_BID/PX_ASK (410/695 = 59% bid/ask coverage)
- **Date range**: 2000-01-03 → 2026-05-22 (vol_smile substrate from 2001-11-28)

### 3.1. Substrate inventory by (type, family) — SQL

```sql
SELECT instrument_type,
       attributes->>'fx_family' AS fx_family,
       COUNT(*) AS n
FROM macro_data.instrument_master
WHERE instrument_type LIKE 'fx_%' OR instrument_type = 'fx_macro_index'
GROUP BY 1, 2 ORDER BY 1, 2;
```

| `instrument_type` | `fx_family` | n |
|---|---|---:|
| fx_forward | EM_FORWARDS | 40 |
| fx_forward | G10_FORWARDS | 42 |
| fx_macro_index | FX_DOLLAR_INDEX | 2 |
| fx_macro_index | FX_VOL_INDEX | 2 |
| fx_ndf | EM_NDF | 30 |
| fx_spot | EM_SPOT | 17 |
| fx_spot | EM_SPOT_REFERENCE | 1 |
| fx_spot | G10_CROSSES | 13 |
| fx_spot | G10_SPOT | 9 |
| fx_vol | EM_FX_VOL | 140 |
| fx_vol | G10_CROSSES_FX_VOL | 55 |
| fx_vol | G10_FX_VOL | 60 |
| fx_vol_smile | EM_FX_VOL_SMILE | 164 |
| fx_vol_smile | G10_FX_VOL_SMILE | 120 |

### 3.2. Totals by `instrument_type` — SQL

```sql
SELECT instrument_type, COUNT(*) AS n
FROM macro_data.instrument_master
WHERE instrument_type LIKE 'fx_%' OR instrument_type = 'fx_macro_index'
GROUP BY 1 ORDER BY 2 DESC;
```

| `instrument_type` | n |
|---|---:|
| fx_vol_smile | 284 |
| fx_vol | 255 |
| fx_forward | 82 |
| fx_spot | 40 |
| fx_ndf | 30 |
| fx_macro_index | 4 |
| **Total** | **695** |

### 3.3. Tenor coverage by substrate — SQL

```sql
SELECT instrument_type, tenor, COUNT(*) AS n
FROM macro_data.instrument_master
WHERE (instrument_type LIKE 'fx_%' OR instrument_type = 'fx_macro_index')
  AND tenor IS NOT NULL
GROUP BY 1, 2 ORDER BY 1, 2;
```

| Substrate | Standard tenors | Extended tenors |
|---|---|---|
| fx_forward | 1W·1M·3M·6M·12M × 14 pairs | 2Y·5Y × 6 G10 pairs |
| fx_ndf | 1W·1M·3M·6M·12M × 6 NDF pairs | — |
| fx_vol (ATM) | 1W·1M·3M·6M·12M × 34 pairs | ON·2W·2M·9M·2Y × 17 pairs |
| fx_vol_smile | 1M·3M·12M × 60 ; 1W·6M × 52 | — |

Note: `fx_spot` and `fx_macro_index` have no tenor (spot/index = level series).

### 3.4. Row counts + date range + field count per substrate — SQL

```sql
SELECT im.instrument_type,
       COUNT(*) AS n_rows,
       MIN(d.trade_date) AS min_date,
       MAX(d.trade_date) AS max_date,
       COUNT(DISTINCT d.field_name) AS n_fields
FROM macro_data.market_data_daily d
JOIN macro_data.instrument_master im ON d.instrument_id = im.instrument_id
WHERE im.instrument_type LIKE 'fx_%' OR im.instrument_type = 'fx_macro_index'
GROUP BY 1 ORDER BY 2 DESC;
```

| `instrument_type` | rows | min_date | max_date | n_fields |
|---|---:|---|---|---:|
| fx_vol_smile | 3,357,369 | 2001-11-28 | 2026-05-22 | 3 |
| fx_vol | 2,902,774 | 2000-01-03 | 2026-05-22 | 3 |
| fx_forward | 1,494,259 | 2000-01-03 | 2026-05-22 | 3 |
| fx_spot | 795,870 | 2000-01-03 | 2026-05-22 | 3 |
| fx_ndf | 578,549 | 2000-01-03 | 2026-05-22 | 3 |
| fx_macro_index | 26,083 | 2000-01-03 | 2026-05-22 | 1 |
| **Total** | **9,154,904** | — | — | — |

### 3.5. Field coverage — SQL

```sql
SELECT d.field_name,
       COUNT(DISTINCT d.instrument_id) AS n_instruments,
       COUNT(*) AS n_rows
FROM macro_data.market_data_daily d
JOIN macro_data.instrument_master im ON d.instrument_id = im.instrument_id
WHERE im.instrument_type LIKE 'fx_%' OR im.instrument_type = 'fx_macro_index'
GROUP BY 1 ORDER BY 3 DESC;
```

| field | n_instruments | n_rows |
|---|---:|---:|
| PX_LAST | 695 | 4,120,417 |
| PX_ASK | 410 | 2,517,552 |
| PX_BID | 410 | 2,516,935 |

Bid/ask coverage = 410/695 = **59%** (heritage from Phase Tradability, PR #228 + #230).

---

## 4. Tools (30 in manifest, all `status: built`)

```
manifesto/03_tool_manifest/fx_agent/01_fx_manifest.yml
```

### 4.1. Counts by sub-agent / category

| Sub-agent | Tools | Categories |
|---|---:|---|
| `spot` | 6 | snapshots(1) · screening(1) · panel(1) · derived(3) |
| `forwards` | 9 | classify(2) · snapshots(2) · derived(1) · panel(2) · scanner(2) |
| `ndf` | 3 | snapshots(1) · derived(1) · screening(1) |
| `vol` | 12 | snapshots(5) · screening(1) · derived(2) · panel(1) · scanner(2) · smile_snapshot(1) |
| **Total** | **30** | snapshots(10) · derived(7) · panel(4) · scanner(4) · screening(3) · classify(2) |

### 4.2. Full list by sub-agent

**`fx_agent/spot/tools/`** (6)
- `get_fx_spot_level` (snapshot)
- `scan_fx_spot` (screening)
- `calculate_fx_panel` (Panel)
- `get_fx_returns_series` · `calculate_fx_drawdown` · `get_fx_realized_vol` (derived)

**`fx_agent/forwards/tools/`** (9)
- `calculate_fx_carry` · `get_fx_forward_curve` (classify)
- `get_fx_implied_yield_differential` · `get_fx_cross_currency_basis` ✨ (snapshots)
- `get_fx_carry_basket` (derived strategy index)
- `calculate_fx_forwards_panel` 🆕 · `calculate_fx_basis_panel` 🆕 (Panels)
- `scan_fx_cross_currency_basis` 🆕 · `scan_fx_implied_yield_differential` 🆕 (scanners)

**`fx_agent/ndf/tools/`** (3)
- `get_fx_ndf_outright` (snapshot) · `calculate_fx_ndf_implied_carry` (derived) · `scan_fx_ndf_carry` (screening)

**`fx_agent/vol/tools/`** (12)
- `get_fx_atm_vol_level` · `get_fx_vol_term_structure` · `get_fx_risk_reversal` · `get_fx_butterfly` · `get_fx_vol_smile` · `get_fx_vol_calendar_spread` (snapshots, 6)
- `scan_fx_vol` (screening)
- `get_fx_vol_z_score` · `get_fx_vol_risk_premium` (derived)
- `calculate_fx_vol_panel` 🆕 (Panel)
- `scan_fx_calendar_spread` 🆕 · `scan_fx_vol_skew` 🆕 (scanners)

✨ Phase C (sign convention BCRX validated 4×) · 🆕 Phase F1 (PR #240)

### 4.3. Recompute these numbers

```python
import yaml
m = yaml.safe_load(open('manifesto/03_tool_manifest/fx_agent/01_fx_manifest.yml'))
tools = m['tools']
from collections import Counter
by_sub = Counter(t['sub_agent'] for t in tools)
by_cat = Counter(t['category'] for t in tools)
print('Total:', len(tools))
print('By sub_agent:', dict(by_sub))
print('By category:', dict(by_cat))
```

---

## 5. Playbooks (`fx_agent/playbooks/`)

**9 active playbooks + 2 cloud-sync duplicates** (the `* 2.yml` / `* 3.yml` are macOS sync artifacts of a legacy v1 file that's been superseded by `fx_forwards.yml`).

| Playbook | Substrate | Wave / version | Coverage |
|---|---|---|---|
| `spot_fx.yml` | fx_spot G10+EM | v5 | 9 G10 + 17 EM + 6 LATAM/APAC top-up |
| `fx_crosses.yml` | fx_spot crosses | v3 (Tradability) | 13 G10 crosses |
| `fx_forwards.yml` | fx_forward G10 | v3 (Phase B BBG) | 6 G10 × 7 tenors (1W/1M/3M/6M/12M + 2Y/5Y) |
| `fx_em_forwards.yml` | fx_forward EM | v2 (Warehouse) | 8 EM × 5 tenors |
| `fx_ndf.yml` | fx_ndf | v2 (Warehouse) | 6 NDF × 5 tenors |
| `fx_vol.yml` | fx_vol ATM | v6 (Tradability) | G10+crosses+EM + extended tenors |
| `fx_vol_smile.yml` | fx_vol_smile | v2 (Warehouse) | 25Δ + 10Δ RR/BF G10+EM 1W/1M/3M/6M/12M |
| `fx_macro_indices.yml` | fx_macro_index | v1 (Warehouse) | DXY, BBDXY, JPMVXYG7, JPMVXYEM |
| `macro_risk_proxies.yml` | divers | — | cross-asset risk proxies |
| ⚠️ `fx_forwards_curve 2.yml` | — | (cloud-sync dupe) | superseded by fx_forwards.yml |
| ⚠️ `fx_forwards_curve 3.yml` | — | (cloud-sync dupe) | superseded by fx_forwards.yml |

The 2 dupes are gitignored by `e6e3e6a` and not pytest-collected. Dedicated cleanup script is task #55 (separate from any FX PR).

---

## 6. Workflows status

`fx_agent/workflows/` does not exist as executable tools. Workflow names appearing in manifest `workflows:` blocks (`fx_carry_monitor`, `fx_vol_workspace`, `fx_basis_monitor`, `fx_panel_workspace`, `fx_correlation_monitor`, etc.) are **UI hooks for future surfacing**, not runtime DAGs. **Phase G** ("workflows substrate") of the ROADMAP remains planned, not started.

---

## 7. Tests status

Phase F1 (latest) — 35/35 PASS live-DB (2026-05-27, 21:30 local):

```bash
# From Macro_Copilot/ — standalone scripts, NO pytest dependency:
python3 tests/test_fx_vol_panel_compute.py        # 9/9 PASS
python3 tests/test_fx_forwards_panel_compute.py   # 10/10 PASS
python3 tests/test_fx_basis_panel_compute.py      # 8/8 PASS (BCRX sign sanity)
python3 tests/test_fx_phase_f1_scanners_compute.py # 8/8 PASS
```

Prior FX phases ship their own 3-test triplets (compute + wiring + sql_validation) + PR15 parity fixtures.

---

## 8. Gaps + Phase F2+ candidates

Ordered by priority (Codex review 2026-05-28 + cross-asset reuse value):

| Priority | Subject | Why |
|---|---|---|
| **HIGH** | `shared/operators/rolling_zscore_panel` | Deduplicates ~50 LOC × 4 Phase F1 scanners + vol_z_score + vol_scanner = 6 FX-side call sites. Best utility/risk ratio per Codex. |
| **HIGH** | `shared/operators/correlation_matrix` | Unlocks Panel artifact value — without it, FX panels are "supplies waiting for consumers". Also serves Rates `sovereign_yield_panel`. |
| **HIGH** | `shared/operators/panel_pca` | Crown jewel for cross-sectional regime classification (USD beta loading, vol regime PCA). Generic Panel → typed PCA output. |
| MED | `scan_fx_vol_risk_premium` | Dropped from Phase F1 plan (needs realized-vol composition). |
| MED | Phase G workflows substrate | Required for the manifest's `workflows:` blocks to become executable. |
| LOW | API routes for Phase F1 7 tools | Wait for UI consumer to materialise. |
| LOW | EM CIP basis extension | Blocked: rates_agent has no EM OIS curves. |
| LOW | Cleanup 78 cloud-sync `* [2-9].py` dupes | Gitignored already; task #55 (separate authorized script). |

**Architecture rule.** All `shared/operators/*` work goes on a **separate branch from `build`**, not stacked on the FX chain. Sreeram must be pinged before — `shared/` is shared territory and additions should be strictly additive (new files, no modification of existing operators).

---

## 9. How to refresh this document

1. Re-run every SQL block in §3 → update §3 tables if any number changed.
2. Re-run the Python in §4.3 → confirm manifest counts.
3. `ls fx_agent/playbooks/` → check §5 hasn't gained / lost playbooks.
4. `gh pr list --search "fx in:branch"` (or curl GraphQL) → §2 stack diagram still accurate.
5. If any §8 priority is built, move it out of "gaps" and add a new entry.
6. Bump the filename date and the **Branch context** block at top.

Document is a **snapshot, not a living doc**. The living doc remains `fx_agent/ROADMAP.md`. This file freezes a precise state for cross-reference when Sreeram or future-Sacha asks "what was in DB / on the manifest / in the stack on date X?".
