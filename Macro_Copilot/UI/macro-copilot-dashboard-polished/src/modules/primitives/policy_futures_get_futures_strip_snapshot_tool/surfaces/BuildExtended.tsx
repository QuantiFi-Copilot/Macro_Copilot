// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// ``policy_futures_get_futures_strip_snapshot_tool``.  WHOLE-STRIP
// SNAPSHOT shape (one row per strip position on a single aligned date).
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.1 + §5 every primitive claiming
// ``custom_build_surface`` ships an extended view; this is the whole-
// strip snapshot's full canvas, mounted by VirtualPrimitiveCanvas for
// single-tool queries AND by the click-to-expand modal from the compact
// card.
//
// The shared ``BuildExtendedShell`` is chart-centric (date-keyed
// chartPoints + reference bands); this tool has NO time series on the
// wire — the desk read is the CROSS-SECTIONAL strip curve (implied rate
// by strip position) at ONE aligned as_of_date.  Following the
// scan_extremes_tool / calculate_half_life_tool precedent for non-LEVEL-
// shaped tools, this component composes the shared sub-pieces
// (ControlsStrip / KPIStrip / MethodologyCard / LineageFooter /
// InfoTooltip / FreshnessPill) inside its own canvas, with:
//   - a tabular-nums flex STRIP-CURVE read (implied_rate_pct by strip
//     position — sanctioned per-tool primary zone), and
//   - a per-position table (position, contract, underlying, implied
//     rate, 1d Δ, z, OI) whose rows surface their own
//     ``row_methodology_card`` via InfoTooltip (catalog standardness
//     guardrail — the disclosure travels with the row).
// The full P5 / ADR 0013 disclosure surfaces VERBATIM from the wire's
// ``methodology_disclosure`` (never a hardcoded TS literal).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { Rows3 } from 'lucide-react';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  ControlsStrip,
  FreshnessPill,
  InfoTooltip,
  KPIStrip,
  LineageFooter,
  MethodologyCard,
  signedFixed,
  toneForChange,
  toneForZScore,
  toneTextClass,
  type BuildExtendedProps,
  type ControlDescriptor,
} from '@/components/shared/build';
import type {
  FuturesStripSnapshotOutput,
  FuturesStripSnapshotRow,
} from '@/types/rates';
import {
  EXTENDED_KPI_PLACEHOLDERS,
  POLICY_FUTURES_CURVE_OPTIONS,
  STRIP_PRICE_FIELD_OPTIONS,
  STRIP_SNAPSHOT_COMPACT_CAVEAT,
  buildMethodologyRows,
  buildReferenceChips,
  curveMetaFor,
  dailyChangeBps,
  extendedKPIs,
  formatContracts,
  slopeReadLabel,
  stripSegmentLabel,
  stripSlopeBps,
  useStripSnapshot,
} from './policyFuturesStripSnapshotShared';

const DEFAULTS = {
  curve_family: 'SOFR_FUT',
};

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  // ----- Resolve effective params (Record<string,string> parsing) -----
  const curveFamily = params.curve_family ?? DEFAULTS.curve_family;
  const asOfDate = params.as_of_date || undefined;
  const priceField = params.last_price_field_name || undefined;
  const oiField = params.open_interest_field_name || undefined;

  const { data, isLoading, errorMessage } = useStripSnapshot({
    curveFamily,
    asOfDate,
    lastPriceFieldName: priceField,
    openInterestFieldName: oiField,
  });

  // ----- URL update on control change (Stage D modal-local edits) -----
  const pushParams = (nextParams: Record<string, string>) => {
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
    const nextParams = { ...params };
    if (value === '') {
      // Empty value = fall through to the backend sentinel (data-max
      // anchor for as_of_date; YAML default for the field overrides).
      delete nextParams[name];
    } else {
      nextParams[name] = value;
    }
    pushParams(nextParams);
  };

  const handleReset = () => {
    pushParams({ curve_family: curveFamily });
  };

  // ----- Controls.  Conventions (z window, strip-positions list,
  //       rounding) are YAML-locked — only the backend Input's four
  //       fields are exposed (PR8 input-schema overreach guard). -----
  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_family',
      label: 'Curve Family',
      kind: 'enum',
      value: curveFamily,
      options: POLICY_FUTURES_CURVE_OPTIONS,
    },
    {
      name: 'as_of_date',
      label: 'As of (YYYY-MM-DD, blank = latest)',
      kind: 'text',
      value: asOfDate ?? '',
    },
    {
      name: 'last_price_field_name',
      label: 'Price Field',
      kind: 'enum',
      value: priceField ?? 'PX_LAST',
      options: STRIP_PRICE_FIELD_OPTIONS,
      advanced: true,
    },
    {
      name: 'open_interest_field_name',
      label: 'OI Field (blank = YAML OPEN_INT)',
      kind: 'text',
      value: oiField ?? '',
      advanced: true,
    },
  ];

  // ----- Identity -----
  const meta = curveMetaFor(curveFamily);
  const slope = data ? stripSlopeBps(data) : null;

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-y-auto"
      data-testid="strip-snapshot-extended"
    >
      {/* ---------- Title section ---------- */}
      <section className="flex flex-col gap-2 border-b border-line-subtle px-6 pt-5 pb-5">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-fg-muted">
          <Rows3 size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
          <span className="font-semibold">POLICY FUTURES STRIP SNAPSHOT</span>
          <span className="text-fg-faint" aria-hidden>•</span>
          <span>SNAPSHOT</span>
          <span className="text-fg-faint" aria-hidden>•</span>
          <span>DETERMINISTIC</span>
        </div>
        <div className="flex items-baseline gap-3">
          <h1 className="text-[24px] font-semibold leading-tight text-fg-primary">
            {meta?.flag && <span className="mr-2">{meta.flag}</span>}
            {meta ? `${meta.shortLabel.toUpperCase()} STRIP` : `${curveFamily} STRIP`}
          </h1>
          <span className="text-[13px] text-fg-secondary">
            {slopeReadLabel(slope)}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-secondary">
          <span>
            {meta?.longLabel ?? curveFamily} · whole strip on one aligned date
          </span>
          {data?.as_of_date && (
            <>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span>As of {data.as_of_date}</span>
            </>
          )}
          {data && (
            <>
              <span className="text-fg-faint" aria-hidden>·</span>
              <span>
                {data.quote_units} · {data.short_rate_regime}
              </span>
            </>
          )}
          <FreshnessPill freshness="fresh" />
        </div>
      </section>

      {/* ---------- Controls strip ---------- */}
      <div className="px-6 pt-4">
        <ControlsStrip
          controls={controls}
          onChange={handleControlChange}
          onReset={handleReset}
        />
      </div>

      {/* ---------- KPI strip ---------- */}
      <div className="px-6 pt-3">
        <KPIStrip kpis={data ? extendedKPIs(data) : EXTENDED_KPI_PLACEHOLDERS} />
      </div>

      {/* ---------- Strip curve + table + methodology ---------- */}
      <div className="grid grid-cols-1 gap-3 px-6 pt-3 pb-2 lg:grid-cols-[3fr_2fr]">
        <div className="flex flex-col gap-3">
          {errorMessage ? (
            <ExtendedError message={errorMessage} />
          ) : isLoading || !data ? (
            <ExtendedSkeleton />
          ) : (
            <>
              <StripCurveRead data={data} />
              <PositionTable data={data} />
            </>
          )}
        </div>
        <div className="flex flex-col gap-3">
          <MethodologyCard
            rows={
              data
                ? buildMethodologyRows(
                    data,
                    priceField ?? 'PX_LAST (YAML default)',
                    oiField ?? 'OPEN_INT (YAML default)',
                  )
                : []
            }
            references={buildReferenceChips()}
          />
        </div>
      </div>

      {/* ---------- Lineage footer ---------- */}
      <div className="mt-auto pt-3">
        <LineageFooter
          lineage={{
            toolName,
            version: 'v1',
            kind: 'Deterministic snapshot (aligned whole-strip read)',
            providers: [
              'TimescaleDB',
              'macro_data.v_market_data_daily_enriched',
              STRIP_SNAPSHOT_COMPACT_CAVEAT,
            ],
            asOf: data?.as_of_date,
            freshness: 'fresh',
          }}
        />
      </div>
    </div>
  );
};

export default BuildExtended;

// ---------------------------------------------------------------------------
// Strip-curve read — tabular-nums flex strip of implied_rate_pct by
// strip position.  Cross-sectional (x = strip slot, NOT time); bars are
// normalised within the strip's own min/max so "steep / flat /
// inverted" reads at a glance.  Sanctioned per-tool primary zone.
// ---------------------------------------------------------------------------

function StripCurveRead({ data }: { data: FuturesStripSnapshotOutput }) {
  const rates = data.snapshot.map((r) => r.implied_rate_pct);
  const min = Math.min(...rates);
  const max = Math.max(...rates);
  const span = max - min;
  const heightPct = (v: number) =>
    span > 0 ? 18 + ((v - min) / span) * 82 : 50;

  return (
    <section className="card px-5 py-4" data-testid="strip-curve-read">
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="kicker text-fg-muted">
          STRIP CURVE — IMPLIED RATE (%) BY POSITION
        </span>
        <span className="text-[10.5px] text-fg-faint">
          Single aligned read · {data.as_of_date}
        </span>
      </div>
      <div className="flex items-end gap-2">
        {data.snapshot.map((row) => (
          <div
            key={row.strip_position}
            className="flex min-w-0 flex-1 flex-col items-center gap-1.5"
          >
            <span className="mono text-[12.5px] font-medium tabular-nums text-fg-primary">
              {row.implied_rate_pct.toFixed(2)}
            </span>
            <div className="flex h-[72px] w-full items-end">
              <div
                className="w-full rounded-t-sm bg-ice-300/25 transition-[height]"
                style={{ height: `${heightPct(row.implied_rate_pct)}%` }}
                title={`${row.contract_code} · ${row.implied_rate_pct.toFixed(2)}%`}
                aria-hidden
              />
            </div>
            <span className="mono text-[10.5px] tabular-nums text-fg-secondary">
              {row.contract_code}
            </span>
            <span className="text-[9px] uppercase tracking-wide text-fg-faint">
              {stripSegmentLabel(row.strip_position)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Per-position table.  Each row carries its OWN methodology card via
// InfoTooltip (catalog standardness guardrail — a desk consumer copying
// one row still sees the regime label + conversion rule).
// ---------------------------------------------------------------------------

const TABLE_GRID =
  'grid grid-cols-[34px_minmax(58px,0.8fr)_minmax(72px,1fr)_auto_auto_auto_auto] items-center gap-x-3';

function PositionTable({ data }: { data: FuturesStripSnapshotOutput }) {
  return (
    <section className="card px-5 py-4" data-testid="strip-position-table">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="kicker text-fg-muted">PER-POSITION DETAIL</span>
        <span className="text-[10.5px] text-fg-faint">
          1D Δ shown in bp (wire carries percent points; ×100)
        </span>
      </div>
      <div
        className={`${TABLE_GRID} px-1 pb-1.5 text-[10.5px] uppercase tracking-wide text-fg-muted`}
      >
        <span>Pos</span>
        <span>Contract</span>
        <span>Underlying</span>
        <span className="text-right">Implied Rate</span>
        <span className="text-right">1D Δ (BP)</span>
        <span className="text-right">Z (252D)</span>
        <span className="text-right">OI</span>
      </div>
      <div className="flex flex-col">
        {data.snapshot.map((row) => (
          <PositionRow key={row.strip_position} row={row} />
        ))}
      </div>
    </section>
  );
}

function PositionRow({ row }: { row: FuturesStripSnapshotRow }) {
  const bps = dailyChangeBps(row);
  return (
    <div
      className={`${TABLE_GRID} border-t border-line-subtle px-1 py-1.5 text-[12.5px]`}
    >
      <span className="mono text-[12px] tabular-nums text-fg-muted">
        {row.strip_position}
      </span>
      <span className="flex min-w-0 items-center gap-1.5 text-fg-primary">
        <span className="mono truncate tabular-nums">{row.contract_code}</span>
        <InfoTooltip
          content={row.row_methodology_card}
          label={`Methodology for ${row.contract_code}`}
        />
      </span>
      <span className="mono truncate text-[12px] tabular-nums text-fg-secondary">
        {row.underlying_contract_code ?? '—'}
        {row.expiry_date ? ` · ${row.expiry_date}` : ''}
      </span>
      <span className="mono text-right tabular-nums text-fg-primary">
        {row.implied_rate_pct.toFixed(2)}%
      </span>
      <span
        className={`mono text-right tabular-nums ${toneTextClass(toneForChange(bps))}`}
      >
        {signedFixed(bps, 1)}
      </span>
      <span
        className={`mono text-right tabular-nums ${toneTextClass(toneForZScore(row.z_score_implied_rate))}`}
      >
        {signedFixed(row.z_score_implied_rate, 2)}
      </span>
      <span className="mono text-right tabular-nums text-fg-secondary">
        {formatContracts(row.open_interest)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function ExtendedSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-busy>
      <div className="card h-[180px] animate-pulse bg-line-subtle/20 px-5 py-4" />
      <div className="card h-[240px] animate-pulse bg-line-subtle/20 px-5 py-4" />
    </div>
  );
}

function ExtendedError({ message }: { message: string }) {
  return (
    <div className="card flex min-h-[180px] items-center justify-center px-6 py-6">
      <p className="max-w-[560px] text-center text-[12px] leading-[1.6] text-coral-300">
        {message}
      </p>
    </div>
  );
}
