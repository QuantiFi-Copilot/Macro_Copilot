// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_otr_ofr_spread_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is OTR/OFR spread's.  Mounted as a node body inside multi-
// tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
//
// The caveat footer reads ``data.methodology_note`` verbatim — the TD #27
// disclosure prose threaded from the wire's top-level field (NOT
// current_metrics like the more recent siblings).  This mirrors the
// financing-rate pattern.
// ============================================================================

import { Activity } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  buildReferenceBands,
  compactKPIs,
  countryMetaFor,
  identitySubtitle,
  sanitiseSpreadSeries,
  useOtrOfrSpreadData,
} from './otrOfrSpreadShared';

const SHORT_CAVEAT =
  'OTR premium — auction-cycle dependent; liquidity-premium proxy.';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const countryParam = (params.country ?? '').toUpperCase();
  const tenorParam = params.tenor ?? '';

  const { data, isLoading, errorMessage } = useOtrOfrSpreadData({
    country: countryParam,
    tenor: tenorParam,
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const country = cm?.country ?? countryParam;
  const tenor = cm?.tenor ?? tenorParam;
  const meta = countryMetaFor(country);

  // Prefer the wire's TOP-LEVEL methodology_note (TD #27 prose) when
  // available — the desk should see the SAME disclosure here as on the
  // Extended methodology card + Monitor tooltip.  Fall back to a short
  // tool-defined caveat only on cold/empty data.
  const caveatText = data?.methodology_note?.trim()
    ? data.methodology_note
    : SHORT_CAVEAT;

  return (
    <BuildCompactShell
      toolDisplayName="OTR-OFR Spread"
      statusPill="SNAPSHOT"
      headerIcon={<Activity size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: meta ? `${meta.short} · ${tenor}` : `${country} · ${tenor}`.trim() || '—',
        secondary: 'OTR-OFR',
        flag: meta?.flag,
        subtitle: country ? identitySubtitle(country) : undefined,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'SPREAD', value: '—', unit: 'bp' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="bp"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={caveatText}
      asOf={cm?.as_of_date}
      freshness="fresh"
      onExpand={onExpand}
      size={size}
      isLoading={isLoading}
      errorMessage={errorMessage ?? undefined}
      callMeta={callMeta}
    />
  );
};

export default BuildCompact;
