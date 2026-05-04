// ============================================================================
// WorkspacePage
// ----------------------------------------------------------------------------
// Top-level page component for /workspace.  Reads `?tool=...&...` from the
// URL, fetches the matching detail endpoint via useWorkspaceData, and renders
// the right view (Spread / CrossMarket / Butterfly / Yield / Regime /
// Scanner / Forward).  Empty URL → WorkspaceEmptyState with quick-launches.
//
// Two URL paths into the page:
//   1) Direct: ?tool=spread&curve_family=UST&short_tenor=2Y&long_tenor=10Y
//   2) From copilot: ?context=<encoded JSON tool list>
//      The page detects (2), translates it to (1) via decodeWorkspaceContext,
//      and rewrites the URL with `replace` so the back button stays clean.
// ============================================================================

import { useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AlertCircle } from 'lucide-react';
import { useWorkspaceData } from '@/hooks/useWorkspaceData';
import { decodeWorkspaceContext, TOOL_TO_VIEW } from '@/lib/workspaceContext';
import {
  paramSpecsForView,
  viewSubtitle,
  viewTitle,
  viewUsesLookbackDays,
} from '@/lib/workspaceParams';
import type { WorkspaceParams, WorkspaceViewType } from '@/types/rates';
import { WorkspaceEmptyState } from './WorkspaceEmptyState';
import { WorkspaceHeader } from './WorkspaceHeader';
import { SpreadView } from './views/SpreadView';
import { CrossMarketView } from './views/CrossMarketView';
import { ButterflyView } from './views/ButterflyView';
import { YieldView } from './views/YieldView';
import { RegimeView } from './views/RegimeView';
import { ScannerView } from './views/ScannerView';
import { ForwardView } from './views/ForwardView';
import FXSpotView from '../fx/FXSpotView';  // Import du composant FXSpotView
import FXCarryView from '../fx/FXCarryView';  // Import du composant FXCarryView
import FXForwardCurveView from '../fx/FXForwardCurveView';
import FXRealizedVolView from '../fx/FXRealizedVolView';
import FXScannerView from '../fx/FXScannerView';
import FXTradeSetupView from '../fx/FXTradeSetupView';
import FXMacroRiskOverlayView from '../fx/FXMacroRiskOverlayView';
import FXCorrelationBetaView from '../fx/FXCorrelationBetaView';
import FXRegimeClassifierView from '../fx/FXRegimeClassifierView';
import FXVolRiskPremiumView from '../fx/FXVolRiskPremiumView';

const VALID_VIEWS = new Set<WorkspaceViewType>([
  'spread',
  'cross_market',
  'butterfly',
  'yield',
  'forward',
  'regime',
  'scanner',
  'fx_spot',  
  'fx_carry', 
  'fx_forward_curve',
  'fx_scanner',
  'fx_realized_vol',
  'fx_trade_setup',
  'fx_macro_risk_overlay',
  'fx_correlation_beta',
  'fx_regime_classifier',
  'fx_vol_risk_premium',
]);

function isWorkspaceView(s: string | null | undefined): s is WorkspaceViewType {
  return !!s && VALID_VIEWS.has(s as WorkspaceViewType);
}

/** Pull the as_of_date out of a successful workspace data payload. */
function extractAsOfDate(data: ReturnType<typeof useWorkspaceData>['data']): string | null {
  if (!data) return null;
  switch (data.kind) {
    case 'spread':
      return data.data.current_metrics?.as_of_date ?? null;
    case 'cross_market':
      return data.data.current_metrics?.as_of_date ?? null;
    case 'butterfly':
      return data.data.current_metrics?.as_of_date ?? null;
    case 'yield':
      return data.data.current_metrics?.as_of_date ?? null;
    case 'regime':
      return data.data.current_metrics?.as_of_date ?? null;
    case 'scanner':
      return data.data.results?.[0]?.as_of_date ?? null;
    case 'fx_spot':  // Nouveau cas ajouté
      return data.data.current_metrics?.as_of_date ?? null; // Utiliser la date appropriée
    case 'fx_carry': // Nouveau cas ajouté
      return data.data.rows?.[0]?.spot_date ?? null; // Utiliser la date appropriée
    case 'fx_forward_curve':
      return data.data.rows?.[0]?.spot_date ?? null;
    case 'fx_scanner':
      return data.data.rows?.[0]?.as_of_date ?? null;
    case 'fx_realized_vol':
      return data.data.current_metrics?.as_of_date ?? null;
    case 'fx_trade_setup':
      return data.data.as_of_date ?? null;
    case 'fx_macro_risk_overlay':
      return data.data.as_of_date ?? null;
    case 'fx_correlation_beta':
      return data.data.as_of_date ?? null;
    case 'fx_regime_classifier':
      return data.data.as_of_date ?? null;
    case 'fx_vol_risk_premium':
      return data.data.current_metrics?.as_of_date ?? null;
    default:
      return null;
  }
}

export function WorkspacePage() {
  const [searchParams, setSearchParams] = useSearchParams();

  // -------------------------------------------------------------------------
  // Step 1: rewrite ?context= into ?tool= form on first mount.  Done in an
  // effect so we can call setSearchParams; we use replace:true so this URL
  // shape never enters history.
  // -------------------------------------------------------------------------
  useEffect(() => {
    const ctx = searchParams.get('context');
    if (!ctx) return;

    const decoded = decodeWorkspaceContext(ctx);
    if (!decoded) {
      // Bad context — drop the param so we don't loop on bad input.
      const next = new URLSearchParams(searchParams);
      next.delete('context');
      setSearchParams(next, { replace: true });
      return;
    }

    const next = new URLSearchParams();
    next.set('tool', decoded.view);
    for (const [k, v] of Object.entries(decoded.params)) {
      next.set(k, v);
    }
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  // -------------------------------------------------------------------------
  // Step 2: parse the (rewritten) URL into a stable (view, params) view-model.
  // -------------------------------------------------------------------------
  const toolParam = searchParams.get('tool');
  const view: WorkspaceViewType | null = isWorkspaceView(toolParam) ? toolParam : null;

  const params: WorkspaceParams = useMemo(() => {
    const out: WorkspaceParams = {};
    searchParams.forEach((value, key) => {
      if (key === 'tool' || key === 'context') return;
      out[key] = value;
    });
    return out;
  }, [searchParams]);

  // -------------------------------------------------------------------------
  // Step 3: fetch the detail payload.
  // -------------------------------------------------------------------------
  const { data, isLoading, error, refetch } = useWorkspaceData(view, params);

  // -------------------------------------------------------------------------
  // Empty state — no view selected.
  // -------------------------------------------------------------------------
  if (!view) {
    return (
      <div className="h-full overflow-y-auto">
        <WorkspaceEmptyState />
      </div>
    );
  }

  // Helper to mutate URL params from the header pickers.
  const handleParamChange = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === '') next.delete(key);
    else next.set(key, value);
    setSearchParams(next, { replace: false });
  };

  const handleLookbackChange = (days: number) => {
    handleParamChange('lookback_days', String(days));
  };

  const lookbackDays = params['lookback_days']
    ? Number(params['lookback_days'])
    : undefined;

  const headerSpecs = paramSpecsForView(view, params);
  const showLookback = viewUsesLookbackDays(view);
  const asOfDate = extractAsOfDate(data);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <WorkspaceHeader
        title={viewTitle(view, params)}
        subtitle={viewSubtitle(view)}
        asOfDate={asOfDate}
        params={headerSpecs}
        lookbackDays={showLookback ? lookbackDays : undefined}
        onLookbackChange={showLookback ? handleLookbackChange : undefined}
        onParamChange={handleParamChange}
        onRefresh={refetch}
        isLoading={isLoading}
      />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <ViewBody
          view={view}
          data={data}
          isLoading={isLoading}
          error={error}
        />
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Inner switch — renders the right view once data is ready, or a loading /
// error state in the meantime.  Pulled out so the header stays mounted while
// the body re-fetches on parameter changes.
// ----------------------------------------------------------------------------
function ViewBody({
  view,
  data,
  isLoading,
  error,
}: {
  view: WorkspaceViewType;
  data: ReturnType<typeof useWorkspaceData>['data'];
  isLoading: boolean;
  error: Error | null;
}) {
  // Forward view: render its own placeholder regardless of data state, since
  // the hook will throw — surfacing the error state would be confusing here.
  if (view === 'forward') return <ForwardView />;

  if (error) {
    return (
      <div className="px-6 py-10">
        <div className="card flex items-start gap-3 px-5 py-4">
          <AlertCircle size={16} className="mt-0.5 shrink-0 text-coral-300" />
          <div className="min-w-0">
            <div className="text-[12.5px] font-semibold text-fg-primary">
              Failed to load workspace data
            </div>
            <div className="mt-1 text-[11.5px] text-fg-secondary">
              {error.message}
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div className="px-6 py-10">
        <div className="card flex h-[320px] items-center justify-center text-[12px] text-fg-muted">
          Loading {view}…
        </div>
      </div>
    );
  }

  // Discriminated-union narrowing — TS will complain if a case is missed.
  switch (data.kind) {
    case 'spread':
      return <SpreadView payload={data.data} />;
    case 'cross_market':
      return <CrossMarketView payload={data.data} />;
    case 'butterfly':
      return <ButterflyView payload={data.data} />;
    case 'yield':
      return <YieldView payload={data.data} />;
    case 'regime':
      return <RegimeView payload={data.data} />;
    case 'scanner':
      return <ScannerView payload={data.data} />;
    case 'fx_spot':  // Nouveau cas ajouté
      return <FXSpotView payload={data.data} />;
    case 'fx_carry': // Nouveau cas ajouté
      return <FXCarryView payload={data.data} />;
    case 'fx_forward_curve':
      return <FXForwardCurveView payload={data.data} />;
    case 'fx_scanner':
      return <FXScannerView payload={data.data} />;
    case 'fx_realized_vol':
      return <FXRealizedVolView payload={data.data} />;
    case 'fx_trade_setup':
      return <FXTradeSetupView payload={data.data} />;
    case 'fx_macro_risk_overlay':
      return <FXMacroRiskOverlayView payload={data.data} />;
    case 'fx_correlation_beta':
      return <FXCorrelationBetaView payload={data.data} />;
    case 'fx_regime_classifier':
      return <FXRegimeClassifierView payload={data.data} />;
    case 'fx_vol_risk_premium':
      return <FXVolRiskPremiumView payload={data.data} />;
    default: {
      const _exhaustive: never = data;
      return null;
    }
  }
}

// Re-export the tool-name → view map for any other module that needs it.
export { TOOL_TO_VIEW };
