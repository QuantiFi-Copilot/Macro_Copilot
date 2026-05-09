import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Sidebar } from '@/components/layout/Sidebar';
import { ChatDrawer } from '@/components/layout/ChatDrawer';
import { TopNav } from '@/components/ui/TopNav';
import { Dashboard } from '@/components/dashboard/Dashboard';
import { RatesPage } from '@/components/rates/RatesPage';
import { WorkspacePage } from '@/components/workspace/WorkspacePage';
import { ToolsCataloguePage } from '@/components/catalogue/ToolsCataloguePage';
import { WorkflowsCataloguePage } from '@/components/catalogue/WorkflowsCataloguePage';
import { AskPage } from '@/components/ask/AskPage';
import { BriefcasePlaceholder } from '@/components/briefcase/BriefcasePlaceholder';
import { useDashboardData } from '@/hooks/useDashboardData';

export function AppShell() {
  const { data, isLoading, error } = useDashboardData();
  const location = useLocation();

  // The Ask page takes the full width — its own internal layout owns
  // the threads rail + conversation canvas + context rail, so the
  // legacy ChatDrawer hides and the sidebar collapses out.  All other
  // routes keep the historic 3-column shell while we revamp them in
  // subsequent PRs.
  const isAskRoute = location.pathname.startsWith('/ask');
  const isBriefcaseRoute = location.pathname.startsWith('/briefcase');
  const isFullWidthRoute = isAskRoute || isBriefcaseRoute;

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center px-6">
        <div className="card max-w-md px-6 py-6">
          <p className="kicker mb-2">System</p>
          <p className="text-[15px] font-semibold text-fg-primary">Dashboard failed to load</p>
          <p className="mt-2 text-[12px] text-fg-secondary">{error.message}</p>
        </div>
      </div>
    );
  }

  // Two layout shapes:
  //   - Full-width (Ask, Briefcase): TopNav + main, no flanking panels.
  //     Ask ships its own internal three-column grid; Briefcase is a
  //     centered placeholder.
  //   - Legacy (everything else): preserved 3-column shell with sidebar
  //     and ChatDrawer.  Will collapse to single-column as remaining
  //     surfaces ship in later PRs.
  const Routed = (
    <Routes>
      <Route
        path="/"
        element={<Dashboard data={data} isLoading={isLoading} />}
      />
      <Route path="/rates" element={<RatesPage />} />

      {/* Placeholder routes — revamp ships in later PRs */}
      <Route path="/fx" element={<Dashboard data={data} isLoading={isLoading} />} />
      <Route path="/policy" element={<Dashboard data={data} isLoading={isLoading} />} />
      <Route path="/events" element={<Dashboard data={data} isLoading={isLoading} />} />
      <Route path="/workspace" element={<WorkspacePage />} />

      <Route path="/workflows" element={<WorkflowsCataloguePage />} />
      <Route path="/tools" element={<ToolsCataloguePage />} />

      {/* PR — Ask surface */}
      <Route path="/ask" element={<AskPage />} />
      <Route path="/briefcase" element={<BriefcasePlaceholder />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );

  if (isFullWidthRoute) {
    return (
      <div className="h-screen w-screen overflow-hidden">
        <main className="flex h-full min-h-0 w-full flex-col overflow-hidden">
          <TopNav />
          <div className="min-h-0 flex-1 overflow-hidden">{Routed}</div>
        </main>
      </div>
    );
  }

  return (
    <div className="h-screen w-screen overflow-hidden">
      <div
        className="grid h-full"
        style={{
          gridTemplateColumns:
            'clamp(248px, 16vw, 296px) minmax(0, 1fr) clamp(340px, 22vw, 420px)',
        }}
      >
        <Sidebar groups={data?.sidebarGroups ?? []} />

        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <TopNav />
          <div className="min-h-0 flex-1 overflow-hidden">{Routed}</div>
        </main>

        <ChatDrawer />
      </div>
    </div>
  );
}
