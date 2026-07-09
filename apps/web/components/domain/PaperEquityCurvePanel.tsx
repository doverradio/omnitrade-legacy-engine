"use client";

import { useEffect, useMemo, useState } from "react";

import EquityCurveChart from "@/components/charts/EquityCurveChart";
import {
  ApiRequestError,
  getPaperEquityCurve,
  type PaperEquityCurveResponse,
} from "@/lib/api/paperAccounts";

function resolveErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load paper equity curve.";
}

function formatPercent(raw: string): string {
  const numeric = Number(raw);
  if (Number.isNaN(numeric)) {
    return "0.00%";
  }
  return `${(numeric * 100).toFixed(2)}%`;
}

function formatWhen(value: string | null | undefined): string {
  if (!value) {
    return "No updates yet";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "No updates yet";
  }
  return parsed.toLocaleString();
}

export default function PaperEquityCurvePanel() {
  const [data, setData] = useState<PaperEquityCurveResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const payload = await getPaperEquityCurve({ window_minutes: 720, interval: 15 });
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
  }, []);

  const chartData = useMemo(() => {
    return (data?.points ?? []).map((point) => ({
      time: point.timestamp,
      equity: Number(point.equity),
    }));
  }, [data]);

  const isFlat = useMemo(() => {
    if (!data || data.points.length === 0) {
      return true;
    }
    const firstEquity = data.points[0]?.equity;
    return data.points.every((point) => point.equity === firstEquity) && data.points.every((point) => point.trade_count_at_point === 0);
  }, [data]);

  return (
    <section className="rounded-lg border border-border bg-muted/60 p-4" aria-labelledby="paper-equity-curve-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="paper-equity-curve-heading" className="text-sm font-semibold uppercase tracking-wide text-foreground/85">
            Paper Equity Curve
          </h2>
          <p className="mt-1 text-xs text-foreground/70">Read-only paper/simulated equity over time from persisted paper evidence.</p>
        </div>
        <span className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-100">
          PAPER / SIMULATED
        </span>
      </div>

      {error && <p className="mt-3 text-sm text-rose-200">{error}</p>}

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">Starting balance</p>
          <p className="mt-2 text-xl font-semibold">{loading ? "Loading..." : data?.starting_balance ?? "0"}</p>
        </article>
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">Current equity</p>
          <p className="mt-2 text-xl font-semibold">{loading ? "Loading..." : data?.current_equity ?? "0"}</p>
        </article>
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">Total return</p>
          <p className="mt-2 text-xl font-semibold">{loading ? "Loading..." : data?.total_return_usd ?? "0"}</p>
          <p className="mt-1 text-xs text-foreground/70">{loading ? "" : formatPercent(data?.total_return_pct ?? "0")}</p>
        </article>
        <article className="rounded-md border border-border bg-background/50 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/70">Latest point</p>
          <p className="mt-2 text-sm text-foreground/90">{loading ? "Loading..." : formatWhen(data?.latest_point_timestamp)}</p>
        </article>
      </div>

      <div className="mt-4">
        <EquityCurveChart data={chartData} />
      </div>

      {isFlat && !loading && (
        <p className="mt-3 rounded-md border border-border bg-background/60 p-3 text-sm text-foreground/75">
          No paper equity movement yet. Showing a flat starting balance until paper trade evidence changes equity.
        </p>
      )}
    </section>
  );
}
