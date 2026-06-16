// ============================================================================
// RatesDataProvider
// ----------------------------------------------------------------------------
// Page-level fetch for the 5 pre-aggregated rates endpoints (yield-
// snapshot, scanner, cross-market, curve-shapes, regimes), exposed via
// context so multiple pre-aggregated widgets on the same surface share
// one fetch instead of duplicating requests.
//
// Parameterized widgets (yield_level, spread_chart, cross_market_spread)
// do NOT read from this context — they fetch independently with their
// own params via the rates detail endpoints.
//
// Why a context instead of just calling `useRatesData` per widget:
//   - 6 pre-aggregated widget instances on a page would otherwise fan
//     out into 6 × 5 = 30 requests.  The context collapses that to 5.
//   - Loading + error states stay coherent across the page (one
//     "rates loading" indicator vs flicker per card).
//   - The same hook works on Monitor and Rates Agent without changes.
// ============================================================================

import {
  createContext,
  useContext,
  useState,
  type ReactNode,
} from 'react';
import { useRatesData } from '@/hooks/useRatesData';
import type { RatesPageData } from '@/types/rates';

type RatesDataContextValue = {
  data: RatesPageData | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
  /** Global as-of date (YYYY-MM-DD) for the page; null = latest live data. */
  asOf: string | null;
  setAsOf: (d: string | null) => void;
};

const RatesDataCtx = createContext<RatesDataContextValue | null>(null);

export function RatesDataProvider({ children }: { children: ReactNode }) {
  // Single fetch, single state.  All pre-aggregated rate widgets read
  // from this — sharing one network request across the surface.  A global
  // as-of date (null = latest live data) threads into every card request so
  // the whole page can be viewed as of a historical trade date.
  const [asOf, setAsOf] = useState<string | null>(null);
  const base = useRatesData(asOf ?? undefined);
  const value = { ...base, asOf, setAsOf };
  return <RatesDataCtx.Provider value={value}>{children}</RatesDataCtx.Provider>;
}

export function useRatesDataContext(): RatesDataContextValue {
  const ctx = useContext(RatesDataCtx);
  if (!ctx) {
    throw new Error(
      'useRatesDataContext must be used inside <RatesDataProvider>. ' +
        'Pre-aggregated rate widgets need this context to share their fetch.',
    );
  }
  return ctx;
}

/** Same as `useRatesDataContext` but returns null instead of throwing
 *  when no provider is mounted.  Use this in components that may be
 *  rendered in contexts where rates data isn't available — e.g. the
 *  Sidebar, which is mounted on the legacy three-column layout that
 *  doesn't currently wrap in RatesDataProvider.  Consumers should
 *  treat null as "data not available" and degrade gracefully. */
export function useOptionalRatesDataContext(): RatesDataContextValue | null {
  return useContext(RatesDataCtx);
}
