-- ============================================================================
-- Migration: populate macro_data.tool_metadata HUMAN-CURATED fields for
--            ``calculate_breakeven_inflation_simple_tool`` (Stage B of the
--            `revamp` Phase-1 pilot; second tool brought to full lifecycle
--            parity with get_real_yield_level_tool).
--
-- WHY THIS EXISTS
-- ---------------
-- The mechanical-field row for this tool already exists from the Phase-0
-- seed (database/migrations/2026-05-26_phase0_tool_metadata_seed.sql).
-- This migration is Phase 1: it fills in the three HUMAN-CURATED TEXT
-- fields per the per-tool lifecycle checklist at
-- ``rates_agent/inflation_indexed_bonds/tools/breakeven_inflation_simple/LIFECYCLE_CHECKLIST.md``
-- Stage 3:
--   - theoretical_reference  (Tuckman 4e Ch. 22 + per-country primary sources)
--   - known_limitations      (inflation-compensation caveat, same-country guard, universe gaps)
--   - desk_narrative         (3-paragraph user-facing macro framing)
--
-- The fourth human-curated field (``source_material_verified``) is
-- explicitly NOT set here.  Per the per-tool checklist Stage 8 (status
-- ``⏸ pending``), the JSONB stays NULL until a human signs off.  Per
-- ADR 0015 the DB models only the end states (NULL = not verified;
-- populated = verified); the pending intent is tracked in the checklist.
--
-- NOTE — this migration has NOT been applied to the live DB as part of
-- the Stage B/C PR (the live DB is treated as off-limits for automated
-- application).  Apply manually when ready; the file is idempotent.
--
-- See:
--   - docs_revamped/03_standards/tool_lifecycle.md §2 axis 1/5/7
--   - docs_revamped/03_standards/lifecycle_checklist_template.md §5
--   - docs_revamped/05_decisions/0015-tool-metadata-db-table.md
--
-- APPLY
-- -----
--   docker exec -i macro-tsdb psql -v ON_ERROR_STOP=1 -U quantuser -d macrodata \
--       < database/migrations/2026-05-28_phase1_calculate_breakeven_inflation_simple_curated.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- UPSERT: idempotent.  Only the three human-curated TEXT fields move.
-- Mechanical fields (tool_name, domain, category, output_field_units) stay
-- as set by the Phase-0 seed; not touched on the UPDATE path.
-- ``source_material_verified`` intentionally absent — Stage 8 close-out.
-- ----------------------------------------------------------------------------

UPDATE macro_data.tool_metadata
SET
    theoretical_reference = $tref$Tuckman & Serrat (2022), Fixed Income Securities: Tools for Today's Markets, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) — textbook framing of the bond-implied breakeven as the nominal-minus-real yield differential and the standard decomposition breakeven = expected inflation + inflation risk premium − relative liquidity premium. Per-country primary issuer documentation for the matched nominal + linker pairs: US Treasury / TreasuryDirect (UST + TIPS); UK Debt Management Office (UK_GILT + Index-Linked Gilts; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (FR_OAT + OATei, HICPxT reference); Bank of Canada (CANADA_GOVT + Real Return Bonds). The level-stat methodology (rolling 252-day z-score, period changes, trailing range) mirrors the sibling linker real_yield_level and sovereign cross_market_spread tools; convention values are pinned by the cross-config lint per docs_revamped/03_standards/methodology_disclosure.md. Per-convention exposure decisions (Phase-1 pilot) recorded inline in rates_agent/inflation_indexed_bonds/tools/breakeven_inflation_simple/config.yaml per docs_revamped/03_standards/methodology_exposure.md.$tref$,

    known_limitations = $klim$Returns INFLATION COMPENSATION, not a clean expected-inflation read — the nominal-minus-real differential carries an inflation risk premium and a relative liquidity premium between the nominal sovereign and the linker. The "simple" suffix is deliberate: this is the unadjusted nominal − real pairing with no carry, seasonality, or risk-premium adjustment (a desk-grade expected-inflation read requires metadata the repo does not yet ingest; the adjusted variant would ship as a separate primitive — see TD #32 / TD #33). Same-country construction only: the nominal and linker legs MUST share both country and currency (UST + USD_TIPS, UK_GILT + GBP_LINKER, FR_OAT + EUR_FR_LINKER, CANADA_GOVT + CAD_RRB); cross-country pairs (e.g. DE_BUND vs EUR_FR_LINKER — same currency, different country; or UK_GILT vs USD_TIPS — different currency) are refused at compute time with a controlled error envelope, and this invariant is enforced in code (no YAML allow-list) per DESIGN_PRINCIPLES.md §5. The instrument_type discriminators are hard-coded in compute.py (linker leg = inflation_linker; nominal leg = sovereign_benchmark) — the load-bearing no-proxy guard; passing two linkers or two nominals returns a controlled error envelope. Generic benchmark series only, not actual bond-level analytics; both legs must have data at the requested tenor (the available tenor intersection is country-specific). UK linkers carry mixed 3-month / 8-month indexation lag; Canadian RRBs are a declining-liquidity legacy market (no issuance since November 2022); the UK RPI → CPIH transition in 2030 bifurcates the gilt curve, so historical z-scores spanning the transition mix two methodology regimes. The wire-frozen trailing_range_window_days (locked at 252 in V1; output field names high_252d_bps / low_252d_bps / percentile_252d embed the number) requires a schema migration + frontend update + parity-fixture regeneration to unlock. Latest-observation cutoff anchors to the data's latest aligned trade_date (matching real_yield_level / OIS rate_level), NOT date.today(). Breakevens can be negative across stress windows; the math is unchanged. Three rolling-z-score conventions (z_score_window_days / z_score_min_periods / z_score_ddof) plus field_name are exposed as per-call Pydantic Input overrides (Phase-1 methodology-exposure pilot); the remaining conventions stay YAML-locked.$klim$,

    desk_narrative = $narr$Returns the current bond-implied breakeven inflation between a nominal sovereign curve and the matching inflation-linked bond at the same tenor — UST vs US TIPS, UK Gilt vs index-linked Gilt, French OAT vs OATei, or Canadian government vs RRB — together with daily / weekly / monthly changes in basis points, a rolling 252-day z-score against the series' own history (override-tunable per call), trailing 252-day high / low / percentile, and the two underlying yields used to form the breakeven so the decomposition can be audited end-to-end. The breakeven is computed as nominal_yield_pct − real_yield_pct (and ×100 for the bps representation); it is the market's required compensation for bearing inflation, which is why a breakeven of 2.4% does NOT mean the market expects exactly 2.4% inflation. Critical framing the desk must keep front of mind: a bond-implied breakeven is inflation COMPENSATION, not expected inflation — it equals expected inflation plus an inflation risk premium minus a relative liquidity premium between the nominal and linker bond. Use it to read the market's inflation pricing and to decompose a nominal-yield move into its real-rate and inflation-compensation components (the four-quadrant pairing with the real-yield level is the canonical desk read: real yields up + breakevens down = real tightening; real yields down + breakevens up = reflationary easing). Sign convention: positive daily_change_bps = breakeven widened (inflation compensation rose); positive z_score = current breakeven is above its trailing-year mean. The tool refuses cross-country pairs by construction — a US-nominal vs UK-linker differential is a sovereign-credit / inflation-regime hybrid, not a generic breakeven, and is not silently computed.$narr$,

    updated_at = NOW()
WHERE tool_name = 'calculate_breakeven_inflation_simple_tool';

-- ----------------------------------------------------------------------------
-- Defensive INSERT fallback (Phase-0 seed not applied → row absent).
-- Same end-state as the UPDATE path.
-- ----------------------------------------------------------------------------

INSERT INTO macro_data.tool_metadata (
    tool_name,
    domain,
    category,
    output_field_units,
    theoretical_reference,
    known_limitations,
    desk_narrative
)
SELECT
    'calculate_breakeven_inflation_simple_tool',
    'inflation_indexed_bonds',
    'cross_market_rv',
    '{"time_series_breakeven": "bps", "time_series_zscore": "z_score"}'::jsonb,
    $tref$Tuckman & Serrat (2022), Fixed Income Securities: Tools for Today's Markets, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) — textbook framing of the bond-implied breakeven as the nominal-minus-real yield differential and the standard decomposition breakeven = expected inflation + inflation risk premium − relative liquidity premium. Per-country primary issuer documentation for the matched nominal + linker pairs: US Treasury / TreasuryDirect (UST + TIPS); UK Debt Management Office (UK_GILT + Index-Linked Gilts; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (FR_OAT + OATei, HICPxT reference); Bank of Canada (CANADA_GOVT + Real Return Bonds). The level-stat methodology (rolling 252-day z-score, period changes, trailing range) mirrors the sibling linker real_yield_level and sovereign cross_market_spread tools; convention values are pinned by the cross-config lint per docs_revamped/03_standards/methodology_disclosure.md. Per-convention exposure decisions (Phase-1 pilot) recorded inline in rates_agent/inflation_indexed_bonds/tools/breakeven_inflation_simple/config.yaml per docs_revamped/03_standards/methodology_exposure.md.$tref$,
    $klim$Returns INFLATION COMPENSATION, not a clean expected-inflation read — the nominal-minus-real differential carries an inflation risk premium and a relative liquidity premium between the nominal sovereign and the linker. The "simple" suffix is deliberate: this is the unadjusted nominal − real pairing with no carry, seasonality, or risk-premium adjustment (a desk-grade expected-inflation read requires metadata the repo does not yet ingest; the adjusted variant would ship as a separate primitive — see TD #32 / TD #33). Same-country construction only: the nominal and linker legs MUST share both country and currency (UST + USD_TIPS, UK_GILT + GBP_LINKER, FR_OAT + EUR_FR_LINKER, CANADA_GOVT + CAD_RRB); cross-country pairs (e.g. DE_BUND vs EUR_FR_LINKER — same currency, different country; or UK_GILT vs USD_TIPS — different currency) are refused at compute time with a controlled error envelope, and this invariant is enforced in code (no YAML allow-list) per DESIGN_PRINCIPLES.md §5. The instrument_type discriminators are hard-coded in compute.py (linker leg = inflation_linker; nominal leg = sovereign_benchmark) — the load-bearing no-proxy guard; passing two linkers or two nominals returns a controlled error envelope. Generic benchmark series only, not actual bond-level analytics; both legs must have data at the requested tenor (the available tenor intersection is country-specific). UK linkers carry mixed 3-month / 8-month indexation lag; Canadian RRBs are a declining-liquidity legacy market (no issuance since November 2022); the UK RPI → CPIH transition in 2030 bifurcates the gilt curve, so historical z-scores spanning the transition mix two methodology regimes. The wire-frozen trailing_range_window_days (locked at 252 in V1; output field names high_252d_bps / low_252d_bps / percentile_252d embed the number) requires a schema migration + frontend update + parity-fixture regeneration to unlock. Latest-observation cutoff anchors to the data's latest aligned trade_date (matching real_yield_level / OIS rate_level), NOT date.today(). Breakevens can be negative across stress windows; the math is unchanged. Three rolling-z-score conventions (z_score_window_days / z_score_min_periods / z_score_ddof) plus field_name are exposed as per-call Pydantic Input overrides (Phase-1 methodology-exposure pilot); the remaining conventions stay YAML-locked.$klim$,
    $narr$Returns the current bond-implied breakeven inflation between a nominal sovereign curve and the matching inflation-linked bond at the same tenor — UST vs US TIPS, UK Gilt vs index-linked Gilt, French OAT vs OATei, or Canadian government vs RRB — together with daily / weekly / monthly changes in basis points, a rolling 252-day z-score against the series' own history (override-tunable per call), trailing 252-day high / low / percentile, and the two underlying yields used to form the breakeven so the decomposition can be audited end-to-end. The breakeven is computed as nominal_yield_pct − real_yield_pct (and ×100 for the bps representation); it is the market's required compensation for bearing inflation, which is why a breakeven of 2.4% does NOT mean the market expects exactly 2.4% inflation. Critical framing the desk must keep front of mind: a bond-implied breakeven is inflation COMPENSATION, not expected inflation — it equals expected inflation plus an inflation risk premium minus a relative liquidity premium between the nominal and linker bond. Use it to read the market's inflation pricing and to decompose a nominal-yield move into its real-rate and inflation-compensation components (the four-quadrant pairing with the real-yield level is the canonical desk read: real yields up + breakevens down = real tightening; real yields down + breakevens up = reflationary easing). Sign convention: positive daily_change_bps = breakeven widened (inflation compensation rose); positive z_score = current breakeven is above its trailing-year mean. The tool refuses cross-country pairs by construction — a US-nominal vs UK-linker differential is a sovereign-credit / inflation-regime hybrid, not a generic breakeven, and is not silently computed.$narr$
WHERE NOT EXISTS (
    SELECT 1
    FROM macro_data.tool_metadata
    WHERE tool_name = 'calculate_breakeven_inflation_simple_tool'
);

-- ----------------------------------------------------------------------------
-- Stage 8 closeout note (NOT applied here):
--
--   UPDATE macro_data.tool_metadata
--   SET source_material_verified = jsonb_build_object(
--           'verifier', '<name>',
--           'date',     '<YYYY-MM-DD>',
--           'source',   'Tuckman 4e Ch. 22 (Inflation-Indexed Bonds; breakeven decomposition) + per-country primary issuer documentation'
--       ),
--       updated_at = NOW()
--   WHERE tool_name = 'calculate_breakeven_inflation_simple_tool';
--
-- Then flip the per-tool LIFECYCLE_CHECKLIST.md Stage 8 row from ⏸ to ☑
-- and update surface_contract.md §10 axis-3 from `in-progress` to `shipped`.
-- ============================================================================
