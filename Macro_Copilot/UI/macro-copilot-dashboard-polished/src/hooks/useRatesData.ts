import { useEffect, useMemo, useState } from 'react';
import type { RatesPageData } from '@/types/rates';
import {
  fetchYieldSnapshot,
  fetchCurveShapes,
  fetchScanner,
  fetchCrossMarket,
  fetchRegimes,
} from '@/services/ratesApi';

type UseRatesDataResult = {
  data: RatesPageData | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
};

export function useRatesData(): UseRatesDataResult {
  const [data, setData] = useState<RatesPageData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setIsLoading(true);
      setError(null);

      try {
        // Fire all 5 requests in parallel — no waterfall
        const [yieldSnapshot, curveShapes, scanner, crossMarket, regimes] =
          await Promise.all([
            fetchYieldSnapshot({ tenors: '2Y,5Y,10Y,30Y' }),
            fetchCurveShapes(),
            fetchScanner({ top_n: 8, min_abs_z_score: 1.5 }),
            fetchCrossMarket(),
            fetchRegimes(),
          ]);

        if (!cancelled) {
          setData({ yieldSnapshot, curveShapes, scanner, crossMarket, regimes });
        }
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught
              : new Error('Failed to load rates data'),
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void load();
    return () => { cancelled = true; };
  }, [tick]);

  const refetch = () => setTick((t) => t + 1);

  return useMemo(
    () => ({ data, isLoading, error, refetch }),
    [data, isLoading, error, refetch],
  );
}
