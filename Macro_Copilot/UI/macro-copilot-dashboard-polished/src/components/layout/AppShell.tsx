import { Sidebar } from '@/components/layout/Sidebar';
import { ChatDrawer } from '@/components/layout/ChatDrawer';
import { TopNav } from '@/components/ui/TopNav';
import { Dashboard } from '@/components/dashboard/Dashboard';
import { useDashboardData } from '@/hooks/useDashboardData';

export function AppShell() {
  const { data, isLoading, error } = useDashboardData();

  if (error) {
    return (
      <div className="flex h-screen items-center justify-center px-6">
        <div className="card max-w-md px-6 py-6">
          <p className="text-kicker kicker mb-2">System</p>
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

        <main className="flex min-w-0 flex-col">
          <TopNav tabs={data?.topNavTabs ?? ['Home', 'Rates', 'FX', 'Policy', 'Events']} />
          <Dashboard data={data} isLoading={isLoading} />
        </main>

        <ChatDrawer
          suggestions={data?.suggestions ?? []}
          messages={data?.chatMessages ?? []}
        />
      </div>
    </div>
  );
}
