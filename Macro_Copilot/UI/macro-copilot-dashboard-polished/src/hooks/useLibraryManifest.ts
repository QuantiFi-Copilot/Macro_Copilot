// ============================================================================
// useLibraryManifest
// ----------------------------------------------------------------------------
// One-shot fetch of the library manifest on mount.  Tiny payload, never
// changes during a browser session (the manifest is committed alongside
// code), so a single fetch + cache is fine — no React Query needed.
// ============================================================================

import { useEffect, useState } from 'react';
import { fetchLibraryManifest } from '@/services/libraryApi';
import type { LibraryManifestResponse } from '@/types/library';

type State = {
  data: LibraryManifestResponse | null;
  isLoading: boolean;
  error: Error | null;
};

export function useLibraryManifest(): State {
  const [state, setState] = useState<State>({
    data: null,
    isLoading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, isLoading: true, error: null });
    fetchLibraryManifest()
      .then((data) => {
        if (!cancelled) setState({ data, isLoading: false, error: null });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setState({
          data: null,
          isLoading: false,
          error: e instanceof Error ? e : new Error('manifest fetch failed'),
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
