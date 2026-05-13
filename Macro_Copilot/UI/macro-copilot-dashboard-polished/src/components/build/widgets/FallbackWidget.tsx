// ============================================================================
// FallbackWidget — renderer used when no per-type entry matches.
// ----------------------------------------------------------------------------
// Reached when the substrate introduces a new artifact type that the
// UI hasn't caught up with yet.  Instead of crashing or rendering an
// empty body, we surface the artifact's identity bits — type, units,
// row count, byte size, hash — so the workspace remains inspectable
// even on a brand-new artifact type.
//
// This is a "we know this exists but don't know how to display it
// yet" affordance — its presence should be a hint to the UI team
// that the renderer registry needs a new entry.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerFallbackRenderer } from '@/components/build/lib/nodeRendererRegistry';

const FallbackWidget: NodeRenderer = ({ artifact }) => {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 px-5 py-4">
      <div className="text-[10.5px] uppercase tracking-[0.16em] text-fg-muted">
        Artifact ready
      </div>
      <div className="flex items-baseline gap-2">
        <span className="font-serif-display text-[22px] font-light leading-tight text-fg-primary">
          {artifact.artifact_type}
        </span>
        {artifact.units && (
          <span className="text-[11px] text-fg-muted">{artifact.units}</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[10.5px]">
        {artifact.row_count != null && (
          <MetaRow label="Rows" value={artifact.row_count.toLocaleString()} />
        )}
        <MetaRow label="Bytes" value={artifact.byte_size.toLocaleString()} />
        <MetaRow
          label="Storage"
          value={artifact.inline ? 'inline' : 'blob'}
        />
        <MetaRow label="Hash" value={artifact.hash.slice(0, 10) + '…'} mono />
      </div>
      <p className="text-[10.5px] leading-[1.5] text-fg-faint">
        The UI doesn't ship a specialised renderer for this artifact
        type yet.  Identity is visible above; payload is available
        via the artifact-store endpoints.
      </p>
    </div>
  );
};

function MetaRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex min-w-0 items-baseline gap-2">
      <span className="text-fg-faint">{label}</span>
      <span
        className={
          mono
            ? 'truncate font-mono text-fg-secondary'
            : 'truncate text-fg-secondary'
        }
      >
        {value}
      </span>
    </div>
  );
}

registerFallbackRenderer(FallbackWidget);
export { FallbackWidget };
