// ============================================================================
// buildFlag.ts — feature flag gate for the redesigned Build surface
// ----------------------------------------------------------------------------
// PR A ships the new Build (Workspace) experience behind a Vite-time
// boolean.  The legacy ``WorkspacePage`` + ``WorkspaceBySlugPage`` stay
// mounted as fallbacks so any existing query-param URL (``/workspace
// ?tool=spread&...``) keeps working while the new shell is in review.
//
// Flip ``VITE_BUILD_V2`` to ``"1"`` in your local ``.env`` (or the
// docker-compose env block) to opt into the new shell.  Once review
// passes, the default flips here.
//
// Reading the flag at module-load time (rather than per-render) means
// every component sees the same value for the lifetime of the page,
// even if the env var changes between rebuilds — there is no implicit
// hot-flip behaviour.
// ============================================================================

/**
 * True when the new Build shell should render at ``/workspace``.
 *
 * Vite exposes ``import.meta.env`` at build time; values that aren't
 * declared in ``.env`` default to ``undefined``.  We treat any of
 * ``"1"`` / ``"true"`` / ``"yes"`` as truthy so the flag is easy to
 * flip from a shell variable without quoting gymnastics.
 */
export const BUILD_V2_ENABLED: boolean = (() => {
  const raw = (import.meta.env.VITE_BUILD_V2 as string | undefined) ?? '';
  return ['1', 'true', 'yes', 'on'].includes(raw.toLowerCase());
})();
