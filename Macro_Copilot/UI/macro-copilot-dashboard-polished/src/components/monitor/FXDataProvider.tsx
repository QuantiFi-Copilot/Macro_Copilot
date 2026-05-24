// ============================================================================
// FXDataProvider
// ----------------------------------------------------------------------------
// Page-level fetch for the FX endpoints consumed by the *non-
// parameterized* widgets and by the sidebar's "Today" panel: scanner,
// EURUSD spot snapshot, 1M carry. The carry payload here is kept for
// the sidebar live-scope summary ("N pairs scanned · M carry rows")
// and for FXAgentPage's headline — the actual FXCarryWidget and
// FXForwardCurveWidget became parameterized in Phase A step 8 and do
// their own per-instance fetches via fxApi (so multiple carry-scanner
// widgets with different tenors / rank modes can coexist on one
// surface).
//
// Mirrors RatesDataProvider exactly — same hook→context shape, same
// strict + optional consumer pattern.  See that file for the original
// rationale (loading state coherence, sidebar Today panel reading the
// same data without a separate fetch).
//
// Mounted in AppShell next to RatesDataProvider so the sidebar can
// surface live FX state on every widget route, not just /fx.  Future
// agent providers (Credit, MacroEquity, …) will adopt the same
// pattern.
// ============================================================================

import {
  createContext,
  useContext,
  type ReactNode,
} from 'react';
import { useFxData } from '@/hooks/useFxData';
import type { FXPageData } from '@/types/fx';

type FXDataContextValue = {
  data: FXPageData | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
};

const FXDataCtx = createContext<FXDataContextValue | null>(null);

export function FXDataProvider({ children }: { children: ReactNode }) {
  // Single fetch, single state. Consumed by the pre-aggregated FX
  // widgets (FXSpotSnapshotWidget, FXScannerWidget), the sidebar
  // Today panel, and the FXAgentPage headline. Parameterized widgets
  // (FXCarryWidget, FXForwardCurveWidget) fetch independently.
  const value = useFxData();
  return <FXDataCtx.Provider value={value}>{children}</FXDataCtx.Provider>;
}

export function useFxDataContext(): FXDataContextValue {
  const ctx = useContext(FXDataCtx);
  if (!ctx) {
    throw new Error(
      'useFxDataContext must be used inside <FXDataProvider>. ' +
        'Pre-aggregated FX widgets need this context to share their fetch.',
    );
  }
  return ctx;
}

/** Same as `useFxDataContext` but returns null instead of throwing
 *  when no provider is mounted.  Use this in components that may be
 *  rendered in contexts where FX data isn't available — e.g. the
 *  Sidebar, which is mounted on layouts that may or may not wrap in
 *  FXDataProvider.  Consumers should treat null as "data not
 *  available" and degrade gracefully. */
export function useOptionalFxDataContext(): FXDataContextValue | null {
  return useContext(FXDataCtx);
}
