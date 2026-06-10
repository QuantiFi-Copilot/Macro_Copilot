// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_cross_country_real_yield_spread_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is cross-country real-yield's.  Mounted as a node body
// inside multi-tool query DAG visualizations.  Design reference:
// ./mockups/Compact.png.
//
// All layout / tone / chart / methodology-disclosure chrome lives in the
// shared shell at @/components/shared/build — finance-blind.  This file
// is fetch + descriptor-mapping + shell composition only.
// ============================================================================

import { Globe2 } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  buildReferenceBands,
  compactKPIs,
  identitySubtitle,
  linkerCurveFor,
  pairShortLabel,
  sanitiseSpreadSeries,
  shortIndexCaveat,
  useCrossCountryRealYieldSpread,
} from './crossCountryRealYieldSpreadSimpleShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const firstCurveParam = params.first_curve_family ?? '';
  const secondCurveParam = params.second_curve_family ?? '';

  const { data, isLoading, errorMessage } = useCrossCountryRealYieldSpread({
    firstCurveFamily: firstCurveParam,
    secondCurveFamily: secondCurveParam,
    tenor: params.tenor ?? '',
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const firstCurve = cm?.first_curve_family ?? firstCurveParam;
  const secondCurve = cm?.second_curve_family ?? secondCurveParam;
  const tenor = cm?.tenor ?? params.tenor ?? '';

  const aMeta = linkerCurveFor(firstCurve);
  const bMeta = linkerCurveFor(secondCurve);
  const pairLabel = pairShortLabel(firstCurve, secondCurve);

  // The wire's ``methodology_label`` is the authoritative load-bearing
  // disclosure (sourced from YAML at runtime — methodology_exposure §1
  // single source of truth).  For the compact footer we render a
  // CONCISE per-leg index-family caveat derived from the wire's curve-
  // family identifiers — methodology_label is the long-form prose,
  // surfaced in full on the Extended methodology card.
  const caveatLine =
    firstCurve && secondCurve
      ? shortIndexCaveat(firstCurve, secondCurve)
      : 'Different linker curves · index families not fungible';

  const pairFlags = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : '';
  const subtitle =
    firstCurve && secondCurve
      ? identitySubtitle(firstCurve, secondCurve, tenor)
      : undefined;

  return (
    <BuildCompactShell
      toolDisplayName="Cross-Country Linker Real-Yield Spread"
      statusPill="SNAPSHOT"
      headerIcon={<Globe2 size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: `${pairLabel} ${tenor} RY`.trim() || '—',
        secondary: subtitle,
        flag: pairFlags || undefined,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'SPREAD', value: '—', unit: '%' },
              { label: '1D CHANGE', value: '—', unit: 'bp' },
              { label: 'Z-SCORE (252D)', value: '—' },
            ]
      }
      chartPoints={sanitiseSpreadSeries(
        data?.time_series_spread?.rows ?? [],
      ).map((r) => ({ date: r.date, value: r.value ?? NaN }))}
      chartUnit="%"
      referenceBands={data ? buildReferenceBands(data) : []}
      caveatText={caveatLine}
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
