// ============================================================================
// shared/build/extended/MethodologyCard.tsx
// ----------------------------------------------------------------------------
// Bottom-right methodology section: a label/value row stack + a chip
// row of references.  Finance-blind — the per-tool wrapper passes
// MethodologyRow[] + ReferenceChip[].
//
// Per docs_revamped/03_standards/rendering_density.md §2.1 the
// extended view MUST surface the full methodology card (per-call
// methodology, theoretical reference, known limitations summary).
// ============================================================================

import { ExternalLink } from 'lucide-react';
import { InfoTooltip } from '../elements/InfoTooltip';
import type { MethodologyRow, ReferenceChip } from '../lib/types';

type Props = {
  rows: ReadonlyArray<MethodologyRow>;
  references?: ReadonlyArray<ReferenceChip>;
  /** Optional click-target for the "View full details" affordance —
   *  typically opens a modal with the long-form caveat / source-
   *  material text. */
  onViewFullDetails?: () => void;
};

export function MethodologyCard({ rows, references, onViewFullDetails }: Props) {
  return (
    <section className="card flex flex-col gap-3 px-5 py-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="kicker text-fg-muted">METHODOLOGY</h3>
          <InfoTooltip content="The per-call methodology card: sources of each convention used in this run, theoretical reference, and known limitations summary.  Per docs_revamped/03_standards/methodology_disclosure.md every value the chart depends on must be reachable here." />
        </div>
        {onViewFullDetails && (
          <button
            type="button"
            onClick={onViewFullDetails}
            className="flex items-center gap-1 text-[11px] text-ice-300 hover:underline"
          >
            View full details
          </button>
        )}
      </div>

      <dl className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-1.5 text-[12px]">
        {rows.map((row, i) => (
          <RowPair key={`${row.label}-${i}`} row={row} />
        ))}
      </dl>

      {references && references.length > 0 && (
        <div className="flex flex-col gap-1.5 border-t border-line-subtle pt-3">
          <span className="kicker text-fg-faint">REFERENCES</span>
          <div className="flex flex-wrap gap-1.5">
            {references.map((ref, i) => (
              <ReferenceBadge key={`${ref.label}-${i}`} reference={ref} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function RowPair({ row }: { row: MethodologyRow }) {
  return (
    <>
      <dt className="text-fg-muted">{row.label}</dt>
      <dd className="text-fg-secondary">{row.value}</dd>
    </>
  );
}

function ReferenceBadge({ reference }: { reference: ReferenceChip }) {
  const inner = (
    <span className="inline-flex items-center gap-1 rounded-md border border-line-subtle bg-bg-elevated px-2 py-0.5 text-[11px] text-fg-secondary">
      {reference.label}
      {reference.href && <ExternalLink size={10} strokeWidth={1.5} />}
    </span>
  );
  if (reference.href) {
    return (
      <a
        href={reference.href}
        target="_blank"
        rel="noopener noreferrer"
        className="no-underline hover:text-fg-primary"
      >
        {inner}
      </a>
    );
  }
  return inner;
}
