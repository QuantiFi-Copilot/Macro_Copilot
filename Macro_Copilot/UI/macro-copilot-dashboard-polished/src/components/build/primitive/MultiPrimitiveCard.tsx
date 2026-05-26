// ============================================================================
// MultiPrimitiveCard — one compact cell in the multi-card comparison grid.
// ----------------------------------------------------------------------------
// R6.3.  When the user clicks "Open in Build" on an Ask answer carrying
// multiple primitive tool calls (e.g. "compare UST/Bund/Gilt 10Y"),
// BuildShell mounts ``MultiPrimitiveCanvas`` which lays out N of these
// cards side-by-side.
//
// Each card:
//   - takes a single ``DecodedTypedPrimitive`` + folds in spec defaults
//   - fetches its own typed-detail payload (isolated state per card)
//   - renders a compact summary: kicker / headline metric / unit /
//     bottom-line context (daily change, z-score) + a click-through to
//     open the full single-card canvas with editable dropdowns
//
// Visual register matches the polished ``.research-card`` chassis the
// rest of Build uses.  Each card is a click-target — the whole tile is
// the affordance.
// ============================================================================

import { useEffect, useState } from 'react';
import { ArrowUpRight, Loader2, AlertCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type {
  ButterflyOutput,
  CrossMarketSpreadOutput,
  CurveSpreadOutput,
  RegimeOutput,
  YieldLevelOutput,
} from '@/types/rates';
import {
  dispatchFetch,
  type DecodedTypedPrimitive,
  type Payload,
} from './fetchDispatcher';
import {
  missingRequiredTypedParams,
  paramSpecsFor,
  resolveParamValue,
} from './paramSpecs';
import { MissingParamsCard } from './MissingParamsCard';
import type { CallMeta } from './MultiPrimitiveCanvas';
import { cn } from '@/utils/cn';

type Props = {
  decoded: DecodedTypedPrimitive;
  /** PR-B-β — true when the URL carried ``handoff=ask``.  Gates the
   *  missing-param tile: when set and a required param is missing
   *  from the RAW context (pre-fold), the card renders
   *  ``MissingParamsCard`` instead of folding spec defaults and
   *  fetching with the wrong instrument.  Library-blank opens
   *  (askHandoff=false) keep the pre-PR-B-β default-folding
   *  behaviour, so blank Library entries still land on a working
   *  default chart. */
  askHandoff?: boolean;
  /** PR-B-β — when ``MultiPrimitiveCanvas`` detects this card shares
   *  a ``(toolName, params)`` signature with siblings in the grid,
   *  it passes the 1-based ordinal + total so the card can render
   *  a ``· call N of M`` chip.  Undefined when the card is unique,
   *  so the chip stays out of the way. */
  callMeta?: CallMeta;
};

export function MultiPrimitiveCard({
  decoded,
  askHandoff = false,
  callMeta,
}: Props) {
  const navigate = useNavigate();
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // PR-B-β — Ask-handoff path: check required params against the RAW
  // ``decoded.params`` BEFORE folding spec defaults.  When missing,
  // skip the fetch entirely and render the honest missing-param tile
  // (handled below as an early return).  Library-blank opens skip
  // this check (askHandoff defaults to false) so the existing
  // default-folding path continues to work.
  const missingForAsk = askHandoff
    ? missingRequiredTypedParams(decoded.kind, decoded.params)
    : [];

  // Fold spec defaults into the params the same way the single canvas
  // does — keeps the compact-card fetch consistent with the full-card
  // fetch so "Open" lands on the same data.
  const effectiveParams: Record<string, string> = {};
  for (const spec of paramSpecsFor(decoded.kind)) {
    effectiveParams[spec.key] = resolveParamValue(spec, decoded.params);
  }
  for (const [k, v] of Object.entries(decoded.params)) {
    if (!(k in effectiveParams)) effectiveParams[k] = v;
  }

  // PR-B-β — skip the fetch entirely when the Ask-handoff missing-
  // param tile would render.  Triggering the fetch with defaulted
  // params would (a) waste the network call AND (b) prime the cache
  // with results for the WRONG instrument; the early-render path
  // below short-circuits the JSX so the fetch effect's payload
  // never reaches the screen, but we still skip the work.
  const shouldFetch = !(askHandoff && missingForAsk.length > 0);

  useEffect(() => {
    if (!shouldFetch) {
      // Reset any previous state so a re-render with stale payload
      // doesn't briefly flash.  Safe — JSX below renders the
      // missing-param card regardless.
      setPayload(null);
      setError(null);
      setIsLoading(false);
      return;
    }
    // Forward / scanner placeholders use a different summary; everything
    // else fetches its typed-detail.
    if (decoded.kind === 'forward') {
      setPayload({ kind: 'forward', data: null });
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setPayload(null);
    dispatchFetch({ ...decoded, params: effectiveParams })
      .then((p) => {
        if (!cancelled) setPayload(p);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [shouldFetch, decoded.kind, decoded.toolName, JSON.stringify(effectiveParams)]); // eslint-disable-line react-hooks/exhaustive-deps

  // Click anywhere → open this primitive in the full single canvas
  // (with editable param dropdowns).  We re-encode ``?context=`` with
  // just this one tool so the destination canvas reads exactly what
  // we showed.
  const handleOpen = () => {
    const ctx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: decoded.toolName, params: effectiveParams }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${ctx}`);
  };

  // PR-B-β — Ask-handoff missing-param early return.  Renders the
  // honest "missing X, Y" tile instead of the silent default chart.
  // Library-blank opens (askHandoff=false) skip this branch entirely
  // and continue with the default-folding render below.
  if (askHandoff && missingForAsk.length > 0) {
    return (
      <MissingParamsCard
        toolName={decoded.toolName}
        kind={decoded.kind}
        missingParams={missingForAsk}
        presentParams={decoded.params}
        compact
      />
    );
  }

  return (
    <button
      type="button"
      onClick={handleOpen}
      className="research-card group relative flex min-h-[180px] flex-col gap-3 overflow-hidden px-4 py-3.5 text-left transition-transform duration-200 ease-sleek hover:-translate-y-px focus-visible:-translate-y-px focus-visible:outline-none"
      style={{ ['--rail-color' as string]: railColorFor(decoded.kind) }}
    >
      <span aria-hidden className="research-card-rail" />

      <Header
        decoded={decoded}
        effectiveParams={effectiveParams}
        callMeta={callMeta}
      />

      <div className="min-h-[60px] flex-1">
        {isLoading && <CompactLoading />}
        {error && <CompactError message={error} />}
        {payload && <CompactBody payload={payload} />}
      </div>
    </button>
  );
}

// ----------------------------------------------------------------------------
// Header
// ----------------------------------------------------------------------------

function Header({
  decoded,
  effectiveParams,
  callMeta,
}: {
  decoded: DecodedTypedPrimitive;
  effectiveParams: Record<string, string>;
  /** PR-B-β — populated when the grid contains 2+ cards with this
   *  same ``(toolName, params)`` signature.  Renders a tiny
   *  ``· call N of M`` chip so duplicate cards don't look like a
   *  rendering bug. */
  callMeta?: CallMeta;
}) {
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="kicker text-fg-muted">{kickerFor(decoded.kind)}</span>
          {callMeta && (
            <span
              className="kicker text-fg-faint"
              data-testid="multi-card-call-chip"
              title={`This (tool, params) signature appears ${callMeta.m} times in the grid; this is the ${callMeta.n}${ordinalSuffix(callMeta.n)} call.`}
            >
              · call {callMeta.n} of {callMeta.m}
            </span>
          )}
        </div>
        <h4 className="mt-0.5 truncate text-[13px] font-semibold tracking-[-0.008em] text-fg-primary">
          {titleFor(decoded, effectiveParams)}
        </h4>
      </div>
      <ArrowUpRight
        size={13}
        strokeWidth={1.75}
        aria-hidden
        className="mt-0.5 shrink-0 text-fg-faint transition-colors group-hover:text-ice-200"
      />
    </div>
  );
}

function ordinalSuffix(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return 'st';
  if (mod10 === 2 && mod100 !== 12) return 'nd';
  if (mod10 === 3 && mod100 !== 13) return 'rd';
  return 'th';
}

// ----------------------------------------------------------------------------
// Compact bodies — per kind
// ----------------------------------------------------------------------------

function CompactBody({ payload }: { payload: Payload }) {
  switch (payload.kind) {
    case 'spread':
      return <SpreadCompactBody data={payload.data} />;
    case 'cross_market':
      return <CrossMarketCompactBody data={payload.data} />;
    case 'butterfly':
      return <ButterflyCompactBody data={payload.data} />;
    case 'yield':
      return <YieldCompactBody data={payload.data} />;
    case 'regime':
      return <RegimeCompactBody data={payload.data} />;
    case 'scanner':
      return (
        <CompactPlaceholder text="Scanner — open card for ranked results." />
      );
    case 'forward':
      return (
        <CompactPlaceholder text="Forward — detail endpoint not yet wired." />
      );
  }
}

function SpreadCompactBody({ data }: { data: CurveSpreadOutput }) {
  const cm = data.current_metrics;
  return (
    <Headline
      value={cm.current_spread_bps?.toFixed(1) ?? '—'}
      unit="bps"
      changeBps={cm.daily_change_bps}
      zScore={cm.current_z_score}
      asOf={cm.as_of_date}
    />
  );
}

function CrossMarketCompactBody({
  data,
}: {
  data: CrossMarketSpreadOutput;
}) {
  const cm = data.current_metrics;
  return (
    <Headline
      value={cm.current_spread_bps?.toFixed(1) ?? '—'}
      unit="bps"
      changeBps={cm.daily_change_bps}
      zScore={cm.current_z_score}
      asOf={cm.as_of_date}
    />
  );
}

function ButterflyCompactBody({ data }: { data: ButterflyOutput }) {
  const cm = data.current_metrics;
  return (
    <Headline
      value={cm.current_butterfly_bps?.toFixed(1) ?? '—'}
      unit="bps"
      changeBps={cm.daily_change_bps}
      zScore={cm.current_z_score}
      asOf={cm.as_of_date}
    />
  );
}

function YieldCompactBody({ data }: { data: YieldLevelOutput }) {
  const cm = data.current_metrics;
  return (
    <Headline
      value={cm.current_yield_pct?.toFixed(3) ?? '—'}
      unit="%"
      changeBps={cm.daily_change_bps}
      zScore={cm.z_score}
      asOf={cm.as_of_date}
    />
  );
}

function RegimeCompactBody({ data }: { data: RegimeOutput }) {
  const cm = data.current_metrics;
  return (
    <div className="flex flex-col gap-2">
      <span
        className={cn(
          'mono inline-flex w-fit items-center rounded-md border px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-[0.04em]',
          regimeBadgeColor(cm.regime_tag),
        )}
      >
        {cm.regime_tag}
      </span>
      <span className="text-[11px] leading-[1.45] text-fg-secondary">
        {cm.regime_description}
      </span>
      <span className="font-mono text-[10px] text-fg-faint">
        as of {cm.as_of_date}
      </span>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Generic headline + states
// ----------------------------------------------------------------------------

function Headline({
  value,
  unit,
  changeBps,
  zScore,
  asOf,
}: {
  value: string;
  unit: string;
  changeBps: number | null | undefined;
  zScore: number | null | undefined;
  asOf?: string | null;
}) {
  const changeTone = changeToneClass(changeBps);
  const formattedChange = formatChange(changeBps);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-1.5">
        <span className="font-serif-display text-[24px] font-light leading-none text-fg-primary">
          {value}
        </span>
        <span className="text-[11px] text-fg-muted">{unit}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px]">
        {formattedChange && (
          <span className={cn('font-mono', changeTone)}>
            {formattedChange} bps today
          </span>
        )}
        {zScore != null && (
          <span className="font-mono text-fg-muted">
            z = {zScore.toFixed(2)}
          </span>
        )}
      </div>
      {asOf && (
        <span className="mt-1 font-mono text-[9.5px] text-fg-faint">
          as of {asOf}
        </span>
      )}
    </div>
  );
}

function CompactLoading() {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-fg-faint">
      <Loader2 size={11} className="animate-spin text-ice-300" />
      <span>loading…</span>
    </div>
  );
}

function CompactError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-1.5 text-[11px] text-coral-300">
      <AlertCircle size={11} className="mt-0.5 shrink-0" />
      <span className="line-clamp-2 leading-[1.45]">{message}</span>
    </div>
  );
}

function CompactPlaceholder({ text }: { text: string }) {
  return (
    <div className="text-[11px] leading-[1.5] text-fg-muted">{text}</div>
  );
}

// ----------------------------------------------------------------------------
// Per-kind labels + rail colour
// ----------------------------------------------------------------------------

function kickerFor(kind: DecodedTypedPrimitive['kind']): string {
  switch (kind) {
    case 'spread':
      return 'Curve spread';
    case 'cross_market':
      return 'Cross-market';
    case 'butterfly':
      return 'Butterfly';
    case 'yield':
      return 'Yield';
    case 'regime':
      return 'Regime';
    case 'scanner':
      return 'Scanner';
    case 'forward':
      return 'OIS forward';
  }
}

function titleFor(
  decoded: DecodedTypedPrimitive,
  p: Record<string, string>,
): string {
  switch (decoded.kind) {
    case 'spread':
      return `${p.curve_family ?? 'UST'} ${p.short_tenor ?? '2Y'}s${p.long_tenor ?? '10Y'}`;
    case 'cross_market':
      return `${p.curve_family_1 ?? '?'}–${p.curve_family_2 ?? '?'} ${p.tenor ?? '10Y'}`;
    case 'butterfly':
      return `${p.curve_family ?? 'UST'} ${p.short_tenor ?? '2Y'}/${
        p.belly_tenor ?? '5Y'
      }/${p.long_tenor ?? '10Y'}`;
    case 'yield':
      return `${p.curve_family ?? 'UST'} ${p.tenor ?? '10Y'}`;
    case 'regime':
      return `${p.curve_family ?? 'UST'} ${p.front_tenor ?? '2Y'}s${
        p.back_tenor ?? '10Y'
      } · ${p.lookback_period ?? '22d'}`;
    case 'scanner':
      return 'Z-score extremes';
    case 'forward':
      return 'OIS forward (pending)';
  }
}

function railColorFor(kind: DecodedTypedPrimitive['kind']): string {
  switch (kind) {
    case 'spread':
    case 'cross_market':
    case 'yield':
      return 'rgba(122, 162, 255, 0.55)'; // ice
    case 'butterfly':
      return 'rgba(243, 183, 85, 0.55)'; // amber
    case 'regime':
      return 'rgba(155, 140, 255, 0.50)'; // violet
    case 'scanner':
      return 'rgba(63, 214, 154, 0.50)'; // mint
    case 'forward':
      return 'rgba(148, 163, 184, 0.35)'; // neutral
  }
}

function regimeBadgeColor(tag: string): string {
  const t = tag.toLowerCase();
  if (t.includes('bull'))
    return 'border-mint-400/30 bg-mint-500/10 text-mint-300';
  if (t.includes('bear'))
    return 'border-coral-400/30 bg-coral-500/10 text-coral-300';
  return 'border-line-soft bg-white/[0.03] text-fg-secondary';
}

function changeToneClass(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'text-fg-muted';
  if (value > 0) return 'text-mint-400';
  if (value < 0) return 'text-coral-400';
  return 'text-fg-muted';
}

function formatChange(value: number | null | undefined): string | null {
  if (value == null || !Number.isFinite(value)) return null;
  const rounded = value.toFixed(1);
  if (Number(rounded) === 0) return rounded;
  return value > 0 ? `+${rounded}` : rounded;
}
