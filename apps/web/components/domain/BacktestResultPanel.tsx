import EquityCurveChart from "@/components/charts/EquityCurveChart";
import DollarAndPercent from "@/components/domain/DollarAndPercent";
import type { BacktestResult } from "@/lib/api/backtests";

type BacktestResultPanelProps = {
  backtest: BacktestResult | null;
  isPolling: boolean;
};

function asPercentLabel(value: string | number | undefined): string {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) {
    return "0.00%";
  }

  return `${(numeric * 100).toFixed(2)}%`;
}

export default function BacktestResultPanel({ backtest, isPolling }: BacktestResultPanelProps) {
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

  return (
    <section className="space-y-4 rounded-xl border border-border bg-muted/30 p-4">
      {backtest.small_account_warning ? (
        <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-4 py-3 text-sm text-amber-100">
          <p className="font-semibold">Small Account Warning</p>
          <p className="mt-1">{backtest.small_account_warning.detail}</p>
        </div>
      ) : null}

      <div className="rounded-lg border border-border bg-background/30 p-4">
        <h2 className="text-lg font-semibold">Backtest Results</h2>
        <p className="mt-1 text-xs text-foreground/70">Backtest Starting Capital: ${Number(backtest.initial_capital).toFixed(2)}</p>

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
              <p className="text-base font-medium">{asPercentLabel(metrics.max_drawdown)}</p>
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
          </div>
        ) : (
          <p className="mt-3 text-sm text-foreground/75">Metrics are not available for this backtest yet.</p>
        )}
      </div>

      <div className="rounded-lg border border-border bg-background/30 p-4">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Equity Curve</h3>
        {curvePoints.length > 0 ? (
          <div className="mt-3">
            <EquityCurveChart data={curvePoints} />
          </div>
        ) : (
          <p className="mt-2 text-sm text-foreground/70">No equity curve data available for this run.</p>
        )}
      </div>

      <div className="rounded-lg border border-border bg-background/30 p-4">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Trades</h3>
        {backtest.trades.length === 0 ? (
          <p className="mt-2 text-sm text-foreground/70">No trades were generated for this backtest.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border text-left text-foreground/70">
                  <th className="px-2 py-2">Time</th>
                  <th className="px-2 py-2">Side</th>
                  <th className="px-2 py-2">Quantity</th>
                  <th className="px-2 py-2">Price</th>
                  <th className="px-2 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {backtest.trades.map((trade, index) => (
                  <tr key={`${trade.executed_at}-${trade.side}-${index}`} className="border-b border-border/50">
                    <td className="px-2 py-2">{new Date(trade.executed_at).toLocaleString()}</td>
                    <td className="px-2 py-2 capitalize">{trade.side}</td>
                    <td className="px-2 py-2">{trade.quantity}</td>
                    <td className="px-2 py-2">${Number(trade.price).toFixed(2)}</td>
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
