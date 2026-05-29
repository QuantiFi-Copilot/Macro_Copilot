-- ============================================================================
-- Migration: populate macro_data.tool_metadata HUMAN-CURATED fields for
--            ``calculate_breakeven_butterfly_tool`` (Phase 1, revamp branch;
--            third tool in the inflation_indexed_bonds family to reach
--            tool_metadata parity with breakeven_inflation_simple).
--
-- WHY THIS EXISTS
-- ---------------
-- The mechanical-field row for this tool already exists from the Phase-0
-- seed (database/migrations/2026-05-26_phase0_tool_metadata_seed.sql).
-- This migration fills in the three HUMAN-CURATED TEXT fields per the
-- per-tool lifecycle checklist Stage 3:
--   - theoretical_reference  (Tuckman 4e Ch. 22 + per-country issuer docs)
--   - known_limitations      (composed-primitive caveats, wire-frozen
--                              field names, no-proxy guards)
--   - desk_narrative         (3-paragraph PM-facing macro framing)
--
-- The fourth human-curated field (``source_material_verified``) stays
-- NULL — Stage 8 close-out is human sign-off per ADR 0015.
--
-- See:
--   - docs_revamped/03_standards/tool_lifecycle.md §2 axis 1/5/7
--   - docs_revamped/05_decisions/0015-tool-metadata-db-table.md
--
-- APPLY
-- -----
--   docker exec -i macro-tsdb psql -v ON_ERROR_STOP=1 -U quantuser -d macrodata \
--       < database/migrations/2026-05-29_phase1_calculate_breakeven_butterfly_curated.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- UPSERT: idempotent.  Only the three human-curated TEXT fields move.
-- ----------------------------------------------------------------------------

UPDATE macro_data.tool_metadata
SET
    theoretical_reference = $tref$Tuckman & Serrat (2022), Fixed Income Securities: Tools for Today's Markets, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) — textbook framing of the bond-implied breakeven as the nominal-minus-real yield differential and the standard decomposition breakeven = expected inflation + inflation risk premium − relative liquidity premium, applied here pillar-by-pillar across three tenors of the same nominal/linker pair to produce a curvature object. Fabozzi (ed.), Handbook of Fixed Income Securities, 9th ed., McGraw-Hill, Ch. 15-16 — standard fixed simple-butterfly weighting (-0.5, +1.0, -0.5) on (short, belly, long) as the canonical 3-point curvature primitive for any rates curve. Per-country primary issuer documentation for the matched nominal + linker curve pairs the primitive supports: US Treasury / TreasuryDirect (UST + USD_TIPS); UK Debt Management Office (UK_GILT + GBP_LINKER; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (FR_OAT + EUR_FR_LINKER, HICPxT reference); Bank of Canada (CANADA_GOVT + CAD_RRB). The level-stat methodology (rolling 252-day z-score, period changes, trailing range) mirrors the sibling breakeven_inflation_simple / breakeven_curve_spread / real_yield_butterfly tools; convention values are pinned by the cross-config lint per docs_revamped/03_standards/methodology_disclosure.md.$tref$,

    known_limitations = $klim$Returns the CURVATURE OF INFLATION COMPENSATION, not the curvature of pure expected inflation — each endpoint breakeven carries an inflation risk premium and a relative liquidity premium between the nominal sovereign and the linker, and the butterfly inherits all three at each of the three endpoints. Same-country invariant is load-bearing and inherited transitively from the spot breakeven primitive's ``_enforce_same_country_invariant`` guard: cross-country triplets are refused at compute time with a controlled error envelope BEFORE any market-data SELECT fires. The fixed simple-butterfly weighting (-0.5, +1.0, -0.5) is a methodology choice locked in YAML — duration-neutral / DV01-weighted variants would ship as separate primitives (TD #planned_extensions). The instrument_type discriminators are hard-coded in the composed spot primitive (linker leg = inflation_linker; nominal leg = sovereign_benchmark) — passing two linkers or two nominals at any endpoint surfaces the spot primitive's controlled error envelope with the offending short/belly/long endpoint attribution. Three-tenor ordering (short_years < belly_years < long_years) is structural and rejected at the schema layer; the tool does NOT silently swap inverted tenors. Per-trade-date alignment uses a strict pandas inner-join across the three endpoint series AFTER each is computed independently — no synthetic butterfly points are produced on dates where ANY endpoint has no data (no ffill across the join). The wire-frozen trailing_range_window_days (locked at 252 in V1; output field names high_252d_bps / low_252d_bps / percentile_252d embed the number) requires a schema migration + frontend update + parity-fixture regeneration to unlock. UK linkers carry mixed 3-month / 8-month indexation lag; Canadian RRBs are a declining-liquidity legacy market (no issuance since November 2022); the UK RPI → CPIH transition in 2030 bifurcates the gilt curve, so historical z-scores spanning the transition mix two methodology regimes. Latest-observation cutoff anchors to the data's latest aligned trade_date (matching breakeven_inflation_simple / breakeven_curve_spread / real_yield_butterfly / OIS rate_level), NOT date.today(). Breakeven butterflies can be negative across stress windows when the belly breakeven was rich relative to a half-weighted wings average; the math is unchanged.$klim$,

    desk_narrative = $narr$Returns the current bond-implied breakeven butterfly between three tenors of the same nominal/linker pair — a UST/USD_TIPS 5s10s30s breakeven, a UK_GILT/GBP_LINKER 2s10s30s breakeven, a FR_OAT/EUR_FR_LINKER 2s5s10s breakeven, or a CANADA_GOVT/CAD_RRB 5s10s30s breakeven — together with daily / weekly / monthly bps changes, a rolling 252-day z-score against the series' own history, trailing 252-day high / low / percentile in bps, the two component wing spreads (belly − short, long − belly), and the three endpoint breakevens so the curvature can be decomposed end-to-end without a second tool call. The number is computed pillar-by-pillar as breakeven = nominal − real at each of three tenors, then weighted (-0.5, +1.0, -0.5) on (short, belly, long). The sign convention matters: POSITIVE = belly breakeven is HIGH relative to the half-weighted wings average (belly is CHEAP versus the wings on a relative-value read); NEGATIVE = belly is RICH. Same sign convention as the sovereign nominal butterfly and the sibling real_yield_butterfly so a desk reader can read across the four-tool family without mental flips. The canonical desk use is reading the SHAPE of inflation-compensation expectations: a belly-rich move on a 5s10s30s breakeven butterfly says the market is pricing higher inflation-compensation pressure at the 10Y point than at the wings — typically a mid-cycle reflation read or a supply-shock pricing read, depending on regime. Pair with the matching real_yield_butterfly on the same curve to decompose: butterfly moves driven by real yields vs by inflation compensation tell different macro stories. Caveat: this is INFLATION-COMPENSATION CURVATURE, not pure-expected-inflation curvature — each endpoint carries an inflation risk premium and a relative nominal-vs-linker liquidity premium, and the butterfly inherits all three at each pillar. Use the implied story-telling carefully; do not confuse this with a clean expected-inflation curve view.$narr$,

    updated_at = NOW()
WHERE tool_name = 'calculate_breakeven_butterfly_tool';

-- ----------------------------------------------------------------------------
-- Defensive INSERT fallback (Phase-0 seed not applied → row absent).
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
    'calculate_breakeven_butterfly_tool',
    'inflation_indexed_bonds',
    'cross_market_rv',
    '{"time_series_butterfly": "bps", "time_series_zscore": "z_score"}'::jsonb,
    $tref$Tuckman & Serrat (2022), Fixed Income Securities: Tools for Today's Markets, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) — textbook framing of the bond-implied breakeven as the nominal-minus-real yield differential and the standard decomposition breakeven = expected inflation + inflation risk premium − relative liquidity premium, applied here pillar-by-pillar across three tenors of the same nominal/linker pair to produce a curvature object. Fabozzi (ed.), Handbook of Fixed Income Securities, 9th ed., McGraw-Hill, Ch. 15-16 — standard fixed simple-butterfly weighting (-0.5, +1.0, -0.5) on (short, belly, long) as the canonical 3-point curvature primitive for any rates curve. Per-country primary issuer documentation for the matched nominal + linker curve pairs the primitive supports: US Treasury / TreasuryDirect (UST + USD_TIPS); UK Debt Management Office (UK_GILT + GBP_LINKER; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (FR_OAT + EUR_FR_LINKER, HICPxT reference); Bank of Canada (CANADA_GOVT + CAD_RRB). The level-stat methodology (rolling 252-day z-score, period changes, trailing range) mirrors the sibling breakeven_inflation_simple / breakeven_curve_spread / real_yield_butterfly tools; convention values are pinned by the cross-config lint per docs_revamped/03_standards/methodology_disclosure.md.$tref$,
    $klim$Returns the CURVATURE OF INFLATION COMPENSATION, not the curvature of pure expected inflation — each endpoint breakeven carries an inflation risk premium and a relative liquidity premium between the nominal sovereign and the linker, and the butterfly inherits all three at each of the three endpoints. Same-country invariant is load-bearing and inherited transitively from the spot breakeven primitive's ``_enforce_same_country_invariant`` guard: cross-country triplets are refused at compute time with a controlled error envelope BEFORE any market-data SELECT fires. The fixed simple-butterfly weighting (-0.5, +1.0, -0.5) is a methodology choice locked in YAML — duration-neutral / DV01-weighted variants would ship as separate primitives (TD #planned_extensions). The instrument_type discriminators are hard-coded in the composed spot primitive (linker leg = inflation_linker; nominal leg = sovereign_benchmark) — passing two linkers or two nominals at any endpoint surfaces the spot primitive's controlled error envelope with the offending short/belly/long endpoint attribution. Three-tenor ordering (short_years < belly_years < long_years) is structural and rejected at the schema layer; the tool does NOT silently swap inverted tenors. Per-trade-date alignment uses a strict pandas inner-join across the three endpoint series AFTER each is computed independently — no synthetic butterfly points are produced on dates where ANY endpoint has no data (no ffill across the join). The wire-frozen trailing_range_window_days (locked at 252 in V1; output field names high_252d_bps / low_252d_bps / percentile_252d embed the number) requires a schema migration + frontend update + parity-fixture regeneration to unlock. UK linkers carry mixed 3-month / 8-month indexation lag; Canadian RRBs are a declining-liquidity legacy market (no issuance since November 2022); the UK RPI → CPIH transition in 2030 bifurcates the gilt curve, so historical z-scores spanning the transition mix two methodology regimes. Latest-observation cutoff anchors to the data's latest aligned trade_date (matching breakeven_inflation_simple / breakeven_curve_spread / real_yield_butterfly / OIS rate_level), NOT date.today(). Breakeven butterflies can be negative across stress windows when the belly breakeven was rich relative to a half-weighted wings average; the math is unchanged.$klim$,
    $narr$Returns the current bond-implied breakeven butterfly between three tenors of the same nominal/linker pair — a UST/USD_TIPS 5s10s30s breakeven, a UK_GILT/GBP_LINKER 2s10s30s breakeven, a FR_OAT/EUR_FR_LINKER 2s5s10s breakeven, or a CANADA_GOVT/CAD_RRB 5s10s30s breakeven — together with daily / weekly / monthly bps changes, a rolling 252-day z-score against the series' own history, trailing 252-day high / low / percentile in bps, the two component wing spreads (belly − short, long − belly), and the three endpoint breakevens so the curvature can be decomposed end-to-end without a second tool call. The number is computed pillar-by-pillar as breakeven = nominal − real at each of three tenors, then weighted (-0.5, +1.0, -0.5) on (short, belly, long). The sign convention matters: POSITIVE = belly breakeven is HIGH relative to the half-weighted wings average (belly is CHEAP versus the wings on a relative-value read); NEGATIVE = belly is RICH. Same sign convention as the sovereign nominal butterfly and the sibling real_yield_butterfly so a desk reader can read across the four-tool family without mental flips. The canonical desk use is reading the SHAPE of inflation-compensation expectations: a belly-rich move on a 5s10s30s breakeven butterfly says the market is pricing higher inflation-compensation pressure at the 10Y point than at the wings — typically a mid-cycle reflation read or a supply-shock pricing read, depending on regime. Pair with the matching real_yield_butterfly on the same curve to decompose: butterfly moves driven by real yields vs by inflation compensation tell different macro stories. Caveat: this is INFLATION-COMPENSATION CURVATURE, not pure-expected-inflation curvature — each endpoint carries an inflation risk premium and a relative nominal-vs-linker liquidity premium, and the butterfly inherits all three at each pillar. Use the implied story-telling carefully; do not confuse this with a clean expected-inflation curve view.$narr$
WHERE NOT EXISTS (
    SELECT 1
    FROM macro_data.tool_metadata
    WHERE tool_name = 'calculate_breakeven_butterfly_tool'
);

-- ----------------------------------------------------------------------------
-- Stage 8 closeout (NOT applied here): set source_material_verified once a
-- human verifier signs off the references above.
-- ============================================================================
