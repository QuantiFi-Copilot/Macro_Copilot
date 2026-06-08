// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// calculate_cross_country_breakeven_spread_simple_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every new primitive ships a compact
// view; this is cross-country breakeven's.  Mounted as a node body inside
// multi-tool query DAG visualizations.  Design reference: ./mockups/Compact.png.
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
  compactKPIs,
  buildReferenceBands,
  countryPairFor,
  identitySubtitle,
  pairShortLabel,
  sanitiseSpreadSeries,
  shortIndexCaveat,
  useCrossCountryBreakevenSpread,
} from './crossCountryBreakevenSpreadSimpleShared';

/** Same packed-pair URL convention as BuildExtended.  Keeps the multi-
 *  tool DAG renderer's URL state in sync between the compact card and
 *  its expand-modal extended view. */
function splitPair(packed: string | undefined): { nominal: string; linker: string } {
  if (!packed || !packed.includes('/')) return { nominal: '', linker: '' };
  const [nominal, linker] = packed.split('/');
  return { nominal, linker };
}

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const aPacked = params.country_a_pair ?? '';
  const bPacked = params.country_b_pair ?? '';
  const { nominal: aNominalParam, linker: aLinkerParam } = splitPair(aPacked);
  const { nominal: bNominalParam, linker: bLinkerParam } = splitPair(bPacked);

  const { data, isLoading, errorMessage } = useCrossCountryBreakevenSpread({
    countryANominalPair: aNominalParam,
    countryALinkerPair: aLinkerParam,
    countryBNominalPair: bNominalParam,
    countryBLinkerPair: bLinkerParam,
    tenor: params.tenor ?? '',
    lookbackDays:
      params.lookback_days != null ? Number(params.lookback_days) : undefined,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;
  const aNominal = cm?.country_a_nominal_pair ?? aNominalParam;
  const aLinker = cm?.country_a_linker_pair ?? aLinkerParam;
  const bNominal = cm?.country_b_nominal_pair ?? bNominalParam;
  const bLinker = cm?.country_b_linker_pair ?? bLinkerParam;
  const tenor = cm?.tenor ?? params.tenor ?? '';

  const aMeta = countryPairFor(aNominal, aLinker);
  const bMeta = countryPairFor(bNominal, bLinker);
  const pairLabel = pairShortLabel(aNominal, aLinker, bNominal, bLinker);

  // The wire's ``methodology_label`` is the authoritative load-bearing
  // disclosure (sourced from YAML at runtime — methodology_exposure §1
  // single source of truth).  For the compact footer we render a
  // CONCISE per-leg index-family caveat derived from the wire's
  // (nominal, linker) pair identifiers — methodology_label is the
  // long-form prose, surfaced in full on the Extended methodology card.
  const caveatLine =
    aNominal && bNominal
      ? shortIndexCaveat(aNominal, aLinker, bNominal, bLinker)
      : 'Different sovereign issuers · index families not fungible';

  const pairFlags = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : '';
  const subtitle =
    aNominal && bNominal
      ? identitySubtitle(aNominal, aLinker, bNominal, bLinker, tenor)
      : undefined;

  return (
    <BuildCompactShell
      toolDisplayName="Cross-Country Bond Breakeven Spread"
      statusPill="SNAPSHOT"
      headerIcon={<Globe2 size={12} strokeWidth={1.75} className="text-ice-300" />}
      identity={{
        primary: `${pairLabel} ${tenor} BE`.trim() || '—',
        secondary: subtitle,
        flag: pairFlags || undefined,
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
