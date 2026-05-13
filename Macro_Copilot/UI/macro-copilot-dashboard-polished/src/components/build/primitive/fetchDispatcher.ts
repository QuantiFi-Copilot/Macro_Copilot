// ============================================================================
// fetchDispatcher.ts — typed-detail fetcher for a decoded primitive.
// ----------------------------------------------------------------------------
// Extracted from VirtualPrimitiveCanvas in R6.3 so both the single-card
// canvas AND the multi-card comparison canvas can reuse the dispatch
// without duplication.
//
// The function takes a ``DecodedTypedPrimitive`` (one of the seven view
// kinds, excluding the builder variant which redirects) and returns a
// discriminated ``Payload`` the views render against.  Params defaults
// + lookback-day coercion live here so the caller doesn't need to
// re-implement them.
// ============================================================================

import {
  fetchDetailSpread,
  fetchDetailCrossMarket,
  fetchDetailButterfly,
  fetchDetailYield,
  fetchDetailRegime,
  fetchScanner,
} from '@/services/ratesApi';
import type {
  ButterflyOutput,
  CrossMarketSpreadOutput,
  CurveSpreadOutput,
  RegimeOutput,
  ScannerResponse,
  YieldLevelOutput,
} from '@/types/rates';
import type { DecodedPrimitive } from './contextDecoder';

/** Discriminated view payload — one variant per primitive view kind. */
export type Payload =
  | { kind: 'spread'; data: CurveSpreadOutput }
  | { kind: 'cross_market'; data: CrossMarketSpreadOutput }
  | { kind: 'butterfly'; data: ButterflyOutput }
  | { kind: 'yield'; data: YieldLevelOutput }
  | { kind: 'regime'; data: RegimeOutput }
  | { kind: 'scanner'; data: ScannerResponse }
  | { kind: 'forward'; data: null };

/** ``DecodedPrimitive`` minus the builder variant — the dispatcher
 *  only handles typed primitives; builder routing happens upstream
 *  via a useEffect redirect.  Exposed for callers that need to gate
 *  before calling ``dispatchFetch``. */
export type DecodedTypedPrimitive = Exclude<DecodedPrimitive, { kind: 'builder' }>;

export function isTypedPrimitive(
  decoded: DecodedPrimitive,
): decoded is DecodedTypedPrimitive {
  return decoded.kind !== 'builder';
}

/** Coerce a lookback-days URL param into the number the typed-detail
 *  endpoint expects.  Accepts bare integers (``"252"``) and the
 *  ``"<N>y"`` shorthand sometimes emitted by the chat layer (``"2y"``
 *  → 504 trading days). */
export function coerceLookbackDays(raw: string | undefined): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw);
  if (Number.isFinite(n) && n > 0) return Math.floor(n);
  const m = raw.match(/^(\d+)y$/i);
  if (m) return Number(m[1]) * 252;
  return undefined;
}

/** Fetch the typed-detail payload for ``decoded``.  Returns a
 *  discriminated ``Payload`` the views render against.  Throws on
 *  transport-level errors; the forward placeholder is a no-op that
 *  resolves immediately. */
export async function dispatchFetch(
  decoded: DecodedTypedPrimitive,
): Promise<Payload> {
  const p = decoded.params;
  const lookback = coerceLookbackDays(p['lookback_days']);
  const field = p['field_name'] || undefined;

  switch (decoded.kind) {
    case 'spread': {
      const data = await fetchDetailSpread({
        curve_family: p['curve_family'] ?? 'UST',
        short_tenor: p['short_tenor'] || undefined,
        long_tenor: p['long_tenor'] || undefined,
        lookback_days: lookback,
        field_name: field,
      });
      return { kind: 'spread', data };
    }
    case 'cross_market': {
      const data = await fetchDetailCrossMarket({
        curve_family_1: p['curve_family_1'] ?? 'IT_BTP',
        curve_family_2: p['curve_family_2'] ?? 'DE_BUND',
        tenor: p['tenor'] || undefined,
        lookback_days: lookback,
        field_name: field,
      });
      return { kind: 'cross_market', data };
    }
    case 'butterfly': {
      const data = await fetchDetailButterfly({
        curve_family: p['curve_family'] ?? 'UST',
        short_tenor: p['short_tenor'] || undefined,
        belly_tenor: p['belly_tenor'] || undefined,
        long_tenor: p['long_tenor'] || undefined,
        lookback_days: lookback,
        field_name: field,
      });
      return { kind: 'butterfly', data };
    }
    case 'yield': {
      const data = await fetchDetailYield({
        curve_family: p['curve_family'] ?? 'UST',
        tenor: p['tenor'] ?? '10Y',
        lookback_days: lookback,
        field_name: field,
      });
      return { kind: 'yield', data };
    }
    case 'regime': {
      const data = await fetchDetailRegime({
        curve_family: p['curve_family'] ?? 'UST',
        front_tenor: p['front_tenor'] || undefined,
        back_tenor: p['back_tenor'] || undefined,
        lookback_period: p['lookback_period'] || undefined,
        field_name: field,
      });
      return { kind: 'regime', data };
    }
    case 'scanner': {
      const top_n = Number(p['top_n']);
      const min_abs = Number(p['min_abs_z_score']);
      const data = await fetchScanner({
        top_n: Number.isFinite(top_n) && top_n > 0 ? top_n : 8,
        min_abs_z_score: Number.isFinite(min_abs) ? min_abs : 1.5,
      });
      return { kind: 'scanner', data };
    }
    case 'forward':
      // Placeholder view — no fetch, no error path.  Caller renders
      // ForwardPrimitiveView directly.
      return { kind: 'forward', data: null };
  }
}
