// ============================================================================
// paramSpecs.ts — per-primitive-view parameter spec declarations.
// ----------------------------------------------------------------------------
// R6.2 / PR2.  Single source of truth for the dropdown controls each
// typed primitive view (Spread / CrossMarket / Butterfly / Yield /
// Regime / Scanner / Forward) surfaces in its header.
//
// Each spec declares:
//   - the URL param key it binds to (matches the typed-detail endpoint's
//     query parameter — e.g. ``curve_family``, ``short_tenor``)
//   - the human label rendered above the dropdown
//   - the available options + default
//   - whether changing it should fire a refetch (vs decorative)
//
// PR2 widens the typed views' control sets so the user can change the
// observation field (``field_name``) and the displayed lookback
// (``lookback_days``) inline — the backend endpoints already accept
// these but the R6.2 specs hadn't exposed them.  See
// ``api/routes/rates/detail.py`` for the per-endpoint param signature.
//
// The legacy ``lib/workspaceParams.ts`` had a similar pattern; we don't
// import it because the legacy file was deleted in Phase 6 and the
// post-cleanup tree shouldn't bring it back without the surrounding
// helpers it depended on.  This module is narrower and lives next to
// the views it serves.
// ============================================================================

import type { PrimitiveViewKind } from './contextDecoder';

/** One dropdown control on a primitive view's header. */
export interface ParamSpec {
  /** URL-param key (e.g. ``curve_family``).  Matches the typed-detail
   *  endpoint's query-parameter name.  Becomes part of the encoded
   *  ``?context=`` blob's params dict. */
  key: string;
  /** Label rendered above the dropdown. */
  label: string;
  /** Closed list of selectable options. */
  options: Array<{ value: string; label: string }>;
  /** Default applied when the URL doesn't carry the key. */
  defaultValue: string;
}

// ----------------------------------------------------------------------------
// Common option sets (the user audit's "give me dropdowns for curve /
// tenor / lookback" ask).  Shared across views so the labels are
// identical wherever a parameter appears.
// ----------------------------------------------------------------------------

const CURVE_FAMILIES: ParamSpec['options'] = [
  { value: 'UST', label: 'UST · US Treasury' },
  { value: 'DE_BUND', label: 'BUND · Germany' },
  { value: 'IT_BTP', label: 'BTP · Italy' },
  { value: 'FR_OAT', label: 'OAT · France' },
  { value: 'ES_BONO', label: 'BONO · Spain' },
  { value: 'UK_GILT', label: 'GILT · UK' },
  { value: 'JP_JGB', label: 'JGB · Japan' },
];

const TENORS: ParamSpec['options'] = [
  { value: '2Y', label: '2Y' },
  { value: '3Y', label: '3Y' },
  { value: '5Y', label: '5Y' },
  { value: '7Y', label: '7Y' },
  { value: '10Y', label: '10Y' },
  { value: '20Y', label: '20Y' },
  { value: '30Y', label: '30Y' },
];

/** Trading-day lookback presets for the typed-detail endpoints that
 *  accept ``lookback_days``.  Bare numbers (no "y" / "d" suffix) so
 *  they pass straight through ``coerceLookbackDays`` on the fetch
 *  side without re-parsing. */
const LOOKBACK_DAYS: ParamSpec['options'] = [
  { value: '63', label: '3M (63d)' },
  { value: '126', label: '6M (126d)' },
  { value: '252', label: '1Y (252d)' },
  { value: '504', label: '2Y (504d)' },
  { value: '1260', label: '5Y (1260d)' },
  { value: '2520', label: '10Y (2520d)' },
];

/** Regime classifier's named lookback window — different vocabulary
 *  from the numeric lookback_days; named periods that map to the
 *  classifier's hard-coded window set. */
const REGIME_LOOKBACK_PERIODS: ParamSpec['options'] = [
  { value: '1d', label: '1d (daily)' },
  { value: '5d', label: '5d (weekly)' },
  { value: '22d', label: '22d (monthly)' },
  { value: '63d', label: '63d (quarterly)' },
];

/** Bloomberg observation field options accepted by the rates typed-
 *  detail endpoints.  ``YLD_YTM_MID`` is the universal default
 *  matching ``default_field_name`` in every primitive's config.yaml;
 *  the alternates are the most-frequently-requested overrides
 *  (bid / ask / discount / par yield).  Closed list because the
 *  underlying database column carries the exact Bloomberg mnemonic
 *  and free-text would produce empty result sets on typos. */
const FIELD_NAMES: ParamSpec['options'] = [
  { value: 'YLD_YTM_MID', label: 'YLD_YTM_MID · mid yield-to-maturity' },
  { value: 'YLD_YTM_BID', label: 'YLD_YTM_BID · bid yield' },
  { value: 'YLD_YTM_ASK', label: 'YLD_YTM_ASK · ask yield' },
  { value: 'YLD_CNV_MID', label: 'YLD_CNV_MID · conventional yield' },
];

// ----------------------------------------------------------------------------
// Per-view spec lists.  Order is the visible order in the header strip.
// ----------------------------------------------------------------------------

/** Returns the dropdown spec list for a primitive-view kind.  Returns
 *  an empty array for views that don't take params (scanner has no
 *  per-curve narrowing today; forward is a placeholder). */
export function paramSpecsFor(kind: PrimitiveViewKind): ParamSpec[] {
  switch (kind) {
    case 'spread':
      return [
        {
          key: 'curve_family',
          label: 'Curve',
          options: CURVE_FAMILIES,
          defaultValue: 'UST',
        },
        {
          key: 'short_tenor',
          label: 'Short tenor',
          options: TENORS,
          defaultValue: '2Y',
        },
        {
          key: 'long_tenor',
          label: 'Long tenor',
          options: TENORS,
          defaultValue: '10Y',
        },
        {
          key: 'lookback_days',
          label: 'Lookback',
          options: LOOKBACK_DAYS,
          defaultValue: '252',
        },
        // PR2 — expose the observation field; ``/detail/spread`` accepts
        // it and the backend defaults to ``YLD_YTM_MID`` but bid/ask
        // and conventional yield are valid overrides.
        {
          key: 'field_name',
          label: 'Field',
          options: FIELD_NAMES,
          defaultValue: 'YLD_YTM_MID',
        },
      ];
    case 'cross_market':
      return [
        {
          key: 'curve_family_1',
          label: 'Curve A',
          options: CURVE_FAMILIES,
          defaultValue: 'IT_BTP',
        },
        {
          key: 'curve_family_2',
          label: 'Curve B',
          options: CURVE_FAMILIES,
          defaultValue: 'DE_BUND',
        },
        {
          key: 'tenor',
          label: 'Tenor',
          options: TENORS,
          defaultValue: '10Y',
        },
        {
          key: 'lookback_days',
          label: 'Lookback',
          options: LOOKBACK_DAYS,
          defaultValue: '252',
        },
        // PR2 — field_name override (Optional[str] on the endpoint, so
        // the empty value here is also valid; the option list always
        // ships the canonical default for cleanliness).
        {
          key: 'field_name',
          label: 'Field',
          options: FIELD_NAMES,
          defaultValue: 'YLD_YTM_MID',
        },
      ];
    case 'butterfly':
      return [
        {
          key: 'curve_family',
          label: 'Curve',
          options: CURVE_FAMILIES,
          defaultValue: 'UST',
        },
        {
          key: 'short_tenor',
          label: 'Short',
          options: TENORS,
          defaultValue: '2Y',
        },
        {
          key: 'belly_tenor',
          label: 'Belly',
          options: TENORS,
          defaultValue: '5Y',
        },
        {
          key: 'long_tenor',
          label: 'Long',
          options: TENORS,
          defaultValue: '10Y',
        },
        {
          key: 'lookback_days',
          label: 'Lookback',
          options: LOOKBACK_DAYS,
          defaultValue: '252',
        },
        // PR2 — butterfly endpoint accepts Optional[str] field_name.
        {
          key: 'field_name',
          label: 'Field',
          options: FIELD_NAMES,
          defaultValue: 'YLD_YTM_MID',
        },
      ];
    case 'yield':
      return [
        {
          key: 'curve_family',
          label: 'Curve',
          options: CURVE_FAMILIES,
          defaultValue: 'UST',
        },
        {
          key: 'tenor',
          label: 'Tenor',
          options: TENORS,
          defaultValue: '10Y',
        },
        // PR2 — yield endpoint accepts lookback_days + field_name; the
        // previous spec only exposed curve+tenor which left the user
        // unable to widen the history window or switch off mid yield.
        {
          key: 'lookback_days',
          label: 'Lookback',
          options: LOOKBACK_DAYS,
          defaultValue: '252',
        },
        {
          key: 'field_name',
          label: 'Field',
          options: FIELD_NAMES,
          defaultValue: 'YLD_YTM_MID',
        },
      ];
    case 'regime':
      return [
        {
          key: 'curve_family',
          label: 'Curve',
          options: CURVE_FAMILIES,
          defaultValue: 'UST',
        },
        {
          key: 'front_tenor',
          label: 'Front',
          options: TENORS,
          defaultValue: '2Y',
        },
        {
          key: 'back_tenor',
          label: 'Back',
          options: TENORS,
          defaultValue: '10Y',
        },
        {
          key: 'lookback_period',
          label: 'Window',
          options: REGIME_LOOKBACK_PERIODS,
          defaultValue: '22d',
        },
        // PR2 — regime classifier endpoint accepts Optional[str]
        // field_name; same FIELD_NAMES vocabulary.
        {
          key: 'field_name',
          label: 'Field',
          options: FIELD_NAMES,
          defaultValue: 'YLD_YTM_MID',
        },
      ];
    case 'scanner':
      return [
        {
          key: 'top_n',
          label: 'Top N',
          options: [
            { value: '4', label: '4' },
            { value: '8', label: '8' },
            { value: '12', label: '12' },
            { value: '20', label: '20' },
          ],
          defaultValue: '8',
        },
        {
          key: 'min_abs_z_score',
          label: 'Min |z|',
          options: [
            { value: '0.5', label: '0.5' },
            { value: '1', label: '1.0' },
            { value: '1.5', label: '1.5' },
            { value: '2', label: '2.0' },
          ],
          defaultValue: '1.5',
        },
      ];
    case 'forward':
      // No controls — the view is a placeholder until /detail/forward
      // ships.  When it does, fold the new spec in here.
      return [];
  }
}

/** Resolve the current value for a param: URL value (if present) wins
 *  over the default declared in the spec. */
export function resolveParamValue(
  spec: ParamSpec,
  current: Record<string, string> | undefined,
): string {
  const url = current?.[spec.key];
  if (url != null && url !== '') return url;
  return spec.defaultValue;
}

// ---------------------------------------------------------------------------
// PR-B-β — required-param taxonomy per typed-view kind.
// ---------------------------------------------------------------------------
//
// Used by ``MultiPrimitiveCard`` / ``VirtualPrimitiveCanvas`` to decide
// whether an Ask-handoff card should render the missing-param tile
// instead of silently folding ``spec.defaultValue`` and producing a
// misleading fake card (e.g. ``IT_BTP-DE_BUND`` standing in for a
// ``UST-Bund`` call whose ``curve_family_1`` / ``curve_family_2`` got
// dropped upstream).
//
// What "required" means here
// --------------------------
// A required param is one without which the card's output would be
// SEMANTICALLY meaningless or misleading — e.g. a cross-market spread
// is undefined without both curve families.  Knobs that are pure
// display choices (``lookback_days``, ``field_name``) are NOT required:
// when missing they fall back to the spec default cleanly, and the
// user sees a still-correct card.
//
// Library-blank opens DO NOT trigger this check — the missing-param
// rule is gated on the ``handoff === 'ask'`` URL signal so blank
// surfaces continue to render with defaults as before.

const REQUIRED_BY_KIND: Record<PrimitiveViewKind, ReadonlyArray<string>> = {
  spread: ['curve_family', 'short_tenor', 'long_tenor'],
  cross_market: ['curve_family_1', 'curve_family_2', 'tenor'],
  butterfly: ['curve_family', 'short_tenor', 'belly_tenor', 'long_tenor'],
  yield: ['curve_family', 'tenor'],
  regime: ['curve_family', 'front_tenor', 'back_tenor'],
  scanner: [],
  forward: [],
};

/** Returns the names of params the kind cannot render meaningfully
 *  without.  Pure, closed-family — adding a new ``PrimitiveViewKind``
 *  requires adding an entry here too. */
export function requiredParamsFor(
  kind: PrimitiveViewKind,
): ReadonlyArray<string> {
  return REQUIRED_BY_KIND[kind] ?? [];
}

/** Compute the list of required-but-missing params for a typed-view
 *  card given the raw URL params (BEFORE spec defaults are folded in).
 *  Pure.  Used by the Ask-handoff missing-param card.  An empty list
 *  means the card is safe to fetch. */
export function missingRequiredTypedParams(
  kind: PrimitiveViewKind,
  rawParams: Record<string, string> | undefined,
): string[] {
  const out: string[] = [];
  for (const key of requiredParamsFor(kind)) {
    const v = rawParams?.[key];
    if (v == null || v === '') out.push(key);
  }
  return out;
}
