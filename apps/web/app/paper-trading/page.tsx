"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import DollarAndPercent from "@/components/domain/DollarAndPercent";
import StartingBalanceInput from "@/components/domain/StartingBalanceInput";
import {
  ApiRequestError,
  createPaperAccount,
  getPaperAccount,
  getPaperTrades,
  resetPaperAccount,
  type PaperAccount,
  type PaperTrade,
} from "@/lib/api/paperAccounts";

type AccountFormState = {
  name: string;
  assetClass: "crypto" | "stock";
  startingBalance: string;
};

type TradeFilters = {
  strategyId: string;
  assetId: string;
  startTime: string;
  endTime: string;
};

type TimelinePoint = {
  id: string;
  label: string;
  timestamp: string;
  equity: number;
  changeUsd: number;
  changePct: number;
  notional: number;
  fee: number;
};

type DrawdownAnalytics = {
  maxDrawdownUsd: number;
  maxDrawdownPct: number;
  peakEquity: number;
  troughEquity: number;
};

type ConsistencyAnalytics = {
  stableStepRate: number;
  positiveStepRate: number;
  averageStepMovePct: number;
  largestStepDropPct: number;
};

const DEFAULT_FORM_STATE: AccountFormState = {
  name: "Family Paper Account",
  assetClass: "crypto",
  startingBalance: "25",
};

const DEFAULT_TRADE_FILTERS: TradeFilters = {
  strategyId: "",
  assetId: "",
  startTime: "",
  endTime: "",
};

function parseDecimal(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }

  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatCurrency(value: number): string {
  const sign = value >= 0 ? "" : "-";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

function formatAccountBalance(value: string): string {
  return formatCurrency(parseDecimal(value));
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown execution time";
  }

  return date.toLocaleString();
}

function toIsoOrUndefined(localDateTime: string): string | undefined {
  if (!localDateTime.trim()) {
    return undefined;
  }

  const parsed = new Date(localDateTime);
  if (Number.isNaN(parsed.getTime())) {
    return undefined;
  }

  return parsed.toISOString();
}

function normalizeTradeSide(side: string): "buy" | "sell" | "other" {
  const lower = side.trim().toLowerCase();
  if (lower === "buy") {
    return "buy";
  }
  if (lower === "sell") {
    return "sell";
  }
  return "other";
}

function sortTradesByExecutedAtDescending(trades: PaperTrade[]): PaperTrade[] {
  return [...trades].sort((a, b) => {
    return new Date(b.executed_at).getTime() - new Date(a.executed_at).getTime();
  });
}

function buildTimelinePoints(account: PaperAccount | null, trades: PaperTrade[]): TimelinePoint[] {
  if (!account) {
    return [];
  }

  const startingBalance = parseDecimal(account.starting_balance);
  const sortedAscending = [...trades].sort((a, b) => {
    return new Date(a.executed_at).getTime() - new Date(b.executed_at).getTime();
  });

  const points: TimelinePoint[] = [
    {
      id: "timeline-start",
      label: "Paper account start",
      timestamp: "Starting balance",
      equity: startingBalance,
      changeUsd: 0,
      changePct: 0,
      notional: 0,
      fee: 0,
    },
  ];

  let runningEquity = startingBalance;

  for (const trade of sortedAscending) {
    const quantity = parseDecimal(trade.quantity);
    const price = parseDecimal(trade.price);
    const fee = parseDecimal(trade.fee);
    const notional = quantity * price;
    const side = normalizeTradeSide(trade.side);

    if (side === "buy") {
      runningEquity -= notional + fee;
    } else if (side === "sell") {
      runningEquity += notional - fee;
    } else {
      runningEquity -= fee;
    }

    const changeUsd = runningEquity - startingBalance;
    points.push({
      id: trade.id,
      label: `${side.toUpperCase()} ${trade.symbol ?? trade.asset_id.slice(0, 8)}`,
      timestamp: trade.executed_at,
      equity: runningEquity,
      changeUsd,
      changePct: startingBalance > 0 ? changeUsd / startingBalance : 0,
      notional,
      fee,
    });
  }

  const currentEquity = parseDecimal(account.equity);
  const currentChangeUsd = currentEquity - startingBalance;

  points.push({
    id: "timeline-current",
    label: "Current paper equity snapshot",
    timestamp: "Now",
    equity: currentEquity,
    changeUsd: currentChangeUsd,
    changePct: startingBalance > 0 ? currentChangeUsd / startingBalance : 0,
    notional: 0,
    fee: 0,
  });

  return points;
}

function computeDrawdownAnalytics(points: TimelinePoint[]): DrawdownAnalytics {
  if (points.length === 0) {
    return {
      maxDrawdownUsd: 0,
      maxDrawdownPct: 0,
      peakEquity: 0,
      troughEquity: 0,
    };
  }

  let rollingPeak = points[0].equity;
  let peakAtWorst = rollingPeak;
  let troughAtWorst = rollingPeak;
  let maxDrawdownUsd = 0;

  for (const point of points) {
    if (point.equity > rollingPeak) {
      rollingPeak = point.equity;
    }

    const drawdownUsd = rollingPeak - point.equity;
    if (drawdownUsd > maxDrawdownUsd) {
      maxDrawdownUsd = drawdownUsd;
      peakAtWorst = rollingPeak;
      troughAtWorst = point.equity;
    }
  }

  const maxDrawdownPct = peakAtWorst > 0 ? maxDrawdownUsd / peakAtWorst : 0;
  return {
    maxDrawdownUsd,
    maxDrawdownPct,
    peakEquity: peakAtWorst,
    troughEquity: troughAtWorst,
  };
}

function computeConsistencyAnalytics(points: TimelinePoint[]): ConsistencyAnalytics {
  if (points.length < 2) {
    return {
      stableStepRate: 1,
      positiveStepRate: 0,
      averageStepMovePct: 0,
      largestStepDropPct: 0,
    };
  }

  let stableSteps = 0;
  let positiveSteps = 0;
  let totalAbsStepMovePct = 0;
  let largestStepDropPct = 0;
  let totalSteps = 0;

  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    const stepDeltaUsd = current.equity - previous.equity;
    const baseline = previous.equity > 0 ? previous.equity : 1;
    const stepMovePct = stepDeltaUsd / baseline;

    totalSteps += 1;
    if (stepDeltaUsd >= 0) {
      positiveSteps += 1;
    }
    if (Math.abs(stepMovePct) <= 0.03) {
      stableSteps += 1;
    }

    totalAbsStepMovePct += Math.abs(stepMovePct);
    if (stepMovePct < largestStepDropPct) {
      largestStepDropPct = stepMovePct;
    }
  }

  return {
    stableStepRate: totalSteps > 0 ? stableSteps / totalSteps : 1,
    positiveStepRate: totalSteps > 0 ? positiveSteps / totalSteps : 0,
    averageStepMovePct: totalSteps > 0 ? totalAbsStepMovePct / totalSteps : 0,
    largestStepDropPct: Math.abs(largestStepDropPct),
  };
}

export default function PaperTradingPage() {
  const [activeAccount, setActiveAccount] = useState<PaperAccount | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [accountIdInput, setAccountIdInput] = useState("");
  const [formState, setFormState] = useState<AccountFormState>(DEFAULT_FORM_STATE);
  const [loading, setLoading] = useState(true);
  const [loadingMessage, setLoadingMessage] = useState("Loading paper account data...");
  const [pageError, setPageError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [resetError, setResetError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isResetConfirmOpen, setIsResetConfirmOpen] = useState(false);

  const [tradeHistory, setTradeHistory] = useState<PaperTrade[]>([]);
  const [tradeFilters, setTradeFilters] = useState<TradeFilters>(DEFAULT_TRADE_FILTERS);
  const [isTradesLoading, setIsTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);

  const accountLabel = useMemo(() => {
    if (!activeAccount) {
      return "No paper account loaded";
    }

    return activeAccount.name;
  }, [activeAccount]);

  const loadAccount = useCallback(async (accountId?: string) => {
    setLoading(true);
    setPageError(null);
    setLoadingMessage(accountId ? "Loading selected paper account..." : "Loading current paper account...");

    try {
      const account = await getPaperAccount(accountId);
      setActiveAccount(account);
      setSelectedAccountId(account.id);
      setAccountIdInput(account.id);
      setResetError(null);
      return account;
    } catch (error) {
      const message = error instanceof ApiRequestError ? error.message : "Failed to load paper account.";
      setPageError(message);
      setActiveAccount(null);
      setTradeHistory([]);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTradeHistory = useCallback(async (accountId: string, filters: TradeFilters) => {
    setIsTradesLoading(true);
    setTradesError(null);

    try {
      const response = await getPaperTrades({
        account_id: accountId,
        strategy_id: filters.strategyId.trim() || undefined,
        asset_id: filters.assetId.trim() || undefined,
        start_time: toIsoOrUndefined(filters.startTime),
        end_time: toIsoOrUndefined(filters.endTime),
        limit: 100,
      });
      setTradeHistory(response.items);
    } catch (error) {
      const message = error instanceof ApiRequestError ? error.message : "Failed to load paper trades.";
      setTradesError(message);
      setTradeHistory([]);
    } finally {
      setIsTradesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAccount();
  }, [loadAccount]);

  useEffect(() => {
    if (!activeAccount?.id) {
      return;
    }

    void loadTradeHistory(activeAccount.id, DEFAULT_TRADE_FILTERS);
  }, [activeAccount?.id, loadTradeHistory]);

  const handleCreateAccount = useCallback(async () => {
    const trimmedName = formState.name.trim();
    const startingBalance = Number(formState.startingBalance);

    if (!trimmedName) {
      setFormError("Paper account name is required.");
      return;
    }

    if (!Number.isFinite(startingBalance) || startingBalance < 25) {
      setFormError("Paper Starting Balance must be at least $25.");
      return;
    }

    setIsCreating(true);
    setFormError(null);

    try {
      const createdAccount = await createPaperAccount({
        name: trimmedName,
        asset_class: formState.assetClass,
        starting_balance: formState.startingBalance,
      });

      setActiveAccount(createdAccount);
      setSelectedAccountId(createdAccount.id);
      setAccountIdInput(createdAccount.id);
      setFormState((previous) => ({
        ...previous,
        name: trimmedName,
      }));
      setPageError(null);
      setResetError(null);
    } catch (error) {
      setFormError(error instanceof ApiRequestError ? error.message : "Failed to create paper account.");
    } finally {
      setIsCreating(false);
    }
  }, [formState.assetClass, formState.name, formState.startingBalance]);

  const handleLoadSelectedAccount = useCallback(async () => {
    const accountId = accountIdInput.trim();
    await loadAccount(accountId || undefined);
  }, [accountIdInput, loadAccount]);

  const handleResetAccount = useCallback(async () => {
    if (!activeAccount) {
      setResetError("Load a paper account before attempting a reset.");
      return;
    }

    setIsResetting(true);
    setResetError(null);

    try {
      const resetAccount = await resetPaperAccount({
        account_id: activeAccount.id,
        confirm: true,
      });

      setActiveAccount({
        ...activeAccount,
        current_cash_balance: resetAccount.current_cash_balance,
      });
      setPageError(null);
      setIsResetConfirmOpen(false);
    } catch (error) {
      setResetError(error instanceof ApiRequestError ? error.message : "Failed to reset paper account.");
    } finally {
      setIsResetting(false);
    }
  }, [activeAccount]);

  const handleApplyTradeFilters = useCallback(async () => {
    if (!activeAccount?.id) {
      return;
    }

    await loadTradeHistory(activeAccount.id, tradeFilters);
  }, [activeAccount?.id, loadTradeHistory, tradeFilters]);

  const handleClearTradeFilters = useCallback(async () => {
    setTradeFilters(DEFAULT_TRADE_FILTERS);
    if (!activeAccount?.id) {
      return;
    }

    await loadTradeHistory(activeAccount.id, DEFAULT_TRADE_FILTERS);
  }, [activeAccount?.id, loadTradeHistory]);

  const displayAssetClass = activeAccount?.asset_class ?? formState.assetClass;
  const equityValue = parseDecimal(activeAccount?.equity);
  const cashValue = parseDecimal(activeAccount?.current_cash_balance);
  const positionValueRollup = Math.max(0, equityValue - cashValue);
  const positions = activeAccount?.positions ?? [];

  const startingBalanceNumber = parseDecimal(activeAccount?.starting_balance);

  const orderedTrades = useMemo(() => {
    return sortTradesByExecutedAtDescending(tradeHistory);
  }, [tradeHistory]);

  const totalFees = useMemo(() => {
    return tradeHistory.reduce((accumulator, trade) => {
      return accumulator + parseDecimal(trade.fee);
    }, 0);
  }, [tradeHistory]);

  const timelinePoints = useMemo(() => {
    return buildTimelinePoints(activeAccount, tradeHistory);
  }, [activeAccount, tradeHistory]);

  const netReturnUsd = useMemo(() => {
    if (!activeAccount) {
      return 0;
    }

    return parseDecimal(activeAccount.equity) - parseDecimal(activeAccount.starting_balance);
  }, [activeAccount]);

  const netReturnPct = useMemo(() => {
    if (!activeAccount) {
      return 0;
    }

    const startingBalance = parseDecimal(activeAccount.starting_balance);
    if (startingBalance <= 0) {
      return 0;
    }

    return netReturnUsd / startingBalance;
  }, [activeAccount, netReturnUsd]);

  const grossReturnBeforeFeesUsd = netReturnUsd + totalFees;
  const feeDragPctOfGrossReturn = grossReturnBeforeFeesUsd > 0 ? totalFees / grossReturnBeforeFeesUsd : 0;
  const feeDragPctOfStartingBalance = startingBalanceNumber > 0 ? totalFees / startingBalanceNumber : 0;
  const showSmallAccountWarning = orderedTrades.length > 0 && grossReturnBeforeFeesUsd > 0 && feeDragPctOfGrossReturn > 0.2;

  const drawdownAnalytics = useMemo(() => {
    return computeDrawdownAnalytics(timelinePoints);
  }, [timelinePoints]);

  const consistencyAnalytics = useMemo(() => {
    return computeConsistencyAnalytics(timelinePoints);
  }, [timelinePoints]);

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border bg-muted/60 p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">Phase 5 shell</p>
        <h1 className="mt-2 text-2xl font-semibold">Portfolio Intelligence + Paper Execution Foundation</h1>
        <p className="mt-2 max-w-3xl text-sm text-foreground/75">
          Paper account lifecycle, trade history, and portfolio timeline views using documented paper endpoints only,
          with explicit PAPER labeling, fee visibility, and dollar + percentage reporting where applicable.
        </p>
      </section>

      {pageError ? (
        <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-100">
          Could not load paper account data. {pageError}
        </p>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <section className="rounded-lg border border-border bg-background p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">PAPER ACCOUNT</p>
              <h2 className="mt-1 text-lg font-semibold">Select active paper account</h2>
            </div>
            <button
              type="button"
              onClick={() => void loadAccount()}
              className="rounded-md border border-border bg-muted px-3 py-2 text-sm transition hover:bg-foreground/10"
            >
              Load primary / most recent
            </button>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
            <label className="flex flex-col gap-1 text-sm text-foreground/90">
              <span>Paper account ID</span>
              <input
                value={accountIdInput}
                onChange={(event) => setAccountIdInput(event.target.value)}
                placeholder="Leave blank to load the primary/most recent account"
                className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
              />
            </label>
            <button
              type="button"
              onClick={() => void handleLoadSelectedAccount()}
              className="rounded-md border border-accent bg-accent/20 px-4 py-2 text-sm font-medium transition hover:bg-accent/30"
            >
              Load paper account
            </button>
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <article className="rounded-lg border border-border bg-muted/40 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Loaded account</p>
              <p className="mt-2 text-lg font-semibold">{loading ? loadingMessage : accountLabel}</p>
              <p className="mt-1 text-sm text-foreground/70">
                {loading
                  ? "PAPER data is loading"
                  : activeAccount
                    ? `Selected account ID: ${selectedAccountId}`
                    : "No paper account selected yet"}
              </p>
            </article>

            <article className="rounded-lg border border-border bg-muted/40 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Cash Rollup (PAPER)</p>
              <p className="mt-2 text-lg font-semibold">Paper Cash Balance</p>
              <p className="mt-1 text-sm text-foreground/80">
                {activeAccount ? formatAccountBalance(activeAccount.current_cash_balance) : "$25.00 minimum default"}
              </p>
            </article>

            <article className="rounded-lg border border-border bg-muted/40 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Position Value Rollup (PAPER)</p>
              <p className="mt-2 text-lg font-semibold">Open Position Value</p>
              <p className="mt-1 text-sm text-foreground/80">{formatCurrency(positionValueRollup)}</p>
            </article>
          </div>

          <div className="mt-6 rounded-lg border border-border bg-muted/30 p-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Paper account metadata</h3>
              <span className="rounded-full border border-border bg-background px-2 py-1 text-xs uppercase tracking-wide text-foreground/70">
                PAPER
              </span>
            </div>

            {loading ? (
              <div className="mt-4 space-y-3">
                <div className="h-4 w-1/2 animate-pulse rounded bg-foreground/10" />
                <div className="h-4 w-2/3 animate-pulse rounded bg-foreground/10" />
                <div className="h-4 w-1/3 animate-pulse rounded bg-foreground/10" />
              </div>
            ) : activeAccount ? (
              <dl className="mt-4 grid gap-4 sm:grid-cols-2">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Name</dt>
                  <dd className="mt-1 text-sm text-foreground/90">{activeAccount.name}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Account ID</dt>
                  <dd className="mt-1 break-all text-sm text-foreground/90">{activeAccount.id}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Asset class</dt>
                  <dd className="mt-1 text-sm text-foreground/90">{displayAssetClass}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Starting balance</dt>
                  <dd className="mt-1 text-sm text-foreground/90">
                    Paper Balance: {formatAccountBalance(activeAccount.starting_balance)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Current cash balance</dt>
                  <dd className="mt-1 text-sm text-foreground/90">
                    Paper Balance: {formatAccountBalance(activeAccount.current_cash_balance)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Paper equity</dt>
                  <dd className="mt-1 text-sm text-foreground/90">Paper Balance: {formatAccountBalance(activeAccount.equity)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Equity return (dollar + percentage)</dt>
                  <dd className="mt-1 text-sm text-foreground/90">
                    <DollarAndPercent usd={activeAccount.equity_return_usd} pct={activeAccount.equity_return_pct} />
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-foreground/65">Status</dt>
                  <dd className="mt-1 text-sm text-foreground/90">
                    {activeAccount.is_active ? "Active PAPER account" : "Inactive PAPER account"}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="mt-4 text-sm text-foreground/70">
                No paper account loaded. Create one below or load the primary/most recent paper account.
              </p>
            )}
          </div>

          <div className="mt-6 rounded-lg border border-border bg-muted/30 p-4">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Position rollups (PAPER)</h3>

            {loading ? (
              <p className="mt-3 text-sm text-foreground/70">Loading position rollups...</p>
            ) : positions.length === 0 ? (
              <p className="mt-3 text-sm text-foreground/70">
                No open PAPER positions. Position value rollups will appear after paper trade activity.
              </p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-foreground/65">
                      <th className="pb-2 pr-4">Symbol</th>
                      <th className="pb-2 pr-4">Quantity</th>
                      <th className="pb-2 pr-4">Avg Entry</th>
                      <th className="pb-2 pr-4">Unrealized P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((position) => (
                      <tr key={position.asset_id} className="border-t border-border/60">
                        <td className="py-2 pr-4 font-medium">{position.symbol}</td>
                        <td className="py-2 pr-4">{position.quantity}</td>
                        <td className="py-2 pr-4">{formatAccountBalance(position.avg_entry_price)}</td>
                        <td className="py-2 pr-4">
                          <DollarAndPercent usd={position.unrealized_pnl_usd} pct={position.unrealized_pnl_pct} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="mt-6 rounded-lg border border-border bg-muted/30 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Trade history (PAPER)</h3>
                <p className="mt-1 text-xs text-foreground/70">
                  Fee visibility and notional impact are shown per trade using the documented /paper/trades contract.
                </p>
              </div>
              <span className="rounded-full border border-border bg-background px-2 py-1 text-xs uppercase tracking-wide text-foreground/70">
                PAPER ONLY
              </span>
            </div>

            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              <label className="flex flex-col gap-1 text-sm text-foreground/90">
                <span>Strategy ID filter (optional)</span>
                <input
                  value={tradeFilters.strategyId}
                  onChange={(event) =>
                    setTradeFilters((previous) => ({
                      ...previous,
                      strategyId: event.target.value,
                    }))
                  }
                  className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
                />
              </label>

              <label className="flex flex-col gap-1 text-sm text-foreground/90">
                <span>Asset ID filter (optional)</span>
                <input
                  value={tradeFilters.assetId}
                  onChange={(event) =>
                    setTradeFilters((previous) => ({
                      ...previous,
                      assetId: event.target.value,
                    }))
                  }
                  className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
                />
              </label>

              <label className="flex flex-col gap-1 text-sm text-foreground/90">
                <span>Start time (optional)</span>
                <input
                  type="datetime-local"
                  value={tradeFilters.startTime}
                  onChange={(event) =>
                    setTradeFilters((previous) => ({
                      ...previous,
                      startTime: event.target.value,
                    }))
                  }
                  className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
                />
              </label>

              <label className="flex flex-col gap-1 text-sm text-foreground/90">
                <span>End time (optional)</span>
                <input
                  type="datetime-local"
                  value={tradeFilters.endTime}
                  onChange={(event) =>
                    setTradeFilters((previous) => ({
                      ...previous,
                      endTime: event.target.value,
                    }))
                  }
                  className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
                />
              </label>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={!activeAccount || isTradesLoading}
                onClick={() => void handleApplyTradeFilters()}
                className="rounded-md border border-accent bg-accent/20 px-3 py-2 text-sm font-medium transition hover:bg-accent/30 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isTradesLoading ? "Loading trades..." : "Apply trade filters"}
              </button>
              <button
                type="button"
                disabled={!activeAccount || isTradesLoading}
                onClick={() => void handleClearTradeFilters()}
                className="rounded-md border border-border bg-muted px-3 py-2 text-sm transition hover:bg-foreground/10 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Clear filters
              </button>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <article className="rounded-md border border-border bg-background/60 p-3">
                <p className="text-xs uppercase tracking-wide text-foreground/65">Trades loaded</p>
                <p className="mt-1 text-lg font-semibold">{orderedTrades.length}</p>
              </article>
              <article className="rounded-md border border-border bg-background/60 p-3">
                <p className="text-xs uppercase tracking-wide text-foreground/65">Total fees (PAPER)</p>
                <p className="mt-1 text-lg font-semibold">{formatCurrency(totalFees)}</p>
              </article>
            </div>

            {tradesError ? (
              <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
                <p>Could not load paper trade history. {tradesError}</p>
                <button
                  type="button"
                  onClick={() => {
                    if (!activeAccount?.id) {
                      return;
                    }
                    void loadTradeHistory(activeAccount.id, tradeFilters);
                  }}
                  className="mt-2 rounded-md border border-red-300/40 bg-red-500/10 px-3 py-1 text-xs transition hover:bg-red-500/20"
                >
                  Retry trade history load
                </button>
              </div>
            ) : null}

            {isTradesLoading ? (
              <div className="mt-4 space-y-2">
                <div className="h-10 animate-pulse rounded bg-foreground/10" />
                <div className="h-10 animate-pulse rounded bg-foreground/10" />
                <div className="h-10 animate-pulse rounded bg-foreground/10" />
              </div>
            ) : orderedTrades.length === 0 ? (
              <p className="mt-4 text-sm text-foreground/70">
                No trades yet for this PAPER account and filter range. Trade history will populate once paper signals execute.
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-foreground/65">
                      <th className="pb-2 pr-4">Executed at</th>
                      <th className="pb-2 pr-4">Side</th>
                      <th className="pb-2 pr-4">Asset</th>
                      <th className="pb-2 pr-4">Quantity</th>
                      <th className="pb-2 pr-4">Price</th>
                      <th className="pb-2 pr-4">Notional</th>
                      <th className="pb-2 pr-4">Fee impact</th>
                      <th className="pb-2 pr-4">Net cash impact</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orderedTrades.map((trade) => {
                      const quantity = parseDecimal(trade.quantity);
                      const price = parseDecimal(trade.price);
                      const notional = quantity * price;
                      const fee = parseDecimal(trade.fee);
                      const side = normalizeTradeSide(trade.side);
                      const netCashImpact = side === "sell" ? notional - fee : -(notional + fee);
                      const notionalPctOfStart = startingBalanceNumber > 0 ? notional / startingBalanceNumber : 0;
                      const feePctOfNotional = notional > 0 ? fee / notional : 0;
                      const netImpactPctOfStart = startingBalanceNumber > 0 ? netCashImpact / startingBalanceNumber : 0;

                      return (
                        <tr key={trade.id} className="border-t border-border/60 align-top">
                          <td className="py-2 pr-4">{formatDateTime(trade.executed_at)}</td>
                          <td className="py-2 pr-4">
                            <span className="rounded border border-border bg-background/50 px-2 py-1 text-xs uppercase">
                              {trade.side}
                            </span>
                          </td>
                          <td className="py-2 pr-4">{trade.symbol ?? trade.asset_id.slice(0, 8)}</td>
                          <td className="py-2 pr-4">{trade.quantity}</td>
                          <td className="py-2 pr-4">{formatCurrency(price)}</td>
                          <td className="py-2 pr-4">{`${formatCurrency(notional)} (${(notionalPctOfStart * 100).toFixed(2)}%)`}</td>
                          <td className="py-2 pr-4">{`${formatCurrency(fee)} (${(feePctOfNotional * 100).toFixed(2)}%)`}</td>
                          <td className="py-2 pr-4">
                            <DollarAndPercent usd={netCashImpact} pct={netImpactPctOfStart} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="mt-6 rounded-lg border border-border bg-muted/30 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Portfolio timeline (PAPER)</h3>
                <p className="mt-1 text-xs text-foreground/70">
                  Timeline points are derived from documented paper account and paper trade data without adding new execution paths.
                </p>
              </div>
              <span className="rounded-full border border-border bg-background px-2 py-1 text-xs uppercase tracking-wide text-foreground/70">
                PAPER EQUITY TIMELINE
              </span>
            </div>

            {isTradesLoading ? (
              <div className="mt-4 space-y-2">
                <div className="h-10 animate-pulse rounded bg-foreground/10" />
                <div className="h-10 animate-pulse rounded bg-foreground/10" />
                <div className="h-10 animate-pulse rounded bg-foreground/10" />
              </div>
            ) : tradesError ? (
              <p className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
                Unable to render portfolio timeline because trade history could not be loaded.
              </p>
            ) : timelinePoints.length === 0 ? (
              <p className="mt-4 text-sm text-foreground/70">
                Load a PAPER account to render portfolio timeline points.
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide text-foreground/65">
                      <th className="pb-2 pr-4">Timeline point</th>
                      <th className="pb-2 pr-4">Time</th>
                      <th className="pb-2 pr-4">Paper equity</th>
                      <th className="pb-2 pr-4">Change vs start</th>
                      <th className="pb-2 pr-4">Trade notional</th>
                      <th className="pb-2 pr-4">Fee</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...timelinePoints].reverse().map((point) => (
                      <tr key={point.id} className="border-t border-border/60 align-top">
                        <td className="py-2 pr-4 font-medium">{point.label}</td>
                        <td className="py-2 pr-4">
                          {point.timestamp === "Starting balance" || point.timestamp === "Now"
                            ? point.timestamp
                            : formatDateTime(point.timestamp)}
                        </td>
                        <td className="py-2 pr-4">{formatCurrency(point.equity)}</td>
                        <td className="py-2 pr-4">
                          <DollarAndPercent usd={point.changeUsd} pct={point.changePct} />
                        </td>
                        <td className="py-2 pr-4">{formatCurrency(point.notional)}</td>
                        <td className="py-2 pr-4">{formatCurrency(point.fee)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="mt-6 rounded-lg border border-border bg-muted/30 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">
                  Performance Analytics (PAPER)
                </h3>
                <p className="mt-1 text-xs text-foreground/70">
                  Beginner summary first, with expandable advanced details for deeper paper-validation analysis.
                </p>
              </div>
              <span className="rounded-full border border-border bg-background px-2 py-1 text-xs uppercase tracking-wide text-foreground/70">
                PORTFOLIO INTELLIGENCE
              </span>
            </div>

            {loading || isTradesLoading ? (
              <div className="mt-4 space-y-2">
                <div className="h-12 animate-pulse rounded bg-foreground/10" />
                <div className="h-12 animate-pulse rounded bg-foreground/10" />
                <div className="h-12 animate-pulse rounded bg-foreground/10" />
              </div>
            ) : !activeAccount ? (
              <p className="mt-4 text-sm text-foreground/70">
                Load a PAPER account to view portfolio performance analytics.
              </p>
            ) : (
              <>
                {tradesError ? (
                  <p className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
                    Trade-derived analytics are partially unavailable because trade history failed to load.
                  </p>
                ) : null}

                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  <article className="rounded-md border border-border bg-background/60 p-3">
                    <p className="text-xs uppercase tracking-wide text-foreground/65">Paper return</p>
                    <p className="mt-1 text-sm font-semibold">
                      <DollarAndPercent usd={netReturnUsd} pct={netReturnPct} />
                    </p>
                    <p className="mt-1 text-xs text-foreground/70">Current paper equity vs starting balance.</p>
                  </article>

                  <article className="rounded-md border border-border bg-background/60 p-3">
                    <p className="text-xs uppercase tracking-wide text-foreground/65">Max drawdown</p>
                    <p className="mt-1 text-sm font-semibold">
                      <DollarAndPercent usd={-drawdownAnalytics.maxDrawdownUsd} pct={-drawdownAnalytics.maxDrawdownPct} />
                    </p>
                    <p className="mt-1 text-xs text-foreground/70">Largest drop from a prior paper-equity peak.</p>
                  </article>

                  <article className="rounded-md border border-border bg-background/60 p-3">
                    <p className="text-xs uppercase tracking-wide text-foreground/65">Fee drag</p>
                    <p className="mt-1 text-sm font-semibold">
                      {`${formatCurrency(totalFees)} (${(feeDragPctOfGrossReturn * 100).toFixed(2)}%)`}
                    </p>
                    <p className="mt-1 text-xs text-foreground/70">Total fees and share of gross pre-fee return.</p>
                  </article>

                  <article className="rounded-md border border-border bg-background/60 p-3">
                    <p className="text-xs uppercase tracking-wide text-foreground/65">Consistency score</p>
                    <p className="mt-1 text-sm font-semibold">{`${(consistencyAnalytics.stableStepRate * 100).toFixed(2)}% stable steps`}</p>
                    <p className="mt-1 text-xs text-foreground/70">Share of timeline steps within a 3% move band.</p>
                  </article>
                </div>

                {showSmallAccountWarning ? (
                  <p className="mt-4 rounded-md border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
                    Small-account warning: Fees consumed {(feeDragPctOfGrossReturn * 100).toFixed(2)}% of gross paper gains at this balance.
                    Consider lower-fee or lower-frequency paper strategies before promotion.
                  </p>
                ) : null}

                {orderedTrades.length === 0 ? (
                  <p className="mt-4 text-sm text-foreground/70">
                    No PAPER trades yet. Return uses current account equity; drawdown, fee drag, and consistency deepen as trade history grows.
                  </p>
                ) : null}

                <details className="mt-4 rounded-md border border-border bg-background/40 p-3">
                  <summary className="cursor-pointer text-sm font-medium text-foreground/90">
                    Show advanced analytics details
                  </summary>

                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <article className="rounded-md border border-border bg-background/60 p-3 text-sm">
                      <p className="text-xs uppercase tracking-wide text-foreground/65">Drawdown context</p>
                      <p className="mt-1 text-foreground/90">
                        Peak equity: {formatCurrency(drawdownAnalytics.peakEquity)}
                      </p>
                      <p className="text-foreground/90">Trough equity: {formatCurrency(drawdownAnalytics.troughEquity)}</p>
                      <p className="text-foreground/90">
                        Drawdown depth: {formatCurrency(drawdownAnalytics.maxDrawdownUsd)} ({(drawdownAnalytics.maxDrawdownPct * 100).toFixed(2)}%)
                      </p>
                    </article>

                    <article className="rounded-md border border-border bg-background/60 p-3 text-sm">
                      <p className="text-xs uppercase tracking-wide text-foreground/65">Fee drag breakdown</p>
                      <p className="mt-1 text-foreground/90">
                        Gross return before fees: {formatCurrency(grossReturnBeforeFeesUsd)}
                      </p>
                      <p className="text-foreground/90">
                        Total fees vs paper balance: {(feeDragPctOfStartingBalance * 100).toFixed(2)}%
                      </p>
                      <p className="text-foreground/90">
                        Fee drag vs gross return: {(feeDragPctOfGrossReturn * 100).toFixed(2)}%
                      </p>
                    </article>

                    <article className="rounded-md border border-border bg-background/60 p-3 text-sm md:col-span-2">
                      <p className="text-xs uppercase tracking-wide text-foreground/65">Consistency diagnostics</p>
                      <p className="mt-1 text-foreground/90">
                        Positive steps: {(consistencyAnalytics.positiveStepRate * 100).toFixed(2)}%
                      </p>
                      <p className="text-foreground/90">
                        Average step move magnitude: {(consistencyAnalytics.averageStepMovePct * 100).toFixed(2)}%
                      </p>
                      <p className="text-foreground/90">
                        Largest single-step drop: {(consistencyAnalytics.largestStepDropPct * 100).toFixed(2)}%
                      </p>
                    </article>
                  </div>
                </details>
              </>
            )}
          </div>

          <div className="mt-6 flex flex-wrap gap-3">
            <button
              type="button"
              disabled={!activeAccount}
              onClick={() => setIsResetConfirmOpen(true)}
              className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-100 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Reset paper account
            </button>
            <button
              type="button"
              onClick={() => void loadAccount(activeAccount?.id)}
              className="rounded-md border border-border bg-muted px-4 py-2 text-sm transition hover:bg-foreground/10"
            >
              Refresh PAPER data
            </button>
          </div>

          {resetError ? (
            <p className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
              {resetError}
            </p>
          ) : null}
        </section>

        <section className="rounded-lg border border-border bg-background p-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">Create PAPER account</p>
              <h2 className="mt-1 text-lg font-semibold">New paper account</h2>
            </div>
            <span className="rounded-full border border-border bg-muted px-2 py-1 text-xs uppercase tracking-wide text-foreground/70">
              $25 minimum
            </span>
          </div>

          <div className="mt-4 space-y-4">
            <label className="flex flex-col gap-1 text-sm text-foreground/90">
              <span>Account name</span>
              <input
                value={formState.name}
                onChange={(event) => setFormState((previous) => ({ ...previous, name: event.target.value }))}
                className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
              />
            </label>

            <label className="flex flex-col gap-1 text-sm text-foreground/90">
              <span>Asset class</span>
              <select
                value={formState.assetClass}
                onChange={(event) =>
                  setFormState((previous) => ({
                    ...previous,
                    assetClass: event.target.value === "stock" ? "stock" : "crypto",
                  }))
                }
                className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm outline-none transition focus:border-accent"
              >
                <option value="crypto">crypto</option>
                <option value="stock">stock</option>
              </select>
            </label>

            <StartingBalanceInput
              id="paper-account-starting-balance"
              label="Paper Account Starting Balance"
              value={formState.startingBalance}
              onChange={(nextValue) => setFormState((previous) => ({ ...previous, startingBalance: nextValue }))}
              min={25}
            />

            <p className="text-xs text-foreground/70">
              The paper account form enforces the documented $25 Small Account Mode floor.
            </p>

            <div className="flex items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => void handleCreateAccount()}
                disabled={isCreating}
                className="rounded-md border border-accent bg-accent/20 px-4 py-2 text-sm font-medium transition hover:bg-accent/30 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isCreating ? "Creating..." : "Create paper account"}
              </button>
              <p className="text-xs uppercase tracking-wide text-foreground/60">PAPER ONLY</p>
            </div>

            {formError ? (
              <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
                {formError}
              </p>
            ) : null}
          </div>
        </section>
      </div>

      {isResetConfirmOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-lg">
            <p className="text-xs font-semibold uppercase tracking-wide text-foreground/70">Confirm reset</p>
            <h2 className="mt-2 text-lg font-semibold">Reset the active paper account?</h2>
            <p className="mt-2 text-sm text-foreground/75">
              This will reset the selected PAPER account back to its starting balance using the documented
              paper-reset contract. No live account behavior is involved.
            </p>

            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setIsResetConfirmOpen(false)}
                className="rounded-md border border-border bg-muted px-4 py-2 text-sm transition hover:bg-foreground/10"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleResetAccount()}
                disabled={isResetting}
                className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-100 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isResetting ? "Resetting..." : "Confirm reset"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
