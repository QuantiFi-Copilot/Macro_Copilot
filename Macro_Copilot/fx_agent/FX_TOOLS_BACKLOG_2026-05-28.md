# FX Tools Backlog — 2026-05-28

**Status: ROADMAP / SPEC ONLY. Do NOT build now.** The FX stack already has 19 open PRs + 30 tools. Adding more backend tools worsens the merge. Feature work is **frozen**; priority is review/merge prep (see `MERGE_PLAYBOOK_2026-05-28.md`). This doc captures the prioritized candidate tools so the work is scoped when a build window opens (ideally **post-merge**).

Companion: `ROADMAP.md` (living), `FX_INVENTORY_2026-05-28.md` (current 30-tool snapshot), `MERGE_PLAYBOOK_2026-05-28.md`.

---

## Build discipline (the line that decides what's a tool here vs not)

- **FX-specific primitive → builds in `fx_agent/`**: carry/vol, basis, skew, vol index, macro risk regime — tools that know FX finance concepts (pairs, RR/BF, CIP basis, dollar index).
- **Asset-agnostic → lives in `shared/operators/` (Sreeram's), we COMPOSE, never duplicate**: correlation, PCA, rolling regression/beta, z-score, rank, trade construction/evaluation (the backtest engine).

So explicitly **NOT** on this list: `fx_correlation_matrix`, `fx_pca`, `fx_beta_to_dollar`, `fx_backtest_engine`. Those are FX panels piped into shared operators.

---

## TIER 1 — highest value, build first (post-merge)

### 1. `get_fx_carry_to_risk`
- **Does**: carry ÷ ATM implied vol = risk-adjusted carry; ranks the cross-section.
- **Substrate**: `fx_forward` (carry) + `fx_vol` ATM, joined per pair at one tenor.
- **Output**: per-pair `{carry_annualized_pct, atm_vol_pct, carry_to_risk_ratio, ratio_z_score_252d, rank}`.
- **Methodology**: `ratio = carry_annualized_pct / atm_vol_pct`; same tenor both legs; long-pair-carry sign via the existing `fx_agent/forwards/_shared.py` helper (no new sign math).
- **Why**: THE carry-quality metric every desk uses. We have carry AND vol but not the ratio. Directly completes the carry scanner.

### 2. `get_fx_vol_index`
- **Does**: snapshot + rolling stats of an FX macro index (the FX "VIX" / dollar index).
- **Substrate**: `fx_macro_index` (JPMVXYG7, JPMVXYEM, DXY, BBDXY) — **4 indices ingested, ZERO tools today**.
- **Output**: `{level, 1d/1w/1m change, z_score_252d, percentile_252d, range, regime_label}`.
- **Methodology**: structural snapshot + 252d rolling stats; regime_label from percentile bands (config-locked).
- **Why**: unlocks an ingested substrate with no coverage; a cross-FX vol/dollar gauge.

### 3. `classify_fx_risk_regime`
- **Does**: risk-on / risk-off / stress / neutral label for the FX tape.
- **Substrate**: `fx_macro_index` (DXY trend + JPMVXYG7 percentile).
- **Output**: `{regime_label, dxy_trend_sign, jpmvxy_percentile, rule_fired}` — **drivers surfaced, not a black box**.
- **Methodology**: **transparent rule table, all thresholds in `config.yaml`** (e.g. JPMVXY pct > 80 → stress; DXY 1m up & JPMVXY rising → risk_off; …). The output must show WHICH rule fired and the input values.
- **Why**: "are we risk-on or risk-off" is a PM's #1 macro-context question. **Caveat (must hold): transparent classification, no opaque scoring.**

---

## TIER 1.5 — UI-friendly (render as curves, like Forward Curve)

### 4. `get_fx_basis_term_structure`
- **Does**: CIP basis across the tenor strip (1W→12M) for one V1 pair = the funding-stress curve.
- **Substrate**: `fx_forward` + OIS across tenors (12M→1Y OIS alias as in `cross_currency_basis`).
- **Output**: rows per tenor `{basis_bps, fx_iyd_pct, ois_diff_pct, z_score}`.
- **Methodology**: same basis math as `cross_currency_basis`, looped across tenors.
- **Why**: turns the point-tenor basis into a **term-structure curve** — funding stress by maturity, very visual.

### 5. `get_fx_skew_term_structure`
- **Does**: RR (and BF) across the tenor strip = how skew/convexity matures.
- **Substrate**: `fx_vol_smile` (RR/BF at 25Δ/10Δ across tenors).
- **Output**: rows per tenor `{rr_vol_pts, bf_vol_pts, z_scores}`.
- **Methodology**: pull RR/BF across tenors; structural, no new math.
- **Why**: options-desk staple; very visual; complements `vol_term_structure` (ATM) and the one-tenor `risk_reversal`/`butterfly`.

---

## TIER 2 — good candidates, more care / lower priority

| Tool | Substrate | Note |
|---|---|---|
| `get_fx_vol_surface` | fx_vol + fx_vol_smile | **ON HOLD** — full 2D surface (tenors × deltas). OK as a simple table/Panel V1, but **NO new `Surface` artifact type without design review**. |
| `get_fx_vol_cone` | fx_spot returns | realized-vol percentile cone by horizon; classic options view. |
| `get_fx_vrp_term_structure` | fx_vol + fx_spot | VRP (implied − realized) across tenors = the vol-premium curve (we only have one-tenor VRP). |
| `get_fx_butterfly_term_structure` | fx_vol_smile | BF across tenors = convexity term structure; pairs with skew term structure. |
| `get_fx_cross_triangulation` | fx_spot majors + crosses | implied cross (e.g. EURGBP via EUR/GBP-USD) vs quoted cross → dislocation/arb flag. |

---

## TIER 3 — blocked / out of data (do NOT attempt now)

| Tool | Blocker |
|---|---|
| EM cross-currency basis | no EM OIS curves in rates_agent |
| `fx_positioning` (CFTC) | data not ingested (deferred — ticker format unresolved) |
| `fx_ppp_reer` (valuation) | BIS REER not ingested (deferred) |
| `fx_synthetic_cross_vol` | needs correlation → shared operator (compose, don't build) |

---

## Carry backtest — research direction, NOT product truth (yet)

The `get_fx_carry_basket` strategy index + the sweep run on 2026-05-28 (EM 12M L/S best, Sharpe ~0.87; G10 long-only > G10 long/short; EM long/short > EM long-only) is a useful **research direction**, but it is **paper** (no transaction costs / slippage, 21d rebalance, equal-weight, 1d signal lag). Before treating any of it as a product conclusion it needs: a deterministic compute/SQL-validation test, an explicit cost model, and locked rebalance/weighting rules. Until then: directional insight only.

---

## Sequencing

1. **Now**: freeze feature work; prep review/merge (MERGE_PLAYBOOK).
2. **Post-merge, build window**: Tier 1 (carry_to_risk → vol_index → risk_regime), then Tier 1.5 curves (basis / skew term structure).
3. **Later / with review**: Tier 2; `vol_surface` only if the `Surface` artifact question is settled with Sreeram.

Each tool ships in the canonical 4-file primitive pattern (`__init__` / `schemas` / `config.yaml` / `compute`) + the 3-test triplet (compute + wiring + sql_validation), per the existing FX tools.
