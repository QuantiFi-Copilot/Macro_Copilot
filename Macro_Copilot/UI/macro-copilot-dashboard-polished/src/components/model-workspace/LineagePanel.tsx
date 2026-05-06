// ============================================================================
// LineagePanel — what just ran (tool + params + conventions resolved)
// ----------------------------------------------------------------------------
// Reads the active run's tool name + the marshalled params + the static
// methodology conventions from the ToolCard.  Renders a compact audit
// surface so a PM / regulator can answer "what did the system do?".
//
// V1 surfaces:
//   - tool identity (tool_name + domain + category)
//   - bound input params (the runnable dict, key + value)
//   - resolved conventions (the read-only knobs from config.yaml)
//
// Future: the run envelope can include a workflow lineage chain when
// the executor records one — wire it in here.
// ============================================================================

import type { ToolCard } from '@/types/workflows';

export function LineagePanel({
  card,
  params,
}: {
  card: ToolCard;
  params: Record<string, unknown>;
}) {
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-line-soft bg-white/[0.012] px-3 py-2.5">
        <p className="text-[10px] uppercase tracking-[0.12em] text-fg-faint">
          Tool
        </p>
        <p className="mt-1 mono text-[11.5px] text-fg-primary">{card.tool_name}</p>
        <p className="mt-1 text-[10.5px] text-fg-muted">
          {card.domain}
          {card.category ? ` · ${card.category}` : ''}
        </p>
      </div>

      <div className="rounded-md border border-line-soft bg-white/[0.012] px-3 py-2.5">
        <p className="text-[10px] uppercase tracking-[0.12em] text-fg-faint">
          Params · this run
        </p>
        {Object.keys(params).length === 0 ? (
          <p className="mt-1 text-[10.5px] text-fg-faint italic">(none — defaults)</p>
        ) : (
          <dl className="mt-1.5 space-y-1">
            {Object.entries(params).map(([k, v]) => (
              <div
                key={k}
                className="flex items-baseline justify-between gap-2 border-b border-line-subtle/40 py-0.5 last:border-b-0"
              >
                <dt className="mono shrink-0 text-[10.5px] text-fg-secondary">
                  {k}
                </dt>
                <dd className="mono min-w-0 truncate text-right text-[10.5px] text-fg-primary">
                  {formatParam(v)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      {card.conventions.length > 0 ? (
        <div className="rounded-md border border-line-soft bg-white/[0.012] px-3 py-2.5">
          <p className="text-[10px] uppercase tracking-[0.12em] text-fg-faint">
            Resolved conventions
          </p>
          <p className="mt-1 text-[10px] leading-snug text-fg-faint">
            Locked at compute time from <code className="mono text-ice-300">config.yaml</code>.
          </p>
          <dl className="mt-1.5 space-y-0.5">
            {card.conventions.map((c) => (
              <div
                key={c.name}
                className="flex items-baseline justify-between gap-2 border-b border-line-subtle/40 py-0.5 last:border-b-0"
              >
                <dt className="mono shrink-0 text-[10.5px] text-fg-secondary">
                  {c.name}
                </dt>
                <dd className="mono min-w-0 truncate text-right text-[10.5px] text-fg-primary">
                  {String(c.value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </div>
  );
}

function formatParam(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '—';
  if (typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return '[]';
    return JSON.stringify(v);
  }
  if (typeof v === 'object') {
    try {
      return JSON.stringify(v);
    } catch {
      return '[object]';
    }
  }
  return String(v);
}
