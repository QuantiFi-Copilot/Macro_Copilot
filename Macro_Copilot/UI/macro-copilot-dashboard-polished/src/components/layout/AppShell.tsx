import { Routes, Route, Navigate } from 'react-router-dom';
import { Sidebar } from '@/components/layout/Sidebar';
import { ChatDrawer } from '@/components/layout/ChatDrawer';
import { TopNav } from '@/components/ui/TopNav';
import { Dashboard } from '@/components/dashboard/Dashboard';
import { RatesPage } from '@/components/rates/RatesPage';
import { WorkspacePage } from '@/components/workspace/WorkspacePage';
import { useDashboardData } from '@/hooks/useDashboardData';

const NAV_TABS = [
  { label: 'Home', path: '/' },
  { label: 'Rates', path: '/rates' },
  { label: 'FX', path: '/fx' },
  { label: 'Policy', path: '/policy' },
  { label: 'Events', path: '/events' },
  { label: 'Workspace', path: '/workspace' },
];

export function AppShell() {
  const { data, isLoading, error } = useDashboardData();

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
          <TopNav tabs={NAV_TABS} />

          <div className="min-h-0 flex-1 overflow-hidden">
            <Routes>
              <Route
                path="/"
                element={<Dashboard data={data} isLoading={isLoading} />}
              />
              <Route path="/rates" element={<RatesPage />} />

              {/* Placeholder routes */}
              <Route path="/fx" element={<Dashboard data={data} isLoading={isLoading} />} />
              <Route path="/policy" element={<Dashboard data={data} isLoading={isLoading} />} />
              <Route path="/events" element={<Dashboard data={data} isLoading={isLoading} />} />
              <Route path="/workspace" element={<WorkspacePage />} />

              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>

        {/* ChatDrawer manages its own state via useCopilot hook */}
        <ChatDrawer />
      </div>
    </div>
  );
}
