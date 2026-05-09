// ============================================================================
// Library API client
// ----------------------------------------------------------------------------
// Single endpoint: GET /api/v1/library/manifest
// Returns the parsed YAML manifest as JSON.  The Library page reads
// this once on mount and renders all filtering / drilling client-side
// (the manifest is small — ~10 KB compressed for 18 tools).
// ============================================================================

import type { LibraryManifestResponse } from '@/types/library';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const LIBRARY_PREFIX = `${API_BASE}/api/v1/library`;

export async function fetchLibraryManifest(): Promise<LibraryManifestResponse> {
  const res = await fetch(`${LIBRARY_PREFIX}/manifest`);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(
      `Library manifest fetch failed (${res.status} ${res.statusText})${
        body ? `: ${body}` : ''
      }`,
    );
  }
  return res.json() as Promise<LibraryManifestResponse>;
}
