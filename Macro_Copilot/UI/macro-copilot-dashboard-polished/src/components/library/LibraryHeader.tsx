// ============================================================================
// LibraryHeader — page-top kicker + display headline
// ----------------------------------------------------------------------------
// One Instrument Serif italic accent on "tool" — same editorial moment
// pattern used on Ask + Monitor + agent placeholders.  Right side
// surfaces the live counts so the page declares its scope at a
// glance (read from manifest).
// ============================================================================

type Props = {
  primitiveCount: number;
  // Workflows + operators are placeholders in this PR; their counts
  // come from elsewhere (workflows live at /workflows; operators
  // ship in V2).
  workflowCount: number;
  operatorCount: number | null;
};

export function LibraryHeader({
  primitiveCount,
  workflowCount,
  operatorCount,
}: Props) {
  return (
    <div className="flex items-end justify-between gap-6 px-8 pb-6 pt-7">
      <div className="min-w-0">
        <p className="font-mono text-[10.5px] font-medium uppercase tracking-[0.16em] text-fg-muted">
          LIBRARY · CATALOGUE
        </p>
        <h1 className="mt-2.5 text-[30px] font-light leading-[1.1] tracking-[-0.018em] text-fg-primary">
          Every{' '}
          <span className="font-serif-display text-[1.05em] font-normal italic text-ice-100">
            tool
          </span>{' '}
          the copilot can call.
        </h1>
        <p className="mt-1.5 text-[13px] leading-[1.55] text-fg-secondary">
          Read directly from the manifest YAMLs.  Adding a tool here means
          writing the manifest entry.
        </p>
      </div>
      <div className="flex shrink-0 items-baseline gap-3 font-mono text-[11px] tracking-[0.02em]">
        <span className="text-fg-secondary">
          {primitiveCount}{' '}
          <span className="text-fg-muted">primitives</span>
        </span>
        <span className="text-fg-faint">·</span>
        <span className="text-fg-secondary">
          {workflowCount}{' '}
          <span className="text-fg-muted">workflows</span>
        </span>
        <span className="text-fg-faint">·</span>
        <span className="text-fg-faint">
          {operatorCount ?? '—'}{' '}
          <span>operators (soon)</span>
        </span>
      </div>
    </div>
  );
}
