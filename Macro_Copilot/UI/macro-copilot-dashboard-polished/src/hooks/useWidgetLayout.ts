// ============================================================================
// useWidgetLayout
// ----------------------------------------------------------------------------
// Local-first widget layout management for /monitor and /rates (and
// future agent surfaces).  Persists to localStorage keyed by `viewId`,
// falls back to the supplied default layout when nothing is stored.
//
// V1 surface (this PR):
//   - localStorage persistence per viewId (e.g. "monitor", "rates")
//   - Add / remove / replace / reorder widgets
//   - Reset to default
//   - Version-stamped: bumping LAYOUT_VERSION invalidates stale layouts
//
// V2 (planned):
//   - Backend persistence via /api/v1/views (multi-device, shareable)
//   - Real-time sync across tabs
//
// The signature returned from this hook is a stable contract so the V2
// swap is a single-file change.  Consumers should never reach into
// localStorage directly.
// ============================================================================

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  LAYOUT_VERSION,
  buildWidgetInstance,
  isKnownWidgetType,
  widgetMeta,
  type LayoutState,
  type WidgetInstance,
  type WidgetSize,
} from '@/components/monitor/registry';

const STORAGE_PREFIX = 'macro_copilot:layout:';

type UseWidgetLayoutResult = {
  /** The current layout (always defined — defaults to `defaultLayout`
   *  when storage is empty / corrupted / version-mismatched). */
  layout: LayoutState;
  /** Add a fresh widget with default params for the type, optionally
   *  with size + params overrides. */
  addWidget: (
    typeId: string,
    overrides?: { size?: WidgetSize; params?: Record<string, unknown> },
  ) => void;
  /** Remove the widget instance with the given id. */
  removeWidget: (instanceId: string) => void;
  /** Replace one widget's params / size.  Used by the catalog modal
   *  when the user reconfigures an existing widget. */
  updateWidget: (
    instanceId: string,
    patch: Partial<Pick<WidgetInstance, 'size' | 'params'>>,
  ) => void;
  /** Move a widget to a different index in the array.  V1: optional —
   *  no DnD UI ships in this PR, but the primitive is here for V2's
   *  drag-and-drop reordering. */
  moveWidget: (instanceId: string, toIndex: number) => void;
  /** Reset to the default layout for this view. */
  resetLayout: () => void;
};

export function useWidgetLayout(
  viewId: string,
  defaultLayout: LayoutState,
): UseWidgetLayoutResult {
  const storageKey = `${STORAGE_PREFIX}${viewId}`;

  const [layout, setLayout] = useState<LayoutState>(() =>
    readFromStorage(storageKey, defaultLayout),
  );

  // Persist on every change.  Synchronous writes are fine here — these
  // are tiny JSON blobs (kilobytes at most).
  useEffect(() => {
    writeToStorage(storageKey, layout);
  }, [storageKey, layout]);

  const addWidget = useCallback<UseWidgetLayoutResult['addWidget']>(
    (typeId, overrides) => {
      const inst = buildWidgetInstance(typeId, overrides);
      if (!inst) return;
      setLayout((prev) => ({ ...prev, widgets: [...prev.widgets, inst] }));
    },
    [],
  );

  const removeWidget = useCallback<UseWidgetLayoutResult['removeWidget']>(
    (instanceId) => {
      setLayout((prev) => ({
        ...prev,
        widgets: prev.widgets.filter((w) => w.id !== instanceId),
      }));
    },
    [],
  );

  const updateWidget = useCallback<UseWidgetLayoutResult['updateWidget']>(
    (instanceId, patch) => {
      setLayout((prev) => ({
        ...prev,
        widgets: prev.widgets.map((w) =>
          w.id === instanceId
            ? {
                ...w,
                size: patch.size ?? w.size,
                params: patch.params ?? w.params,
              }
            : w,
        ),
      }));
    },
    [],
  );

  const moveWidget = useCallback<UseWidgetLayoutResult['moveWidget']>(
    (instanceId, toIndex) => {
      setLayout((prev) => {
        const fromIndex = prev.widgets.findIndex((w) => w.id === instanceId);
        if (fromIndex === -1 || fromIndex === toIndex) return prev;
        const next = [...prev.widgets];
        const [moved] = next.splice(fromIndex, 1);
        next.splice(Math.max(0, Math.min(next.length, toIndex)), 0, moved);
        return { ...prev, widgets: next };
      });
    },
    [],
  );

  const resetLayout = useCallback<UseWidgetLayoutResult['resetLayout']>(() => {
    setLayout(defaultLayout);
  }, [defaultLayout]);

  return useMemo(
    () => ({ layout, addWidget, removeWidget, updateWidget, moveWidget, resetLayout }),
    [layout, addWidget, removeWidget, updateWidget, moveWidget, resetLayout],
  );
}

// ----------------------------------------------------------------------------
// Storage helpers — defensive read with version + schema validation,
// fall back to the supplied default on any inconsistency.

function readFromStorage(key: string, fallback: LayoutState): LayoutState {
  if (typeof window === 'undefined' || !window.localStorage) return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as unknown;
    if (!isValidLayout(parsed)) return fallback;
    if (parsed.version !== LAYOUT_VERSION) return fallback;
    // Drop unknown widget types defensively (e.g. a deploy removed
    // a widget the user had configured).  Also enforce that each
    // widget's size is in its registered allowedSizes list.
    return {
      version: parsed.version,
      widgets: parsed.widgets.filter((w) => {
        if (!isKnownWidgetType(w.type)) return false;
        const meta = widgetMeta(w.type);
        return !!meta && meta.allowedSizes.includes(w.size);
      }),
    };
  } catch {
    return fallback;
  }
}

function writeToStorage(key: string, layout: LayoutState): void {
  if (typeof window === 'undefined' || !window.localStorage) return;
  try {
    window.localStorage.setItem(key, JSON.stringify(layout));
  } catch {
    // Quota / privacy mode — silently no-op.  V2's backend persistence
    // makes this case go away.
  }
}

function isValidLayout(x: unknown): x is LayoutState {
  if (!x || typeof x !== 'object') return false;
  const v = x as Record<string, unknown>;
  if (typeof v.version !== 'number') return false;
  if (!Array.isArray(v.widgets)) return false;
  return v.widgets.every(isValidInstance);
}

function isValidInstance(x: unknown): x is WidgetInstance {
  if (!x || typeof x !== 'object') return false;
  const v = x as Record<string, unknown>;
  return (
    typeof v.id === 'string' &&
    typeof v.type === 'string' &&
    typeof v.size === 'string' &&
    !!v.params &&
    typeof v.params === 'object'
  );
}
