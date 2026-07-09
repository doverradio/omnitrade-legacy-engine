"use client";

import { useEffect, useState } from "react";

import {
  ApiRequestError,
  getPaperPerformanceSummary,
  type PaperPerformanceSummary,
} from "@/lib/api/paperAccounts";

function resolveErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load paper performance summary.";
}

function toPercent(value: string): string {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return "0.00%";
  }
  return `${(numeric * 100).toFixed(2)}%`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "No trades yet";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "No trades yet";
  }
  return parsed.toLocaleString();
}

export default function PaperPerformancePanel() {
  const [summary, setSummary] = useState<PaperPerformanceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const payload = await getPaperPerformanceSummary();
        if (active) {
          setSummary(payload);
        }
      } catch (requestError) {
        if (active) {
          setError(resolveErrorMessage(requestError));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      active = false;
    };
  }, []);

  const latestTrade = summary?.latest_trade ?? null;

  return (
    <section className="rounded-lg border border-border bg-muted/60 p-4" aria-labelledby="paper-performance-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="paper-performance-heading" className="text-sm font-semibold uppercase tracking-wide text-foreground/85">
            Paper Performance Summary
          </h2>
          <p className="mt-1 text-xs text-foreground/70">Read-only paper/simulated performance evidence. Not live trading.</p>
        </div>
        <span className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-100">
          PAPER / SIMULATED
        </span>
      </div>

      {error && <p className="mt-3 text-sm text-rose-200">{error}</p>}

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">Equity</p>
          <p className="mt-2 text-xl font-semibold">{loading ? "Loading..." : summary?.equity ?? "0"}</p>
        </article>
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">Total return</p>
          <p className="mt-2 text-xl font-semibold">{loading ? "Loading..." : summary?.total_return_usd ?? "0"}</p>
          <p className="mt-1 text-xs text-foreground/70">{loading ? "" : toPercent(summary?.total_return_pct ?? "0")}</p>
        </article>
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">Trade count</p>
          <p className="mt-2 text-xl font-semibold">{loading ? "Loading..." : summary?.trade_count ?? 0}</p>
        </article>
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">Win rate</p>
          <p className="mt-2 text-xl font-semibold">{loading ? "Loading..." : toPercent(summary?.win_rate ?? "0")}</p>
        </article>
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">PnL</p>
          <p className="mt-2 text-sm text-foreground/90">Realized: {loading ? "..." : summary?.realized_pnl ?? "0"}</p>
          <p className="mt-1 text-sm text-foreground/90">Unrealized: {loading ? "..." : summary?.unrealized_pnl ?? "0"}</p>
        </article>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        <section className="rounded-md border border-border bg-background/50 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/80">Latest trade</h3>
          {latestTrade ? (
            <div className="mt-2 text-sm text-foreground/90">
              <p>{latestTrade.symbol ?? latestTrade.asset_id}</p>
              <p className="mt-1">{latestTrade.side.toUpperCase()} {latestTrade.quantity} @ {latestTrade.price}</p>
              <p className="mt-1">{formatDate(latestTrade.executed_at)}</p>
            </div>
          ) : (
            <p className="mt-2 text-sm text-foreground/75">No paper trades yet.</p>
          )}
        </section>

        <section className="rounded-md border border-border bg-background/50 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/80">Open positions</h3>
          {summary?.positions?.length ? (
            <ul className="mt-2 space-y-1 text-sm text-foreground/90">
              {summary.positions.map((position) => (
                <li key={`${position.asset_id}-${position.symbol}`}>
                  {position.symbol}: {position.quantity} (uPnL {position.unrealized_pnl_usd})
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-foreground/75">No open positions.</p>
          )}
        </section>

        <section className="rounded-md border border-border bg-background/50 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/80">By asset / strategy</h3>
          <p className="mt-2 text-xs text-foreground/75">Assets: {summary?.by_asset?.length ?? 0} | Strategies: {summary?.by_strategy?.length ?? 0}</p>
          <p className="mt-2 text-xs text-foreground/75">Next dashboard focus: stability of win rate and repeatable paper outcomes.</p>
        </section>
      </div>
    </section>
  );
}
