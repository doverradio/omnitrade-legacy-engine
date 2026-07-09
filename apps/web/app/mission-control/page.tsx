"use client";

import { useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  getOperationsStatus,
  type OperationalHealthIndicator,
  type OperationalStatus,
} from "@/lib/api/arena";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

function badgeClass(state: string): string {
  if (state === "green") {
    return "border-emerald-500/40 bg-emerald-500/15 text-emerald-200";
  }
  if (state === "yellow") {
    return "border-amber-500/40 bg-amber-500/15 text-amber-200";
  }
  return "border-rose-500/40 bg-rose-500/15 text-rose-200";
}

function formatCurrency(value: string): string {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(numeric);
}

function formatRelativeCountdown(expectedEnd: string): string {
  const end = new Date(expectedEnd);
  if (Number.isNaN(end.getTime())) {
    return "Not available";
  }

  const diffMs = end.getTime() - Date.now();
  if (diffMs <= 0) {
    return "Completed";
  }

  const total = Math.floor(diffMs / 1000);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${days}d ${hours}h ${minutes}m`;
}

function HealthChip({ label, indicator }: { label: string; indicator: OperationalHealthIndicator }) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3" role="listitem">
      <p className="text-xs uppercase tracking-wide text-foreground/70">{label}</p>
      <div className="mt-2 flex items-center gap-2">
        <span className={`rounded-full border px-2 py-1 text-xs font-medium ${badgeClass(indicator.state)}`}>
          {indicator.state.toUpperCase()}
        </span>
        <span className="text-sm text-foreground/80">{indicator.detail}</span>
      </div>
    </div>
  );
}

export default function MissionControlPage() {
  const [data, setData] = useState<OperationalStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);

      try {
        const payload = await getOperationsStatus();
        if (active) {
          setData(payload);
        }
      } catch (requestError) {
        if (active) {
          setError(errorMessage(requestError, "Failed to load mission control status."));
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 15000);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const countdown = useMemo(() => (data ? formatRelativeCountdown(data.run_status.expected_end) : "Not available"), [data]);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Mission Control</h1>
        <p className="max-w-3xl text-sm text-foreground/75">
          Autonomous 72-hour operational heartbeat and research observability dashboard.
        </p>
      </header>

      {error ? (
        <section className="rounded-md border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          API unavailable: {error}
        </section>
      ) : null}

      {loading ? (
        <section className="rounded-md border border-border bg-muted/30 p-3 text-sm text-foreground/80">Loading mission control...</section>
      ) : null}

      {!loading && !error && !data ? (
        <section className="rounded-md border border-border bg-muted/30 p-3 text-sm text-foreground/80">No mission data available.</section>
      ) : null}

      {data ? (
        <>
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <article className="rounded-lg border border-border bg-muted/30 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">System Health</p>
              <p className="mt-2 text-xl font-semibold">{data.overall_health.toUpperCase()}</p>
              <p className="mt-1 text-xs text-foreground/70">Phase: {data.run_status.current_phase}</p>
            </article>
            <article className="rounded-lg border border-border bg-muted/30 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">72-Hour Countdown</p>
              <p className="mt-2 text-xl font-semibold">{countdown}</p>
              <p className="mt-1 text-xs text-foreground/70">Uptime: {data.run_status.uptime}</p>
            </article>
            <article className="rounded-lg border border-border bg-muted/30 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Current Campaign</p>
              <p className="mt-2 text-base font-semibold">{data.research_status.current_campaign ?? "None"}</p>
              <p className="mt-1 text-xs text-foreground/70">Status: {data.research_status.campaign_status}</p>
            </article>
            <article className="rounded-lg border border-border bg-muted/30 p-4">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Current Champion</p>
              <p className="mt-2 text-base font-semibold">{data.research_status.current_champion ?? "None"}</p>
              <p className="mt-1 text-xs text-foreground/70">Paper equity: {formatCurrency(data.monitoring.paper_equity)}</p>
            </article>
          </section>

          <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" role="list" aria-label="System health indicators">
            <HealthChip label="API" indicator={data.system_health.api} />
            <HealthChip label="Orchestrator" indicator={data.system_health.orchestrator} />
            <HealthChip label="Database" indicator={data.system_health.database} />
            <HealthChip label="Research Agent" indicator={data.system_health.research_agent} />
          </section>

          <section className="rounded-lg border border-border bg-muted/30 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Research Status</h2>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="rounded-md border border-border bg-background/40 p-3">
                <p className="text-xs text-foreground/70">Signals Today</p>
                <p className="mt-1 text-lg font-semibold">{data.monitoring.signals_today}</p>
              </div>
              <div className="rounded-md border border-border bg-background/40 p-3">
                <p className="text-xs text-foreground/70">Trades Today</p>
                <p className="mt-1 text-lg font-semibold">{data.monitoring.trades_today}</p>
              </div>
              <div className="rounded-md border border-border bg-background/40 p-3">
                <p className="text-xs text-foreground/70">Research Candidates</p>
                <p className="mt-1 text-lg font-semibold">{data.monitoring.candidate_count}</p>
              </div>
              <div className="rounded-md border border-border bg-background/40 p-3">
                <p className="text-xs text-foreground/70">Evolution Progress</p>
                <p className="mt-1 text-lg font-semibold">{data.monitoring.evolution_count}</p>
              </div>
              <div className="rounded-md border border-border bg-background/40 p-3">
                <p className="text-xs text-foreground/70">Laboratory Runs</p>
                <p className="mt-1 text-lg font-semibold">{data.monitoring.laboratory_runs}</p>
              </div>
              <div className="rounded-md border border-border bg-background/40 p-3">
                <p className="text-xs text-foreground/70">Research Memory Growth</p>
                <p className="mt-1 text-lg font-semibold">{data.monitoring.research_memory_growth}</p>
              </div>
            </div>
          </section>

          <section className="rounded-lg border border-border bg-muted/30 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Monitoring</h2>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-md border border-border bg-background/40 p-3"><p className="text-xs text-foreground/70">Candles Processed</p><p className="mt-1 text-lg font-semibold">{data.monitoring.candles_processed}</p></div>
              <div className="rounded-md border border-border bg-background/40 p-3"><p className="text-xs text-foreground/70">Signals Generated</p><p className="mt-1 text-lg font-semibold">{data.monitoring.signals_generated}</p></div>
              <div className="rounded-md border border-border bg-background/40 p-3"><p className="text-xs text-foreground/70">Paper Trades Executed</p><p className="mt-1 text-lg font-semibold">{data.monitoring.paper_trades_executed}</p></div>
              <div className="rounded-md border border-border bg-background/40 p-3"><p className="text-xs text-foreground/70">Decision Records Created</p><p className="mt-1 text-lg font-semibold">{data.monitoring.decision_records_created}</p></div>
              <div className="rounded-md border border-border bg-background/40 p-3"><p className="text-xs text-foreground/70">Replay Count</p><p className="mt-1 text-lg font-semibold">{data.monitoring.replay_count}</p></div>
              <div className="rounded-md border border-border bg-background/40 p-3"><p className="text-xs text-foreground/70">Campaign Count</p><p className="mt-1 text-lg font-semibold">{data.monitoring.campaign_count}</p></div>
              <div className="rounded-md border border-border bg-background/40 p-3"><p className="text-xs text-foreground/70">Current Champion</p><p className="mt-1 text-sm font-semibold">{data.monitoring.current_champion ?? "None"}</p></div>
              <div className="rounded-md border border-border bg-background/40 p-3"><p className="text-xs text-foreground/70">Paper Equity</p><p className="mt-1 text-lg font-semibold">{formatCurrency(data.monitoring.paper_equity)}</p></div>
            </div>
          </section>

          <section className="rounded-lg border border-border bg-muted/30 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Alerts</h2>
            {data.alerts.length === 0 ? (
              <p className="mt-3 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-100">No active alerts.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {data.alerts.map((alert) => (
                  <li key={alert.code} className={`rounded-md border p-3 text-sm ${badgeClass(alert.severity)}`}>
                    <p className="font-medium">{alert.message}</p>
                    <p className="text-xs opacity-80">{alert.code}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
