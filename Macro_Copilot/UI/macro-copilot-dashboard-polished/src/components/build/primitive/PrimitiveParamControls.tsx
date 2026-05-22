// ============================================================================
// PrimitiveParamControls — dropdown strip rendered in every primitive view.
// ----------------------------------------------------------------------------
// R6.2.  The legacy ``WorkspaceHeader`` had a parameter-control strip
// that mutated ``?curve_family=...&tenor=...`` URL params and re-
// fetched.  Phase 6 dropped that pattern when restoring the primitive
// views in R1/R2.  This component reintroduces it on the new Build
// chassis, scoped to the typed primitive views (Spread / CrossMarket /
// Butterfly / Yield / Regime / Scanner).
//
// Visual register
// ---------------
// A row of ``label: <select>`` pairs sitting under the header
// (PrimitiveCanvasShell renders this in the body slot for views that
// declare a non-empty spec list).  Selects use a tight monospace label
// pattern + the same ``.research-card``-adjacent surface treatment as
// the rest of Build.
//
// Change semantics
// ----------------
// Picking a value fires ``onChange(key, value)``.  The parent
// (``VirtualPrimitiveCanvas``) re-encodes ``?context=`` with the new
// params and pushes the URL; the canvas's existing decode-then-fetch
// effect re-runs with the new tool args.  This keeps the URL as the
// single source of truth — bookmark / share / refresh all work.
// ============================================================================

import type { ParamSpec } from './paramSpecs';
import { resolveParamValue } from './paramSpecs';

type Props = {
  specs: ParamSpec[];
  /** Current URL params (the same dict ``VirtualPrimitiveCanvas`` got
   *  from ``decodePrimitiveContext``).  Values not in this dict fall
   *  back to the spec's ``defaultValue``. */
  current: Record<string, string>;
  /** Disabled flag for the in-flight state — the controls render with
   *  a faint pulse so the user sees the re-fetch happening. */
  isLoading?: boolean;
  /** Fired when the user picks a new value.  Caller updates the URL
   *  and the canvas re-fetches. */
  onChange: (key: string, value: string) => void;
};

export function PrimitiveParamControls({
  specs,
  current,
  isLoading,
  onChange,
}: Props) {
  if (specs.length === 0) return null;
  return (
    <div
      className={[
        'flex flex-wrap items-end gap-3 border-t border-line-subtle bg-white/[0.012] px-6 py-3',
        isLoading ? 'opacity-70' : '',
      ].join(' ')}
    >
      {specs.map((spec) => {
        const value = resolveParamValue(spec, current);
        return (
          <label
            key={spec.key}
            className="flex flex-col gap-1 text-[10px] uppercase tracking-[0.16em] text-fg-muted"
          >
            <span>{spec.label}</span>
            <select
              value={value}
              disabled={isLoading}
              onChange={(e) => onChange(spec.key, e.target.value)}
              className="cursor-pointer rounded-md border border-line-soft bg-ink-900/60 px-2 py-1 font-mono text-[12px] normal-case tracking-normal text-fg-primary transition-colors hover:border-ice-400/40 focus:border-ice-400/55 focus:outline-none disabled:cursor-wait"
            >
              {spec.options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
        );
      })}
      {isLoading && (
        <span className="ml-auto self-center text-[10.5px] text-fg-faint">
          Refreshing…
        </span>
      )}
    </div>
  );
}
