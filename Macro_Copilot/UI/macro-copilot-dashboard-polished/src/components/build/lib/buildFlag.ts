// ============================================================================
// buildFlag.ts — feature flag gate for the redesigned Build surface
// ----------------------------------------------------------------------------
// PRs A + B shipped the new Build (Workspace) experience.  PR C flips
// the default to ON: the new shell is what users see at ``/workspace``
// unless ``VITE_BUILD_V2`` is explicitly set to a falsy value (``"0"``,
// ``"false"``, ``"off"``).  The legacy ``WorkspacePage`` /
// ``WorkspaceBySlugPage`` stay mounted as fallbacks so any in-flight
// debugging session can opt back into the old surface without a
// re-deploy.
//
// Reading the flag at module-load time (rather than per-render) means
// every component sees the same value for the lifetime of the page,
// even if the env var changes between rebuilds — there is no implicit
// hot-flip behaviour.
// ============================================================================

/**
 * True when the new Build shell should render at ``/workspace``.
 *
 * Defaults to ``true``.  Set ``VITE_BUILD_V2=0`` (or ``false`` / ``off``)
 * to opt back into the legacy workspace page.  Any other value — or
 * the var being unset — leaves the new shell active.
 */
export const BUILD_V2_ENABLED: boolean = (() => {
  const raw = (import.meta.env.VITE_BUILD_V2 as string | undefined) ?? '';
  const normalized = raw.toLowerCase().trim();
  // Explicit opt-OUT — anything that looks like a "no" turns the flag off.
  if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
  // Default: ON.
  return true;
})();
