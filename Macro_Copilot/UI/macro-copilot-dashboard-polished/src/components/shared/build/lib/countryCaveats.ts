// ============================================================================
// shared/build/lib/countryCaveats.ts — Per-curve_family caveat + flag registry.
// ----------------------------------------------------------------------------
// Shared infrastructure (FP9 finance-blind layer): tools that need to
// surface country-specific caveats (currently linker-domain tools — real
// yield, breakeven, real-yield curve spread, etc.) call this registry
// to look up:
//
//   - the country flag emoji or icon for the curve_family
//   - the one-line caveat copy to surface in the compact view (footer)
//     and the extended view (top-right card / methodology row)
//
// Per docs_revamped/03_standards/rendering_density.md §2.2 + §12 the
// caveat is REQUIRED disclosure (anti-pattern: hiding methodology
// entirely).  The registry is the single source of truth so every
// linker-domain tool surfaces identical caveats per curve_family —
// no drift between tools.
//
// Source: docs_revamped's metadata package
// (tmp/tool_descriptions/get_real_yield_level_tool.md §6 + §8.2) +
// the institutional doc at
// manifesto/01_instruments/rates_agent/04_inflation_indexed_bonds.md.
// Adding a new curve_family entry is a one-line addition + a code-
// review check.
// ============================================================================

export interface CountryCaveatEntry {
  /** Flag emoji or short ISO-3 country code. */
  flag: string;
  /** Short human-facing name for the linker market. */
  shortLabel: string;
  /** One-line caveat surfaced in the compact view's footer + the
   *  extended view's "Country caveat" card.  Designed to fit on a
   *  single line in the compact view. */
  caveat: string;
  /** Full-paragraph caveat — used in the extended view's methodology
   *  card and tooltip-expanded form of the compact caveat. */
  caveatLong: string;
}

const REGISTRY: Record<string, CountryCaveatEntry> = {
  USD_TIPS: {
    flag: '🇺🇸',
    shortLabel: 'TIPS',
    caveat: 'Generic benchmark; 20Y TIPS issuance discontinued in 2009.',
    caveatLong:
      'Generic real-yield benchmark for US Treasury Inflation-Protected '
      + 'Securities (CPI-U NSA, 3-month indexation lag with daily '
      + 'interpolation, par-style deflation floor at maturity).  The '
      + '20Y TIPS sector existed historically but new issuance was '
      + 'discontinued in 2009; any 20Y series should be treated as a '
      + 'constant-maturity / outstanding-sector benchmark, not as an '
      + 'actively-issued on-the-run tenor.',
  },
  GBP_LINKER: {
    flag: '🇬🇧',
    shortLabel: 'Linker',
    caveat: 'RPI → CPIH transition in 2030 bifurcates the curve.',
    caveatLong:
      'Generic real-yield benchmark for UK Index-Linked Gilts (RPI, '
      + '3-month indexation lag for newer issues / 8-month lag for '
      + 'legacy issues, no TIPS-style par deflation floor).  UK '
      + 'announced in November 2020 that RPI would be aligned to CPIH '
      + 'from 2030 with no compensation to existing holders; '
      + 'post-2030-maturity gilt cash flows blend pre/post reform and '
      + 'historical z-scores spanning the transition mix two '
      + 'methodology regimes.',
  },
  EUR_FR_LINKER: {
    flag: '🇫🇷',
    shortLabel: 'OATei',
    caveat: 'OATei (HICPxT-referenced), NOT OATi.',
    caveatLong:
      'Generic real-yield benchmark for French OATei (Obligations '
      + 'Assimilables du Trésor indexées sur l\'inflation européenne; '
      + 'euro-area HICP excluding tobacco, 3-month indexation lag '
      + 'with interpolation, par-style deflation floor at maturity).  '
      + 'Distinct from OATi (French-CPI-referenced) — French domestic '
      + 'inflation vs euro-area inflation basis requires OATi data, '
      + 'which is not in scope here.',
  },
  CAD_RRB: {
    flag: '🇨🇦',
    shortLabel: 'RRB',
    caveat: 'Declining-liquidity legacy market since November 2022.',
    caveatLong:
      'Generic real-yield benchmark for Canadian Real Return Bonds '
      + '(Canada CPI all-items NSA, approximately 3-month indexation '
      + 'lag).  Canada ceased new RRB issuance after the November 2022 '
      + 'Fall Economic Statement.  Outstanding RRBs continue to trade '
      + 'but no new issuance means declining benchmark quality and '
      + 'liquidity over time; z-scores carry a lower-confidence '
      + 'caveat than US TIPS or UK linker analogs.',
  },
};

/** Look up the caveat entry for a curve_family.  Returns null when the
 *  family is not in the registry — caller should render no caveat (NOT
 *  fall back to a generic placeholder; missing entries should fail
 *  loudly in code review). */
export function countryCaveatFor(curveFamily: string): CountryCaveatEntry | null {
  return REGISTRY[curveFamily] ?? null;
}

/** Return all registered curve_family codes — useful for tests + lint. */
export function registeredCurveFamilies(): ReadonlyArray<string> {
  return Object.keys(REGISTRY).sort();
}
