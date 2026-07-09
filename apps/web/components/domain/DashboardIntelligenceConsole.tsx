"use client";

import { useEffect, useMemo, useState } from "react";

import PaperEquityCurvePanel from "@/components/domain/PaperEquityCurvePanel";
import PaperPerformancePanel from "@/components/domain/PaperPerformancePanel";
import PaperPipelineFlow from "@/components/domain/PaperPipelineFlow";
import PaperTradeHistoryPanel from "@/components/domain/PaperTradeHistoryPanel";
import {
  ApiRequestError,
  getDashboardIntelligenceScore,
  type DashboardIntelligenceRange,
  type DashboardIntelligenceScore,
} from "@/lib/api/dashboard";

type TabId = "intelligence" | "equity" | "strategy" | "pipeline" | "activity";

const TABS: Array<{ id: TabId; label: string }> = [
  { id: "intelligence", label: "Intelligence Timeline" },
  { id: "equity", label: "Paper Equity" },
  { id: "strategy", label: "Strategy Performance" },
  { id: "pipeline", label: "Pipeline Flow" },
  { id: "activity", label: "Recent Activity" },
];

const RANGE_OPTIONS: Array<{ value: DashboardIntelligenceRange; label: string }> = [
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
];

function resolveErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load dashboard intelligence.";
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function clampScore(value: number): number {
  return Math.max(0, Math.min(100, value));
}

function IntelligenceChart({ data }: { data: DashboardIntelligenceScore["timeline"] }) {
  if (data.length === 0) {
    return (
      <div className="flex h-72 items-center justify-center rounded-2xl border border-dashed border-border bg-background/50 px-6 text-sm text-foreground/70">
        No intelligence data yet. The chart remains flat until paper, decision, and research evidence accumulate.
      </div>
    );
  }

  const width = 920;
  const height = 280;
  const padding = 24;
  const points = data
    .map((item, index) => {
      const x = padding + (index / Math.max(data.length - 1, 1)) * (width - padding * 2);
      const y = padding + (1 - clampScore(item.score) / 100) * (height - padding * 2);
      return `${x},${y}`;
    })
    .join(" ");

  const latest = data[data.length - 1];

  return (
    <div className="rounded-2xl border border-border bg-slate-950/60 p-4">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-72 w-full" role="img" aria-label="System intelligence timeline">
        <defs>
          <linearGradient id="intelligence-line" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="rgb(56 189 248)" />
            <stop offset="100%" stopColor="rgb(129 140 248)" />
          </linearGradient>
        </defs>
        {[25, 50, 75].map((level) => (
          <line
            key={level}
            x1={padding}
            x2={width - padding}
            y1={padding + (1 - level / 100) * (height - padding * 2)}
            y2={padding + (1 - level / 100) * (height - padding * 2)}
            className="stroke-border/70"
            strokeDasharray="4 8"
          />
        ))}
        <polyline
          points={points}
          fill="none"
          stroke="url(#intelligence-line)"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {data.map((item, index) => {
          const x = padding + (index / Math.max(data.length - 1, 1)) * (width - padding * 2);
          const y = padding + (1 - clampScore(item.score) / 100) * (height - padding * 2);
          return <circle key={`${item.timestamp}-${index}`} cx={x} cy={y} r="4" fill="rgb(125 211 252)" />;
        })}
      </svg>

      <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <article className="rounded-xl border border-border bg-background/55 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/65">Latest score</p>
          <p className="mt-1 text-xl font-semibold text-foreground">{latest.score}</p>
        </article>
        <article className="rounded-xl border border-border bg-background/55 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/65">Latest equity</p>
          <p className="mt-1 text-xl font-semibold text-foreground">{latest.equity}</p>
        </article>
        <article className="rounded-xl border border-border bg-background/55 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/65">Decision quality</p>
          <p className="mt-1 text-xl font-semibold text-foreground">{latest.decision_quality}</p>
        </article>
        <article className="rounded-xl border border-border bg-background/55 p-3">
          <p className="text-[11px] uppercase tracking-wide text-foreground/65">Updated</p>
          <p className="mt-1 text-sm font-medium text-foreground">{formatTimestamp(latest.timestamp)}</p>
        </article>
      </div>
    </div>
  );
}

function ComponentCards({ components }: { components: DashboardIntelligenceScore["components"] }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
      {components.map((component) => (
        <article key={component.name} className="rounded-2xl border border-border bg-background/60 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-foreground">{component.name}</p>
              <p className="mt-1 text-xs text-foreground/65">Weight {component.weight}%</p>
            </div>
            <span className="rounded-full border border-cyan-400/40 bg-cyan-500/10 px-2 py-1 text-sm font-semibold text-cyan-100">
              {component.score}
            </span>
          </div>
          <p className="mt-3 text-sm text-foreground/75">{component.explanation}</p>
        </article>
      ))}
    </div>
  );
}

export default function DashboardIntelligenceConsole() {
  const [activeTab, setActiveTab] = useState<TabId>("intelligence");
  const [range, setRange] = useState<DashboardIntelligenceRange>("24h");
  const [payload, setPayload] = useState<DashboardIntelligenceScore | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const next = await getDashboardIntelligenceScore(range);
        if (active) {
          setPayload(next);
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
  }, [range]);

  const score = payload?.score ?? 0;
  const completeness = payload?.data_completeness ?? 0;
  const timeline = useMemo(() => payload?.timeline ?? [], [payload]);

  return (
    <section className="overflow-hidden rounded-[2rem] border border-border/80 bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 shadow-[0_24px_80px_rgba(15,23,42,0.35)]">
      <div className="border-b border-white/10 px-5 py-6 sm:px-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.35em] text-cyan-200/75">Dashboard Intelligence Console</p>
            <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">System Intelligence Console</h1>
            <p className="mt-3 max-w-3xl text-sm text-slate-200/75">
              Read-only intelligence summary for paper-mode operations. This score is research-only and does not guarantee future profit.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:min-w-[26rem]">
            <article className="rounded-2xl border border-cyan-400/20 bg-cyan-500/10 p-4 text-cyan-50">
              <p className="text-[11px] uppercase tracking-wide text-cyan-100/75">System intelligence score</p>
              <p className="mt-2 text-4xl font-semibold">{loading ? "--" : score}</p>
              <p className="mt-1 text-xs text-cyan-50/75">0-100 composite from paper, research, risk, and operational health signals.</p>
            </article>
            <article className="rounded-2xl border border-white/10 bg-white/5 p-4 text-slate-50">
              <p className="text-[11px] uppercase tracking-wide text-slate-200/70">Data completeness</p>
              <p className="mt-2 text-4xl font-semibold">{loading ? "--" : `${completeness}%`}</p>
              <p className="mt-1 text-xs text-slate-200/70">Higher is better. Missing inputs reduce confidence, not trading behavior.</p>
            </article>
          </div>
        </div>
      </div>

      <div className="px-5 py-5 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div role="tablist" aria-label="Dashboard intelligence tabs" className="flex flex-wrap gap-2">
            {TABS.map((tab) => {
              const active = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={`rounded-full border px-4 py-2 text-sm font-medium transition ${
                    active
                      ? "border-cyan-400/40 bg-cyan-500/20 text-cyan-50 shadow-[0_0_24px_rgba(34,211,238,0.18)]"
                      : "border-white/10 bg-white/5 text-slate-200/75 hover:border-white/20 hover:bg-white/10"
                  }`}
                  onClick={() => setActiveTab(tab.id)}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>

          {activeTab === "intelligence" ? (
            <div className="flex flex-wrap gap-2">
              {RANGE_OPTIONS.map((option) => {
                const active = range === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                      active
                        ? "border-amber-300/40 bg-amber-500/20 text-amber-50"
                        : "border-white/10 bg-white/5 text-slate-200/75 hover:border-white/20"
                    }`}
                    onClick={() => setRange(option.value)}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>

        {error ? <p className="mt-4 rounded-xl border border-rose-400/30 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</p> : null}

        <div className="mt-5" role="tabpanel">
          {activeTab === "intelligence" ? (
            <div className="space-y-5">
              <IntelligenceChart data={timeline} />

              <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr),minmax(0,1fr)]">
                <ComponentCards components={payload?.components ?? []} />

                <aside className="space-y-4 rounded-2xl border border-border bg-background/60 p-4">
                  <div>
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Why this exists</h2>
                    <p className="mt-2 text-sm text-foreground/75">
                      This score is research-only and does not guarantee future profit. It summarizes read-only signals from paper performance,
                      decision quality, research progress, risk discipline, and operational health.
                    </p>
                  </div>
                  <div className="rounded-xl border border-border bg-slate-950/60 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-foreground/65">Range</p>
                    <p className="mt-1 text-sm font-medium text-foreground">{payload?.range ?? range}</p>
                    <p className="mt-2 text-[11px] uppercase tracking-wide text-foreground/65">Generated at</p>
                    <p className="mt-1 text-sm text-foreground/75">{payload ? formatTimestamp(payload.generated_at) : "Loading..."}</p>
                  </div>
                </aside>
              </div>
            </div>
          ) : null}

          {activeTab === "equity" ? <PaperEquityCurvePanel /> : null}
          {activeTab === "strategy" ? <PaperPerformancePanel /> : null}
          {activeTab === "pipeline" ? <PaperPipelineFlow /> : null}
          {activeTab === "activity" ? <PaperTradeHistoryPanel /> : null}
        </div>
      </div>
    </section>
  );
}