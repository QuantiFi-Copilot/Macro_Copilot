// ============================================================================
// useWorkspaceSiblings — fetch workspaces sharing a parent for VariantStrip.
// ----------------------------------------------------------------------------
// Returns the list of sibling workspaces (same ``parent_workspace_id``)
// plus the parent's own listing entry when the active workspace is a
// child.  When the active workspace has no parent (top-level run), we
// return its DIRECT children so the user sees the variant tree from
// either entry point.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  listWorkspaces,
  type WorkspaceListItem,
} from '@/services/workspaceApi';

export interface UseWorkspaceSiblingsResult {
  /** Workspaces in the variant family (excluding the active one). */
  siblings: WorkspaceListItem[];
  isLoading: boolean;
  error: Error | null;
}

export function useWorkspaceSiblings(args: {
  /** The active workspace's UUID — used to filter itself out of the
   *  results.  When null (e.g. while detail is still loading), the
   *  hook returns idle. */
  selfId: string | null;
  /** The active workspace's parent_workspace_id (when forked) or
   *  the active workspace's own id (when looking at its children). */
  rootId: string | null;
}): UseWorkspaceSiblingsResult {
  const [siblings, setSiblings] = useState<WorkspaceListItem[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!args.rootId) {
      setSiblings([]);
      setIsLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    setError(null);

    listWorkspaces({
      parent_workspace_id: args.rootId,
      limit: 20,
    })
      .then((res) => {
        if (cancelled) return;
        const filtered = args.selfId
          ? res.items.filter((w) => w.workspace_id !== args.selfId)
          : res.items;
        setSiblings(filtered);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [args.rootId, args.selfId]);

  return { siblings, isLoading, error };
}
