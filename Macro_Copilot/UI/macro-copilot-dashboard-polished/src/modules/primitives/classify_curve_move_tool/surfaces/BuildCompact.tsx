// ============================================================================
// surfaces/BuildCompact.tsx — Compact Build view for
// classify_curve_move_tool.
// ----------------------------------------------------------------------------
// Per rendering_density.md §2.2 + §5 every primitive ships a compact
// view; this is the classifier's grid card.  KPI-CENTRIC card (NO
// sparkline): the wire is a SINGLE classified observation — a
// time-series visual would misrepresent a snapshot as history (the
// same §2.2 semantic-contract guardrail the half-life compact
// documents).  Identity / headline KPIs / methodology caveat /
// expand / tone cues are all honoured.
// ============================================================================

import { GitBranch } from 'lucide-react';
import {
  BuildCompactShell,
  type BuildCompactProps,
} from '@/components/shared/build';
import {
  CLASSIFY_COMPACT_CAVEAT,
  CLASSIFY_DEFAULTS,
  compactKPIs,
  useRegimeData,
} from './classifyCurveMoveShared';

const BuildCompact: React.FC<BuildCompactProps> = ({
  params,
  size = 'small',
  onExpand,
  callMeta,
}) => {
  const curveFamily = params.curve_family || CLASSIFY_DEFAULTS.curve_family;
  const frontTenor = params.front_tenor || CLASSIFY_DEFAULTS.front_tenor;
  const backTenor = params.back_tenor || CLASSIFY_DEFAULTS.back_tenor;
  const lookbackPeriod =
    params.lookback_period || CLASSIFY_DEFAULTS.lookback_period;

  const { data, isLoading, errorMessage } = useRegimeData({
    curveFamily,
    frontTenor,
    backTenor,
    lookbackPeriod,
    fieldName: params.field_name || undefined,
  });

  const cm = data?.current_metrics;

  return (
    <BuildCompactShell
      toolDisplayName="Curve Move Classifier"
      statusPill="SNAPSHOT"
      headerIcon={
        <GitBranch size={12} strokeWidth={1.75} className="text-ice-300" />
      }
      identity={{
        primary: `${curveFamily} ${frontTenor}/${backTenor}`,
        secondary: `${lookbackPeriod} move`,
      }}
      kpis={
        data
          ? compactKPIs(data)
          : [
              { label: 'REGIME', value: '—' },
              { label: 'SPREAD Δ', value: '—' },
              { label: 'DRIVER', value: '—' },
            ]
      }
      // Single classified observation — no series exists on the wire,
      // so chartPoints is OMITTED: the shell renders the chartless
      // KPI-card layout (rendering_density.md §2.2 semantic contract;
      // an empty ARRAY would instead show the misleading "No data in
      // window" empty-series state).
      caveatText={CLASSIFY_COMPACT_CAVEAT}
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
