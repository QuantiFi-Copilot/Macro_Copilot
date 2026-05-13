// ============================================================================
// SavedPresetsPanel — list / load / delete saved configurations for a model
// ----------------------------------------------------------------------------
// Reads from services/modelPresets (localStorage v1).  Each preset binds:
//   - tool_name (which model)
//   - title (human label)
//   - params (the runnable dict)
//   - notes (optional)
// ============================================================================

import { useEffect, useState } from 'react';
import { Bookmark, Loader2, Play, Trash2 } from 'lucide-react';
import {
  loadPresetsFor,
  removePreset,
  type ModelPreset,
} from '@/services/modelPresets';
import { cn } from '@/utils/cn';

export function SavedPresetsPanel({
  toolName,
  refreshKey,
  onLoad,
  isRunning,
}: {
  toolName: string;
  /** Bumping this re-reads localStorage (e.g. after a save). */
  refreshKey: number;
  onLoad: (preset: ModelPreset, runImmediately?: boolean) => void;
  isRunning: boolean;
}) {
  const [presets, setPresets] = useState<ModelPreset[]>([]);

  useEffect(() => {
    setPresets(loadPresetsFor(toolName));
  }, [toolName, refreshKey]);

  if (presets.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-line-subtle bg-white/[0.005] px-3 py-3 text-center">
        <Bookmark size={12} className="mx-auto text-fg-faint" />
        <p className="mt-1.5 text-[10.5px] leading-snug text-fg-faint">
          No saved presets yet. Configure the model and click{' '}
          <span className="text-fg-secondary">Save preset</span> to create one.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      {presets.map((p) => (
        <div
          key={p.id}
          className="rounded-md border border-line-soft bg-white/[0.012] px-2.5 py-2"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <p className="truncate text-[11.5px] font-semibold text-fg-primary">
                {p.title}
              </p>
              {p.notes ? (
                <p className="mt-0.5 line-clamp-2 text-[10.5px] leading-snug text-fg-muted">
                  {p.notes}
                </p>
              ) : null}
              <p className="mt-1 mono text-[9.5px] text-fg-faint">
                {new Date(p.updatedAt).toLocaleDateString('en-US', {
                  month: 'short',
                  day: 'numeric',
                  year: 'numeric',
                })}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={() => onLoad(p)}
                title="Load into controls"
                className="rounded p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary"
              >
                <Bookmark size={11} />
              </button>
              <button
                type="button"
                onClick={() => onLoad(p, true)}
                disabled={isRunning}
                title="Load and run"
                className={cn(
                  'rounded p-1 transition-colors',
                  isRunning
                    ? 'cursor-not-allowed text-fg-faint'
                    : 'text-ice-300 hover:bg-ice-500/15',
                )}
              >
                {isRunning ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : (
                  <Play size={11} />
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Delete preset "${p.title}"?`)) {
                    removePreset(p.id);
                    setPresets(loadPresetsFor(toolName));
                  }
                }}
                title="Delete"
                className="rounded p-1 text-fg-muted transition-colors hover:bg-coral-500/10 hover:text-coral-300"
              >
                <Trash2 size={11} />
              </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
