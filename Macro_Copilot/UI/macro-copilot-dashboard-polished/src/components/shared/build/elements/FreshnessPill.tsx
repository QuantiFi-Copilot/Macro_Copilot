// ============================================================================
// shared/build/elements/FreshnessPill.tsx
// ----------------------------------------------------------------------------
// Tiny status pill: ●  Fresh  / ●  Stale.  Used in the compact card's
// footer and the extended canvas's bottom lineage row.  Finance-blind —
// any tool that surfaces a freshness signal can reuse this.
// ============================================================================

type Props = {
  freshness: 'fresh' | 'stale' | 'unknown';
};

export function FreshnessPill({ freshness }: Props) {
  const label =
    freshness === 'fresh' ? 'Fresh' : freshness === 'stale' ? 'Stale' : 'Unknown';
  const dotClass =
    freshness === 'fresh'
      ? 'bg-mint-300'
      : freshness === 'stale'
        ? 'bg-amber-300'
        : 'bg-fg-faint';
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-fg-secondary">
      <span className={`inline-block h-2 w-2 rounded-full ${dotClass}`} aria-hidden />
      {label}
    </span>
  );
}
