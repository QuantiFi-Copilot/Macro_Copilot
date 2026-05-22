// ============================================================================
// ArtifactSparkline — minimal sparkline scoped to a workspace node.
// ----------------------------------------------------------------------------
// Wraps the existing ``<Sparkline>`` primitive so node-widget renderers
// don't repeat the preview-row → SparklinePoint mapping.  Two
// responsibilities:
//
//   1. Skip the sparkline when the artifact has no preview values
//      (e.g. snapshot-only primitives, an executed-but-empty Series).
//      Returns ``null`` rather than rendering an empty chart shell so
//      the parent card gets vertical space back.
//
//   2. Tone the line color to match the parent stage's category — input
//      cards get an ice-blue line, transform cards get violet, output
//      cards get amber.  Keeps the per-card visual identity consistent
//      between the gradient rail (top) and the sparkline (body).
// ============================================================================

import { useMemo } from 'react';
import { Sparkline } from '@/components/ui/Sparkline';
import type { ChartTone } from '@/lib/chart';
import type { ArtifactSummary } from '@/services/workspaceApi';
import type { StageCategory } from '@/components/build/lib/buildTypes';

type Props = {
  artifact: ArtifactSummary;
  category: StageCategory;
  height?: number;
  mode?: 'line' | 'area';
};

// Maps the Build stage category onto an existing ChartTone in the
// shared chart palette.  We use ``blue`` for input (matches Monitor's
// CrossMarketSpreadsWidget), ``rates`` for transform (matches Monitor's
// CurveSpreadsWidget — the "primary" analysis tone), and ``amber`` for
// output (matches Monitor's anomaly tone).  Adding "ice" / "violet" as
// distinct tones is a future palette extension if the visual register
// needs more separation.
const TONE_BY_CATEGORY: Record<StageCategory, ChartTone> = {
  input: 'blue',
  transform: 'rates',
  output: 'amber',
};

export function ArtifactSparkline({
  artifact,
  category,
  height = 64,
  mode = 'area',
}: Props) {
  const data = useMemo(() => {
    const values = artifact.preview_values ?? [];
    const idx = artifact.preview_index ?? [];
    // Filter null values (the wire shape allows them) so recharts
    // doesn't render gaps as zero — null breaks the line cleanly.
    const points: { value: number; index?: string }[] = [];
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (v == null || Number.isNaN(v)) continue;
      points.push({ value: v, index: idx[i] ?? String(i) });
    }
    return points;
  }, [artifact]);

  if (data.length < 2) return null;

  return (
    <div className="px-1.5">
      <Sparkline
        data={data}
        tone={TONE_BY_CATEGORY[category]}
        height={height}
        mode={mode}
      />
    </div>
  );
}
