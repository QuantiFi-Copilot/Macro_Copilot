// ============================================================================
// shared/build/lib/format.ts — Number / date formatters for KPI rendering.
// ----------------------------------------------------------------------------
// Centralised so every shell + per-tool wrapper formats values
// identically.  Per-tool wrappers should NOT hand-roll formatting —
// they call these helpers.
// ============================================================================

/** Format a signed numeric value with an explicit ``+``/``-`` prefix
 *  and fixed decimal precision.  Returns ``"—"`` for null/undefined/NaN. */
export function signedFixed(
  value: number | null | undefined,
  decimals: number,
): string {
  if (value == null || Number.isNaN(value)) return '—';
  const fixed = Math.abs(value).toFixed(decimals);
  if (value > 0) return `+${fixed}`;
  if (value < 0) return `-${fixed}`;
  return fixed;
}

/** Format an unsigned numeric value with fixed decimal precision.
 *  Returns ``"—"`` for null/undefined/NaN. */
export function unsignedFixed(
  value: number | null | undefined,
  decimals: number,
): string {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(decimals);
}

/** Format a signed value with an explicit ``+``/``-`` prefix and an
 *  optional unit suffix.  Convenience over signedFixed for inline-
 *  rendered values where the unit is part of the formatted string. */
export function signedFixedWithUnit(
  value: number | null | undefined,
  decimals: number,
  unit: string,
): string {
  const v = signedFixed(value, decimals);
  if (v === '—') return v;
  return `${v}${unit}`;
}

/** Format a percentile (0-100) with a positional suffix (1st, 2nd, 3rd,
 *  Nth).  Returns ``"—"`` for null/undefined/NaN. */
export function percentileLabel(
  p: number | null | undefined,
): string {
  if (p == null || Number.isNaN(p)) return '—';
  const n = Math.round(p);
  const lastTwo = n % 100;
  const lastDigit = n % 10;
  let suffix = 'th';
  if (lastTwo < 11 || lastTwo > 13) {
    if (lastDigit === 1) suffix = 'st';
    else if (lastDigit === 2) suffix = 'nd';
    else if (lastDigit === 3) suffix = 'rd';
  }
  return `${n}${suffix}`;
}

/** Format an observation count with thousands separators. */
export function observationCount(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  return n.toLocaleString();
}

/** Format a date string (YYYY-MM-DD) as a long-form display.  Returns
 *  the input unchanged on parse failure. */
export function longDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr + 'T00:00:00Z');
    if (Number.isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return dateStr;
  }
}

/** Compute the ratio of bps to percent for the inline-subtext pattern
 *  shown in the compact view ("(+0.065%)" under "+6.5 bp").  Returns
 *  ``null`` when the input is null. */
export function bpsAsPercentSubtext(
  bps: number | null | undefined,
  decimals: number = 3,
): string | undefined {
  if (bps == null || Number.isNaN(bps)) return undefined;
  return `(${signedFixed(bps / 100, decimals)}%)`;
}
