import { useEffect, useMemo, useState } from 'react';
import { getDashboardData } from '@/services/mockData';
import type { DashboardData } from '@/types/dashboard';

type UseDashboardDataResult = {
  data: DashboardData | null;
  isLoading: boolean;
  error: Error | null;
};

export function useDashboardData(): UseDashboardDataResult {
  const [data, setData] = useState<DashboardData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let mounted = true;

    async function loadDashboard() {
      try {
        setIsLoading(true);
        const response = await getDashboardData();
        if (mounted) {
          setData(response);
        }
      } catch (caught) {
        if (mounted) {
          setError(
            caught instanceof Error ? caught : new Error('Failed to load dashboard data'),
          );
        }
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    }

    void loadDashboard();

    return () => {
      mounted = false;
    };
  }, []);

  return useMemo(
    () => ({
      data,
      isLoading,
      error,
    }),
    [data, isLoading, error],
  );
}
