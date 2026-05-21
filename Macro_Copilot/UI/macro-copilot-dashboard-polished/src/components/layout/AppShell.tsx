// ============================================================================
// AppShell — top-level layout selection and routing
// ----------------------------------------------------------------------------
// Single global TopNav at viewport top, then a body region that picks
// one of three shapes based on route:
//
//   1. FULL-WIDTH (Ask, Briefcase, Library, Build/Workspace): main only.
//      Each surface owns whatever internal grid it needs.  Build's
//      ``BuildShell`` carries its own 3-column layout (sidebar + canvas
//      + copilot rail) so it lives here.
//
//   2. SIDEBAR + MAIN (Monitor, Rates Agent, agent placeholders):
//      Two-column shell (sidebar + main).  No right-rail chat.
//
//   3. LEGACY THREE-COLUMN (Workflows catalogue): kept until that
//      surface ships its redesign.
//
// IMPORTANT: TopNav is mounted ONCE at the top of the shell, not
// inside each layout shape.  An earlier version mounted TopNav inside
// the main column for sidebar layouts, which made the brand glyph
// jump ~260px (sidebar width) when navigating between sidebar routes
// and full-width routes — the page felt like "two static layouts
// stitched together".  Hoisting TopNav makes the brand a fixed anchor
// across every route, and only the body region below it reflows.
// ============================================================================

import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Sidebar } from '@/components/layout/Sidebar';
import { ChatDrawer } from '@/components/layout/ChatDrawer';
import { TopNav } from '@/components/ui/TopNav';
import { RatesDataProvider } from '@/components/monitor/RatesDataProvider';
import { FXDataProvider } from '@/components/monitor/FXDataProvider';
import { MonitorPage } from '@/components/monitor/MonitorPage';
import { RatesAgentPage } from '@/components/agents/RatesAgentPage';
import { FXPage } from '@/components/fx/FXPage';
import {
  CreditAgentPlaceholder,
  MacroEquityPlaceholder,
  PolicyEventsPlaceholder,
  PmOrchestratorPlaceholder,
} from '@/components/agents/AgentPlaceholderPage';
import { LibraryPage } from '@/components/library/LibraryPage';
import { WorkflowsCataloguePage } from '@/components/catalogue/WorkflowsCataloguePage';
import { AskPage } from '@/components/ask/AskPage';
import { BriefcasePlaceholder } from '@/components/briefcase/BriefcasePlaceholder';
// Build surface — owns both ``/workspace`` (empty / building) and
// ``/workspace/:slug`` (completed).  Carries its own 3-column grid
// internally; AppShell drops it into full-width chrome so the shell-
// level layout stays out of its way.
import { BuildShell } from '@/components/build/BuildShell';

export function AppShell() {
  const { pathname } = useLocation();

  const isBuildRoute = pathname.startsWith('/workspace');

  const isFullWidth =
    pathname.startsWith('/ask') ||
    pathname.startsWith('/briefcase') ||
    pathname.startsWith('/library') ||
    pathname.startsWith('/tools') || // legacy alias for Library
    isBuildRoute;
  const isWidgetSurface =
    pathname === '/' ||
    pathname.startsWith('/rates') ||
    pathname.startsWith('/fx') ||
    pathname.startsWith('/credit') ||
    pathname.startsWith('/macro-equity') ||
    pathname.startsWith('/policy') ||
    pathname.startsWith('/pm-orchestrator');

  const Routed = (
    <Routes>
      {/* Widget surfaces — Monitor + agent pages */}
      <Route path="/" element={<MonitorPage />} />
      <Route path="/rates" element={<RatesAgentPage />} />
      <Route path="/fx" element={<FXPage />} />
      <Route path="/credit" element={<CreditAgentPlaceholder />} />
      <Route path="/macro-equity" element={<MacroEquityPlaceholder />} />
      <Route path="/policy" element={<PolicyEventsPlaceholder />} />
      <Route path="/pm-orchestrator" element={<PmOrchestratorPlaceholder />} />

      {/* Build / Workspace — single shell for both empty + slug-bound
          states.  ``BuildShell`` reads the ``:slug`` param itself and
          swaps the canvas between BuildEmptyState / BuildBuilding /
          BuildCompleted accordingly. */}
      <Route path="/workspace" element={<BuildShell />} />
      <Route path="/workspace/:slug" element={<BuildShell />} />
      <Route path="/workflows" element={<WorkflowsCataloguePage />} />

      {/* Library — new full-width catalogue surface.
          /tools is the legacy alias from the old top-nav setup;
          redirect to /library so any saved bookmarks keep working. */}
      <Route path="/library" element={<LibraryPage />} />
      <Route path="/tools" element={<Navigate to="/library" replace />} />

      {/* Ask + Briefcase (full-width surfaces) */}
      <Route path="/ask" element={<AskPage />} />
      <Route path="/briefcase" element={<BriefcasePlaceholder />} />

      <Route path="/events" element={<Navigate to="/policy" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      {/* Global TopNav — single source of truth for the top chrome.
          The brand lives at the same x-coordinate on every route. */}
      <TopNav />

      {/* Body region — changes shape per route, but TopNav stays
          rooted at the top. */}
      <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
        {isFullWidth ? (
          <main className="h-full overflow-hidden">{Routed}</main>
        ) : isWidgetSurface ? (
          // RatesDataProvider lifted to the layout level so the
          // Sidebar's "Today" panel + status ribbon can read live
          // rates state (data freshness, scanner count, curve
          // universe size) without a separate fetch.  Monitor and
          // Rates Agent pages used to mount their own providers
          // internally; with the provider here, both pages share a
          // single fetch + the data persists across navigation
          // between widget surfaces.  Agent placeholder routes
          // (FX, Credit, etc.) don't read the data themselves but
          // the provider stays mounted so the sidebar's Today panel
          // works on every widget route.
          <RatesDataProvider>
            <FXDataProvider>
              <div
                className="grid h-full"
                style={{
                  gridTemplateColumns: 'clamp(220px, 14vw, 264px) minmax(0, 1fr)',
                }}
              >
                <Sidebar />
                <main className="min-h-0 min-w-0 overflow-hidden">{Routed}</main>
              </div>
            </FXDataProvider>
          </RatesDataProvider>
        ) : (
          // Legacy three-column shell — kept for ``/workflows`` until
          // that surface ships its redesign.
          //
          // Wrapped in RatesDataProvider because the Sidebar (rendered
          // here too) reads from the rates context for its scope
          // subtexts + Today panel + status ribbon.  Without the
          // wrapper the sidebar would run with `useOptionalRatesData
          // Context` returning null and degrade to neutral state —
          // which is correct, but mounting the provider here means
          // the sidebar shows real data even on legacy routes.  The
          // legacy pages themselves don't read from it; the cost is
          // a single rates-data fetch per session, which is amortized
          // across navigation.
          <RatesDataProvider>
            <FXDataProvider>
              <div
                className="grid h-full"
                style={{
                  gridTemplateColumns:
                    'clamp(220px, 14vw, 264px) minmax(0, 1fr) clamp(340px, 22vw, 420px)',
                }}
              >
                <Sidebar />
                <main className="min-h-0 min-w-0 overflow-hidden">{Routed}</main>
                <ChatDrawer />
              </div>
            </FXDataProvider>
          </RatesDataProvider>
        )}
      </div>
    </div>
  );
}
