// ============================================================================
// Saved model presets — localStorage v1
// ----------------------------------------------------------------------------
// Stores the user's saved configurations for a model (e.g. "UST 10Y vs SOFR
// 2Y rolling beta · 60d window") as JSON in localStorage under one well-
// known key.  V1 is intentionally local-only — everything the future
// backend-backed presets API needs is already in the type signature, so
// swapping in a fetch-backed service is a one-file change.
//
// Discipline
// ----------
// - Presets are scoped per tool_name.  Different models cannot collide.
// - Every preset carries a stable `id` (created at save time) so the UI
//   can update / delete by id without coupling to the title.
// - `createdAt` / `updatedAt` are ISO strings.
// - The `params` field is the raw param dict the user submitted (the
//   same shape the workflow run endpoint expects).
//
// Future swap to backend
// ----------------------
// Replace `loadAll`, `save`, `remove` with HTTP calls.  The signature
// + return shape stays.  Components consuming this don't change.
// ============================================================================

const STORAGE_KEY = 'macro-copilot:model-presets:v1';

export type ModelPreset = {
  /** Stable id, randomly assigned at save time. */
  id: string;
  /** Backend tool_name this preset binds to. */
  toolName: string;
  /** User-supplied label (e.g. "UST 10Y vs SOFR 2Y rolling beta"). */
  title: string;
  /** Raw param dict — same shape passed to POST /tools/{name}/run. */
  params: Record<string, unknown>;
  /** Optional notes the user typed when saving. */
  notes?: string;
  createdAt: string;
  updatedAt: string;
};

type StoreShape = {
  version: 1;
  presets: ModelPreset[];
};

function readStore(): StoreShape {
  try {
    const raw =
      typeof window === 'undefined'
        ? null
        : window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { version: 1, presets: [] };
    const parsed = JSON.parse(raw) as StoreShape;
    if (parsed.version !== 1 || !Array.isArray(parsed.presets)) {
      return { version: 1, presets: [] };
    }
    return parsed;
  } catch {
    return { version: 1, presets: [] };
  }
}

function writeStore(store: StoreShape): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Quota errors etc. — silently swallow; the user's session
    // continues, the preset just doesn't persist.
  }
}

export function loadAllPresets(): ModelPreset[] {
  return readStore().presets;
}

export function loadPresetsFor(toolName: string): ModelPreset[] {
  return loadAllPresets()
    .filter((p) => p.toolName === toolName)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export function savePreset(input: {
  toolName: string;
  title: string;
  params: Record<string, unknown>;
  notes?: string;
}): ModelPreset {
  const store = readStore();
  const now = new Date().toISOString();
  const preset: ModelPreset = {
    id: `mp_${Math.random().toString(36).slice(2, 10)}_${Date.now()}`,
    toolName: input.toolName,
    title: input.title.trim() || 'Untitled preset',
    params: input.params,
    notes: input.notes?.trim() || undefined,
    createdAt: now,
    updatedAt: now,
  };
  store.presets.push(preset);
  writeStore(store);
  return preset;
}

export function removePreset(id: string): void {
  const store = readStore();
  store.presets = store.presets.filter((p) => p.id !== id);
  writeStore(store);
}

export function updatePreset(
  id: string,
  patch: Partial<Pick<ModelPreset, 'title' | 'notes' | 'params'>>,
): ModelPreset | null {
  const store = readStore();
  const idx = store.presets.findIndex((p) => p.id === id);
  if (idx === -1) return null;
  const next: ModelPreset = {
    ...store.presets[idx],
    ...patch,
    updatedAt: new Date().toISOString(),
  };
  store.presets[idx] = next;
  writeStore(store);
  return next;
}
