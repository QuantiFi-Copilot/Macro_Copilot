// ============================================================================
// VirtualPrimitiveCanvas — Build canvas for a single-primitive Ask handoff.
// ----------------------------------------------------------------------------
// Phase R1.2.  When the user clicks "Open in Build" on an Ask answer, the
// chat encodes the supervisor turn's ``workspaceContext`` into
// ``/workspace?context=<encoded JSON>``.  BuildShell.SlugFreeShell detects
// that param and mounts this canvas — which:
//
//   1. Hands the raw context string to ``decodePrimitiveContext`` to pick
//      the most informative tool call.
//   2. Calls the matching typed-detail endpoint on the rates API (the
//      same endpoint Monitor + the deleted WorkspacePage used).
//   3. Renders the result through the matching primitive view
//      (SpreadPrimitiveView, CrossMarketPrimitiveView, …).
//
// This is a *virtual* canvas — no workspace is persisted, no slug exists.
// Until the backend ships supervisor-side workspace persistence (Phase R5),
// this is the only Ask→Build handoff path for single-primitive calls and
// the loop closes entirely on the frontend.
// ============================================================================

import { useEffect, useState } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
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
import {
  decodePrimitiveContext,
  type DecodedPrimitive,
  type PrimitiveViewKind,
} from './contextDecoder';
import { SpreadPrimitiveView } from './SpreadPrimitiveView';
import { CrossMarketPrimitiveView } from './CrossMarketPrimitiveView';
import { ButterflyPrimitiveView } from './ButterflyPrimitiveView';
import { YieldPrimitiveView } from './YieldPrimitiveView';
import { RegimePrimitiveView } from './RegimePrimitiveView';
import { ScannerPrimitiveView } from './ScannerPrimitiveView';
import { ForwardPrimitiveView } from './ForwardPrimitiveView';

// ----------------------------------------------------------------------------
// Per-view payload discriminator + fetcher dispatcher
// ----------------------------------------------------------------------------

type Payload =
  | { kind: 'spread'; data: CurveSpreadOutput }
  | { kind: 'cross_market'; data: CrossMarketSpreadOutput }
  | { kind: 'butterfly'; data: ButterflyOutput }
  | { kind: 'yield'; data: YieldLevelOutput }
  | { kind: 'regime'; data: RegimeOutput }
  | { kind: 'scanner'; data: ScannerResponse }
  | { kind: 'forward'; data: null };

/** Optional lookback presets accepted by the typed-detail endpoints. */
function coerceLookbackDays(raw: string | undefined): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw);
  if (Number.isFinite(n) && n > 0) return Math.floor(n);
  // Accept "1y" / "2y" / "5y" shorthand from the chat layer.
  const m = raw.match(/^(\d+)y$/i);
  if (m) return Number(m[1]) * 252;
  return undefined;
}

async function dispatchFetch(
  decoded: DecodedPrimitive,
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
      // Placeholder view — no fetch, no error path.  The view itself
      // renders the "not wired" caption.
      return { kind: 'forward', data: null };
  }
}

// ----------------------------------------------------------------------------
// Component
// ----------------------------------------------------------------------------

type Props = {
  /** Raw value of the ``?context=`` URL param.  Already URI-encoded JSON. */
  contextParam: string;
};

export function VirtualPrimitiveCanvas({ contextParam }: Props) {
  const decoded = decodePrimitiveContext(contextParam);
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (!decoded) {
      setPayload(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    // The forward placeholder doesn't fetch; mount the view directly.
    if (decoded.kind === 'forward') {
      setPayload({ kind: 'forward', data: null });
      setIsLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setPayload(null);
    dispatchFetch(decoded)
      .then((p) => {
        if (cancelled) return;
        setPayload(p);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [decoded?.kind, decoded?.toolName, JSON.stringify(decoded?.params)]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!decoded) {
    return <DecodeError contextParam={contextParam} />;
  }
  if (error) {
    return <FetchError decoded={decoded} message={error} />;
  }
  if (isLoading || !payload) {
    return <LoadingCanvas kind={decoded.kind} />;
  }
  return <PrimitiveDispatcher payload={payload} />;
}

// ----------------------------------------------------------------------------
// View dispatcher
// ----------------------------------------------------------------------------

function PrimitiveDispatcher({ payload }: { payload: Payload }) {
  switch (payload.kind) {
    case 'spread':
      return <SpreadPrimitiveView payload={payload.data} />;
    case 'cross_market':
      return <CrossMarketPrimitiveView payload={payload.data} />;
    case 'butterfly':
      return <ButterflyPrimitiveView payload={payload.data} />;
    case 'yield':
      return <YieldPrimitiveView payload={payload.data} />;
    case 'regime':
      return <RegimePrimitiveView payload={payload.data} />;
    case 'scanner':
      return <ScannerPrimitiveView payload={payload.data} />;
    case 'forward':
      return <ForwardPrimitiveView />;
  }
}

// ----------------------------------------------------------------------------
// Loading + error states
// ----------------------------------------------------------------------------

function LoadingCanvas({ kind }: { kind: PrimitiveViewKind }) {
  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-center gap-3 px-6">
      <Loader2 size={16} className="animate-spin text-ice-300" />
      <span className="text-[12px] text-fg-muted">
        Loading {prettyKind(kind)} from rates API…
      </span>
    </div>
  );
}

function FetchError({
  decoded,
  message,
}: {
  decoded: DecodedPrimitive;
  message: string;
}) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6">
      <div className="card flex max-w-[520px] items-start gap-3 px-5 py-4">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-coral-300" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-fg-primary">
            Could not load {prettyKind(decoded.kind)}
          </div>
          <div className="mt-1 font-mono text-[10.5px] text-fg-muted">
            {decoded.toolName}
          </div>
          <div className="mt-2 text-[11.5px] leading-[1.5] text-fg-secondary">
            {message}
          </div>
        </div>
      </div>
    </div>
  );
}

function DecodeError({ contextParam }: { contextParam: string }) {
  return (
    <div className="flex h-full min-h-0 items-center justify-center px-6">
      <div className="card flex max-w-[520px] items-start gap-3 px-5 py-4">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-amber-300" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-fg-primary">
            Could not decode workspace context
          </div>
          <div className="mt-1 truncate font-mono text-[10.5px] text-fg-muted">
            ?context={contextParam.slice(0, 64)}…
          </div>
          <div className="mt-2 text-[11.5px] leading-[1.5] text-fg-secondary">
            The link from Ask carries a tool context Build doesn't recognise
            yet.  Open the Ask answer again, or start a new analysis from the
            empty state below.
          </div>
        </div>
      </div>
    </div>
  );
}

function prettyKind(kind: PrimitiveViewKind): string {
  switch (kind) {
    case 'spread':
      return 'curve spread';
    case 'cross_market':
      return 'cross-market spread';
    case 'butterfly':
      return 'butterfly';
    case 'yield':
      return 'yield level';
    case 'regime':
      return 'curve regime';
    case 'scanner':
      return 'scanner';
    case 'forward':
      return 'OIS forward';
  }
}
