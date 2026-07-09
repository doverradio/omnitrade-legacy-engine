"use client";

import { useEffect, useState } from "react";

import {
  ApiRequestError,
  getPaperTradeHistory,
  type PaperTradeHistoryItem,
  type PaperTradeHistoryResponse,
} from "@/lib/api/paperAccounts";

const PAGE_SIZE = 10;

function resolveErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load paper trade history.";
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "-";
  }
  return parsed.toLocaleString();
}

function sideClass(side: string): string {
  return side.toLowerCase() === "buy"
    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-100"
    : "border-rose-500/30 bg-rose-500/10 text-rose-100";
}

export default function PaperTradeHistoryPanel() {
  const [data, setData] = useState<PaperTradeHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const payload = await getPaperTradeHistory({ limit: PAGE_SIZE, offset });
        if (active) {
          setData(payload);
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
  }, [offset]);

  const items = data?.items ?? [];
  const hasPrev = offset > 0;
  const hasNext = Boolean(data?.has_more);

  return (
    <section className="rounded-lg border border-border bg-muted/60 p-4" aria-labelledby="paper-trade-history-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="paper-trade-history-heading" className="text-sm font-semibold uppercase tracking-wide text-foreground/85">
            Paper Trade History
          </h2>
          <p className="mt-1 text-xs text-foreground/70">Immutable chronological paper/simulated execution evidence.</p>
        </div>
        <span className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-100">
          PAPER / SIMULATED
        </span>
      </div>

      {error && <p className="mt-3 text-sm text-rose-200">{error}</p>}

      {loading ? (
        <p className="mt-4 text-sm text-foreground/70">Loading paper trade history...</p>
      ) : items.length === 0 ? (
        <p className="mt-4 rounded-md border border-border bg-background/60 p-3 text-sm text-foreground/75">
          No paper trade evidence yet.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <thead className="text-foreground/70">
              <tr>
                <th className="px-2 py-2">Time</th>
                <th className="px-2 py-2">Asset</th>
                <th className="px-2 py-2">Side</th>
                <th className="px-2 py-2">Price</th>
                <th className="px-2 py-2">Quantity</th>
                <th className="px-2 py-2">Notional</th>
                <th className="px-2 py-2">Strategy</th>
                <th className="px-2 py-2">Decision</th>
                <th className="px-2 py-2">PnL</th>
                <th className="px-2 py-2">Paper</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item: PaperTradeHistoryItem) => (
                <tr key={item.trade_id} className="border-t border-border/70 text-foreground/90">
                  <td className="px-2 py-2">{formatDate(item.executed_at)}</td>
                  <td className="px-2 py-2">{item.asset ?? "-"}</td>
                  <td className="px-2 py-2">
                    <span className={`rounded border px-1.5 py-0.5 font-medium ${sideClass(item.side)}`}>
                      {item.side.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-2 py-2">{item.execution_price}</td>
                  <td className="px-2 py-2">{item.quantity}</td>
                  <td className="px-2 py-2">{item.notional}</td>
                  <td className="px-2 py-2">{item.strategy_id ?? "-"}</td>
                  <td className="px-2 py-2">{item.decision_record_id ?? "-"}</td>
                  <td className="px-2 py-2">{item.realized_pnl ?? "-"}</td>
                  <td className="px-2 py-2">
                    <span className="rounded border border-cyan-500/40 bg-cyan-500/10 px-1.5 py-0.5 font-medium text-cyan-100">
                      PAPER
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between">
        <p className="text-xs text-foreground/70">
          Showing {items.length} of {data?.total ?? 0} trades
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded border border-border bg-background/60 px-3 py-1 text-xs disabled:opacity-50"
            onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
            disabled={!hasPrev}
          >
            Previous
          </button>
          <button
            type="button"
            className="rounded border border-border bg-background/60 px-3 py-1 text-xs disabled:opacity-50"
            onClick={() => setOffset((value) => value + PAGE_SIZE)}
            disabled={!hasNext}
          >
            Next
          </button>
        </div>
      </div>
    </section>
  );
}
