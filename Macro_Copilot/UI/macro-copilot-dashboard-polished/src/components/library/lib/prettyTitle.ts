// ============================================================================
// prettyTitle — convert a manifest tool name into a human-readable title
// ----------------------------------------------------------------------------
// Tool names in the manifest are snake_case lowercase machine ids
// (e.g. `calculate_ois_curve_spread`).  The Library UI wants them
// rendered as user-friendly titles ("OIS Curve Spread"), with three
// rules a naive title-caser can't get right:
//
//   1. Strip leading verb prefixes (calculate_, get_, scan_, classify_)
//      so the title focuses on the noun, not the implementation verb.
//
//   2. Acronyms stay uppercase: ois → OIS, pca → PCA.  Naive
//      title-casing turns these into "Ois" and "Pca" which read as
//      typos.  Single-token replacements live in TOKEN_REPLACEMENTS
//      below.
//
//   3. Compound terms join with hyphens: "cross_market" → "Cross-
//      Market", "half_life" → "Half-Life".  These are post-pass
//      replacements applied after title-casing each part.
//
// If you add a new tool whose name renders weirdly, the fix is one
// of the maps below — never reach into ToolCard / ToolDetailDrawer
// to override per-tool.  Single source of truth.
// ============================================================================

/** Whole-token replacements applied during the per-part pass.  Keys
 *  are lowercase; values are the rendered form.  Add a new entry when
 *  a new token appears in a manifest tool name (e.g. when an FX tool
 *  introduces "ndf" we'd add `'ndf': 'NDF'`). */
const TOKEN_REPLACEMENTS: Record<string, string> = {
  // Acronyms — always uppercase.
  ois: 'OIS',
  pca: 'PCA',
  rv: 'RV',
  ust: 'UST',
  // Compound single-tokens that look weird title-cased.
  zscore: 'Z-Score',
};

/** Multi-word phrases that should join with a hyphen rather than a
 *  space.  Applied AFTER the per-part title-case pass — keys are
 *  the title-cased phrase, values are the hyphenated form. */
const HYPHENATED_PHRASES: Array<[RegExp, string]> = [
  [/\bCross Market\b/g, 'Cross-Market'],
  [/\bHalf Life\b/g, 'Half-Life'],
  [/\bBeta Adjusted\b/g, 'Beta-Adjusted'],
];

/** Verb prefixes stripped from the front of a tool name before
 *  title-casing.  These exist purely as implementation conventions
 *  in the backend (`calculate_*`, `get_*`, `scan_*`, `classify_*`)
 *  and don't help the user identify what the tool does. */
const VERB_PREFIX = /^(calculate|get|scan|classify)_/;

export function prettyTitle(name: string): string {
  const stripped = name.replace(VERB_PREFIX, '');
  let title = stripped
    .split('_')
    .map((part) => {
      const lower = part.toLowerCase();
      if (TOKEN_REPLACEMENTS[lower]) return TOKEN_REPLACEMENTS[lower];
      // Default: standard title-case (first letter up, rest lower).
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join(' ');

  for (const [re, replacement] of HYPHENATED_PHRASES) {
    title = title.replace(re, replacement);
  }
  return title;
}
