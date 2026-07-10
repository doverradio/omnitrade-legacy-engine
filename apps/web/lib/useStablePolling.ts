import { useCallback, useEffect, useRef, useState } from "react";

type PollState<T> = {
  data: T | null;
  initialLoading: boolean;
  refreshing: boolean;
  error: string | null;
  lastSuccessAt: string | null;
};

export function useStablePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  options: { intervalMs: number; enabled?: boolean },
): PollState<T> & { refreshNow: () => Promise<void> } {
  const { intervalMs, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSuccessAt, setLastSuccessAt] = useState<string | null>(null);
  const dataRef = useRef<T | null>(null);
  const inFlightRef = useRef(false);
  const generationRef = useRef(0);

  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  const run = useCallback(async () => {
    if (!enabled || inFlightRef.current) {
      return;
    }

    const controller = new AbortController();
    const requestGeneration = generationRef.current + 1;
    generationRef.current = requestGeneration;

    inFlightRef.current = true;
    const hasData = dataRef.current != null;
    setRefreshing(hasData);
    if (!hasData) {
      setInitialLoading(true);
    }

    try {
      const next = await fetcher(controller.signal);
      if (generationRef.current !== requestGeneration) {
        return;
      }
      setData(next);
      setError(null);
      setLastSuccessAt(new Date().toISOString());
    } catch (requestError) {
      if (generationRef.current !== requestGeneration) {
        return;
      }
      setError(requestError instanceof Error ? requestError.message : "Request failed.");
    } finally {
      if (generationRef.current === requestGeneration) {
        setInitialLoading(false);
        setRefreshing(false);
      }
      inFlightRef.current = false;
      controller.abort();
    }
  }, [enabled, fetcher]);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    void run();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden") {
        return;
      }
      void run();
    }, intervalMs);

    return () => {
      window.clearInterval(timer);
    };
  }, [enabled, intervalMs, run]);

  return {
    data,
    initialLoading,
    refreshing,
    error,
    lastSuccessAt,
    refreshNow: run,
  };
}
