// ============================================================================
// AppShell — top-level layout selection and routing
// ----------------------------------------------------------------------------
// Three layout shapes, picked by route:
//
//   1. FULL-WIDTH (Ask, Briefcase): TopNav + main, no flanking panels.
//      Ask owns its own internal three-column grid; Briefcase is
//      centered placeholder.
//
//   2. SIDEBAR + MAIN (Monitor, Rates Agent, agent placeholders):
//      Two-column shell with the new sidebar (Workspace + Agents) and
//      a wide main column.  No right-rail chat.  This is the new
//      default for widget surfaces.
//
//   3. LEGACY THREE-COLUMN (Workspace detail, Tools catalog, Workflows
//      catalog): keeps the old Sidebar + Main + ChatDrawer shape so
//      these pre-revamp surfaces don't break while their redesigns
//      ship in later PRs.
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

  // Layout selector — kept declarative to make the shape obvious.
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
      {/* New widget surfaces — Monitor + agent pages */}
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

      {/* Legacy / events fallback — until /events surface lands */}
      <Route path="/events" element={<Navigate to="/policy" replace />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );

  // ---- Layout 1: full-width (Ask + Briefcase) ----
  if (isFullWidth) {
    return (
      <div className="h-screen w-screen overflow-hidden">
        <main className="flex h-full min-h-0 w-full flex-col overflow-hidden">
          <TopNav />
          <div className="min-h-0 flex-1 overflow-hidden">{Routed}</div>
        </main>
      </div>
    );
  }

  // ---- Layout 2: sidebar + main (Monitor + agent surfaces) ----
  if (isWidgetSurface) {
    return (
      <div className="h-screen w-screen overflow-hidden">
        <div
          className="grid h-full"
          style={{
            gridTemplateColumns: 'clamp(220px, 14vw, 264px) minmax(0, 1fr)',
          }}
        >
          <Sidebar />
          <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
            <TopNav />
            <div className="min-h-0 flex-1 overflow-hidden">{Routed}</div>
          </main>
        </div>
      </div>
    );
  }

  // ---- Layout 3: legacy three-column (Workspace / Tools / Workflows) ----
  return (
    <div className="h-screen w-screen overflow-hidden">
      <div
        className="grid h-full"
        style={{
          gridTemplateColumns:
            'clamp(220px, 14vw, 264px) minmax(0, 1fr) clamp(340px, 22vw, 420px)',
        }}
      >
        <Sidebar />
        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <TopNav />
          <div className="min-h-0 flex-1 overflow-hidden">{Routed}</div>
        </main>
        <ChatDrawer />
      </div>
    </div>
  );
}
