// ============================================================================
// useWorkspaceDetail — fetch a single workspace + its replay drift snapshot.
// ----------------------------------------------------------------------------
// Parallel fetch of:
//   - ``GET /workspace/{slug}``           — DAG + per-node artifact summaries
//   - ``GET /workspace/{slug}/replay?mode=current`` — methodology drift
//
// The replay call is best-effort: a failure does NOT block the detail
// render (the drift surface just shows as null).  Mirrors the pattern
// the current ``WorkspaceBySlugPage`` already uses, lifted into a hook
// so the new Build shell can share state across the DagView / Results
// view / Copilot rail without prop-drilling a giant detail object.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  getWorkspace,
  replayWorkspace,
  type WorkspaceDetail,
  type WorkspaceReplay,
} from '@/services/workspaceApi';

export interface UseWorkspaceDetailResult {
  detail: WorkspaceDetail | null;
  replay: WorkspaceReplay | null;
  isLoading: boolean;
  error: Error | null;
}

export function useWorkspaceDetail(
  slug: string | undefined | null,
): UseWorkspaceDetailResult {
  const [detail, setDetail] = useState<WorkspaceDetail | null>(null);
  const [replay, setReplay] = useState<WorkspaceReplay | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!slug) {
      setDetail(null);
      setReplay(null);
      setIsLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    setError(null);

    Promise.all([
      getWorkspace(slug),
      replayWorkspace(slug, 'current').catch(() => null),
    ])
      .then(([d, r]) => {
        if (cancelled) return;
        setDetail(d);
        setReplay(r);
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
  }, [slug]);

  return { detail, replay, isLoading, error };
}
