"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import ValidationRunTimeline from "@/components/domain/ValidationRunTimeline";
import {
  ApiRequestError,
  type OperationalAlert,
  type OperationalHealthIndicator,
  type ValidationRunEvent,
} from "@/lib/api/arena";
import {
  getMissionControlIntelligence,
  type MissionControlIntelligenceHistoryPoint,
  type MissionControlIntelligenceMetric,
  type MissionControlIntelligenceRange,
  type MissionControlIntelligenceResponse,
  type MissionControlIntelligenceTimelineEvent,
} from "@/lib/api/mission-control";

type AccordionKey = "intelligence" | "validationRuns" | "research" | "monitoring" | "infrastructure" | "paperTrading" | "alerts" | "recentTimeline";

const RANGE_OPTIONS: Array<{ value: MissionControlIntelligenceRange; label: string }> = [
  { value: "24h", label: "24H" },
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
  { value: "90d", label: "90D" },
  { value: "all", label: "ALL" },
];

const DEFAULT_OPEN_SECTIONS: Record<AccordionKey, boolean> = {
  intelligence: true,
  validationRuns: true,
  research: false,
  monitoring: false,
  infrastructure: false,
  paperTrading: false,
  alerts: false,
  recentTimeline: false,
};

const ACCORDION_STORAGE_KEY = "mission-control-open-sections";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

function scoreClass(score: number): string {
  if (score >= 85) {
    return "text-emerald-100";
  }
  if (score >= 70) {
    return "text-amber-100";
  }
  return "text-rose-100";
}

function trendClass(direction: MissionControlIntelligenceResponse["trend"]["direction"]): string {
  if (direction === "up") {
    return "text-emerald-100";
  }
  if (direction === "down") {
    return "text-rose-100";
  }
  return "text-cyan-100";
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "Not available";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function formatCurrency(value: string | null | undefined): string {
  if (value == null) {
    return "Not available";
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(numeric);
}

function formatPercent(value: number | null | undefined): string {
  if (value == null) {
    return "Not available";
  }
  return `${value.toFixed(2)}%`;
}

function healthBadgeClass(state: string): string {
  if (state === "green") {
    return "border-emerald-500/40 bg-emerald-500/15 text-emerald-100";
  }
  if (state === "yellow") {
    return "border-amber-500/40 bg-amber-500/15 text-amber-100";
  }
  return "border-rose-500/40 bg-rose-500/15 text-rose-100";
}

function severityBadgeClass(severity: string): string {
  if (severity === "green") {
    return "border-emerald-500/40 bg-emerald-500/15 text-emerald-100";
  }
  if (severity === "blue") {
    return "border-sky-500/40 bg-sky-500/15 text-sky-100";
  }
  if (severity === "purple") {
    return "border-violet-500/40 bg-violet-500/15 text-violet-100";
  }
  if (severity === "yellow") {
    return "border-amber-500/40 bg-amber-500/15 text-amber-100";
  }
  if (severity === "red") {
    return "border-rose-500/40 bg-rose-500/15 text-rose-100";
  }
  return "border-slate-500/40 bg-slate-500/15 text-slate-100";
}

function prettyLabel(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function badgeLabel(direction: MissionControlIntelligenceResponse["trend"]["direction"]): string {
  if (direction === "up") {
    return "↑ Improving";
  }
  if (direction === "down") {
    return "↓ Softening";
  }
  return "→ Stable";
}

function AccordionSection({
  id,
  title,
  count,
  open,
  onToggle,
  children,
}: {
  id: AccordionKey;
  title: string;
  count?: number;
  open: boolean;
  onToggle: (key: AccordionKey) => void;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border/80 bg-slate-950/60">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        onClick={() => onToggle(id)}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">{title}</h2>
          {typeof count === "number" ? (
            <span className="rounded-full border border-border bg-background/60 px-2 py-0.5 text-xs text-foreground/75">{count}</span>
          ) : null}
        </div>
        <span className="text-xs text-foreground/65">{open ? "Hide" : "Show"}</span>
      </button>
      {open ? <div className="border-t border-border px-4 py-4">{children}</div> : null}
    </section>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length === 0) {
    return <div className="h-10 rounded-md border border-dashed border-border/60 bg-background/40" />;
  }

  const width = 120;
  const height = 40;
  const padding = 4;
  const points = values
    .map((item, index) => {
      const x = padding + (index / Math.max(values.length - 1, 1)) * (width - padding * 2);
      const y = padding + (1 - Math.max(0, Math.min(100, item)) / 100) * (height - padding * 2);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-10 w-full" role="img" aria-label="Metric sparkline">
      <polyline points={points} fill="none" stroke="rgb(125 211 252)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function MetricCard({ metric }: { metric: MissionControlIntelligenceMetric }) {
  return (
    <article className="rounded-2xl border border-border bg-background/55 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-foreground">{metric.name}</p>
          <p className="mt-1 text-xs text-foreground/65">{metric.trend.label}</p>
        </div>
        <span className={`rounded-full border px-2 py-1 text-sm font-semibold ${scoreClass(metric.score)}`}>{metric.score}</span>
      </div>
      <div className="mt-3">
        <Sparkline values={metric.sparkline} />
      </div>
      <p className="mt-2 text-sm text-foreground/75">{metric.details}</p>
    </article>
  );
}

function MetricStat({ label, value, helper }: { label: string; value: string; helper?: string }) {
  return (
    <article className="rounded-2xl border border-border bg-background/55 p-4">
      <p className="text-[11px] uppercase tracking-wide text-foreground/65">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-foreground">{value}</p>
      {helper ? <p className="mt-1 text-xs text-foreground/70">{helper}</p> : null}
    </article>
  );
}

function MissionControlHeroChart({
  history,
  events,
  selectedEventId,
  onSelectEvent,
}: {
  history: MissionControlIntelligenceHistoryPoint[];
  events: MissionControlIntelligenceTimelineEvent[];
  selectedEventId: string | null;
  onSelectEvent: (eventId: string) => void;
}) {
  const chart = useMemo(() => {
    const width = 1200;
    const height = 420;
    const padding = 28;
    const historyPoints = history.length > 0 ? history : [{ timestamp: new Date().toISOString(), score: 0 } as MissionControlIntelligenceHistoryPoint];
    const timestamps = historyPoints.map((item) => new Date(item.timestamp).getTime());
    const min = Math.min(...timestamps);
    const max = Math.max(...timestamps);
    const scoreMin = Math.max(0, Math.min(...historyPoints.map((item) => item.score)) - 8);
    const scoreMax = Math.min(100, Math.max(...historyPoints.map((item) => item.score)) + 8);

    const points = historyPoints.map((item) => {
      const timestamp = new Date(item.timestamp).getTime();
      const score = item.score;
      const x = padding + ((timestamp - min) / Math.max(max - min, 1)) * (width - padding * 2);
      const y = padding + (1 - (score - scoreMin) / Math.max(scoreMax - scoreMin, 1)) * (height - padding * 2);
      return { x, y };
    });

    const eventMarkers = events.map((item) => {
      const timestamp = new Date(item.timestamp).getTime();
      const x = padding + ((timestamp - min) / Math.max(max - min, 1)) * (width - padding * 2);
      const scoreIndex = historyPoints.findIndex((point) => new Date(point.timestamp).getTime() >= timestamp);
      const nearest = scoreIndex >= 0 ? historyPoints[scoreIndex] : historyPoints[historyPoints.length - 1];
      const y = padding + (1 - ((nearest?.score ?? 0) - scoreMin) / Math.max(scoreMax - scoreMin, 1)) * (height - padding * 2);
      return { ...item, x, y };
    });

    return { width, height, padding, points, eventMarkers };
  }, [events, history]);

  const linePoints = chart.points.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <div className="rounded-[2rem] border border-border/80 bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 p-4 shadow-[0_24px_80px_rgba(15,23,42,0.35)]">
      <div className="relative">
        <svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="h-[24rem] w-full" role="img" aria-label="Intelligence timeline chart">
          <defs>
            <linearGradient id="mission-control-line" x1="0" x2="1" y1="0" y2="0">
              <stop offset="0%" stopColor="rgb(34 211 238)" />
              <stop offset="100%" stopColor="rgb(167 139 250)" />
            </linearGradient>
            <linearGradient id="mission-control-fill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="rgb(34 211 238)" stopOpacity="0.2" />
              <stop offset="100%" stopColor="rgb(15 23 42)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {[25, 50, 75].map((level) => (
            <line
              key={level}
              x1={chart.padding}
              x2={chart.width - chart.padding}
              y1={chart.padding + (1 - level / 100) * (chart.height - chart.padding * 2)}
              y2={chart.padding + (1 - level / 100) * (chart.height - chart.padding * 2)}
              className="stroke-border/50"
              strokeDasharray="5 9"
            />
          ))}
          <polygon
            points={`${chart.padding},${chart.height - chart.padding} ${linePoints} ${chart.width - chart.padding},${chart.height - chart.padding}`
            }
            fill="url(#mission-control-fill)"
          />
          <polyline points={linePoints} fill="none" stroke="url(#mission-control-line)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
          {chart.points.map((point, index) => (
            <circle key={index} cx={point.x} cy={point.y} r="4" fill="rgb(125 211 252)" />
          ))}
        </svg>

        {chart.eventMarkers.map((item) => {
          const selected = selectedEventId === item.event_id;
          return (
            <button
              key={item.event_id}
              type="button"
              aria-label={`Open ${item.title}`}
              title={item.title}
              className={`absolute h-5 w-5 rounded-full border-2 shadow-lg transition-transform hover:scale-110 ${selected ? "border-cyan-200 bg-cyan-400" : "border-white/70 bg-slate-700"}`}
              style={{ left: `${item.x}px`, top: `${item.y}px`, transform: "translate(-50%, -50%)" }}
              onClick={() => onSelectEvent(item.event_id)}
            />
          );
        })}
      </div>
    </div>
  );
}

export default function MissionControlIntelligenceCenter() {
  const [range, setRange] = useState<MissionControlIntelligenceRange>("24h");
  const [payload, setPayload] = useState<MissionControlIntelligenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openSections, setOpenSections] = useState<Record<AccordionKey, boolean>>(DEFAULT_OPEN_SECTIONS);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [isMobile, setIsMobile] = useState(false);
  const initialSelectionAppliedRef = useRef(false);

  useEffect(() => {
    setIsMobile(window.innerWidth < 768);
    const handleResize = () => {
      setIsMobile(window.innerWidth < 768);
    };

    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, []);

  useEffect(() => {
    const raw = window.localStorage.getItem(ACCORDION_STORAGE_KEY);
    if (!raw) {
      return;
    }

    try {
      const parsed = JSON.parse(raw) as Partial<Record<AccordionKey, boolean>>;
      setOpenSections((previous) => ({ ...previous, ...parsed }));
    } catch {
      // Ignore malformed persisted state.
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(ACCORDION_STORAGE_KEY, JSON.stringify(openSections));
  }, [openSections]);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const next = await getMissionControlIntelligence(range);
        if (!active) {
          return;
        }
        setPayload(next);
      } catch (requestError) {
        if (active) {
          setError(errorMessage(requestError, "Unable to load mission control intelligence."));
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
  }, [range]);

  const validationRunTimelineEvents = useMemo<ValidationRunEvent[]>(() => {
    if (!payload) {
      return [];
    }

    return payload.timeline_events.map((item, index) => ({
      id: index + 1,
      validation_run_id: item.related_validation_run ?? payload.selected_validation_run_id ?? "00000000-0000-0000-0000-000000000000",
      timestamp: item.timestamp,
      event_type: item.event_type,
      category: item.category,
      severity: item.severity,
      title: item.title,
      description: item.description,
      metadata: item.metadata,
    }));
  }, [payload]);

  const selectedEvent = useMemo(() => {
    if (!payload) {
      return null;
    }
    return payload.timeline_events.find((item) => item.event_id === selectedEventId) ?? null;
  }, [payload, selectedEventId]);

  useEffect(() => {
    if (!payload?.timeline_events.length) {
      return;
    }

    const selectedExists = selectedEventId ? payload.timeline_events.some((item) => item.event_id === selectedEventId) : false;
    if (!initialSelectionAppliedRef.current) {
      initialSelectionAppliedRef.current = true;
      if (!selectedEventId && !isMobile) {
        setSelectedEventId(payload.timeline_events[0].event_id);
      }
      return;
    }

    if (!selectedExists && selectedEventId !== null) {
      setSelectedEventId(payload.timeline_events[0]?.event_id ?? null);
    }
  }, [isMobile, payload, selectedEventId]);

  const selectedRun = useMemo(() => {
    if (!payload) {
      return null;
    }
    if (!payload.selected_validation_run_id) {
      return payload.validation_runs[0] ?? null;
    }
    return payload.validation_runs.find((item) => String(item.validation_run_id) === payload.selected_validation_run_id) ?? null;
  }, [payload]);

  const selectedEventMeta = selectedEvent
    ? [
        { label: "Timestamp", value: formatTimestamp(selectedEvent.timestamp) },
        { label: "Event title", value: selectedEvent.title },
        { label: "Description", value: selectedEvent.description },
        { label: "Related validation run", value: selectedEvent.related_validation_run ?? "Not available" },
        { label: "Health at that moment", value: selectedEvent.health_at_that_moment == null ? "Not available" : String(selectedEvent.health_at_that_moment) },
        { label: "Paper equity", value: selectedEvent.paper_equity ?? "Not available" },
        { label: "Paper PnL", value: selectedEvent.paper_pnl ?? "Not available" },
        { label: "Signals", value: selectedEvent.signals == null ? "Not available" : String(selectedEvent.signals) },
        { label: "Trades", value: selectedEvent.trades == null ? "Not available" : String(selectedEvent.trades) },
        { label: "Decision count", value: selectedEvent.decision_count == null ? "Not available" : String(selectedEvent.decision_count) },
      ]
    : [];

  const selectedTimelineDetail = selectedEvent ?? payload?.timeline_events[0] ?? null;

  function toggleAccordion(key: AccordionKey) {
    setOpenSections((previous) => ({ ...previous, [key]: !previous[key] }));
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">Mission Control</h1>
        <p className="max-w-3xl text-sm text-foreground/75">
          Is the system becoming smarter? Mission Control Intelligence Center V1 blends operational health, validation progress, paper execution,
          and research activity into a read-only operator view.
        </p>
      </header>

      {error ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          API unavailable: {error}
        </section>
      ) : null}

      {loading ? (
        <section className="rounded-2xl border border-border bg-muted/30 p-3 text-sm text-foreground/80">Loading mission control intelligence...</section>
      ) : null}

      {payload ? (
        <div className="space-y-4">
          <AccordionSection id="intelligence" title="Intelligence" open={openSections.intelligence} onToggle={toggleAccordion}>
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricStat label="System Intelligence" value={`${payload.current_score} / 100`} helper={payload.notes} />
                <MetricStat label="Trend" value={badgeLabel(payload.trend.direction)} helper={payload.trend.label} />
                <MetricStat label="Delta" value={payload.delta_label} helper="Compared with the start of the selected range." />
                <MetricStat label="Confidence" value={payload.confidence} helper={payload.trend.confidence} />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {RANGE_OPTIONS.map((option) => {
                  const active = range === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
                        active
                          ? "border-cyan-400/40 bg-cyan-500/20 text-cyan-50"
                          : "border-white/10 bg-white/5 text-slate-200/75 hover:border-white/20 hover:bg-white/10"
                      }`}
                      onClick={() => setRange(option.value)}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>

              <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr),minmax(20rem,1fr)]">
                <div className="space-y-4">
                  <MissionControlHeroChart
                    history={payload.history}
                    events={payload.timeline_events}
                    selectedEventId={selectedEventId}
                    onSelectEvent={setSelectedEventId}
                  />

                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {payload.metric_breakdown.map((metric) => (
                      <MetricCard key={metric.name} metric={metric} />
                    ))}
                  </div>
                </div>

                {isMobile ? null : (
                  <aside className="space-y-3 rounded-[2rem] border border-border bg-background/55 p-4">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <p className="text-xs uppercase tracking-wide text-foreground/65">Event Detail</p>
                        <h3 className="mt-1 text-lg font-semibold text-foreground">{selectedEvent?.title ?? "Select a point"}</h3>
                      </div>
                      {selectedEvent ? (
                        <span className={`rounded-full border px-2 py-1 text-xs font-medium uppercase tracking-wide ${severityBadgeClass(selectedEvent.severity)}`}>
                          {selectedEvent.severity}
                        </span>
                      ) : null}
                    </div>

                    {selectedEvent ? (
                      <div className="space-y-2 text-sm text-foreground/75">
                        {selectedEventMeta.map((item) => (
                          <div key={item.label} className="rounded-xl border border-border bg-slate-950/40 p-3">
                            <p className="text-[11px] uppercase tracking-wide text-foreground/60">{item.label}</p>
                            <p className="mt-1 text-sm text-foreground">{item.value}</p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="rounded-xl border border-dashed border-border bg-slate-950/30 p-3 text-sm text-foreground/70">
                        Click a chart event to open the detail panel.
                      </p>
                    )}
                  </aside>
                )}
              </div>
            </div>
          </AccordionSection>

          <AccordionSection id="validationRuns" title="Validation Runs" count={payload.validation_runs.length} open={openSections.validationRuns} onToggle={toggleAccordion}>
            <div className="grid gap-3 lg:grid-cols-2">
              {payload.validation_runs.map((run) => (
                <article key={String(run.validation_run_id)} className="rounded-2xl border border-border bg-background/55 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <p className="text-sm font-semibold text-foreground">{run.name}</p>
                      <p className="mt-1 text-xs text-foreground/65">{run.objective}</p>
                    </div>
                    <span className={`rounded-full border px-2 py-1 text-xs font-medium uppercase tracking-wide ${healthBadgeClass(run.status === "RUNNING" ? "green" : "yellow")}`}>
                      {run.status}
                    </span>
                  </div>
                  <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                    <p>Duration: {run.duration_hours}h</p>
                    <p>Health: {run.health_score ?? "Not available"}</p>
                    <p>Paper capital: {formatCurrency(String(run.paper_capital))}</p>
                    <p>Result: {run.result_status}</p>
                  </div>
                </article>
              ))}
            </div>
          </AccordionSection>

          <AccordionSection id="research" title="Research" count={Number(payload.operations.monitoring.candidate_count)} open={openSections.research} onToggle={toggleAccordion}>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <MetricStat label="Current Campaign" value={String(payload.operations.research_status.current_campaign ?? "None")} />
              <MetricStat label="Current Champion" value={String(payload.operations.research_status.current_champion ?? "None")} />
              <MetricStat label="Campaign Status" value={String(payload.operations.research_status.campaign_status ?? "Unknown")} />
              <MetricStat label="Candidates" value={String(payload.operations.monitoring.candidate_count)} />
              <MetricStat label="Laboratory Runs" value={String(payload.operations.monitoring.laboratory_runs)} />
              <MetricStat label="Evolution Progress" value={String(payload.operations.monitoring.evolution_count)} />
              <MetricStat label="Memory Growth" value={String(payload.operations.monitoring.research_memory_growth)} />
            </div>
          </AccordionSection>

          <AccordionSection id="monitoring" title="Monitoring" count={payload.operations.monitoring.candles_processed} open={openSections.monitoring} onToggle={toggleAccordion}>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <MetricStat label="Candles Processed" value={String(payload.operations.monitoring.candles_processed)} />
              <MetricStat label="Signals Generated" value={String(payload.operations.monitoring.signals_generated)} />
              <MetricStat label="Decision Records" value={String(payload.operations.monitoring.decision_records_created)} />
              <MetricStat label="Replay Count" value={String(payload.operations.monitoring.replay_count)} />
              <MetricStat label="Signals Today" value={String(payload.operations.monitoring.signals_today)} />
              <MetricStat label="Trades Today" value={String(payload.operations.monitoring.trades_today)} />
              <MetricStat label="Paper Trades" value={String(payload.operations.monitoring.paper_trades_executed)} />
              <MetricStat label="Paper Equity" value={formatCurrency(payload.operations.monitoring.paper_equity)} />
            </div>
          </AccordionSection>

          <AccordionSection id="infrastructure" title="Infrastructure" open={openSections.infrastructure} onToggle={toggleAccordion}>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" role="list" aria-label="System health indicators">
              {([
                ["API", payload.operations.system_health.api],
                ["Orchestrator", payload.operations.system_health.orchestrator],
                ["Database", payload.operations.system_health.database],
                ["Research Agent", payload.operations.system_health.research_agent],
              ] as Array<[string, OperationalHealthIndicator]>).map(([label, indicator]) => (
                <div key={label} className="rounded-2xl border border-border bg-background/55 p-4" role="listitem">
                  <p className="text-xs uppercase tracking-wide text-foreground/70">{label}</p>
                  <div className="mt-2 flex items-center gap-2">
                    <span className={`rounded-full border px-2 py-1 text-xs font-medium ${healthBadgeClass(indicator.state)}`}>{indicator.state.toUpperCase()}</span>
                    <span className="text-sm text-foreground/80">{indicator.detail}</span>
                  </div>
                </div>
              ))}
              <MetricStat label="Run Phase" value={payload.operations.run_status.current_phase} />
              <MetricStat label="Health Status" value={payload.operations.run_status.health_status.toUpperCase()} />
              <MetricStat label="Uptime" value={payload.operations.run_status.uptime} />
              <MetricStat label="Expected End" value={formatTimestamp(payload.operations.run_status.expected_end)} />
            </div>
          </AccordionSection>

          <AccordionSection id="paperTrading" title="Paper Trading" open={openSections.paperTrading} onToggle={toggleAccordion}>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <MetricStat label="Paper Equity" value={formatCurrency(payload.operations.monitoring.paper_equity)} />
              <MetricStat label="Current Champion" value={String(payload.operations.monitoring.current_champion ?? payload.operations.research_status.current_champion ?? "None")} />
              <MetricStat label="Paper Trades Executed" value={String(payload.operations.monitoring.paper_trades_executed)} />
              <MetricStat label="Signals Today" value={String(payload.operations.monitoring.signals_today)} />
              <MetricStat label="Trades Today" value={String(payload.operations.monitoring.trades_today)} />
            </div>
          </AccordionSection>

          <AccordionSection id="alerts" title="Alerts" count={payload.operations.alerts.length} open={openSections.alerts} onToggle={toggleAccordion}>
            {payload.operations.alerts.length === 0 ? (
              <p className="rounded-2xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-100">No active alerts.</p>
            ) : (
              <ul className="space-y-2">
                {payload.operations.alerts.map((alert: OperationalAlert) => (
                  <li key={alert.code} className={`rounded-2xl border p-3 text-sm ${healthBadgeClass(alert.severity === "red" ? "red" : alert.severity === "yellow" ? "yellow" : "green")}`}>
                    <p className="font-medium">{alert.message}</p>
                    <p className="text-xs opacity-80">{alert.code}</p>
                  </li>
                ))}
              </ul>
            )}
          </AccordionSection>

          <AccordionSection id="recentTimeline" title="Recent Timeline" count={validationRunTimelineEvents.length} open={openSections.recentTimeline} onToggle={toggleAccordion}>
            <ValidationRunTimeline
              title="Recent Timeline"
              events={validationRunTimelineEvents}
              query={{ order: "newest", window: "entire_run", category: "all", severity: "all", search: "" }}
              loading={false}
              showControls={false}
              maxHeightClass="max-h-[28rem]"
              emptyMessage="No validation timeline events available yet."
            />
          </AccordionSection>
        </div>
      ) : null}

      {isMobile && selectedTimelineDetail ? (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/80 p-4 sm:items-center" role="dialog" aria-modal="true" aria-label="Timeline event detail">
          <div className="w-full max-w-xl rounded-[2rem] border border-border bg-slate-950 p-4 shadow-2xl">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-foreground/65">Event Detail</p>
                <h3 className="mt-1 text-lg font-semibold text-foreground">{selectedTimelineDetail.title}</h3>
              </div>
              <button
                type="button"
                className="rounded-full border border-border bg-background/60 px-3 py-1 text-sm"
                onClick={() => setSelectedEventId(null)}
              >
                Close
              </button>
            </div>
            <div className="mt-3 space-y-2 text-sm text-foreground/75">
              {selectedEventMeta.map((item) => (
                <div key={item.label} className="rounded-xl border border-border bg-background/40 p-3">
                  <p className="text-[11px] uppercase tracking-wide text-foreground/60">{item.label}</p>
                  <p className="mt-1 text-sm text-foreground">{item.value}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}