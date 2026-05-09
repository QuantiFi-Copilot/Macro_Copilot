// ============================================================================
// AppShell — top-level layout selection and routing
// ----------------------------------------------------------------------------
// Single global TopNav at viewport top, then a body region that picks
// one of three shapes based on route:
//
//   1. FULL-WIDTH (Ask, Briefcase): main only.  Ask owns its own
//      internal three-column grid; Briefcase is centered placeholder.
//
//   2. SIDEBAR + MAIN (Monitor, Rates Agent, agent placeholders):
//      Two-column shell (sidebar + main).  No right-rail chat.
//
//   3. LEGACY THREE-COLUMN (Workspace detail, Tools catalog, Workflows
//      catalog): kept until those surfaces are redesigned.
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
import { MonitorPage } from '@/components/monitor/MonitorPage';
import { RatesAgentPage } from '@/components/agents/RatesAgentPage';
import {
  FxAgentPlaceholder,
  CreditAgentPlaceholder,
  MacroEquityPlaceholder,
  PolicyEventsPlaceholder,
  PmOrchestratorPlaceholder,
} from '@/components/agents/AgentPlaceholderPage';
import { WorkspacePage } from '@/components/workspace/WorkspacePage';
import { ToolsCataloguePage } from '@/components/catalogue/ToolsCataloguePage';
import { WorkflowsCataloguePage } from '@/components/catalogue/WorkflowsCataloguePage';
import { AskPage } from '@/components/ask/AskPage';
import { BriefcasePlaceholder } from '@/components/briefcase/BriefcasePlaceholder';

export function AppShell() {
  const { pathname } = useLocation();

  const isFullWidth =
    pathname.startsWith('/ask') || pathname.startsWith('/briefcase');
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
      <Route path="/fx" element={<FxAgentPlaceholder />} />
      <Route path="/credit" element={<CreditAgentPlaceholder />} />
      <Route path="/macro-equity" element={<MacroEquityPlaceholder />} />
      <Route path="/policy" element={<PolicyEventsPlaceholder />} />
      <Route path="/pm-orchestrator" element={<PmOrchestratorPlaceholder />} />

      {/* Existing — kept until subsequent PRs redesign them */}
      <Route path="/workspace" element={<WorkspacePage />} />
      <Route path="/workflows" element={<WorkflowsCataloguePage />} />
      <Route path="/tools" element={<ToolsCataloguePage />} />

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
          <div
            className="grid h-full"
            style={{
              gridTemplateColumns: 'clamp(220px, 14vw, 264px) minmax(0, 1fr)',
            }}
          >
            <Sidebar />
            <main className="min-h-0 min-w-0 overflow-hidden">{Routed}</main>
          </div>
        ) : (
          // Legacy three-column shell — keeps Workspace / Tools /
          // Workflows working until each ships its redesign.
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
        )}
      </div>
    </div>
  );
}
