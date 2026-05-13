// ============================================================================
// ToolParamSubtitle — inline dot-separated param chips beneath a widget header.
// ----------------------------------------------------------------------------
// PR C — the mockup renders each result card with a tight subtitle line
// that surfaces the most relevant parameters of the primitive ("UST ·
// 2Y-10Y · window 252d").  Per-tool widgets pass in a label/value
// array; this helper renders the inline "·"-separated layout that
// matches the mockup density.
//
// The labels stay short and uppercase-tracked; values render in mono
// to read as "literal parameter values" rather than prose.  Empty
// entries are filtered so a per-tool widget can pass conditional rows
// without branching at the call site.
// ============================================================================

import { cn } from '@/utils/cn';

export interface SubtitleChip {
  label: string;
  value: string;
}

type Props = {
  chips: Array<SubtitleChip | null | undefined>;
  className?: string;
};

export function ToolParamSubtitle({ chips, className }: Props) {
  const visible = chips.filter(
    (c): c is SubtitleChip => !!c && !!c.value && c.value.length > 0,
  );
  if (visible.length === 0) return null;

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-x-3 gap-y-1 px-5 pt-2 text-[10.5px]',
        className,
      )}
    >
      {visible.map((chip, i) => (
        <span key={chip.label + i} className="flex items-center gap-1.5">
          <span className="font-medium uppercase tracking-[0.14em] text-fg-faint">
            {chip.label}
          </span>
          <span className="font-mono text-[11px] text-fg-secondary">
            {chip.value}
          </span>
          {i < visible.length - 1 && (
            <span aria-hidden className="ml-1 text-fg-faint/50">
              ·
            </span>
          )}
        </span>
      ))}
    </div>
  );
}

/** Read a parameter out of a NodeSummary's ``params`` blob, honouring
 *  the substrate's nested shape: tool params live under ``params.params``
 *  (the outer object is ``{tool_name, output_field, params: {...}}``).
 *  Returns ``undefined`` when the param is absent or not a scalar
 *  string/number, so call sites can pass result to ``SubtitleChip``
 *  without runtime checks. */
export function extractToolParam(
  params: Record<string, unknown> | null | undefined,
  key: string,
): string | undefined {
  if (!params) return undefined;
  const inner =
    typeof params['params'] === 'object' && params['params'] != null
      ? (params['params'] as Record<string, unknown>)
      : params;
  const raw = inner[key];
  if (raw == null) return undefined;
  if (typeof raw === 'string') return raw;
  if (typeof raw === 'number') return String(raw);
  if (Array.isArray(raw)) {
    // Render arrays of scalars as comma-joined ("2Y,10Y").
    return raw
      .filter(
        (x): x is string | number =>
          typeof x === 'string' || typeof x === 'number',
      )
      .map(String)
      .join(',');
  }
  return undefined;
}
