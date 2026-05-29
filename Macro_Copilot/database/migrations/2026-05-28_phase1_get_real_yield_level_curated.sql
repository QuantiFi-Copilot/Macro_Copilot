-- ============================================================================
-- Migration: populate macro_data.tool_metadata HUMAN-CURATED fields for
--            ``get_real_yield_level_tool`` (Phase 1 of the `revamp` branch).
--            First pilot tool under the new tool-lifecycle standards.
--
-- WHY THIS EXISTS
-- ---------------
-- The mechanical-field row for this tool already exists from the
-- Phase-0 seed:
--   database/migrations/2026-05-26_phase0_tool_metadata_seed.sql:409-416
--
-- Phase 0 populated:
--   tool_name           = 'get_real_yield_level_tool'
--   domain              = 'inflation_indexed_bonds'
--   category            = 'snapshots'
--   output_field_units  = '{"time_series": "percent"}'::jsonb
--
-- This migration is Phase 1: it fills in the three HUMAN-CURATED
-- TEXT fields per the metadata package at
-- ``tmp/tool_descriptions/get_real_yield_level_tool.md`` §9 and per the
-- per-tool lifecycle checklist at
-- ``rates_agent/inflation_indexed_bonds/tools/real_yield_level/LIFECYCLE_CHECKLIST.md``
-- Stage 3:
--   - theoretical_reference  (Tuckman 4e Ch. 22 + per-country primary sources)
--   - known_limitations      (universe gaps, country-specific caveats, wire-frozen items)
--   - desk_narrative         (3-paragraph user-facing macro framing)
--
-- The fourth human-curated field (``source_material_verified``) is
-- explicitly NOT set here.  Per the per-tool checklist Stage 8 (status
-- ``⏸ pending``), the JSONB stays NULL until a human signs off.  The
-- pending intent is tracked in the checklist; the DB models only the
-- end states (NULL = not verified; populated = verified) per ADR 0015.
--
-- ON CONFLICT (tool_name) DO UPDATE preserves the mechanical fields
-- managed by ``populate_tool_metadata.py`` (those are NOT in the
-- INSERT here; the existing row carries them already).  Re-runnable.
--
-- See:
--   - docs_revamped/03_standards/tool_lifecycle.md §2 axis 1 (theoretical_reference),
--     axis 5 (known_limitations), axis 7 (desk_narrative)
--   - docs_revamped/03_standards/lifecycle_checklist_template.md §5
--     (source_material_verified ``pending`` state)
--   - docs_revamped/05_decisions/0015-tool-metadata-db-table.md
--   - tmp/tool_descriptions/get_real_yield_level_tool.md (metadata package draft)
--
-- APPLY
-- -----
--   docker exec -i macro-tsdb psql -v ON_ERROR_STOP=1 -U quantuser -d macrodata \
--       < database/migrations/2026-05-28_phase1_get_real_yield_level_curated.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- UPSERT: idempotent.  Only the three human-curated TEXT fields move.
-- Mechanical fields (tool_name, domain, category, output_field_units) stay
-- as set by the Phase-0 seed; not touched here.
-- ``source_material_verified`` intentionally absent — Stage 8 close-out.
-- ----------------------------------------------------------------------------

UPDATE macro_data.tool_metadata
SET
    -- ------------------------------------------------------------------
    -- theoretical_reference (Axis 1) — compound form per metadata
    -- package §4.2 recommendation.  Textbook chapter + per-country
    -- primary issuer documentation + methodology-disclosure pointer.
    -- ------------------------------------------------------------------
    theoretical_reference = $tref$Tuckman & Serrat (2022), Fixed Income Securities: Tools for Today's Markets, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) — textbook framing of real yield, indexation mechanics (CPI-U / RPI / HICP-ex-tobacco / Canadian CPI; 3-month and 8-month indexation lag; daily interpolation), and breakeven decomposition. Per-country primary issuer documentation: US Treasury / TreasuryDirect (TIPS issuance + mechanics; 20Y discontinuation 2009); UK Debt Management Office (Index-Linked Gilts; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (OATei mechanics; HICPxT reference); Bank of Canada (Real Return Bonds technical documentation; November 2022 issuance cessation). The level-stat methodology (rolling 252-day z-score, period changes, trailing range) mirrors sovereign get_yield_levels_tool / OIS get_ois_rate_level_tool; convention values are pinned by the cross-config lint per docs_revamped/03_standards/methodology_disclosure.md. Per-convention exposure decisions (Phase-1 pilot) recorded inline in rates_agent/inflation_indexed_bonds/tools/real_yield_level/config.yaml per docs_revamped/03_standards/methodology_exposure.md.$tref$,

    -- ------------------------------------------------------------------
    -- known_limitations (Axis 5) — per metadata package §6 (13
    -- numbered items, condensed into prose paragraphs).
    -- ------------------------------------------------------------------
    known_limitations = $klim$Generic real-yield benchmarks only — not actual bond-level analytics. Exact carry, settlement, clean-vs-dirty real price, index ratio, accrued interest, and deflation-floor terms require actual bond-level data which this tool does not consume. Universe is 24 instruments across 4 countries: US TIPS (5Y/10Y/20Y/30Y; new 20Y issuance discontinued 2009 — 20Y generic should be treated as a constant-maturity / outstanding-sector benchmark), UK linkers (1Y..50Y; mixed 3-month and 8-month indexation lag within the same curve), French OATei (2Y/5Y/7Y/10Y/15Y; HICPxT-referenced, NOT French-CPI-referenced OATi), Canadian RRBs (5Y..30Y; no new issuance since November 2022 — declining-liquidity legacy market; z-scores carry a lower-confidence caveat). The UK RPI → CPIH transition in 2030 bifurcates the linker curve; post-2030-maturity gilt cash flows blend pre/post reform and historical z-scores spanning the transition mix two methodology regimes. The wire-frozen trailing_range_window_days (locked at 252 in V1; output field names embed the number) requires a schema migration + frontend update + parity-fixture regeneration to unlock — path documented in config.yaml:methodology.planned_extensions. Observation-count cutoff anchors to the data's latest observation date (matching OIS rate_level), NOT to date.today() — linker daily feeds can lag wall-clock by several business days; sovereign yield_levels currently anchors to date.today() (the inconsistency is documented as a separate planned extension). No deflation-floor valuation, no carry projection, no CPI-seasonality adjustment, no inflation-risk-premium decomposition; those are separate (planned) primitives. The instrument_type='inflation_linker' filter at the fetch boundary is hard-coded in compute.py (not YAML) per the no-proxy guard — a nominal curve_family returns a controlled-error envelope routing the caller to get_yield_levels_tool. Three rolling-z-score conventions (z_score_window_days / z_score_min_periods / z_score_ddof) are exposed as per-call Pydantic Input overrides (Phase-1 methodology-exposure pilot); the remaining nine conventions stay YAML-locked.$klim$,

    -- ------------------------------------------------------------------
    -- desk_narrative (Axis 7) — per metadata package §8.1 (3
    -- paragraphs of user-facing prose suitable for the Library page).
    -- ------------------------------------------------------------------
    desk_narrative = $narr$Returns the current real yield on a single (curve_family, tenor) point of a sovereign-linker bond — US TIPS, UK index-linked Gilts, French OATei, or Canadian RRB — together with daily / weekly / monthly changes in basis points, a rolling 252-day z-score against the series' own history (override-tunable per call), and trailing 252-day high / low / percentile. A linker's real yield is the yield-to-maturity computed on cash flows that have been indexed to the relevant consumer price index (CPI-U NSA for US TIPS; RPI for UK linkers — transitioning to RPI aligned with CPIH from 2030; HICP excluding tobacco for French OATei; Canadian CPI all-items NSA for RRB). It represents the market's required real rate of return AFTER inflation indexation — not a forecast of realized real economic growth. Real yields are a macro state variable: a rise in real yields is often a tightening of financial conditions even when nominal yields are unchanged (the 2022 real-rate shock is the canonical recent example). The four-quadrant pairing with breakeven moves — real yields up / breakevens down = real tightening; real yields down / breakevens up = reflationary easing — is the canonical desk read. Sign convention: positive daily_change_bps = real yields rose; positive z_score = current real yield is above its trailing-year mean. Critical caveat to remember: these are generic benchmark series, not actual bond-level analytics — and intermediate / long linkers carry meaningful real-rate duration, so they are NOT a pure inflation bet (2022 TIPS lost roughly 12% despite high realized CPI because the real-yield selloff dominated inflation accrual).$narr$,

    updated_at = NOW()
WHERE tool_name = 'get_real_yield_level_tool';

-- ----------------------------------------------------------------------------
-- Defensive: if the Phase-0 seed has not been applied to this DB (the row
-- doesn't exist yet), the UPDATE above is a no-op.  In that case, fall
-- back to a full INSERT.  The two paths give the same end-state.
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
    'get_real_yield_level_tool',
    'inflation_indexed_bonds',
    'snapshots',
    '{"time_series": "percent"}'::jsonb,
    $tref$Tuckman & Serrat (2022), Fixed Income Securities: Tools for Today's Markets, 4th ed., Wiley, Ch. 22 (Inflation-Indexed Bonds) — textbook framing of real yield, indexation mechanics (CPI-U / RPI / HICP-ex-tobacco / Canadian CPI; 3-month and 8-month indexation lag; daily interpolation), and breakeven decomposition. Per-country primary issuer documentation: US Treasury / TreasuryDirect (TIPS issuance + mechanics; 20Y discontinuation 2009); UK Debt Management Office (Index-Linked Gilts; 3-month and 8-month lag; RPI → CPIH 2030 transition); Agence France Trésor (OATei mechanics; HICPxT reference); Bank of Canada (Real Return Bonds technical documentation; November 2022 issuance cessation). The level-stat methodology (rolling 252-day z-score, period changes, trailing range) mirrors sovereign get_yield_levels_tool / OIS get_ois_rate_level_tool; convention values are pinned by the cross-config lint per docs_revamped/03_standards/methodology_disclosure.md. Per-convention exposure decisions (Phase-1 pilot) recorded inline in rates_agent/inflation_indexed_bonds/tools/real_yield_level/config.yaml per docs_revamped/03_standards/methodology_exposure.md.$tref$,
    $klim$Generic real-yield benchmarks only — not actual bond-level analytics. Exact carry, settlement, clean-vs-dirty real price, index ratio, accrued interest, and deflation-floor terms require actual bond-level data which this tool does not consume. Universe is 24 instruments across 4 countries: US TIPS (5Y/10Y/20Y/30Y; new 20Y issuance discontinued 2009 — 20Y generic should be treated as a constant-maturity / outstanding-sector benchmark), UK linkers (1Y..50Y; mixed 3-month and 8-month indexation lag within the same curve), French OATei (2Y/5Y/7Y/10Y/15Y; HICPxT-referenced, NOT French-CPI-referenced OATi), Canadian RRBs (5Y..30Y; no new issuance since November 2022 — declining-liquidity legacy market; z-scores carry a lower-confidence caveat). The UK RPI → CPIH transition in 2030 bifurcates the linker curve; post-2030-maturity gilt cash flows blend pre/post reform and historical z-scores spanning the transition mix two methodology regimes. The wire-frozen trailing_range_window_days (locked at 252 in V1; output field names embed the number) requires a schema migration + frontend update + parity-fixture regeneration to unlock — path documented in config.yaml:methodology.planned_extensions. Observation-count cutoff anchors to the data's latest observation date (matching OIS rate_level), NOT to date.today() — linker daily feeds can lag wall-clock by several business days; sovereign yield_levels currently anchors to date.today() (the inconsistency is documented as a separate planned extension). No deflation-floor valuation, no carry projection, no CPI-seasonality adjustment, no inflation-risk-premium decomposition; those are separate (planned) primitives. The instrument_type='inflation_linker' filter at the fetch boundary is hard-coded in compute.py (not YAML) per the no-proxy guard — a nominal curve_family returns a controlled-error envelope routing the caller to get_yield_levels_tool. Three rolling-z-score conventions (z_score_window_days / z_score_min_periods / z_score_ddof) are exposed as per-call Pydantic Input overrides (Phase-1 methodology-exposure pilot); the remaining nine conventions stay YAML-locked.$klim$,
    $narr$Returns the current real yield on a single (curve_family, tenor) point of a sovereign-linker bond — US TIPS, UK index-linked Gilts, French OATei, or Canadian RRB — together with daily / weekly / monthly changes in basis points, a rolling 252-day z-score against the series' own history (override-tunable per call), and trailing 252-day high / low / percentile. A linker's real yield is the yield-to-maturity computed on cash flows that have been indexed to the relevant consumer price index (CPI-U NSA for US TIPS; RPI for UK linkers — transitioning to RPI aligned with CPIH from 2030; HICP excluding tobacco for French OATei; Canadian CPI all-items NSA for RRB). It represents the market's required real rate of return AFTER inflation indexation — not a forecast of realized real economic growth. Real yields are a macro state variable: a rise in real yields is often a tightening of financial conditions even when nominal yields are unchanged (the 2022 real-rate shock is the canonical recent example). The four-quadrant pairing with breakeven moves — real yields up / breakevens down = real tightening; real yields down / breakevens up = reflationary easing — is the canonical desk read. Sign convention: positive daily_change_bps = real yields rose; positive z_score = current real yield is above its trailing-year mean. Critical caveat to remember: these are generic benchmark series, not actual bond-level analytics — and intermediate / long linkers carry meaningful real-rate duration, so they are NOT a pure inflation bet (2022 TIPS lost roughly 12% despite high realized CPI because the real-yield selloff dominated inflation accrual).$narr$
WHERE NOT EXISTS (
    SELECT 1
    FROM macro_data.tool_metadata
    WHERE tool_name = 'get_real_yield_level_tool'
);

-- ----------------------------------------------------------------------------
-- Stage 8 closeout note (NOT applied here):
--
-- When source-material verification completes, run:
--
--   UPDATE macro_data.tool_metadata
--   SET source_material_verified = jsonb_build_object(
--           'verifier', '<name>',
--           'date',     '<YYYY-MM-DD>',
--           'source',   'Tuckman 4e Ch. 22 (Inflation-Indexed Bonds) + per-country primary issuer documentation'
--       ),
--       updated_at = NOW()
--   WHERE tool_name = 'get_real_yield_level_tool';
--
-- Then flip the per-tool LIFECYCLE_CHECKLIST.md Stage 8 row from ⏸ to ☑
-- and update surface_contract.md §10.3 axis-3 from `in-progress` to
-- `shipped`.
-- ============================================================================
