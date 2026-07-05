import EquityCurveChart from "@/components/charts/EquityCurveChart";
import DollarAndPercent from "@/components/domain/DollarAndPercent";
import type { BacktestResult } from "@/lib/api/backtests";

type BacktestResultPanelProps = {
  backtest: BacktestResult | null;
  isPolling: boolean;
  strategyLabel?: string;
  assetLabel?: string;
  interval?: string;
  startTime?: string;
  endTime?: string;
};

type TimelineEntry = {
  step: string;
  event: string;
  cash: number;
  positionValue: number;
  totalEquity: number;
};

function asPercentLabel(value: string | number | undefined): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) {
    return "0.00%";
  }

  return `${(numeric * 100).toFixed(2)}%`;
}

function asCurrencyLabel(value: number): string {
  if (!Number.isFinite(value)) {
    return "$0.00";
  }

  const sign = value >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

function currencyClass(value: number): string {
  if (value > 0) {
    return "text-emerald-300";
  }
  if (value < 0) {
    return "text-red-300";
  }
  return "text-foreground";
}

function equityClass(current: number, baseline: number): string {
  if (current > baseline) {
    return "text-emerald-300";
  }
  if (current < baseline) {
    return "text-red-300";
  }
  return "text-foreground";
}

function warningClass(value: number): string {
  if (value > 0) {
    return "text-amber-300";
  }
  return "text-foreground";
}

function formatDateTimeLabel(value: string | undefined): string {
  if (!value) {
    return "Not available";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }

  return parsed.toLocaleString();
}

function getOptionalNumericMetric(metrics: Record<string, unknown>, key: string): number | null {
  const value = metrics[key];
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function buildEstimatedTimeline(
  initialCapital: number,
  endingEquity: number,
  trades: BacktestResult["trades"],
  assetLabel: string | undefined,
): TimelineEntry[] {
  const entries: TimelineEntry[] = [
    {
      step: "Start",
      event: "Initial Capital",
      cash: initialCapital,
      positionValue: 0,
      totalEquity: initialCapital,
    },
  ];

  let cash = initialCapital;
  let positionValue = 0;

  for (const trade of trades) {
    const quantity = Number(trade.quantity);
    const price = Number(trade.price);
    const tradeValue = Number.isFinite(quantity) && Number.isFinite(price) ? quantity * price : 0;
    const side = String(trade.side).toLowerCase();

    if (side === "buy") {
      cash -= tradeValue;
      positionValue += tradeValue;
      entries.push({
        step: `Buy ${assetLabel ?? "Asset"}`,
        event: "Opened position",
        cash,
        positionValue,
        totalEquity: cash + positionValue,
      });
      continue;
    }

    if (side === "sell") {
      cash += tradeValue;
      positionValue = Math.max(0, positionValue - tradeValue);
      entries.push({
        step: `Sell ${assetLabel ?? "Asset"}`,
        event: "Closed position",
        cash,
        positionValue,
        totalEquity: cash + positionValue,
      });
    }
  }

  entries.push({
    step: "Finish",
    event: "Final Equity",
    cash: endingEquity,
    positionValue: 0,
    totalEquity: endingEquity,
  });

  return entries;
}

export default function BacktestResultPanel({
  backtest,
  isPolling,
  strategyLabel,
  assetLabel,
  interval,
  startTime,
  endTime,
}: BacktestResultPanelProps) {
  if (!backtest) {
    return (
      <section className="rounded-xl border border-dashed border-border bg-muted/20 p-6 text-sm text-foreground/80">
        No backtests run yet - configure one above to get started.
      </section>
    );
  }

  if (backtest.status === "running" || backtest.status === "pending") {
    return (
      <section className="rounded-xl border border-border bg-muted/30 p-6">
        <h2 className="text-lg font-semibold">Backtest in progress</h2>
        <p className="mt-2 text-sm text-foreground/80">Running historical simulation. This panel will refresh automatically.</p>
        <p className="mt-2 text-xs text-foreground/70">Status: {backtest.status}{isPolling ? " (polling)" : ""}</p>
      </section>
    );
  }

  if (backtest.status === "failed") {
    return (
      <section className="rounded-xl border border-red-500/30 bg-red-500/10 p-6">
        <h2 className="text-lg font-semibold text-red-100">Backtest failed</h2>
        <p className="mt-2 text-sm text-red-100/90">{backtest.error_detail ?? "The backtest run failed. Reconfigure and retry."}</p>
      </section>
    );
  }

  const metrics = backtest.metrics;
  const curve = metrics?.equity_curve ?? [];
  const curvePoints = curve
    .map((point) => {
      return {
        time: point.time,
        equity: typeof point.equity === "number" ? point.equity : Number(point.equity),
      };
    })
    .filter((point) => Number.isFinite(point.equity));

  const initialCapital = Number(backtest.initial_capital);
  const totalReturnUsd = Number(metrics?.total_return_usd ?? 0);
  const totalReturnPct = Number(metrics?.total_return_pct ?? 0);

  const endingFromMetrics = metrics
    ? getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "ending_equity") ??
      getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "ending_equity_usd") ??
      getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "current_equity") ??
      getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "current_equity_usd")
    : null;
  const computedEnding = (Number.isFinite(initialCapital) ? initialCapital : 0) + (Number.isFinite(totalReturnUsd) ? totalReturnUsd : 0);
  const endingEquity = endingFromMetrics ?? computedEnding;
  const timelineEntries = buildEstimatedTimeline(
    Number.isFinite(initialCapital) ? initialCapital : 0,
    Number.isFinite(endingEquity) ? endingEquity : 0,
    backtest.trades,
    assetLabel,
  );

  const optionalMetrics = metrics ? [
    { label: "Cash Remaining", value: getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "cash_remaining") ?? getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "cash_remaining_usd"), kind: "currency" as const },
    { label: "Current/Ending Equity", value: endingFromMetrics, kind: "currency" as const },
    { label: "Fees Paid", value: getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "fees_paid") ?? getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "fees_paid_usd"), kind: "currency" as const },
    { label: "Slippage Cost", value: getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "slippage_cost") ?? getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "slippage_cost_usd"), kind: "currency" as const },
    { label: "Largest Winning Trade", value: getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "largest_winning_trade") ?? getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "largest_winning_trade_usd"), kind: "currency" as const },
    { label: "Largest Losing Trade", value: getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "largest_losing_trade") ?? getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "largest_losing_trade_usd"), kind: "currency" as const },
    { label: "Average Hold Time", value: getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "average_hold_time_minutes") ?? getOptionalNumericMetric(metrics as unknown as Record<string, unknown>, "average_hold_time_hours"), kind: "duration" as const },
  ].filter((item) => item.value !== null) : [];

  return (
    <section className="space-y-4 rounded-xl border border-border bg-muted/30 p-4">
      {backtest.small_account_warning ? (
        <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          <p className="font-semibold">Small Account Warning</p>
          <p className="mt-1">{backtest.small_account_warning.detail}</p>
        </div>
      ) : null}

      <div className="rounded-lg border border-border bg-background/30 p-4">
        <h2 className="text-lg font-semibold">Run Summary</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Status</p>
            <p className="text-base font-medium capitalize">{backtest.status}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Strategy</p>
            <p className="text-base font-medium">{strategyLabel ?? backtest.strategy_id}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Asset</p>
            <p className="text-base font-medium">{assetLabel ?? backtest.asset_id}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Interval</p>
            <p className="text-base font-medium">{interval ?? "Not available"}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm md:col-span-2 lg:col-span-2">
            <p className="text-foreground/70">Date Range</p>
            <p className="text-base font-medium">{formatDateTimeLabel(startTime)} to {formatDateTimeLabel(endTime)}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Backtest Starting Capital</p>
            <p className="text-base font-medium">${(Number.isFinite(initialCapital) ? initialCapital : 0).toFixed(2)}</p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Ending Equity</p>
            <p
              className={["text-base font-medium", equityClass(Number.isFinite(endingEquity) ? endingEquity : 0, Number.isFinite(initialCapital) ? initialCapital : 0)].join(" ")}
              data-testid="ending-equity-value"
            >
              ${(Number.isFinite(endingEquity) ? endingEquity : 0).toFixed(2)}
            </p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Net Profit / Loss</p>
            <p
              className={["text-base font-medium", currencyClass(Number.isFinite(totalReturnUsd) ? totalReturnUsd : 0)].join(" ")}
              data-testid="net-profit-value"
            >
              {asCurrencyLabel(Number.isFinite(totalReturnUsd) ? totalReturnUsd : 0)}
            </p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Total Return %</p>
            <p
              className={["text-base font-medium", currencyClass(Number.isFinite(totalReturnPct) ? totalReturnPct : 0)].join(" ")}
              data-testid="total-return-pct-value"
            >
              {asPercentLabel(Number.isFinite(totalReturnPct) ? totalReturnPct : 0)}
            </p>
          </div>
          <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
            <p className="text-foreground/70">Fee Drag</p>
            <p className={["text-base font-medium", warningClass(Number(metrics?.fee_drag_pct ?? 0))].join(" ")}>{asPercentLabel(metrics?.fee_drag_pct)}</p>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-background/30 p-4">
        <h2 className="text-lg font-semibold">Backtest Metrics</h2>

        {metrics ? (
          <div className="mt-4 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
              <p className="text-foreground/70">Total Return</p>
              <DollarAndPercent usd={metrics.total_return_usd} pct={metrics.total_return_pct} className="text-base" />
            </div>
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
              <p className="text-foreground/70">Win Rate</p>
              <p className="text-base font-medium">{asPercentLabel(metrics.win_rate)}</p>
            </div>
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
              <p className="text-foreground/70">Max Drawdown</p>
              <p className={["text-base font-medium", warningClass(Number(metrics.max_drawdown ?? 0))].join(" ")}>{asPercentLabel(metrics.max_drawdown)}</p>
            </div>
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
              <p className="text-foreground/70">Sharpe-like</p>
              <p className="text-base font-medium">{Number(metrics.sharpe_like).toFixed(2)}</p>
            </div>
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
              <p className="text-foreground/70">Average Trade</p>
              <p className="text-base font-medium">${Number(metrics.average_trade_usd).toFixed(2)}</p>
            </div>
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
              <p className="text-foreground/70">Trade Count</p>
              <p className="text-base font-medium">{metrics.trade_count}</p>
            </div>
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm">
              <p className="text-foreground/70">Fee Drag</p>
              <p className="text-base font-medium">{asPercentLabel(metrics.fee_drag_pct)}</p>
            </div>
            {optionalMetrics.map((item) => (
              <div key={item.label} className="rounded-md border border-border bg-muted/40 p-3 text-sm">
                <p className="text-foreground/70">{item.label}</p>
                <p className="text-base font-medium">
                  {item.kind === "currency" ? `$${Number(item.value).toFixed(2)}` : `${Number(item.value).toFixed(2)}${item.label === "Average Hold Time" ? "h" : ""}`}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-3 text-sm text-foreground/75">Metrics are not available for this backtest yet.</p>
        )}
      </div>

      <div className="rounded-lg border border-border bg-background/30 p-4">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Account Timeline</h3>
        <p className="mt-2 text-xs text-foreground/70">
          Estimated timeline based on available backtest trades and metrics. Exact cash and position values are not provided by the current API.
        </p>
        <div className="mt-3 overflow-x-auto" data-testid="account-timeline-scroll-wrapper">
          <table className="min-w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-foreground/70">
                <th className="px-2 py-2">Step</th>
                <th className="px-2 py-2">Event</th>
                <th className="px-2 py-2">Cash</th>
                <th className="px-2 py-2">Position Value</th>
                <th className="px-2 py-2">Total Equity</th>
              </tr>
            </thead>
            <tbody>
              {timelineEntries.map((entry, index) => (
                <tr key={`${entry.step}-${index}`} className="border-b border-border/50">
                  <td className="px-2 py-2 font-medium">{entry.step}</td>
                  <td className="px-2 py-2 text-foreground/80">{entry.event}</td>
                  <td className="px-2 py-2">${entry.cash.toFixed(2)}</td>
                  <td className="px-2 py-2">${entry.positionValue.toFixed(2)}</td>
                  <td className="px-2 py-2">${entry.totalEquity.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-background/30 p-4">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Equity Curve</h3>
        {curvePoints.length > 0 ? (
          <div className="mt-3">
            <EquityCurveChart data={curvePoints} />
          </div>
        ) : (
          <p className="mt-2 text-sm text-foreground/70">No equity curve data is available for this run yet. Metrics and trades are still available.</p>
        )}
      </div>

      <div className="rounded-lg border border-border bg-background/30 p-4">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Trades</h3>
        <p className="mt-2 text-sm text-foreground/70">
          Each BUY uses part of the backtest starting capital to open a simulated position. Each SELL closes a simulated position and returns cash to the backtest balance.
        </p>
        <p className="mt-1 text-xs text-foreground/65">
          Buy and sell rows are execution events. Trade Count may represent completed buy/sell round trips.
        </p>
        {backtest.trades.length === 0 ? (
          <p className="mt-2 text-sm text-foreground/70">No trades were generated for this backtest.</p>
        ) : (
          <div className="mt-3 overflow-x-auto" data-testid="trades-table-scroll-wrapper">
            <table className="min-w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border text-left text-foreground/70">
                  <th className="px-2 py-2">Time</th>
                  <th className="px-2 py-2">Side</th>
                  <th className="px-2 py-2">Quantity</th>
                  <th className="px-2 py-2">Price</th>
                  <th className="px-2 py-2">Estimated Trade Value</th>
                  <th className="px-2 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {backtest.trades.map((trade, index) => (
                  <tr key={`${trade.executed_at}-${trade.side}-${index}`} className="border-b border-border/50">
                    <td className="px-2 py-2">{new Date(trade.executed_at).toLocaleString()}</td>
                    <td className="px-2 py-2">
                      {String(trade.side).toLowerCase() === "buy" ? (
                        <span className="inline-flex rounded-full border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-emerald-200">
                          BUY
                        </span>
                      ) : String(trade.side).toLowerCase() === "sell" ? (
                        <span className="inline-flex rounded-full border border-red-500/40 bg-red-500/15 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-red-200">
                          SELL
                        </span>
                      ) : (
                        <span className="inline-flex rounded-full border border-border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-foreground/80">
                          {trade.side}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-2">{trade.quantity}</td>
                    <td className="px-2 py-2">${Number(trade.price).toFixed(2)}</td>
                    <td className="px-2 py-2">${(Number(trade.quantity) * Number(trade.price)).toFixed(2)}</td>
                    <td className="px-2 py-2 text-foreground/80">{trade.reason ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
