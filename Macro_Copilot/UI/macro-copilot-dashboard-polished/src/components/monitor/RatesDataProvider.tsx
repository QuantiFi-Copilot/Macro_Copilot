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
  type ReactNode,
} from 'react';
import { useRatesData } from '@/hooks/useRatesData';
import type { RatesPageData } from '@/types/rates';

type RatesDataContextValue = {
  data: RatesPageData | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
};

const RatesDataCtx = createContext<RatesDataContextValue | null>(null);

export function RatesDataProvider({ children }: { children: ReactNode }) {
  // Single fetch, single state.  All pre-aggregated rate widgets read
  // from this — sharing one network request across the surface.
  const value = useRatesData();
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
