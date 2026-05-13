// ============================================================================
// notesStorage.ts — localStorage-backed notes per workspace.
// ----------------------------------------------------------------------------
// V1 strategy: per-workspace markdown blobs live in localStorage,
// keyed by ``mc.notes.<slug>``.  No backend write — keeps PR B
// focused on the interactive surface; PR C (when notes become
// shareable across users) will lift this to a substrate-side table.
//
// The hook + helpers in this file are intentionally trivial —
// localStorage's quota (5-10MB) + the per-workspace key shape are
// the only operational concerns.
// ============================================================================

const KEY_PREFIX = 'mc.notes.';
const VERSION_KEY = 'mc.notes._schema_version';
const CURRENT_VERSION = 1;

/** Resolve the localStorage key for a workspace's notes blob. */
export function notesKey(slug: string): string {
  return `${KEY_PREFIX}${encodeURIComponent(slug)}`;
}

/** Read the notes blob for ``slug``.  Returns empty string when
 *  nothing has been written.  Defensive: handles a corrupt /
 *  non-string value by returning ''. */
export function readNotes(slug: string): string {
  try {
    ensureVersion();
    const raw = window.localStorage.getItem(notesKey(slug));
    return typeof raw === 'string' ? raw : '';
  } catch {
    return '';
  }
}

/** Write the notes blob for ``slug``.  No-op on quota error — we
 *  treat localStorage as best-effort and silently degrade rather
 *  than crashing the tab. */
export function writeNotes(slug: string, content: string): void {
  try {
    ensureVersion();
    window.localStorage.setItem(notesKey(slug), content);
  } catch {
    // Quota exceeded or storage unavailable — silent degrade.
  }
}

/** Wipe the notes blob.  Called by the "Clear notes" affordance on
 *  the tab + by the test cleanup. */
export function clearNotes(slug: string): void {
  try {
    window.localStorage.removeItem(notesKey(slug));
  } catch {
    // Silent.
  }
}

function ensureVersion(): void {
  try {
    const current = window.localStorage.getItem(VERSION_KEY);
    if (current === String(CURRENT_VERSION)) return;
    // Version bump path — currently a no-op (no migrations).  Set
    // the version so future bumps can branch.
    window.localStorage.setItem(VERSION_KEY, String(CURRENT_VERSION));
  } catch {
    // Silent.
  }
}
