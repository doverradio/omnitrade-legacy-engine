"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiRequestError,
  getPaperPipelineHealth,
  type PaperPipelineHealth,
  type PipelineActivity,
} from "@/lib/api/paperAccounts";

type StageTone = "green" | "yellow" | "red" | "gray";

type Stage = {
  id: string;
  label: string;
  count: number;
  tone: StageTone;
  status: string;
  hint: string;
};

const REFRESH_INTERVAL_MS = 5000;

const TONE_CLASS: Record<StageTone, string> = {
  green: "border-emerald-500/35 bg-emerald-500/10 text-emerald-100 shadow-[0_0_28px_rgba(16,185,129,0.12)]",
  yellow: "border-amber-400/35 bg-amber-500/10 text-amber-100 shadow-[0_0_24px_rgba(251,191,36,0.10)]",
  red: "border-rose-400/30 bg-rose-500/10 text-rose-100 shadow-[0_0_24px_rgba(244,63,94,0.10)]",
  gray: "border-slate-500/40 bg-slate-500/10 text-slate-100",
};

const ACTIVE_PULSE_CLASS: Record<StageTone, string> = {
  green: "animate-pulse",
  yellow: "animate-pulse",
  red: "animate-pulse",
  gray: "",
};

function resolveErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load paper pipeline health.";
}

function formatWhen(value: string | Date | null | undefined): string {
  if (!value) {
    return "No updates yet";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "No updates yet";
  }
  return parsed.toLocaleString();
}

function stageToneLabel(tone: StageTone): string {
  if (tone === "green") {
    return "Flowing";
  }
  if (tone === "yellow") {
    return "Waiting";
  }
  if (tone === "red") {
    return "Blocked";
  }
  return "No data";
}

function toStages(data: PaperPipelineHealth | null): Stage[] {
  if (!data) {
    return [
      { id: "candles", label: "Candles", count: 0, tone: "gray", status: "No data", hint: "Market data ingestion" },
      { id: "signals", label: "Signals", count: 0, tone: "gray", status: "No data", hint: "Signal creation" },
      { id: "candidates", label: "Candidates", count: 0, tone: "gray", status: "No data", hint: "BUY/SELL candidates" },
      { id: "attempted", label: "Attempts", count: 0, tone: "gray", status: "No data", hint: "Execution attempts" },
      { id: "risk", label: "Risk", count: 0, tone: "gray", status: "No data", hint: "Risk gate" },
      { id: "trades", label: "Trades", count: 0, tone: "gray", status: "No data", hint: "Paper fills" },
      { id: "decisions", label: "Decisions", count: 0, tone: "gray", status: "No data", hint: "Decision records" },
    ];
  }

  const signalTone: StageTone = data.signals_created > 0 ? "green" : "gray";
  const candidateTone: StageTone = data.execution_candidates > 0 ? "green" : data.signals_created > 0 ? "yellow" : "gray";
  const attemptedTone: StageTone =
    data.executions_attempted > 0 ? "green" : data.execution_candidates > 0 ? "yellow" : "gray";
  const riskTone: StageTone =
    data.risk_rejected > 0
      ? "red"
      : data.risk_events > 0
        ? "green"
        : data.executions_attempted > 0
          ? "yellow"
          : "gray";
  const tradesTone: StageTone =
    data.trades > 0 ? "green" : data.risk_events > 0 ? "yellow" : data.executions_attempted > 0 ? "yellow" : "gray";
  const decisionsTone: StageTone = data.decision_records > 0 ? "green" : "gray";

  return [
    {
      id: "candles",
      label: "Candles",
      count: data.candles,
      tone: data.candles > 0 ? "green" : "gray",
      status: data.candles > 0 ? "Flowing" : "No data",
      hint: "Market data ingestion",
    },
    {
      id: "signals",
      label: "Signals",
      count: data.signals_created,
      tone: signalTone,
      status: stageToneLabel(signalTone),
      hint: `HOLD ${data.hold_signals} / BUY+SELL ${data.buy_sell_signals}`,
    },
    {
      id: "candidates",
      label: "Candidates",
      count: data.execution_candidates,
      tone: candidateTone,
      status: stageToneLabel(candidateTone),
      hint: "BUY/SELL candidates",
    },
    {
      id: "attempted",
      label: "Attempts",
      count: data.executions_attempted,
      tone: attemptedTone,
      status: stageToneLabel(attemptedTone),
      hint: "Orchestrator attempts",
    },
    {
      id: "risk",
      label: "Risk",
      count: data.risk_events,
      tone: riskTone,
      status: riskTone === "red" ? "Blocking safely" : stageToneLabel(riskTone),
      hint: data.risk_rejected > 0 ? `Rejected ${data.risk_rejected}` : "Risk evaluation events",
    },
    {
      id: "trades",
      label: "Trades",
      count: data.trades,
      tone: tradesTone,
      status: stageToneLabel(tradesTone),
      hint: "Paper fills only",
    },
    {
      id: "decisions",
      label: "Decisions",
      count: data.decision_records,
      tone: decisionsTone,
      status: stageToneLabel(decisionsTone),
      hint: "Decision intelligence records",
    },
  ];
}

function machineStatus(data: PaperPipelineHealth | null): string {
  if (!data) {
    return "Waiting for paper pipeline data";
  }
  if (data.candles === 0) {
    return "Waiting for market data";
  }
  if (data.buy_sell_signals === 0) {
    return "Waiting for BUY/SELL";
  }
  if (data.risk_rejected > 0 && data.trades === 0) {
    return "Risk blocking safely";
  }
  if (data.trades > 0) {
    return "Paper trades flowing";
  }
  if (data.executions_attempted > 0) {
    return "Execution machine active";
  }
  return "Pipeline active in paper mode";
}

function actionBadgeClass(action: string): string {
  const normalized = action.toLowerCase();
  if (normalized === "hold") {
    return "border-amber-400/40 bg-amber-500/15 text-amber-100";
  }
  if (normalized === "buy" || normalized === "sell") {
    return "border-indigo-400/40 bg-indigo-500/15 text-indigo-100";
  }
  return "border-slate-400/40 bg-slate-500/15 text-slate-100";
}

function statusBadgeClass(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized.includes("risk_rejected") || normalized.includes("rejected")) {
    return "border-rose-400/40 bg-rose-500/15 text-rose-100";
  }
  if (normalized.includes("executed") || normalized.includes("trade")) {
    return "border-emerald-400/40 bg-emerald-500/15 text-emerald-100";
  }
  return "border-slate-400/40 bg-slate-500/15 text-slate-100";
}

function hasTrade(item: PipelineActivity): boolean {
  const status = item.status.toLowerCase();
  return status.includes("executed") || status.includes("trade") || status.includes("filled");
}

function AnimatedCount({
  value,
  durationMs = 450,
  className,
}: {
  value: number;
  durationMs?: number;
  className?: string;
}) {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);

  useEffect(() => {
    const from = fromRef.current;
    const to = value;
    const steps = 12;
    const stepMs = Math.max(16, Math.floor(durationMs / steps));
    let step = 0;

    const timerId = window.setInterval(() => {
      step += 1;
      const progress = Math.min(1, step / steps);
      const next = Math.round(from + (to - from) * progress);
      setDisplay(next);
      if (progress >= 1) {
        fromRef.current = to;
        window.clearInterval(timerId);
      }
    }, stepMs);

    return () => window.clearInterval(timerId);
  }, [value, durationMs]);

  return <span className={className}>{display}</span>;
}

export default function PaperPipelineFlow() {
  const [data, setData] = useState<PaperPipelineHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastFetchAt, setLastFetchAt] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load(isInitial: boolean) {
      if (isInitial) {
        setLoading(true);
      } else {
        setRefreshing(true);
      }
      try {
        const payload = await getPaperPipelineHealth(120);
        if (active) {
          setData(payload);
          setLastFetchAt(new Date());
          setError(null);
        }
      } catch (requestError) {
        if (active) {
          setError(resolveErrorMessage(requestError));
        }
      } finally {
        if (active) {
          if (isInitial) {
            setLoading(false);
          } else {
            setRefreshing(false);
          }
        }
      }
    }

    void load(true);

    const intervalId = window.setInterval(() => {
      void load(false);
    }, REFRESH_INTERVAL_MS);

    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, []);

  const stages = useMemo(() => toStages(data), [data]);
  const latestReason = data?.latest_rejection_reason ?? null;
  const activity = data?.recent_activity ?? [];
  const summaryStatus = machineStatus(data);

  return (
    <section
      className="rounded-lg border border-border bg-[linear-gradient(145deg,rgba(15,23,42,0.75),rgba(30,41,59,0.55))] p-4"
      aria-labelledby="paper-pipeline-flow-heading"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="paper-pipeline-flow-heading" className="text-sm font-semibold uppercase tracking-wide text-foreground/85">
            Paper Pipeline Flow
          </h2>
          <p className="mt-1 text-xs text-foreground/70">Living paper/simulated decision engine visibility (no live trading).</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-100">
            PAPER / SIMULATED
          </span>
          <span className="rounded border border-border bg-background/50 px-2 py-1 text-xs text-foreground/80">
            {refreshing ? "Refreshing..." : "Auto-refresh: 5s"}
          </span>
        </div>
      </div>

      {error && <p className="mt-3 text-sm text-rose-200">{error}</p>}

      <div className="mt-4 grid gap-3 md:grid-cols-[2fr_1fr]">
        <section className="rounded-lg border border-border/80 bg-background/35 p-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-cyan-100/80">PAPER DECISION RECORDS</p>
          <p className="mt-2 text-4xl font-semibold leading-none text-cyan-100" aria-label="Paper decision records value">
            <AnimatedCount value={data?.decision_records ?? 0} durationMs={550} />
          </p>
          <p className="mt-2 text-xs text-foreground/75">Simulated evidence records from the paper decision pipeline, not profit.</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <div className="rounded border border-border/80 bg-background/40 px-2 py-1">
              <p className="text-[10px] uppercase tracking-wide text-foreground/70">Machine status</p>
              <p className="text-sm text-foreground/90">{summaryStatus}</p>
            </div>
            <div className="rounded border border-border/80 bg-background/40 px-2 py-1">
              <p className="text-[10px] uppercase tracking-wide text-foreground/70">Last updated</p>
              <p className="text-sm text-foreground/90">{formatWhen(lastFetchAt ?? data?.latest_updated_at)}</p>
            </div>
          </div>
        </section>

        <section className="rounded-lg border border-border/80 bg-background/35 p-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground/80">Machine Summary</p>
          <ul className="mt-2 space-y-2 text-xs text-foreground/85">
            <li className="rounded border border-border/70 bg-background/50 px-2 py-1">{data?.candles ? "Market data flowing" : "Market data idle"}</li>
            <li className="rounded border border-border/70 bg-background/50 px-2 py-1">
              {data?.buy_sell_signals ? "Directional signals present" : "Waiting for BUY/SELL"}
            </li>
            <li className="rounded border border-border/70 bg-background/50 px-2 py-1">
              {data?.risk_rejected ? "Risk blocking safely" : "Risk gate normal"}
            </li>
            <li className="rounded border border-border/70 bg-background/50 px-2 py-1">
              {data?.trades ? "Paper trades flowing" : "No paper trades yet"}
            </li>
          </ul>
        </section>
      </div>

      <div className="mt-4 grid gap-2 md:grid-cols-7" role="list" aria-label="Paper pipeline stages">
        {stages.map((stage, index) => (
          <div key={stage.id} className="relative" role="listitem">
            <article className={`rounded-md border p-3 transition-all duration-500 ${TONE_CLASS[stage.tone]} ${ACTIVE_PULSE_CLASS[stage.tone]}`}>
              <p className="text-[11px] font-semibold uppercase tracking-wide">{stage.label}</p>
              <p className="mt-2 text-2xl font-semibold leading-none tabular-nums">
                <AnimatedCount value={stage.count} />
              </p>
              <p className="mt-1 text-[11px] font-medium opacity-90">{stage.status}</p>
              <p className="mt-1 text-[11px] opacity-75">{stage.hint}</p>
            </article>
            {index < stages.length - 1 && (
              <span className="pointer-events-none absolute -right-1 top-1/2 hidden h-0.5 w-3 -translate-y-1/2 bg-foreground/35 md:inline" />
            )}
          </div>
        ))}
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-md border border-border bg-background/40 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-foreground/80">Latest rejection reason</p>
          <p className="mt-2 text-sm text-foreground/90">{latestReason ?? "None"}</p>
        </div>
        <div className="rounded-md border border-border bg-background/40 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-foreground/80">Last updated</p>
          <p className="mt-2 text-sm text-foreground/90">{loading ? "Loading..." : formatWhen(lastFetchAt ?? data?.latest_updated_at)}</p>
        </div>
      </div>

      <div className="mt-4 rounded-md border border-border bg-background/40 p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-foreground/80">Recent activity</p>
        {activity.length === 0 ? (
          <p className="mt-2 text-sm text-foreground/70">No recent signals in this window.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {activity.map((item: PipelineActivity) => (
              <li key={`${item.signal_id}-${item.created_at}`} className="rounded border border-border/70 bg-background/60 p-2 text-xs">
                <div className="flex flex-wrap items-center gap-1">
                  <span className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${actionBadgeClass(item.action)}`}>
                    {item.action.toUpperCase()}
                  </span>
                  <span className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${statusBadgeClass(item.status)}`}>
                    {item.status}
                  </span>
                  <span
                    className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${
                      hasTrade(item)
                        ? "border-emerald-400/40 bg-emerald-500/15 text-emerald-100"
                        : "border-slate-400/40 bg-slate-500/15 text-slate-100"
                    }`}
                  >
                    {hasTrade(item) ? "trade: yes" : "trade: no"}
                  </span>
                </div>
                <p className="mt-1 text-foreground/70">Signal {item.signal_id}</p>
                <p className="mt-1 text-foreground/70">Reason: {item.reason ?? "-"}</p>
                <p className="mt-1 text-foreground/60">{formatWhen(item.created_at)}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
