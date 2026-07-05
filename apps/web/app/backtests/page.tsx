"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import BacktestConfigForm, { type BacktestFormValues } from "@/components/domain/BacktestConfigForm";
import BacktestResultPanel from "@/components/domain/BacktestResultPanel";
import {
  ApiRequestError,
  getBacktest,
  getBacktests,
  runBacktest,
  type BacktestListItem,
  type BacktestResult,
} from "@/lib/api/backtests";
import { getMarketsAssets, type MarketAsset } from "@/lib/api/markets";
import { getParameterSets, type ParameterSetItem } from "@/lib/api/parameterSets";
import { getStrategies, type StrategyItem } from "@/lib/api/strategies";

function toDateTimeLocalValue(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");

  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function buildInitialValues(): BacktestFormValues {
  const now = new Date();
  const oneMonthAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);

  return {
    strategyId: "",
    parameterSetId: "",
    assetId: "",
    interval: "1h",
    startDateTime: toDateTimeLocalValue(oneMonthAgo),
    endDateTime: toDateTimeLocalValue(now),
    startingBalance: "25",
    feeBps: "10",
    slippageBps: "5",
  };
}

function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiRequestError ? error.message : fallback;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown date";
  }

  return date.toLocaleDateString();
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "Unavailable from /backtests response";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unavailable from /backtests response";
  }

  return date.toLocaleString();
}

function formatReturnLine(item: BacktestListItem): string {
  if (!item.metrics) {
    return "Return: n/a";
  }

  const returnUsd = Number(item.metrics.total_return_usd);
  const returnPct = Number(item.metrics.total_return_pct);
  if (!Number.isFinite(returnUsd) || !Number.isFinite(returnPct)) {
    return "Return: n/a";
  }

  const usdSign = returnUsd >= 0 ? "+" : "";
  const pctSign = returnPct >= 0 ? "+" : "";
  return `Return: ${usdSign}$${returnUsd.toFixed(2)} (${pctSign}${(returnPct * 100).toFixed(2)}%)`;
}

export default function BacktestsPage() {
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [parameterSets, setParameterSets] = useState<ParameterSetItem[]>([]);
  const [assets, setAssets] = useState<MarketAsset[]>([]);
  const [history, setHistory] = useState<BacktestListItem[]>([]);
  const [activeBacktest, setActiveBacktest] = useState<BacktestResult | null>(null);
  const [activeBacktestId, setActiveBacktestId] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<BacktestFormValues>(buildInitialValues);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadBacktest = useCallback(async (backtestId: string) => {
    const detail = await getBacktest(backtestId);
    setActiveBacktest(detail);
    return detail;
  }, []);

  const loadHistory = useCallback(async () => {
    const items = await getBacktests();
    setHistory(items);
    return items;
  }, []);

  const loadBootstrapData = useCallback(async () => {
    setIsBootstrapping(true);
    setMetadataError(null);

    try {
      const [strategyItems, parameterSetItems, assetItems, historyItems] = await Promise.all([
        getStrategies(),
        getParameterSets(),
        getMarketsAssets({ isActive: true }),
        getBacktests(),
      ]);

      setStrategies(strategyItems);
      setParameterSets(parameterSetItems);
      setAssets(assetItems);
      setHistory(historyItems);

      setFormValues((previous) => {
        const strategyId = previous.strategyId || strategyItems[0]?.id || "";
        const parameterSetId =
          previous.parameterSetId ||
          parameterSetItems.find((parameterSet) => parameterSet.strategy_id === strategyId)?.id ||
          parameterSetItems[0]?.id ||
          "";
        const assetId = previous.assetId || assetItems[0]?.id || "";

        return {
          ...previous,
          strategyId,
          parameterSetId,
          assetId,
        };
      });

      if (historyItems.length > 0) {
        const mostRecent = historyItems[0];
        setActiveBacktestId(mostRecent.id);
        const detail = await getBacktest(mostRecent.id);
        setActiveBacktest(detail);
      }
    } catch (error) {
      setMetadataError(getErrorMessage(error, "Failed to load backtest configuration data."));
    } finally {
      setIsBootstrapping(false);
    }
  }, []);

  useEffect(() => {
    void loadBootstrapData();
  }, [loadBootstrapData]);

  useEffect(() => {
    if (!activeBacktestId || !activeBacktest || activeBacktest.status !== "running") {
      return;
    }

    const polling = window.setInterval(() => {
      void loadBacktest(activeBacktestId);
    }, 2000);

    return () => {
      window.clearInterval(polling);
    };
  }, [activeBacktest, activeBacktestId, loadBacktest]);

  const onChangeField = useCallback(<K extends keyof BacktestFormValues>(key: K, value: BacktestFormValues[K]) => {
    setFormValues((previous) => {
      if (key !== "strategyId") {
        return {
          ...previous,
          [key]: value,
        };
      }

      const nextStrategyId = String(value);
      const firstMatchingParameterSet = parameterSets.find((parameterSet) => {
        return parameterSet.strategy_id === nextStrategyId;
      });

      return {
        ...previous,
        strategyId: nextStrategyId,
        parameterSetId: firstMatchingParameterSet?.id ?? "",
      };
    });
    setFormError(null);
  }, [parameterSets]);

  const validateForm = useCallback((): string | null => {
    if (!formValues.strategyId || !formValues.parameterSetId || !formValues.assetId) {
      return "Select a strategy, parameter set, and asset before running.";
    }

    const startingBalance = Number(formValues.startingBalance);
    if (!Number.isFinite(startingBalance) || startingBalance < 25) {
      return "Backtest Starting Capital must be at least $25.";
    }

    const feeBps = Number(formValues.feeBps);
    const slippageBps = Number(formValues.slippageBps);
    if (!Number.isFinite(feeBps) || feeBps < 0 || !Number.isFinite(slippageBps) || slippageBps < 0) {
      return "Fee and slippage must be zero or greater.";
    }

    const start = new Date(formValues.startDateTime);
    const end = new Date(formValues.endDateTime);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start >= end) {
      return "Start time must be before end time.";
    }

    return null;
  }, [formValues]);

  const handleRunBacktest = useCallback(async () => {
    const validationMessage = validateForm();
    if (validationMessage) {
      setFormError(validationMessage);
      return;
    }

    setIsSubmitting(true);
    setFormError(null);

    try {
      const accepted = await runBacktest({
        strategy_id: formValues.strategyId,
        parameter_set_id: formValues.parameterSetId,
        asset_id: formValues.assetId,
        interval: formValues.interval,
        start_time: new Date(formValues.startDateTime).toISOString(),
        end_time: new Date(formValues.endDateTime).toISOString(),
        initial_capital: formValues.startingBalance,
        fee_bps: formValues.feeBps,
        slippage_bps: formValues.slippageBps,
      });

      setActiveBacktestId(accepted.backtest_id);
      setActiveBacktest({
        id: accepted.backtest_id,
        status: accepted.status,
        strategy_id: formValues.strategyId,
        parameter_set_id: formValues.parameterSetId,
        asset_id: formValues.assetId,
        initial_capital: formValues.startingBalance,
        metrics: null,
        small_account_warning: null,
        trades: [],
      });

      await loadBacktest(accepted.backtest_id);
      await loadHistory();
    } catch (error) {
      setFormError(getErrorMessage(error, "Failed to run backtest."));
    } finally {
      setIsSubmitting(false);
    }
  }, [formValues, loadBacktest, loadHistory, validateForm]);

  const isPolling = activeBacktest?.status === "running";
  const selectedComparisonIds = useMemo(() => {
    return history.slice(0, 2).map((item) => item.id);
  }, [history]);

  const assetSymbolById = useMemo(() => {
    const mapping = new Map<string, string>();
    for (const asset of assets) {
      mapping.set(asset.id, asset.symbol);
    }
    return mapping;
  }, [assets]);

  const strategyLabelById = useMemo(() => {
    const mapping = new Map<string, string>();
    for (const strategy of strategies) {
      mapping.set(strategy.id, strategy.name || strategy.slug);
    }
    return mapping;
  }, [strategies]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Backtests</h1>
        <p className="mt-1 text-sm text-foreground/70">Run historical simulations and review metrics with Small Account Mode visibility.</p>
      </div>

      {metadataError ? (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-100">
          {metadataError}
        </div>
      ) : null}

      <BacktestConfigForm
        values={formValues}
        strategies={strategies}
        parameterSets={parameterSets}
        assets={assets}
        disabled={isBootstrapping || isSubmitting}
        errorMessage={formError}
        onChange={onChangeField}
        onSubmit={handleRunBacktest}
      />

      <div className="grid gap-4 xl:grid-cols-[2fr_minmax(0,1fr)]">
        <BacktestResultPanel backtest={activeBacktest} isPolling={isPolling} />

        <aside className="space-y-4">
          <section className="rounded-xl border border-border bg-muted/30 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Backtest History</h2>
            {history.length === 0 ? (
              <p className="mt-2 text-sm text-foreground/70">No historical runs yet.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {history.map((item) => {
                  const isSelected = activeBacktestId === item.id;
                  const assetLabel = assetSymbolById.get(item.asset_id) ?? `Asset ID: ${item.asset_id.slice(0, 8)}...`;
                  const strategyLabel = strategyLabelById.get(item.strategy_id) ?? `Strategy ID: ${item.strategy_id.slice(0, 8)}...`;
                  const createdAt = formatDateTime((item as BacktestListItem & { created_at?: string }).created_at);
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setActiveBacktestId(item.id);
                          void loadBacktest(item.id);
                        }}
                        className={[
                          "w-full rounded-md border px-3 py-2 text-left text-sm",
                          isSelected ? "border-accent bg-accent/20" : "border-border bg-background/20 hover:bg-foreground/10",
                        ].join(" ")}
                      >
                        <p className="font-medium">{assetLabel} · {strategyLabel} · {item.interval}</p>
                        <p className="text-xs text-foreground/70">{formatDate(item.start_time)} → {formatDate(item.end_time)}</p>
                        <p className="mt-0.5 text-xs text-foreground/70">Created: {createdAt}</p>
                        <p className="mt-1 text-xs text-foreground/75">
                          {item.status.charAt(0).toUpperCase() + item.status.slice(1)} · Starting Capital: ${Number(item.initial_capital).toFixed(2)} · {formatReturnLine(item)}
                        </p>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="rounded-xl border border-dashed border-border bg-muted/20 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Comparison</h2>
            <p className="mt-2 text-sm text-foreground/70">
              Comparison mode scaffold is ready. Select two runs to compare in a future prompt.
            </p>
            <p className="mt-1 text-xs text-foreground/60">Prepared IDs: {selectedComparisonIds.join(", ") || "none"}</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
