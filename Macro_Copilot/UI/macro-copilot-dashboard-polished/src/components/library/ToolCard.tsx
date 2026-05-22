// ============================================================================
// ToolCard — one card per manifest tool
// ----------------------------------------------------------------------------
// Reuses .research-card chrome from Ask + Monitor; gradient top-rule
// keyed to the category's tone (data → ice, analysis → violet,
// anomaly → amber).
//
// Anatomy (top → bottom):
//   - Top-rail (gradient, color-keyed)
//   - Kicker:    CATEGORY · BUCKET-LABEL          (mono, uppercase)
//   - Title:     User-friendly tool name          (Inter, medium)
//   - Mono ref:  the actual tool_function name    (JetBrains Mono)
//   - Body:      one_liner from manifest          (Inter, regular)
//   - Hairline divider
//   - Chip row:  [SUB] [N controls] [N related] [N workflows]
//   - Provenance footer: lineage hash + built_date
// ============================================================================

import {
  BUCKET_LABELS,
  CATEGORY_LABELS,
  CATEGORY_TONE,
  SUB_AGENT_LABELS,
  type ManifestTool,
} from '@/types/library';
import { cn } from '@/utils/cn';
import { prettyTitle } from './lib/prettyTitle';

type Props = {
  tool: ManifestTool;
  onOpen: () => void;
};

export function ToolCard({ tool, onOpen }: Props) {
  const tone = CATEGORY_TONE[tool.category] ?? 'data';
  const railColor = railColorFor(tone);
  const subLabel = SUB_AGENT_LABELS[tool.sub_agent] ?? tool.sub_agent;
  const catLabel = CATEGORY_LABELS[tool.category] ?? tool.category;
  const bucketLabel = BUCKET_LABELS[tool.bucket] ?? tool.bucket;
  const lineageHash = stableShortHash(tool.name + tool.implementation.compute);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="research-card relative flex h-full min-h-[260px] w-full flex-col overflow-hidden text-left"
      style={{ ['--rail-color' as string]: railColor }}
    >
      <span aria-hidden className="research-card-rail" />

      {/* Header */}
      <div className="flex flex-col gap-1.5 px-5 pt-4 pb-2">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full shadow-[0_0_4px_currentColor]',
              tone === 'data'
                ? 'bg-ice-300'
                : tone === 'analysis'
                  ? 'bg-lineage-300'
                  : 'bg-amber-300',
            )}
          />
          <span className="font-mono text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
            {catLabel} · {bucketLabel}
          </span>
        </div>
        <h3 className="text-[15.5px] font-medium tracking-[-0.012em] text-fg-primary">
          {prettyTitle(tool.name)}
        </h3>
        <p className="font-mono text-[11px] tracking-[-0.005em] text-ice-300/80">
          {tool.implementation.tool_function}
        </p>
      </div>

      {/* One-liner body */}
      <p className="flex-1 px-5 pb-3 text-[13.5px] leading-[1.55] text-fg-secondary">
        {tool.one_liner}
      </p>

      {/* Hairline divider */}
      <div className="research-card-divider mx-5" />

      {/* Chip row */}
      <div className="flex flex-wrap items-center gap-1.5 px-5 pt-3">
        <Pill>{subLabel.toUpperCase()}</Pill>
        <Pill muted>
          {tool.pm_overridable.length} control{tool.pm_overridable.length !== 1 ? 's' : ''}
        </Pill>
        <Pill muted>
          {tool.related_tools.length} related
        </Pill>
        <Pill muted>
          {tool.workflows.length} workflow{tool.workflows.length !== 1 ? 's' : ''}
        </Pill>
      </div>

      {/* Provenance footer */}
      <div className="mt-auto flex items-center gap-2 px-5 pb-3 pt-3 font-mono text-[10px] text-fg-muted">
        <span className="lineage-chip">
          <span className="opacity-70">lineage</span>
          <span>{lineageHash}</span>
        </span>
        <span className="text-fg-faint">·</span>
        <span>built {tool.built_date}</span>
      </div>
    </button>
  );
}

// ----------------------------------------------------------------------------

function Pill({
  children,
  muted,
}: {
  children: React.ReactNode;
  muted?: boolean;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-px font-mono text-[9.5px] font-medium uppercase tracking-[0.14em]',
        muted
          ? 'bg-white/[0.025] text-fg-muted ring-1 ring-line-soft'
          : 'bg-ice-400/[0.08] text-ice-200 ring-1 ring-ice-400/25',
      )}
    >
      {children}
    </span>
  );
}

function railColorFor(tone: 'data' | 'analysis' | 'anomaly'): string {
  switch (tone) {
    case 'data':     return 'rgba(122, 162, 255, 0.55)';
    case 'analysis': return 'rgba(155, 140, 255, 0.45)';
    case 'anomaly':  return 'rgba(243, 183, 85, 0.55)';
  }
}

function stableShortHash(seed: string): string {
  let h = 5381;
  for (let i = 0; i < seed.length; i++) {
    h = ((h << 5) + h + seed.charCodeAt(i)) | 0;
  }
  return (h >>> 0).toString(16).padStart(8, '0').slice(-6);
}
