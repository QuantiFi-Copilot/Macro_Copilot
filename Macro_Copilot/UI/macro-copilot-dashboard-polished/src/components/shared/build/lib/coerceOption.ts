// ============================================================================
// shared/build/lib/coerceOption.ts — Clamp a pre-filled param to a closed
// option set (the enum-validation guard for Build surfaces).
// ----------------------------------------------------------------------------
// WHY THIS EXISTS.  Params arriving via `?context=` (the Ask→Build hand-off
// or a hand-crafted deep link) are copied through `contextDecoder` verbatim —
// scalars are stringified, nothing is validated against any control's option
// list.  A per-tool surface then seeds its controls straight from those raw
// params (`params.field_name || DEFAULT`), which only guards the EMPTY case.
//
// A non-empty but out-of-set value (e.g. an OIS leg pre-filled with the
// sovereign-style `field_name="MID"`, which no OIS curve carries) then slips
// through to the backend and 500s.  Worse, a controlled <select value="MID">
// whose <option>s don't include "MID" renders the FIRST option (e.g.
// "PX_LAST") while React state — and therefore the outgoing request — keeps
// "MID".  The UI silently lies about what it is sending.
//
// Resolving every enum-constrained param through this guard makes the
// DISPLAYED value and the QUERIED value identical and always backend-valid:
// an out-of-set value falls back to `fallback`.  NOTE: apply this ONLY to
// params whose closed option set mirrors a backend enum (curve_family,
// tenor, field_name).  Do NOT clamp free-form values whose dropdown is a
// convenience preset rather than a constraint (e.g. `lookback_days`, which
// the backend accepts as any positive integer) — clamping those would reject
// legitimate custom values.
// ============================================================================

/** Return `value` when it is a member of `options`; otherwise `fallback`.
 *  `value` may be undefined (missing pre-fill) — that also yields `fallback`. */
export function coerceToOption(
  value: string | undefined,
  options: ReadonlyArray<{ value: string }>,
  fallback: string,
): string {
  if (value != null && options.some((o) => o.value === value)) return value;
  return fallback;
}
