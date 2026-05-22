// ============================================================================
// useWorkspaceNotes — debounced localStorage hook for the Notes tab.
// ----------------------------------------------------------------------------
// Reads on mount, writes-back on a 400ms debounce so the user's
// typing doesn't hammer ``window.localStorage`` synchronously.
// Clearing the slug (e.g. user navigates between workspaces) flushes
// the pending write before swapping state.
// ============================================================================

import { useEffect, useRef, useState } from 'react';
import {
  clearNotes,
  readNotes,
  writeNotes,
} from '@/components/build/notes/lib/notesStorage';

export interface UseWorkspaceNotesResult {
  value: string;
  setValue: (next: string) => void;
  clear: () => void;
}

const DEBOUNCE_MS = 400;

export function useWorkspaceNotes(
  slug: string | null,
): UseWorkspaceNotesResult {
  const [value, setValueRaw] = useState<string>('');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const slugRef = useRef<string | null>(null);
  const pendingWriteRef = useRef<string | null>(null);

  // Load on slug change.  Also flushes any pending write from the
  // previous slug so a navigation doesn't lose unsaved characters.
  useEffect(() => {
    if (debounceRef.current && slugRef.current && pendingWriteRef.current != null) {
      writeNotes(slugRef.current, pendingWriteRef.current);
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
      pendingWriteRef.current = null;
    }
    slugRef.current = slug;
    setValueRaw(slug ? readNotes(slug) : '');
  }, [slug]);

  const setValue = (next: string) => {
    setValueRaw(next);
    if (!slug) return;
    pendingWriteRef.current = next;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      if (slugRef.current) {
        writeNotes(slugRef.current, next);
      }
      pendingWriteRef.current = null;
      debounceRef.current = null;
    }, DEBOUNCE_MS);
  };

  const clear = () => {
    if (!slug) return;
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    pendingWriteRef.current = null;
    clearNotes(slug);
    setValueRaw('');
  };

  return { value, setValue, clear };
}
