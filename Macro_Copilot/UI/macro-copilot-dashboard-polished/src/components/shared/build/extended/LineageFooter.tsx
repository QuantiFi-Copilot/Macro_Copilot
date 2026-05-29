// ============================================================================
// shared/build/extended/LineageFooter.tsx
// ----------------------------------------------------------------------------
// Bottom-of-canvas lineage row: short hash chip + tool name + version +
// kind + provider chain + as-of timestamp + freshness pill.
//
// Finance-blind — accepts a LineageDescriptor.  Per
// docs_revamped/03_standards/rendering_density.md §2.1 the extended
// view MUST surface provenance (tool name + as-of-date + lineage hash).
// ============================================================================

import type { LineageDescriptor } from '../lib/types';
import { FreshnessPill } from '../elements/FreshnessPill';

type Props = {
  lineage: LineageDescriptor;
};

export function LineageFooter({ lineage }: Props) {
  return (
    <footer
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line-subtle px-5 py-3 text-[11px] text-fg-muted"
      data-testid="lineage-footer"
    >
      {lineage.hash && (
        <span className="inline-flex items-center gap-1.5 rounded-md border border-line-subtle bg-bg-elevated px-2 py-0.5 font-mono">
          <span className="text-fg-faint">lineage</span>
          <span className="text-fg-secondary">{lineage.hash}</span>
        </span>
      )}
      <span className="font-mono text-fg-secondary">{lineage.toolName}</span>
      {lineage.version && (
        <>
          <span className="text-fg-faint" aria-hidden>·</span>
          <span>{lineage.version}</span>
        </>
      )}
      {lineage.kind && (
        <>
          <span className="text-fg-faint" aria-hidden>·</span>
          <span>{lineage.kind}</span>
        </>
      )}
      {lineage.providers &&
        lineage.providers.map((p, i) => (
          <span key={p} className="inline-flex items-center gap-3">
            <span className="text-fg-faint" aria-hidden>·</span>
            <span>{p}</span>
          </span>
        ))}
      <span className="ml-auto flex items-center gap-3">
        {lineage.asOf && <span>As of {lineage.asOf}</span>}
        {lineage.freshness && <FreshnessPill freshness={lineage.freshness} />}
      </span>
    </footer>
  );
}
