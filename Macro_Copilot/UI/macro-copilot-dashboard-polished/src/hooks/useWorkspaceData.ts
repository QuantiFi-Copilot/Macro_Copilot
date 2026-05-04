// ============================================================================
// useWorkspaceData
// ----------------------------------------------------------------------------
// Generic detail-endpoint fetcher for the workspace.  Given a tool view type
// and a flat parameter dictionary parsed from the URL, picks the right
// /api/v1/rates/detail/* endpoint, fetches it, and exposes loading/error
// state plus a refetch handle.
//
// Scanner uses the existing /scanner card endpoint (no separate detail
// endpoint exists); the hook handles that branch too so the workspace
// scanner view can use a single data-loading interface.
// ============================================================================

import { useCallback, useEffect, useState } from 'react';
import {
  fetchDetailButterfly,
  fetchDetailCrossMarket,
  fetchDetailRegime,
  fetchDetailSpread,
  fetchDetailYield,
  fetchScanner,
} from '@/services/ratesApi';
import type {
  ButterflyOutput,
  CrossMarketSpreadOutput,
  CurveSpreadOutput,
  RegimeOutput,
  ScannerResponse,
  WorkspaceParams,
  WorkspaceViewType,
  YieldLevelOutput,
} from '@/types/rates';

import {
  fetchDetailFXCarry,
  fetchDetailFXCorrelationBeta,
  fetchDetailFXDataHealth,
  fetchDetailFXForwardCurve,
  fetchDetailFXMacroRiskOverlay,
  fetchDetailFXRealizedVol,
  fetchDetailFXRegimeClassifier,
  fetchDetailFXSpotLevel,
  fetchDetailFXTradeSetup,
  fetchDetailFXVolRiskPremium,
  fetchFXScanner,
} from '@/services/fxApi';

import type {
  FXCarryResponse,
  FXCorrelationBetaResponse,
  FXDataHealthResponse,
  FXForwardCurveResponse,
  FXMacroRiskOverlayResponse,
  FXRealizedVolResponse,
  FXRegimeClassifierResponse,
  FXScannerResponse,
  FXSpotLevelResponse,
  FXTradeSetupResponse,
  FXVolRiskPremiumResponse,
} from '@/types/fx';

// Discriminated union — each view returns its own detail payload type so
// downstream view components can narrow without `any`.
export type WorkspaceData =
  | { kind: 'spread'; data: CurveSpreadOutput }
  | { kind: 'cross_market'; data: CrossMarketSpreadOutput }
  | { kind: 'butterfly'; data: ButterflyOutput }
  | { kind: 'yield'; data: YieldLevelOutput }
  | { kind: 'regime'; data: RegimeOutput }
  | { kind: 'scanner'; data: ScannerResponse }
  | { kind: 'fx_data_health'; data: FXDataHealthResponse }
  | { kind: 'fx_spot'; data: FXSpotLevelResponse }
  | { kind: 'fx_carry'; data: FXCarryResponse }
  | { kind: 'fx_forward_curve'; data: FXForwardCurveResponse }
  | { kind: 'fx_scanner'; data: FXScannerResponse }
  | { kind: 'fx_realized_vol'; data: FXRealizedVolResponse }
  | { kind: 'fx_trade_setup'; data: FXTradeSetupResponse }
  | { kind: 'fx_macro_risk_overlay'; data: FXMacroRiskOverlayResponse }
  | { kind: 'fx_correlation_beta'; data: FXCorrelationBetaResponse }
  | { kind: 'fx_regime_classifier'; data: FXRegimeClassifierResponse }
  | { kind: 'fx_vol_risk_premium'; data: FXVolRiskPremiumResponse };

export type UseWorkspaceDataResult = {
  data: WorkspaceData | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
};

/**
 * Pull a parameter from the URL params dict, falling back to a default.
 * Empty strings count as missing.
 */
function pick(
  params: WorkspaceParams,
  key: string,
  fallback?: string,
): string | undefined {
  const v = params[key];
  if (v === undefined || v === '') return fallback;
  return v;
}

/**
 * Pull lookback_days as a number from URL params (string by default).
 */
function pickLookback(params: WorkspaceParams): number | undefined {
  const raw = params['lookback_days'];
  if (!raw) return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

export function useWorkspaceData(
  toolType: WorkspaceViewType | null,
  params: WorkspaceParams,
): UseWorkspaceDataResult {
  const [data, setData] = useState<WorkspaceData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // Stable string fingerprint of (toolType, params) so we can use it as the
  // effect dep without re-fetching on every parent re-render.
  const fingerprint = JSON.stringify({ toolType, params });

  const load = useCallback(async () => {
    if (!toolType) {
      setData(null);
      setIsLoading(false);
      setError(null);
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      let result: WorkspaceData;

      switch (toolType) {
        case 'spread': {
          const cf = pick(params, 'curve_family');
          if (!cf) throw new Error('spread view requires curve_family');
          const out = await fetchDetailSpread({
            curve_family: cf,
            short_tenor: pick(params, 'short_tenor', '2Y'),
            long_tenor: pick(params, 'long_tenor', '10Y'),
            lookback_days: pickLookback(params),
            field_name: pick(params, 'field_name'),
          });
          result = { kind: 'spread', data: out };
          break;
        }

        case 'cross_market': {
          const cf1 = pick(params, 'curve_family_1');
          const cf2 = pick(params, 'curve_family_2');
          if (!cf1 || !cf2)
            throw new Error('cross_market view requires curve_family_1 and curve_family_2');
          const out = await fetchDetailCrossMarket({
            curve_family_1: cf1,
            curve_family_2: cf2,
            tenor: pick(params, 'tenor', '10Y'),
            lookback_days: pickLookback(params),
            field_name: pick(params, 'field_name'),
          });
          result = { kind: 'cross_market', data: out };
          break;
        }

        case 'butterfly': {
          const cf = pick(params, 'curve_family');
          if (!cf) throw new Error('butterfly view requires curve_family');
          const out = await fetchDetailButterfly({
            curve_family: cf,
            short_tenor: pick(params, 'short_tenor', '2Y'),
            belly_tenor: pick(params, 'belly_tenor', '5Y'),
            long_tenor: pick(params, 'long_tenor', '10Y'),
            lookback_days: pickLookback(params),
            field_name: pick(params, 'field_name'),
          });
          result = { kind: 'butterfly', data: out };
          break;
        }

        case 'yield': {
          const cf = pick(params, 'curve_family');
          const tenor = pick(params, 'tenor');
          if (!cf || !tenor)
            throw new Error('yield view requires curve_family and tenor');
          const out = await fetchDetailYield({
            curve_family: cf,
            tenor,
            lookback_days: pickLookback(params),
            field_name: pick(params, 'field_name'),
          });
          result = { kind: 'yield', data: out };
          break;
        }

        case 'regime': {
          const cf = pick(params, 'curve_family');
          if (!cf) throw new Error('regime view requires curve_family');
          const out = await fetchDetailRegime({
            curve_family: cf,
            front_tenor: pick(params, 'front_tenor', '2Y'),
            back_tenor: pick(params, 'back_tenor', '10Y'),
            lookback_period: pick(params, 'lookback_period', '22d'),
            field_name: pick(params, 'field_name'),
          });
          result = { kind: 'regime', data: out };
          break;
        }

        case 'scanner': {
          const out = await fetchScanner({
            top_n: params['top_n']
              ? Number(params['top_n'])
              : undefined,
            min_abs_z_score: params['min_abs_z_score']
              ? Number(params['min_abs_z_score'])
              : undefined,
          });
          result = { kind: 'scanner', data: out };
          break;
        }

        case 'fx_data_health': {
          const out = await fetchDetailFXDataHealth({
            lookback_days: pickLookback(params),
            stale_after_days: params['stale_after_days']
              ? Number(params['stale_after_days'])
              : undefined,
            field_name: pick(params, 'field_name'),
          });

          result = { kind: 'fx_data_health', data: out };
          break;
        }

        case 'fx_spot': {
          const pair = pick(params, 'pair', 'EURUSD');
          if (!pair) throw new Error('fx_spot view requires pair');

          const out = await fetchDetailFXSpotLevel({
            pair,
            lookback_days: pickLookback(params),
            field_name: pick(params, 'field_name'),
          });

          result = { kind: 'fx_spot', data: out };
          break;
        }

        case 'fx_carry': {
          const out = await fetchDetailFXCarry({
            tenor: pick(params, 'tenor', '1M'),
          });

          result = { kind: 'fx_carry', data: out };
          break;
        }

        case 'fx_forward_curve': {
          const pair = pick(params, 'pair', 'EURUSD');
          if (!pair) throw new Error('fx_forward_curve view requires pair');

          const out = await fetchDetailFXForwardCurve({ pair });

          result = { kind: 'fx_forward_curve', data: out };
          break;
        }

        case 'fx_scanner': {
          const out = await fetchFXScanner({
            top_n: params['top_n'] ? Number(params['top_n']) : 10,
            market_scope: pick(params, 'market_scope'),
          });

          result = { kind: 'fx_scanner', data: out };
          break;
        }

        case 'fx_realized_vol': {
          const pair = pick(params, 'pair', 'EURUSD');
          if (!pair) throw new Error('fx_realized_vol view requires pair');

          const out = await fetchDetailFXRealizedVol({
            pair,
            window_observations: params['window_observations']
              ? Number(params['window_observations'])
              : undefined,
            lookback_days: pickLookback(params),
            return_type:
              params['return_type'] === 'simple_return'
                ? 'simple_return'
                : 'log_return',
            field_name: pick(params, 'field_name'),
          });

          result = { kind: 'fx_realized_vol', data: out };
          break;
        }

        case 'fx_trade_setup': {
          const pair = pick(params, 'pair', 'EURUSD');
          if (!pair) throw new Error('fx_trade_setup view requires pair');

          const out = await fetchDetailFXTradeSetup({
            pair,
            tenor: pick(params, 'tenor', '1M'),
            vol_window_observations: params['vol_window_observations']
              ? Number(params['vol_window_observations'])
              : undefined,
            lookback_days: pickLookback(params),
          });

          result = { kind: 'fx_trade_setup', data: out };
          break;
        }

        case 'fx_macro_risk_overlay': {
          const pair = pick(params, 'pair', 'EURUSD');
          if (!pair) throw new Error('fx_macro_risk_overlay view requires pair');

          const out = await fetchDetailFXMacroRiskOverlay({
            pair,
            lookback_days: pickLookback(params),
            correlation_window_observations: params['correlation_window_observations']
              ? Number(params['correlation_window_observations'])
              : undefined,
            field_name: pick(params, 'field_name'),
          });

          result = { kind: 'fx_macro_risk_overlay', data: out };
          break;
        }

        case 'fx_correlation_beta': {
          const pair = pick(params, 'pair', 'EURUSD');
          if (!pair) throw new Error('fx_correlation_beta view requires pair');

          const out = await fetchDetailFXCorrelationBeta({
            pair,
            window_observations: params['window_observations']
              ? Number(params['window_observations'])
              : undefined,
            lookback_days: pickLookback(params),
            field_name: pick(params, 'field_name'),
          });

          result = { kind: 'fx_correlation_beta', data: out };
          break;
        }

        case 'fx_regime_classifier': {
          const out = await fetchDetailFXRegimeClassifier({
            anchor_pair: pick(params, 'anchor_pair', 'EURUSD'),
            tenor: pick(params, 'tenor', '1M'),
            realized_window_observations: params['realized_window_observations']
              ? Number(params['realized_window_observations'])
              : undefined,
            correlation_window_observations: params['correlation_window_observations']
              ? Number(params['correlation_window_observations'])
              : undefined,
            lookback_days: pickLookback(params),
            field_name: pick(params, 'field_name'),
          });

          result = { kind: 'fx_regime_classifier', data: out };
          break;
        }

        case 'fx_vol_risk_premium': {
          const pair = pick(params, 'pair', 'EURUSD');
          if (!pair) throw new Error('fx_vol_risk_premium view requires pair');

          const out = await fetchDetailFXVolRiskPremium({
            pair,
            tenor: pick(params, 'tenor', '1M'),
            realized_window_observations: params['realized_window_observations']
              ? Number(params['realized_window_observations'])
              : undefined,
            lookback_days: pickLookback(params),
            field_name: pick(params, 'field_name'),
          });

          result = { kind: 'fx_vol_risk_premium', data: out };
          break;
        }

        case 'forward': {
          // OIS forward-rate detail endpoint not yet implemented backend-side.
          // Return a clear error so the view layer can render an explanatory
          // empty state rather than silently failing.
          throw new Error(
            'Forward-rate workspace view requires an OIS detail endpoint that is not yet wired. ' +
              'Use the chat copilot for forward-rate queries until the endpoint ships.',
          );
        }

        default: {
          // Exhaustiveness check — fails the build if a new view type is
          // added to WorkspaceViewType without a case here.
          const _exhaustive: never = toolType;
          throw new Error(`Unknown workspace view type: ${_exhaustive}`);
        }
      }

      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
      setData(null);
    } finally {
      setIsLoading(false);
    }
    // load() depends only on the stable fingerprint of inputs.  Deliberate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fingerprint]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, isLoading, error, refetch: load };
}
