// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for ``get_otr_history_tool``.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive claiming
// ``custom_build_surface`` ships an extended view; this is the OTR
// transition log's full canvas.  CATEGORICAL SCD2 TIMELINE shape — the
// wire is an identity snapshot plus a list of date-windowed identifier
// rows (NO numeric series anywhere), so the canvas is a benchmark-
// succession read, not a chart:
//
//   identity   — flag + issuer label + tenor slot, lookback
//   KPI strip  — current OTR identifier / transition count / current-
//                since / maturity (all verbatim wire fields)
//   timeline   — the SCD2 window table, most-recent first, with the
//                open window (effective_to == null) marked CURRENT
//   methodology — wire ``methodology_note`` verbatim (P5) + slot rows
//   lineage    — the standard footer
//
// FP9: every cell is a verbatim wire field or a pure formatting of one —
// no "days on the run", no roll-frequency estimates.  P6: nullable
// identifier fields render an honest '—'.
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  ControlsStrip,
  KPIStrip,
  LineageFooter,
  MethodologyCard,
  type BuildExtendedProps,
  type ControlDescriptor,
} from '@/components/shared/build';
import type { OtrHistoryTransitionRow } from '@/types/rates';
import {
  OTR_COUNTRY_OPTIONS,
  OTR_LOOKBACK_OPTIONS,
  OTR_TENOR_OPTIONS,
  buildMethodologyRows,
  buildReferenceChips,
  extendedKPIs,
  formatShortDate,
  formatWindowEnd,
  otrCountryMetaFor,
  sortTransitionsForDisplay,
  useOtrHistoryData,
} from './otrHistoryShared';

const OTR_DEFAULTS: Record<string, string> = {
  country: 'US',
  tenor: '10Y',
  lookback_days: '365',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params -----
  const country = params.country || OTR_DEFAULTS.country;
  const tenor = params.tenor || OTR_DEFAULTS.tenor;
  const lookbackStr = params.lookback_days || OTR_DEFAULTS.lookback_days;
  const lookbackDays = Number(lookbackStr);

  const { data, isLoading, errorMessage } = useOtrHistoryData({
    country,
    tenor,
    lookbackDays: Number.isFinite(lookbackDays) ? lookbackDays : undefined,
  });

  // ----- URL update on control change (pilot pushParams pattern) -----
  const pushParams = (nextParams: Record<string, string>) => {
    // Inside the multi-tool DAG expand-to-modal, edits stay local
    // (onParamsChange) instead of navigating the global URL.
    if (onParamsChange) {
      onParamsChange(nextParams);
      return;
    }
    const nextCtx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: toolName, params: nextParams }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${nextCtx}`, { replace: true });
  };

  const handleControlChange = (name: string, value: string) => {
    pushParams({ ...params, [name]: value });
  };

  const handleReset = () => {
    pushParams({ ...OTR_DEFAULTS });
  };

  // ----- Controls -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'country',
      label: 'Country',
      kind: 'enum',
      value: country,
      options: OTR_COUNTRY_OPTIONS,
    },
    {
      name: 'tenor',
      label: 'Tenor',
      kind: 'enum',
      value: tenor,
      options: OTR_TENOR_OPTIONS,
    },
    {
      name: 'lookback_days',
      label: 'Lookback',
      kind: 'enum',
      value: lookbackStr,
      options: OTR_LOOKBACK_OPTIONS,
    },
  ];

  const cm = data?.current_metrics ?? null;
  const meta = otrCountryMetaFor(country);
  const transitions = data ? sortTransitionsForDisplay(data.transitions) : [];

  return (
    <div className="flex flex-col gap-4">
      {/* ---------- Identity header ---------- */}
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="kicker text-fg-muted">
            ON-THE-RUN TRANSITION LOG — SCD2 RESOLVER READ
          </div>
          <h2 className="mt-0.5 text-[19px] font-semibold text-fg-primary">
            {meta && (
              <span className="mr-1.5 text-[17px]" aria-hidden>
                {meta.flag}
              </span>
            )}
            {meta ? meta.issuerLabel : country} {tenor}
            <span className="ml-2 text-[12.5px] font-normal text-fg-secondary">
              {lookbackStr}d lookback
            </span>
          </h2>
        </div>
        {cm && (
          <span className="text-[11.5px] text-fg-muted">
            As of {cm.as_of_date}
          </span>
        )}
      </header>

      {/* ---------- Controls strip ---------- */}
      <ControlsStrip
        controls={controls}
        onChange={handleControlChange}
        onReset={handleReset}
      />

      {/* ---------- Result body ---------- */}
      {errorMessage ? (
        <ExtendedError message={errorMessage} />
      ) : isLoading || !data || !cm ? (
        <ExtendedSkeleton />
      ) : (
        <div className="space-y-5">
          <KPIStrip kpis={extendedKPIs(data)} />
          <TransitionTimeline rows={transitions} />
          <MethodologyCard
            rows={buildMethodologyRows(data)}
            references={buildReferenceChips()}
          />
        </div>
      )}

      {/* ---------- Lineage footer ---------- */}
      <LineageFooter
        lineage={{
          toolName,
          version: 'v1',
          kind: 'Pure-INGEST SCD2 read (forward-only OTR resolver, ADR 0007)',
          providers: ['TimescaleDB', 'macro_data.otr_history (ADR 0003)'],
          asOf: cm?.as_of_date,
          freshness: 'fresh',
        }}
      />
    </div>
  );
};

export default BuildExtended;

// ---------------------------------------------------------------------------
// SCD2 timeline table — one row per resolver window, most-recent first.
// Open window (effective_to == null) carries the CURRENT pill.  Every
// cell is verbatim (P6 '—' on absent identifiers).
// ---------------------------------------------------------------------------

const TIMELINE_GRID =
  'grid grid-cols-[minmax(150px,1.2fr)_auto_minmax(96px,0.9fr)_minmax(110px,1fr)_minmax(90px,0.9fr)_auto] items-center gap-3 px-1';

function TransitionTimeline({
  rows,
}: {
  rows: ReadonlyArray<OtrHistoryTransitionRow>;
}) {
  return (
    <section className="card px-5 py-4">
      <div className="flex items-baseline justify-between gap-3">
        <span className="kicker text-fg-muted">BENCHMARK SUCCESSION</span>
        <span className="text-[11px] text-fg-faint">
          Resolver windows, most recent first — effective dates are
          detection dates (TD #27).
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="px-1 py-4 text-[12px] text-fg-secondary">
          No resolver-observed OTR windows intersect this lookback for
          this slot (honest absence — the substrate has no rows, the view
          does not infer any).
        </p>
      ) : (
        <div className="mt-3 flex flex-col">
          <div
            className={`${TIMELINE_GRID} text-[10.5px] uppercase tracking-wide text-fg-muted`}
          >
            <span>Window</span>
            <span />
            <span>CUSIP</span>
            <span>ISIN</span>
            <span>Ticker</span>
            <span className="text-right">Maturity</span>
          </div>
          {rows.map((row) => {
            const isOpen = row.effective_to == null;
            return (
              <div
                key={`${row.otr_instrument_id}-${row.effective_from}`}
                className={`${TIMELINE_GRID} border-t border-line-subtle py-2 text-[12.5px] ${isOpen ? 'bg-surface-overlay/40' : ''}`}
              >
                <span className="font-mono tabular-nums text-fg-primary">
                  {formatShortDate(row.effective_from)}
                  <span className="mx-1 text-fg-faint">→</span>
                  {row.effective_to ? (
                    formatShortDate(row.effective_to)
                  ) : (
                    <span className="text-fg-secondary">
                      {formatWindowEnd(row.effective_to)}
                    </span>
                  )}
                </span>
                <span>
                  {isOpen && (
                    <span className="rounded-full border border-mint-300/40 bg-mint-300/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-mint-300">
                      Current
                    </span>
                  )}
                </span>
                <span className="font-mono text-fg-primary">
                  {row.cusip ?? '—'}
                </span>
                <span className="font-mono text-fg-secondary">
                  {row.isin ?? '—'}
                </span>
                <span className="font-mono text-fg-secondary">
                  {row.vendor_ticker ?? '—'}
                </span>
                <span className="text-right font-mono tabular-nums text-fg-secondary">
                  {formatShortDate(row.maturity_date)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function ExtendedSkeleton() {
  return (
    <div className="space-y-5">
      <div className="card grid grid-cols-2 gap-3 px-5 py-4 md:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <div className="h-2 w-20 animate-pulse rounded bg-line-subtle" />
            <div className="h-6 w-24 animate-pulse rounded bg-line-subtle" />
          </div>
        ))}
      </div>
      <div className="card h-[220px] animate-pulse bg-line-subtle/20" />
      <div className="card h-[160px] animate-pulse bg-line-subtle/20" />
    </div>
  );
}

function ExtendedError({ message }: { message: string }) {
  return (
    <section className="card flex min-h-[200px] items-center justify-center px-6 py-8">
      <p className="max-w-[560px] text-center text-[12px] leading-[1.6] text-coral-300">
        {message}
      </p>
    </section>
  );
}
