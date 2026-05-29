// ============================================================================
// build/lib/focusedMode.tsx — Focused-mode context + hook.
// ----------------------------------------------------------------------------
// When a per-tool extended Build surface mounts, it can request that the
// shell collapse the workspaces-sidebar + copilot-rail so the canvas
// gets the full viewport width.  This is the standard pattern for
// "deep analysis" surfaces (Bloomberg Terminal, TradingView, etc.) and
// matches the rendering-density mockup's full-width assumption.
//
// Per docs_revamped/03_standards/rendering_density.md the extended view
// owns the entire Build canvas — but the AROUND-CANVAS chrome
// (sidebar + rail) is shell infrastructure, not per-tool.  Without a
// way for the per-tool surface to ASK the shell to step back, the
// chrome squeezes the canvas to ~60% of viewport width and the
// mockup's spacious layout becomes cramped.
//
// Pattern:
//   1. Per-tool surface (e.g. real_yield_level/surfaces/BuildExtended.tsx)
//      calls ``useRequestFocusedMode(true)`` on mount.
//   2. ``BuildShellLayout`` reads the context, switches its grid from
//      "sidebar | canvas | rail" to "canvas only" + small edge toggle
//      buttons to bring back the chrome on demand.
//   3. On unmount the per-tool surface releases focused mode and the
//      shell reverts to the 3-column layout.
//
// User remains in control: small chevron buttons on the left/right
// edges of the canvas let them re-open the sidebar / rail at any time.
// State is per-Build-mount (not URL-persisted) so navigating away +
// back resets to default focused (or not) per the new surface's
// request.
// ============================================================================

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

interface FocusedModeContextValue {
  /** True when the canvas has requested focused mode AND the user
   *  hasn't manually overridden by re-opening a panel. */
  isFocused: boolean;
  /** Per-side override state — when a panel is manually re-opened by
   *  the user, that panel renders even in focused mode. */
  sidebarOverride: boolean | null;
  railOverride: boolean | null;
  /** Set the canvas-side focused-mode request.  The canvas calls
   *  this with ``true`` on mount, ``false`` on unmount. */
  requestFocus: (focused: boolean) => void;
  /** User-side per-panel toggles (called from the edge buttons). */
  toggleSidebar: () => void;
  toggleRail: () => void;
}

const FocusedModeContext = createContext<FocusedModeContextValue | null>(null);

/** Provider — mount inside BuildShell, around the BuildShellLayout. */
export function FocusedModeProvider({ children }: { children: ReactNode }) {
  const [isFocused, setIsFocused] = useState(false);
  const [sidebarOverride, setSidebarOverride] = useState<boolean | null>(null);
  const [railOverride, setRailOverride] = useState<boolean | null>(null);

  const value = useMemo<FocusedModeContextValue>(
    () => ({
      isFocused,
      sidebarOverride,
      railOverride,
      requestFocus: (focused: boolean) => {
        setIsFocused(focused);
        // When entering focused mode, reset user overrides so the
        // chrome collapses cleanly.  When exiting, also clear them so
        // the next focused-request starts fresh.
        setSidebarOverride(null);
        setRailOverride(null);
      },
      toggleSidebar: () => {
        setSidebarOverride((prev) =>
          prev == null ? true : prev ? false : null,
        );
      },
      toggleRail: () => {
        setRailOverride((prev) =>
          prev == null ? true : prev ? false : null,
        );
      },
    }),
    [isFocused, sidebarOverride, railOverride],
  );

  return (
    <FocusedModeContext.Provider value={value}>
      {children}
    </FocusedModeContext.Provider>
  );
}

/** Read the current focused-mode state.  Returns null when called
 *  outside the provider (shell-level code that doesn't care). */
export function useFocusedMode(): FocusedModeContextValue | null {
  return useContext(FocusedModeContext);
}

/** Per-tool surfaces call this on mount to request focused mode.
 *  Safe to call outside the provider (no-op).  Releases on unmount. */
export function useRequestFocusedMode(focused: boolean): void {
  const ctx = useContext(FocusedModeContext);
  useEffect(() => {
    if (!ctx) return;
    ctx.requestFocus(focused);
    return () => {
      ctx.requestFocus(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focused]);
}

/** Resolve the effective per-panel visibility — combines the canvas-
 *  side request + the user-side override.
 *
 *  Truth table:
 *    isFocused=false, override=null   → panel visible (default 3-col)
 *    isFocused=false, override=true   → panel visible (user override no-op)
 *    isFocused=false, override=false  → panel HIDDEN (user manually hid)
 *    isFocused=true,  override=null   → panel HIDDEN (focused default)
 *    isFocused=true,  override=true   → panel visible (user re-opened)
 *    isFocused=true,  override=false  → panel HIDDEN (focused + user agreed)
 */
export function resolvePanelVisibility(
  isFocused: boolean,
  override: boolean | null,
): boolean {
  if (override != null) return override;
  return !isFocused;
}
