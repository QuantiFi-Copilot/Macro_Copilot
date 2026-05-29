-- ============================================================================
-- Migration: populate macro_data.tool_metadata HUMAN-CURATED fields for
--            ``calculate_real_yield_curve_spread_tool`` (Stage C of the
--            `revamp` Phase-1 pilot; third tool brought to full lifecycle
--            parity with get_real_yield_level_tool).
--
-- WHY THIS EXISTS
-- ---------------
-- The mechanical-field row for this tool already exists from the Phase-0
-- seed (database/migrations/2026-05-26_phase0_tool_metadata_seed.sql).
-- This migration is Phase 1: it fills in the three HUMAN-CURATED TEXT
-- fields per the per-tool lifecycle checklist at
-- ``rates_agent/inflation_indexed_bonds/tools/real_yield_curve_spread/LIFECYCLE_CHECKLIST.md``
-- Stage 3:
--   - theoretical_reference  (Tuckman 4e Ch. 22 + Ch. 5 + per-country primary sources)
--   - known_limitations      (term-structure framing, same-curve guard, composition path)
--   - desk_narrative         (3-paragraph user-facing macro framing)
--
-- The fourth human-curated field (``source_material_verified``) is
-- explicitly NOT set here.  Per the per-tool checklist Stage 8 (status
-- ``⏸ pending``), the JSONB stays NULL until a human signs off, per
-- ADR 0015 (DB models only NULL / populated end states).
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
--       < database/migrations/2026-05-28_phase1_calculate_real_yield_curve_spread_curated.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- UPSERT: idempotent.  Only the three human-curated TEXT fields move.
-- Mechanical fields stay as set by the Phase-0 seed on the UPDATE path.
-- ``source_material_verified`` intentionally absent — Stage 8 close-out.
-- ----------------------------------------------------------------------------

UPDATE macro_data.tool_metadata
SET
    theoretical_reference = $tref$Tuckman & Serrat (2022), Fixed Income Securities: Tools for Today's Markets, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) for real-yield definition + indexation mechanics, and Ch. 5 (term structure / curve shape) for the spread / curve-steepness framing applied to the real-yield curve. Per-country primary issuer documentation for the linker universe: US Treasury / TreasuryDirect (TIPS; 20Y discontinuation 2009); UK Debt Management Office (Index-Linked Gilts; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (OATei; HICPxT reference); Bank of Canada (Real Return Bonds; November 2022 issuance cessation). The spread is composed from two get_real_yield_level calls (one per endpoint tenor) so the level primitive's no-proxy guard (instrument_type='inflation_linker') is inherited transitively; the level-stat methodology (rolling 252-day z-score, period changes, trailing range) and convention values are pinned by the cross-config lint per docs_revamped/03_standards/methodology_disclosure.md. Per-convention exposure decisions (Phase-1 pilot) recorded inline in rates_agent/inflation_indexed_bonds/tools/real_yield_curve_spread/config.yaml per docs_revamped/03_standards/methodology_exposure.md.$tref$,

    known_limitations = $klim$Returns the term structure of REAL YIELDS (real-yield curve shape), distinct from a breakeven curve spread (which differences two inflation-compensation pairs) and from a nominal sovereign curve spread (which differences two nominal yields). Same-country / same-curve-family construction only: both endpoints share a single linker curve_family (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB); cross-curve real-yield spreads are not expressible by the input shape (and would be a sovereign-credit / regime-hybrid object, not a pure curve-shape read). Composed from two get_real_yield_level calls; a non-linker curve_family at either endpoint returns a controlled error envelope from the inner level call, surfaced with the offending endpoint named. The two endpoint series are aligned by a strict pandas inner-join on trade_date AFTER each is computed independently — dates where ONE endpoint has no data are dropped, never carried forward as synthetic spread points. Tenor ordering is structural: short_tenor must map to a strictly smaller year fraction than long_tenor (validated via tenor_to_years); the tool does not silently swap an inverted pair. Generic benchmark series only, not bond-level analytics; both pillars must exist on the curve (the ingested grid is country-specific — USD_TIPS 5Y/10Y/20Y/30Y; GBP_LINKER 1Y..50Y; EUR_FR_LINKER 2Y/5Y/7Y/10Y/15Y; CAD_RRB 5Y..30Y). Canadian RRBs are a declining-liquidity legacy market (no issuance since November 2022); the UK RPI → CPIH 2030 transition mixes methodology regimes for spanning histories. The spread is reported in PERCENT (same units as the underlying real yields, NOT multiplied by 100); daily / weekly / monthly changes are reported in BPS per desk convention. The wire-frozen trailing_range_window_days (locked at 252 in V1; output field names high_252d_pct / low_252d_pct / percentile_252d embed the number) requires a schema migration + frontend update + parity-fixture regeneration to unlock. Real-yield curve spreads can be negative (curve inversion) across parts of the post-2008 / 2020-21 history; the math is unchanged. The three rolling-z-score Input overrides (z_score_window_days / z_score_min_periods / z_score_ddof) apply to the spread's own rolling z-score and the fetch-window buffer; the inner endpoint level calls use the YAML-default z-score (their z-score is not consumed). Latest-observation cutoff anchors to the data's latest aligned trade_date, NOT date.today().$klim$,

    desk_narrative = $narr$Returns the current same-country real-yield curve spread between two tenors of one sovereign linker curve — e.g. US TIPS 5s10s, UK linker 2s10s, French OATei 5s10s, or Canadian RRB 5s30s — together with daily / weekly / monthly changes in basis points, a rolling 252-day z-score against the spread's own history (override-tunable per call), trailing 252-day high / low / percentile, and the two endpoint real yields and year fractions used to form the spread so the decomposition can be audited. The spread is computed per-trade-date as long_real_yield_pct − short_real_yield_pct and reported in PERCENT (same units as the underlying real yields); it describes the SHAPE of the real-yield curve, which is a distinct object from a breakeven curve spread (inflation-compensation term structure) and from a nominal sovereign curve spread. Use it to read how the real-rate term structure is steepening or flattening — a real-yield curve steepening (long real yields rising relative to short, spread widening) often signals the market pricing higher forward real rates / term premium, while flattening or inversion signals the opposite; the real-yield curve also moves differently from the nominal curve when the inflation-compensation term structure shifts, so the two read together decompose a nominal curve move. Sign convention: a positive current_spread_pct means the long real yield sits above the short real yield (upward-sloping real curve); positive daily_change_bps means the spread widened (real curve steepened) on the day; positive z_score means the current curve shape is steeper than its trailing-year mean. These are generic benchmark series, not bond-level analytics; the spread is computed only on dates where both endpoint pillars have data, and the tool refuses non-linker curve families by construction.$narr$,

    updated_at = NOW()
WHERE tool_name = 'calculate_real_yield_curve_spread_tool';

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
    'calculate_real_yield_curve_spread_tool',
    'inflation_indexed_bonds',
    'curve_shape',
    '{"time_series_spread": "percent", "time_series_zscore": "z_score"}'::jsonb,
    $tref$Tuckman & Serrat (2022), Fixed Income Securities: Tools for Today's Markets, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) for real-yield definition + indexation mechanics, and Ch. 5 (term structure / curve shape) for the spread / curve-steepness framing applied to the real-yield curve. Per-country primary issuer documentation for the linker universe: US Treasury / TreasuryDirect (TIPS; 20Y discontinuation 2009); UK Debt Management Office (Index-Linked Gilts; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (OATei; HICPxT reference); Bank of Canada (Real Return Bonds; November 2022 issuance cessation). The spread is composed from two get_real_yield_level calls (one per endpoint tenor) so the level primitive's no-proxy guard (instrument_type='inflation_linker') is inherited transitively; the level-stat methodology (rolling 252-day z-score, period changes, trailing range) and convention values are pinned by the cross-config lint per docs_revamped/03_standards/methodology_disclosure.md. Per-convention exposure decisions (Phase-1 pilot) recorded inline in rates_agent/inflation_indexed_bonds/tools/real_yield_curve_spread/config.yaml per docs_revamped/03_standards/methodology_exposure.md.$tref$,
    $klim$Returns the term structure of REAL YIELDS (real-yield curve shape), distinct from a breakeven curve spread (which differences two inflation-compensation pairs) and from a nominal sovereign curve spread (which differences two nominal yields). Same-country / same-curve-family construction only: both endpoints share a single linker curve_family (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB); cross-curve real-yield spreads are not expressible by the input shape (and would be a sovereign-credit / regime-hybrid object, not a pure curve-shape read). Composed from two get_real_yield_level calls; a non-linker curve_family at either endpoint returns a controlled error envelope from the inner level call, surfaced with the offending endpoint named. The two endpoint series are aligned by a strict pandas inner-join on trade_date AFTER each is computed independently — dates where ONE endpoint has no data are dropped, never carried forward as synthetic spread points. Tenor ordering is structural: short_tenor must map to a strictly smaller year fraction than long_tenor (validated via tenor_to_years); the tool does not silently swap an inverted pair. Generic benchmark series only, not bond-level analytics; both pillars must exist on the curve (the ingested grid is country-specific — USD_TIPS 5Y/10Y/20Y/30Y; GBP_LINKER 1Y..50Y; EUR_FR_LINKER 2Y/5Y/7Y/10Y/15Y; CAD_RRB 5Y..30Y). Canadian RRBs are a declining-liquidity legacy market (no issuance since November 2022); the UK RPI → CPIH 2030 transition mixes methodology regimes for spanning histories. The spread is reported in PERCENT (same units as the underlying real yields, NOT multiplied by 100); daily / weekly / monthly changes are reported in BPS per desk convention. The wire-frozen trailing_range_window_days (locked at 252 in V1; output field names high_252d_pct / low_252d_pct / percentile_252d embed the number) requires a schema migration + frontend update + parity-fixture regeneration to unlock. Real-yield curve spreads can be negative (curve inversion) across parts of the post-2008 / 2020-21 history; the math is unchanged. The three rolling-z-score Input overrides (z_score_window_days / z_score_min_periods / z_score_ddof) apply to the spread's own rolling z-score and the fetch-window buffer; the inner endpoint level calls use the YAML-default z-score (their z-score is not consumed). Latest-observation cutoff anchors to the data's latest aligned trade_date, NOT date.today().$klim$,
    $narr$Returns the current same-country real-yield curve spread between two tenors of one sovereign linker curve — e.g. US TIPS 5s10s, UK linker 2s10s, French OATei 5s10s, or Canadian RRB 5s30s — together with daily / weekly / monthly changes in basis points, a rolling 252-day z-score against the spread's own history (override-tunable per call), trailing 252-day high / low / percentile, and the two endpoint real yields and year fractions used to form the spread so the decomposition can be audited. The spread is computed per-trade-date as long_real_yield_pct − short_real_yield_pct and reported in PERCENT (same units as the underlying real yields); it describes the SHAPE of the real-yield curve, which is a distinct object from a breakeven curve spread (inflation-compensation term structure) and from a nominal sovereign curve spread. Use it to read how the real-rate term structure is steepening or flattening — a real-yield curve steepening (long real yields rising relative to short, spread widening) often signals the market pricing higher forward real rates / term premium, while flattening or inversion signals the opposite; the real-yield curve also moves differently from the nominal curve when the inflation-compensation term structure shifts, so the two read together decompose a nominal curve move. Sign convention: a positive current_spread_pct means the long real yield sits above the short real yield (upward-sloping real curve); positive daily_change_bps means the spread widened (real curve steepened) on the day; positive z_score means the current curve shape is steeper than its trailing-year mean. These are generic benchmark series, not bond-level analytics; the spread is computed only on dates where both endpoint pillars have data, and the tool refuses non-linker curve families by construction.$narr$
WHERE NOT EXISTS (
    SELECT 1
    FROM macro_data.tool_metadata
    WHERE tool_name = 'calculate_real_yield_curve_spread_tool'
);

-- ----------------------------------------------------------------------------
-- Stage 8 closeout note (NOT applied here):
--
--   UPDATE macro_data.tool_metadata
--   SET source_material_verified = jsonb_build_object(
--           'verifier', '<name>',
--           'date',     '<YYYY-MM-DD>',
--           'source',   'Tuckman 4e Ch. 22 + Ch. 5 + per-country primary issuer documentation'
--       ),
--       updated_at = NOW()
--   WHERE tool_name = 'calculate_real_yield_curve_spread_tool';
--
-- Then flip the per-tool LIFECYCLE_CHECKLIST.md Stage 8 row from ⏸ to ☑
-- and update surface_contract.md §10 axis-3 from `in-progress` to `shipped`.
-- ============================================================================
