// ============================================================================
// useArtifactPayload — shared loader/cache for persisted artifact bodies.
// ----------------------------------------------------------------------------
// PR3 — the canonical entry point Build widgets use to fetch a
// persisted artifact's full ``StoredArtifact`` body by hash.  Wraps
// ``services/workspaceApi.getArtifactPayload`` with:
//
//   1. A process-wide cache keyed on the 64-char hash.  Same hash =
//      same artifact (substrate invariant); the cache survives across
//      remounts so toggling between Build tabs doesn't re-fetch the
//      same body.
//   2. In-flight de-duplication: if two widgets mount with the same
//      hash on the same render, they share one network request.
//   3. An ``AbortController`` per fetch so a widget that unmounts
//      while the fetch is still pending cancels it (saves bandwidth
//      on the most painful slow-network case — a workspace with
//      twenty large artifacts loading in parallel).
//   4. A ``refetch`` returned by the hook so a stale or errored
//      artifact can be re-pulled on user action (e.g. the widget
//      shows a "Retry" button after a transient backend hiccup).
//   5. ``loading`` / ``error`` / ``data`` flags shaped to match the
//      project's existing ``useWorkflows`` hooks for caller
//      consistency.
//
// The cache lives in a module-scoped Map so it's shared across
// every consumer in the bundle.  This matches the discipline in
// ``useWorkflows`` / ``useRatesData`` — small bespoke caches
// rather than pulling in React Query for a single endpoint.
//
// Concurrency
// -----------
// ``Map<hash, Entry>`` is the source of truth.  An Entry can be in
// one of three states (encoded structurally — no enum needed):
//
//   ``promise``-only  : fetch in flight, no result yet.
//   ``payload`` set   : settled successfully, cached forever.
//   ``error`` set     : settled with a non-2xx; consumers can call
//                        ``refetch`` to retry (this clears the slot
//                        before re-firing).
//
// We DON'T cache transient transport errors as "permanent" — the
// hook clears the error entry when ``refetch`` runs, so the next
// mount retries fresh.
//
// What this hook is NOT
// ---------------------
// Not a render-time mutation queue.  Not a stale-while-revalidate
// implementation (no background revalidation, no TTL — artifacts
// are content-addressed by SHA-256 so the body for a hash never
// changes server-side; refetching wouldn't help).  Not a wrapper
// for the catalogue/tools API — those have their own hooks in
// ``useWorkflows``.
// ============================================================================

import { useCallback, useEffect, useState } from 'react';
import type { ArtifactPayloadResponse } from '@/types/artifacts';
import {
  ArtifactPayloadError,
  getArtifactPayload,
  isLikelyArtifactHash,
} from '@/services/workspaceApi';

// ----------------------------------------------------------------------------
// Module-scoped cache.  Exported under the ``__testing`` namespace for
// the focused tests in ``src/components/build/widgets/__tests__/`` so
// each test starts from a known-empty state — production callers
// MUST NOT poke at these directly.
// ----------------------------------------------------------------------------

interface CacheEntry {
  /** Resolved payload (success path).  Mutually exclusive with ``error``. */
  payload?: ArtifactPayloadResponse;
  /** Settled error (failure path).  Cleared by ``refetch``. */
  error?: ArtifactPayloadError | Error;
  /** In-flight fetch, used for de-duplication across concurrent
   *  callers.  Cleared when the fetch settles. */
  promise?: Promise<ArtifactPayloadResponse>;
}

const _cache = new Map<string, CacheEntry>();

export const __testing = {
  clear(): void {
    _cache.clear();
  },
  get(hash: string): CacheEntry | undefined {
    return _cache.get(hash);
  },
  size(): number {
    return _cache.size;
  },
};

// ----------------------------------------------------------------------------
// Core fetch path — used by both the hook + the imperative
// ``prefetchArtifactPayload`` helper.  Honours the in-flight cache so
// two concurrent calls for the same hash share one network round-trip.
// ----------------------------------------------------------------------------

async function fetchAndCache(
  hash: string,
  signal?: AbortSignal,
): Promise<ArtifactPayloadResponse> {
  const cached = _cache.get(hash);
  if (cached?.payload) return cached.payload;
  if (cached?.promise) return cached.promise;

  // Start a fresh fetch.  Stash the promise in the cache slot BEFORE
  // awaiting so concurrent callers see + share it.
  const promise = getArtifactPayload(hash, { signal })
    .then((payload) => {
      // Resolve into the same slot the concurrent callers are awaiting.
      // Don't replace if the entry was cleared by a refetch in the
      // meantime; a refetch issues a fresh fetchAndCache and clears
      // the slot, so we'd overwrite the new fetch with this stale
      // result.  The conditional protects against that interleaving.
      const slot = _cache.get(hash);
      if (slot && slot.promise === promise) {
        _cache.set(hash, { payload });
      }
      return payload;
    })
    .catch((err) => {
      // fix-PR5 — never persist an ``AbortError`` as a permanent
      // cache result.  Pre-PR5 we wrote it into ``slot.error`` and
      // every future consumer of the same hash inherited a fake
      // "Fetch is aborted" failure even though the underlying
      // artifact was perfectly fetchable.  The retry button worked
      // because it deleted the slot first, which masked the bug
      // from quick local testing.
      //
      // On abort we instead CLEAR the in-flight slot so the next
      // consumer triggers a fresh request, and we still re-throw so
      // the awaiting hook's per-consumer ``cancelled`` flag (or its
      // own ``AbortError`` filter) sees the rejection.
      const isAbort =
        (err as { name?: string })?.name === 'AbortError' ||
        // ``DOMException`` from ``fetch`` on some runtimes uses
        // ``code === 20`` for the abort case.  Defensive check so
        // we never inadvertently poison the cache on the same
        // semantic event regardless of how the platform spells it.
        (err as { code?: number })?.code === 20;
      const slot = _cache.get(hash);
      if (slot && slot.promise === promise) {
        if (isAbort) {
          _cache.delete(hash);
        } else {
          _cache.set(hash, { error: err as Error });
        }
      }
      throw err;
    });

  _cache.set(hash, { promise });
  return promise;
}

/** Imperatively prime the cache for a hash without rendering a hook.
 *  Useful for pages that know they're about to render many widgets
 *  pointing at the same artifact (e.g. the workspace shell could
 *  prefetch the focus node's payload as soon as the workspace detail
 *  lands).  Optional optimisation — every consumer can still rely on
 *  ``useArtifactPayload`` alone. */
export function prefetchArtifactPayload(hash: string): void {
  if (!isLikelyArtifactHash(hash)) return;
  // Fire-and-forget; consumers will read the cache on mount.  Errors
  // settle into the cache slot via ``fetchAndCache``'s internal
  // ``.catch`` — we swallow the re-thrown rejection here so a failed
  // prefetch doesn't surface as an unhandled-promise warning.  The
  // next hook consumer for this hash will see the cached error and
  // render the local error state.
  fetchAndCache(hash).catch(() => {
    /* error already cached; intentional swallow */
  });
}

// ----------------------------------------------------------------------------
// Hook
// ----------------------------------------------------------------------------

export interface UseArtifactPayloadResult {
  /** The resolved payload, or ``null`` while loading / errored / no-hash. */
  data: ArtifactPayloadResponse | null;
  /** True while the fetch is in flight (also true on first mount
   *  before a cache check completes). */
  isLoading: boolean;
  /** Settled error if the fetch failed, ``null`` otherwise.
   *  ``ArtifactPayloadError`` carries the HTTP status so callers can
   *  branch on 404 vs 503 etc. */
  error: ArtifactPayloadError | Error | null;
  /** Trigger a fresh fetch, clearing the cache entry first.  Useful
   *  for a "Retry" button on the failure state. */
  refetch: () => void;
}

/** Load a persisted artifact's full body by hash, with caching + in-
 *  flight de-duplication.  Returns ``{data: null, isLoading: false,
 *  error: null}`` when ``hash`` is null / undefined / malformed — the
 *  hook never fires a network request for a missing hash.
 *
 *  Cancellation (fix-PR5)
 *  ---------------------
 *  Pre-PR5 the hook owned a per-consumer ``AbortController`` and
 *  aborted the in-flight fetch on unmount.  That collaboration had
 *  two failure modes:
 *
 *    1. The aborted fetch's rejection settled into the shared cache
 *       slot as a permanent error.  Any subsequent consumer reading
 *       the cache (including the very next render under React
 *       StrictMode) saw "Fetch is aborted" until the user clicked
 *       Retry.
 *
 *    2. ``fetchAndCache`` only accepts the FIRST caller's signal.
 *       If consumer A aborted while consumers B / C were also
 *       awaiting the same shared promise, B / C inherited A's
 *       cancellation and saw the same fake failure.
 *
 *  PR5 stops passing a signal from the hook.  Each consumer keeps a
 *  per-effect ``cancelled`` flag and uses it to ignore stale
 *  callbacks; the underlying fetch runs to completion and primes
 *  the cache for whichever consumer is still listening (or none —
 *  the cache survives an empty audience because content-addressed
 *  artifacts are valid forever).  ``fetchAndCache`` still accepts
 *  ``signal?`` for backwards compatibility — any future caller that
 *  truly wants cancellation can pass one — and additionally guards
 *  against poisoning the cache on abort. */
export function useArtifactPayload(
  hash: string | null | undefined,
): UseArtifactPayloadResult {
  const [data, setData] = useState<ArtifactPayloadResponse | null>(() => {
    if (!hash || !isLikelyArtifactHash(hash)) return null;
    return _cache.get(hash)?.payload ?? null;
  });
  const [error, setError] = useState<ArtifactPayloadError | Error | null>(
    () => {
      if (!hash || !isLikelyArtifactHash(hash)) return null;
      return _cache.get(hash)?.error ?? null;
    },
  );
  const [isLoading, setIsLoading] = useState<boolean>(() => {
    if (!hash || !isLikelyArtifactHash(hash)) return false;
    const slot = _cache.get(hash);
    if (slot?.payload || slot?.error) return false;
    return true; // Not yet cached → we'll fire on mount.
  });

  // Monotonic counter that bumps on refetch — drives the effect to
  // re-fire the network call even when the hash is unchanged.
  const [refetchSeq, setRefetchSeq] = useState(0);

  useEffect(() => {
    if (!hash || !isLikelyArtifactHash(hash)) {
      // Reset to the "no-hash" state cleanly.  Don't read the cache
      // here — if the hash is null we have no business reading from
      // it.
      setData(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    const cached = _cache.get(hash);
    if (cached?.payload) {
      setData(cached.payload);
      setError(null);
      setIsLoading(false);
      return;
    }
    if (cached?.error) {
      setData(null);
      setError(cached.error);
      setIsLoading(false);
      return;
    }

    setData(null);
    setError(null);
    setIsLoading(true);

    // PR5 — per-consumer cancellation lives on this local flag.  We
    // intentionally do NOT pass an AbortSignal into fetchAndCache:
    // see the hook docstring above for why aborting the shared
    // underlying fetch on unmount caused the "Fetch is aborted"
    // cache poisoning bug.
    let cancelled = false;

    fetchAndCache(hash)
      .then((payload) => {
        if (cancelled) return;
        setData(payload);
        setError(null);
        setIsLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        // Defensive — fetchAndCache itself never persists AbortError
        // since PR5, but consumers passing their own signal could
        // still surface one.  Drop it on the floor so the loading
        // state doesn't flip to a confusing "aborted" message.
        if ((err as { name?: string })?.name === 'AbortError') return;
        setError(err as Error);
        setData(null);
        setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [hash, refetchSeq]);

  const refetch = useCallback(() => {
    if (!hash) return;
    // Clear the cache slot before incrementing the sequence so the
    // effect's cache check on the next run misses (and re-fetches)
    // rather than reading the stale errored slot.
    _cache.delete(hash);
    setRefetchSeq((n) => n + 1);
  }, [hash]);

  return { data, isLoading, error, refetch };
}
