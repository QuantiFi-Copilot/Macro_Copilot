// ============================================================================
// buildFlag.ts — feature flag gate for the Build surface.
// ----------------------------------------------------------------------------
// As of the Build revamp (Phase 0), the new shell is the only Build
// surface — there is no legacy fallback at runtime.  This module is
// kept ONLY as a no-op shim so that any in-flight branch still
// importing ``BUILD_V2_ENABLED`` keeps compiling.  A follow-up cleanup
// pass deletes this file once those branches merge.
//
// Do NOT add new readers of this constant.  The flag is permanently
// on; conditional code should be removed, not gated.
// ============================================================================

/**
 * Permanently ``true``.  Kept exported for source-compatibility with
 * branches that still reference the old gate.  Slated for removal in
 * the Build revamp cleanup phase.
 *
 * @deprecated
 */
export const BUILD_V2_ENABLED = true as const;
