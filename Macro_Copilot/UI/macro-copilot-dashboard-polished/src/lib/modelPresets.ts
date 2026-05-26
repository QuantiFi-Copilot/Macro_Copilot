// ============================================================================
// src/lib/modelPresets.ts — preset arrays shared by rich-model module specs.
// ----------------------------------------------------------------------------
// Stage 4b — extracted from src/lib/modelRegistry.ts so per-module
// ``module.ts`` files can value-import the presets without triggering
// the runtime cycle:
//
//   modelRegistry → @/modules → module.ts → modelRegistry
//
// (modelRegistry imports ``ALL_PRIMITIVE_MODULES`` to derive ``MODELS``;
// any module.ts that value-imports back into modelRegistry would land
// before modelRegistry finished initialising its ``export const`` bodies,
// leaving the value undefined.)  Hosting the bare arrays in their own
// file with NO @/modules dependency breaks the cycle.
//
// modelRegistry re-exports both constants for backward compatibility
// with any non-module consumer.
// ============================================================================

/** Default lookback-window preset values (calendar days) for the
 *  ``lookback_slider`` control.  Used by rich-model ``paramHints`` to
 *  populate the slider step / snap-to-preset behaviour. */
export const DEFAULT_LOOKBACK_PRESETS = [365, 730, 1095, 1825, 3650];

/** Default rolling-window preset values (trading days) for the
 *  ``window_slider`` control — rolling-regression / beta-adjusted
 *  hedge-ratio windows. */
export const DEFAULT_WINDOW_PRESETS = [22, 60, 126, 252, 504];
