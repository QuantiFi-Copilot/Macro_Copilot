// ============================================================================
// AsOfControl — global "view the page as of <date>" selector
// ----------------------------------------------------------------------------
// Reads/writes the page-level as-of date in RatesDataContext.  null = latest
// live data (the default).  Picking a date re-fetches every pre-aggregated
// card with ?as_of_date=<date>, so the WHOLE Monitor surface re-computes as of
// that historical trade date (replayable view).  Renders nothing when no
// RatesDataProvider is mounted (degrades gracefully on legacy layouts).
// ============================================================================

import { CalendarClock } from 'lucide-react';
import { useOptionalRatesDataContext } from './RatesDataProvider';

export function AsOfControl() {
  const ctx = useOptionalRatesDataContext();
  if (!ctx) return null;
  const { asOf, setAsOf } = ctx;

  return (
    <div
      className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-medium text-fg-secondary ring-1 ring-line-soft"
      title="View the whole page as of a historical trade date (replayable). Clear to return to the latest live data."
    >
      <CalendarClock size={11} className="text-fg-muted" />
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-fg-muted">
        As&nbsp;of
      </span>
      <input
        type="date"
        value={asOf ?? ''}
        onChange={(e) => setAsOf(e.target.value || null)}
        className="bg-transparent text-fg-primary outline-none [color-scheme:dark]"
        aria-label="As-of date"
      />
      {asOf && (
        <button
          type="button"
          onClick={() => setAsOf(null)}
          className="ml-0.5 rounded px-1 text-[10.5px] text-fg-muted transition-colors hover:text-fg-primary"
          title="Back to latest (live) data"
        >
          Live
        </button>
      )}
    </div>
  );
}
