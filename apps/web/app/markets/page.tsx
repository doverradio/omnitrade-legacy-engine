"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { UTCTimestamp } from "lightweight-charts";

import CandleChart, { type CandleChartPoint } from "@/components/charts/CandleChart";
import AssetList from "@/components/domain/AssetList";
import IntervalSelector, { type MarketInterval } from "@/components/domain/IntervalSelector";
import { ApiRequestError, getMarketCandles, type MarketAsset, type MarketCandle } from "@/lib/api/markets";

const LOOKBACK_BY_INTERVAL_MS: Record<MarketInterval, number> = {
  "1m": 2 * 60 * 60 * 1000,
  "5m": 24 * 60 * 60 * 1000,
  "15m": 7 * 24 * 60 * 60 * 1000,
  "1h": 30 * 24 * 60 * 60 * 1000,
  "1d": 366 * 24 * 60 * 60 * 1000,
};

function toChartPoints(candles: MarketCandle[]): CandleChartPoint[] {
  return candles
    .map((candle) => ({
      time: Math.floor(new Date(candle.open_time).getTime() / 1000) as UTCTimestamp,
      open: Number(candle.open),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
    }))
    .filter((point) => {
      return (
        Number.isFinite(point.time) &&
        Number.isFinite(point.open) &&
        Number.isFinite(point.high) &&
        Number.isFinite(point.low) &&
        Number.isFinite(point.close)
      );
    });
}

export default function MarketsPage() {
  const [selectedAsset, setSelectedAsset] = useState<MarketAsset | null>(null);
  const [interval, setInterval] = useState<MarketInterval>("1m");
  const [candles, setCandles] = useState<MarketCandle[]>([]);
  const [candlesLoading, setCandlesLoading] = useState(false);
  const [candlesError, setCandlesError] = useState<string | null>(null);
  const [assetListError, setAssetListError] = useState<string | null>(null);

  const fetchCandles = useCallback(async () => {
    if (!selectedAsset) {
      setCandles([]);
      setCandlesError(null);
      return;
    }

    setCandlesLoading(true);
    setCandlesError(null);

    try {
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - LOOKBACK_BY_INTERVAL_MS[interval]);

      const items = await getMarketCandles({
        assetId: selectedAsset.id,
        interval,
        startTime: startTime.toISOString(),
        endTime: endTime.toISOString(),
      });
      setCandles(items);
    } catch (error) {
      const message = error instanceof ApiRequestError ? error.message : "Failed to load candles";
      setCandlesError(message);
    } finally {
      setCandlesLoading(false);
    }
  }, [interval, selectedAsset]);

  useEffect(() => {
    void fetchCandles();
  }, [fetchCandles]);

  const chartPoints = useMemo(() => toChartPoints(candles), [candles]);
  const showNoCandlesEmptyState = Boolean(selectedAsset && !candlesLoading && !candlesError && candles.length === 0);

  return (
    <div className="flex h-full flex-col gap-4">
      <h1 className="text-2xl font-semibold">Markets</h1>

      {assetListError ? (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-100">
          Could not load assets. {assetListError}
        </div>
      ) : null}

      <div className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <AssetList
          selectedAssetId={selectedAsset?.id ?? null}
          onSelectAsset={(asset) => setSelectedAsset(asset)}
          onErrorChange={setAssetListError}
        />

        <section className="rounded-xl border border-border bg-muted/30 p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">{selectedAsset ? selectedAsset.symbol : "No asset selected"}</h2>
              <p className="text-xs text-foreground/70">{selectedAsset ? selectedAsset.exchange : "Select an asset"}</p>
            </div>

            <IntervalSelector value={interval} onChange={setInterval} />
          </div>

          <div className="relative rounded-lg border border-border bg-background/20 p-3">
            <CandleChart candles={chartPoints} />

            {candlesLoading ? (
              <div className="absolute inset-0 flex flex-col items-center justify-center rounded-lg bg-background/70 backdrop-blur-sm">
                <div className="h-7 w-7 animate-spin rounded-full border-2 border-foreground/20 border-t-accent" />
                <p className="mt-3 text-sm text-foreground/90">Loading candles...</p>
              </div>
            ) : null}

            {candlesError ? (
              <div className="absolute inset-0 flex flex-col items-center justify-center rounded-lg bg-background/80 p-4 text-center">
                <p className="text-sm text-red-200">Failed to load candles: {candlesError}</p>
                <button
                  type="button"
                  onClick={() => void fetchCandles()}
                  className="mt-3 rounded-md border border-border bg-muted px-3 py-1.5 text-xs font-medium hover:bg-foreground/10"
                >
                  Retry candles
                </button>
              </div>
            ) : null}

            {showNoCandlesEmptyState ? (
              <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-background/70 p-4 text-center text-sm text-foreground/85">
                No candle data available for this range yet
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
