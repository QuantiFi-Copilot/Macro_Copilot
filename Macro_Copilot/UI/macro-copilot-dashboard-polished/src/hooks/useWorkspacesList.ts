// ============================================================================
// useWorkspacesList — fetch + cache the Build sidebar's workspace list.
// ----------------------------------------------------------------------------
// Thin client-side hook around ``listWorkspaces`` (services/workspaceApi).
// Owns a single fetch per filter+limit pair; supports cheap refetching
// when a workspace is created / renamed elsewhere.
//
// No TanStack Query here — the project's other hooks (useRatesData,
// useLibraryManifest, useThreads) all use vanilla useState +
// useEffect with abort-cancellation.  Matching the existing pattern
// keeps the code uniform; we can promote everything to a single
// query library in a later PR if/when caching gets non-trivial.
// ============================================================================

import { useCallback, useEffect, useState } from 'react';
import {
  listWorkspaces,
  type WorkspaceListItem,
  type WorkspaceListFilter,
} from '@/services/workspaceApi';

export interface UseWorkspacesListResult {
  /** Current list (empty array until first fetch resolves). */
  items: WorkspaceListItem[];
  isLoading: boolean;
  error: Error | null;
  /** Re-runs the fetch; useful after creating / renaming a workspace
   *  elsewhere in the app so the sidebar updates without a navigation. */
  refetch: () => void;
}

export function useWorkspacesList(args?: {
  filter?: WorkspaceListFilter;
  limit?: number;
}): UseWorkspacesListResult {
  const filter = args?.filter ?? 'recent';
  const limit = args?.limit ?? 25;

  const [items, setItems] = useState<WorkspaceListItem[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  // Monotonic refetch counter — bumping it triggers the effect below
  // to re-fire without changing the filter / limit dependencies.
  const [refetchSeq, setRefetchSeq] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    listWorkspaces({ filter, limit })
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof Error
            ? err
            : new Error(String(err ?? 'unknown error')),
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filter, limit, refetchSeq]);

  const refetch = useCallback(() => {
    setRefetchSeq((n) => n + 1);
  }, []);

  return { items, isLoading, error, refetch };
}
