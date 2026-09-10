import { useEffect, useMemo, useState } from 'react';
import type { FXPageData } from '@/types/fx';
import { fetchFXCarry, fetchFXScanner, fetchFXSpotLevel } from '@/services/fxApi';

type UseFxDataResult = {
  data: FXPageData | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
};

export function useFxData(): UseFxDataResult {
  const [data, setData] = useState<FXPageData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setIsLoading(true);
      setError(null);

      try {
        const [scanner, eurusd, carry] = await Promise.all([
          fetchFXScanner({ top_n: 10 }),
          fetchFXSpotLevel({ pair: 'EURUSD' }),
          fetchFXCarry({ tenor: '1M' }),
        ]);

        if (!cancelled) {
          setData({ scanner, eurusd, carry });
        }
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught
              : new Error('Failed to load FX data'),
          );
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const refetch = () => setTick((t) => t + 1);

  return useMemo(
    () => ({ data, isLoading, error, refetch }),
    [data, isLoading, error],
  );
}