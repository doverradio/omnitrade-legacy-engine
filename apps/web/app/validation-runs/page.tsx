"use client";

import { useEffect, useMemo, useState } from "react";

import ValidationRunTimeline, { type TimelineQuery } from "@/components/domain/ValidationRunTimeline";
import {
  ApiRequestError,
  cancelValidationRun,
  createValidationRun,
  getValidationRun,
  getValidationRunEvents,
  getValidationRunMetrics,
  getValidationRuns,
  startValidationRun,
  type ValidationRun,
  type ValidationRunDetail,
  type ValidationRunEvent,
  type ValidationRunEventListResponse,
  type ValidationRunMetrics,
  type ValidationRunScorecard,
} from "@/lib/api/arena";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

function statusClass(status: string): string {
  if (status === "RUNNING" || status === "PASS") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "FAILED" || status === "FAIL" || status === "CANCELLED") {
    return "border-rose-500/40 bg-rose-500/10 text-rose-100";
  }
  return "border-amber-500/40 bg-amber-500/10 text-amber-100";
}

function scoreClass(score: number): string {
  if (score >= 85) {
    return "text-emerald-200";
  }
  if (score >= 65) {
    return "text-amber-200";
  }
  return "text-rose-200";
}

function formatCurrency(value: string): string {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(numeric);
}

function formatTime(value: string | null): string {
  if (!value) {
    return "Not available";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

const DURATION_OPTIONS = [24, 72, 168] as const;

export default function ValidationRunsPage() {
  const [name, setName] = useState("72h Validation Run");
  const [objective, setObjective] = useState("Validate paper-mode reliability and research progression.");
  const [durationPreset, setDurationPreset] = useState<string>("72");
  const [customDuration, setCustomDuration] = useState("72");
  const [paperCapital, setPaperCapital] = useState("25");
  const [enabledStrategies, setEnabledStrategies] = useState<string[]>(["MA Crossover", "RSI"]);
  const [enabledAgents, setEnabledAgents] = useState<string[]>(["Baseline", "OpenAI Sandbox"]);
  const [enabledFeatures, setEnabledFeatures] = useState<string[]>([
    "Laboratory",
    "Evolution",
    "Tournament",
    "Capital Allocation",
  ]);

  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [activeMetrics, setActiveMetrics] = useState<ValidationRunMetrics | null>(null);
  const [activeDetail, setActiveDetail] = useState<ValidationRunDetail | null>(null);
  const [timelineEvents, setTimelineEvents] = useState<ValidationRunEvent[]>([]);
  const [timelinePage, setTimelinePage] = useState(1);
  const [timelineHasMore, setTimelineHasMore] = useState(false);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineQuery, setTimelineQuery] = useState<TimelineQuery>({
    order: "newest",
    window: "entire_run",
    category: "all",
    search: "",
  });
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeRun = useMemo(() => runs.find((item) => item.status === "RUNNING") ?? null, [runs]);
  const selectedTimelineRunId = useMemo(() => {
    if (expandedRunId) {
      return expandedRunId;
    }
    if (activeRun) {
      return activeRun.validation_run_id;
    }
    return runs[0]?.validation_run_id ?? null;
  }, [expandedRunId, activeRun, runs]);

  async function loadTimeline(runId: string, reset: boolean): Promise<ValidationRunEventListResponse> {
    setTimelineLoading(true);
    try {
      const page = reset ? 1 : timelinePage + 1;
      const result = await getValidationRunEvents(runId, {
        page,
        pageSize: 30,
        order: timelineQuery.order,
        window: timelineQuery.window,
        category: timelineQuery.category,
        search: timelineQuery.search,
      });

      setTimelinePage(result.page);
      setTimelineHasMore(result.has_more);
      setTimelineEvents((previous) => (reset ? result.items : [...previous, ...result.items]));
      return result;
    } finally {
      setTimelineLoading(false);
    }
  }

  async function refreshAll() {
    const list = await getValidationRuns();
    setRuns(list.items);

    const running = list.items.find((item) => item.status === "RUNNING") ?? null;
    if (running) {
      const [detail, metrics, events] = await Promise.all([
        getValidationRun(running.validation_run_id),
        getValidationRunMetrics(running.validation_run_id),
        getValidationRunEvents(running.validation_run_id, {
          page: 1,
          pageSize: 30,
          order: timelineQuery.order,
          window: timelineQuery.window,
          category: timelineQuery.category,
          search: timelineQuery.search,
        }),
      ]);
      setActiveDetail(detail);
      setActiveMetrics(metrics);
      setTimelineEvents(events.items);
      setTimelinePage(events.page);
      setTimelineHasMore(events.has_more);
    } else {
      setActiveDetail(null);
      setActiveMetrics(null);
      setTimelineEvents([]);
      setTimelinePage(1);
      setTimelineHasMore(false);
    }
  }

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        await refreshAll();
      } catch (fetchError) {
        if (active) {
          setError(errorMessage(fetchError, "Failed to load validation runs."));
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

  useEffect(() => {
    if (!selectedTimelineRunId) {
      return;
    }

    setError(null);
    void loadTimeline(selectedTimelineRunId, true).catch((timelineError) => {
      setError(errorMessage(timelineError, "Failed to load validation run timeline."));
    });
  }, [selectedTimelineRunId, timelineQuery]);

  useEffect(() => {
    const intervalMs = activeRun ? 5000 : 15000;
    const timer = window.setInterval(() => {
      setError(null);
      void refreshAll().catch((refreshError) => {
        setError(errorMessage(refreshError, "Failed to refresh validation runs."));
      });

      if (activeRun && selectedTimelineRunId) {
        void loadTimeline(selectedTimelineRunId, true).catch((timelineError) => {
          setError(errorMessage(timelineError, "Failed to refresh timeline events."));
        });
      }
    }, intervalMs);

    return () => {
      window.clearInterval(timer);
    };
  }, [activeRun, selectedTimelineRunId, timelineQuery]);

  const effectiveDuration = durationPreset === "custom" ? Number(customDuration) : Number(durationPreset);

  async function handleStartValidationRun() {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createValidationRun({
        name: name.trim(),
        objective: objective.trim(),
        duration_hours: effectiveDuration,
        paper_capital: paperCapital,
        enabled_strategies: enabledStrategies,
        enabled_research_agents: enabledAgents,
        enabled_research_features: enabledFeatures,
      });
      await startValidationRun(created.validation_run_id);
      await refreshAll();
    } catch (submitError) {
      setError(errorMessage(submitError, "Failed to start validation run."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCancelRun(runId: string) {
    setSubmitting(true);
    setError(null);
    try {
      await cancelValidationRun(runId);
      await refreshAll();
    } catch (cancelError) {
      setError(errorMessage(cancelError, "Failed to cancel validation run."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggleDetail(runId: string) {
    if (expandedRunId === runId) {
      setExpandedRunId(null);
      return;
    }

    setExpandedRunId(runId);
    try {
      const detail = await getValidationRun(runId);
      setActiveDetail(detail);
      await loadTimeline(runId, true);
      if (activeRun && activeRun.validation_run_id === runId) {
        const metrics = await getValidationRunMetrics(runId);
        setActiveMetrics(metrics);
      }
    } catch (detailError) {
      setError(errorMessage(detailError, "Failed to load validation run details."));
    }
  }

  function toggleSelection(values: string[], value: string): string[] {
    if (values.includes(value)) {
      return values.filter((item) => item !== value);
    }
    return [...values, value];
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Validation Runs</h1>
        <p className="max-w-3xl text-sm text-foreground/75">
          Controlled paper-mode experiment management for proving runs. No live trading, no strategy promotion, no AI behavior changes.
        </p>
      </header>

      {error ? (
        <section className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-100" role="alert">
          {error}
        </section>
      ) : null}

      <section className="rounded-lg border border-border bg-muted/30 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">New Validation Run</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Name</span>
            <input
              className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Paper capital</span>
            <input
              className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
              value={paperCapital}
              onChange={(event) => setPaperCapital(event.target.value)}
            />
            <span className="mt-1 block text-xs text-foreground/65">Default proving capital is $25 in Small Account Mode.</span>
          </label>

          <label className="text-sm md:col-span-2">
            <span className="mb-1 block text-foreground/80">Objective</span>
            <textarea
              className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
            />
          </label>

          <div className="space-y-2 text-sm md:col-span-2">
            <p className="text-foreground/80">Duration</p>
            <div className="flex flex-wrap gap-3">
              {DURATION_OPTIONS.map((hours) => (
                <label key={hours} className="inline-flex items-center gap-2">
                  <input
                    type="radio"
                    name="duration"
                    checked={durationPreset === String(hours)}
                    onChange={() => setDurationPreset(String(hours))}
                  />
                  <span>{hours === 168 ? "7 days" : `${hours} hours`}</span>
                </label>
              ))}
              <label className="inline-flex items-center gap-2">
                <input
                  type="radio"
                  name="duration"
                  checked={durationPreset === "custom"}
                  onChange={() => setDurationPreset("custom")}
                />
                <span>Custom hours</span>
              </label>
              {durationPreset === "custom" ? (
                <input
                  className="w-32 rounded-md border border-border bg-background/60 px-3 py-1"
                  value={customDuration}
                  onChange={(event) => setCustomDuration(event.target.value)}
                />
              ) : null}
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-3 md:col-span-2">
            <fieldset className="rounded-md border border-border bg-background/40 p-3">
              <legend className="px-1 text-xs uppercase tracking-wide text-foreground/70">Strategies</legend>
              {["MA Crossover", "RSI"].map((item) => (
                <label key={item} className="mt-2 flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={enabledStrategies.includes(item)}
                    onChange={() => setEnabledStrategies((prev) => toggleSelection(prev, item))}
                  />
                  <span>{item}</span>
                </label>
              ))}
            </fieldset>

            <fieldset className="rounded-md border border-border bg-background/40 p-3">
              <legend className="px-1 text-xs uppercase tracking-wide text-foreground/70">Research Agents</legend>
              {["Baseline", "OpenAI Sandbox"].map((item) => (
                <label key={item} className="mt-2 flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={enabledAgents.includes(item)}
                    onChange={() => setEnabledAgents((prev) => toggleSelection(prev, item))}
                  />
                  <span>{item}</span>
                </label>
              ))}
            </fieldset>

            <fieldset className="rounded-md border border-border bg-background/40 p-3">
              <legend className="px-1 text-xs uppercase tracking-wide text-foreground/70">Research Features</legend>
              {["Laboratory", "Evolution", "Tournament", "Capital Allocation"].map((item) => (
                <label key={item} className="mt-2 flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={enabledFeatures.includes(item)}
                    onChange={() => setEnabledFeatures((prev) => toggleSelection(prev, item))}
                  />
                  <span>{item}</span>
                </label>
              ))}
            </fieldset>
          </div>
        </div>

        <button
          className="mt-4 rounded-md border border-emerald-500/40 bg-emerald-500/20 px-4 py-2 text-sm font-medium text-emerald-100 disabled:opacity-50"
          onClick={() => void handleStartValidationRun()}
          disabled={submitting || effectiveDuration <= 0 || Number.isNaN(effectiveDuration)}
        >
          Start Validation Run
        </button>
      </section>

      <section className="rounded-lg border border-border bg-muted/30 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Active Validation Run</h2>
        {activeRun && activeMetrics ? (
          <div className="mt-3 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-lg font-semibold">{activeRun.name}</p>
              <span className={`rounded-full border px-2 py-1 text-xs font-medium ${statusClass(activeRun.status)}`}>
                {activeRun.status}
              </span>
            </div>

            <div className="h-3 overflow-hidden rounded-full border border-border bg-background/40">
              <div className="h-full bg-emerald-500/60" style={{ width: `${activeMetrics.elapsed_percentage}%` }} />
            </div>

            <div className="grid gap-3 md:grid-cols-4 text-sm">
              <div><p className="text-foreground/70">Elapsed</p><p>{activeMetrics.elapsed_percentage.toFixed(2)}%</p></div>
              <div><p className="text-foreground/70">Time remaining</p><p>{activeMetrics.time_remaining}</p></div>
              <div><p className="text-foreground/70">Current phase</p><p>{activeRun.status}</p></div>
              <div><p className="text-foreground/70">Health score</p><p>{activeRun.health_score ?? activeDetail?.overall_score ?? 0}</p></div>
              <div><p className="text-foreground/70">Current champion</p><p>{activeMetrics.current_champion ?? "None"}</p></div>
              <div><p className="text-foreground/70">Paper PnL</p><p>{formatCurrency(activeMetrics.paper_pnl_during_run)}</p></div>
              <div><p className="text-foreground/70">Alerts</p><p>{activeMetrics.alerts_count}</p></div>
            </div>

            <button
              className="rounded-md border border-rose-500/40 bg-rose-500/15 px-4 py-2 text-sm text-rose-100 disabled:opacity-50"
              onClick={() => void handleCancelRun(activeRun.validation_run_id)}
              disabled={submitting}
            >
              Cancel Run
            </button>
          </div>
        ) : (
          <p className="mt-3 rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/75">
            No active validation run.
          </p>
        )}
      </section>

      <section className="rounded-lg border border-border bg-muted/30 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Scorecard</h2>
        {activeDetail?.scorecards?.length ? (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {activeDetail.scorecards.map((item: ValidationRunScorecard) => (
              <article key={item.category} className="rounded-md border border-border bg-background/40 p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">{item.category}</p>
                  <span className={`text-sm font-semibold ${scoreClass(item.score)}`}>{item.score}</span>
                </div>
                <p className="mt-1 text-xs text-foreground/70">{item.status}</p>
                <p className="mt-2 text-xs text-foreground/75">{item.notes}</p>
              </article>
            ))}
          </div>
        ) : (
          <p className="mt-3 rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/75">No scorecard yet.</p>
        )}
      </section>

      <section className="rounded-lg border border-border bg-muted/30 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Validation Run History</h2>
        {loading ? (
          <p className="mt-3 text-sm text-foreground/75">Loading history...</p>
        ) : runs.length === 0 ? (
          <p className="mt-3 rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/75">No validation runs yet.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full border-collapse text-sm">
              <thead>
                <tr className="text-left text-foreground/70">
                  <th className="border-b border-border px-2 py-2">Run Name</th>
                  <th className="border-b border-border px-2 py-2">Duration</th>
                  <th className="border-b border-border px-2 py-2">Status</th>
                  <th className="border-b border-border px-2 py-2">Result</th>
                  <th className="border-b border-border px-2 py-2">Health</th>
                  <th className="border-b border-border px-2 py-2">Paper PnL</th>
                  <th className="border-b border-border px-2 py-2">Candidates Generated</th>
                  <th className="border-b border-border px-2 py-2">Started</th>
                  <th className="border-b border-border px-2 py-2">Completed</th>
                  <th className="border-b border-border px-2 py-2">Detail</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.validation_run_id} className="align-top">
                    <td className="border-b border-border px-2 py-2">{run.name}</td>
                    <td className="border-b border-border px-2 py-2">{run.duration_hours}h</td>
                    <td className="border-b border-border px-2 py-2">{run.status}</td>
                    <td className="border-b border-border px-2 py-2">{run.result_status}</td>
                    <td className="border-b border-border px-2 py-2">{run.health_score ?? "-"}</td>
                    <td className="border-b border-border px-2 py-2">{activeRun?.validation_run_id === run.validation_run_id && activeMetrics ? formatCurrency(activeMetrics.paper_pnl_during_run) : "-"}</td>
                    <td className="border-b border-border px-2 py-2">{activeRun?.validation_run_id === run.validation_run_id && activeMetrics ? activeMetrics.candidates_generated : "-"}</td>
                    <td className="border-b border-border px-2 py-2">{formatTime(run.started_at)}</td>
                    <td className="border-b border-border px-2 py-2">{formatTime(run.completed_at)}</td>
                    <td className="border-b border-border px-2 py-2">
                      <button
                        className="rounded border border-border bg-background/60 px-2 py-1 text-xs"
                        onClick={() => void handleToggleDetail(run.validation_run_id)}
                      >
                        {expandedRunId === run.validation_run_id ? "Hide" : "View"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ValidationRunTimeline
        title="Validation Timeline"
        events={timelineEvents}
        query={timelineQuery}
        loading={timelineLoading}
        hasMore={timelineHasMore}
        emptyMessage={selectedTimelineRunId ? "No timeline events for this run yet." : "Select a validation run to view timeline."}
        onQueryChange={(next) => setTimelineQuery(next)}
        onLoadMore={() => {
          if (!selectedTimelineRunId) {
            return;
          }
          void loadTimeline(selectedTimelineRunId, false).catch((timelineError) => {
            setError(errorMessage(timelineError, "Failed to load more timeline events."));
          });
        }}
      />

      {expandedRunId && activeDetail ? (
        <section className="rounded-lg border border-border bg-muted/30 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Validation Run Detail</h2>
          <article className="mt-3 rounded-md border border-border bg-background/40 p-3">
            <h3 className="text-xs uppercase tracking-wide text-foreground/70">Final Result Summary</h3>
            <p className="mt-2 text-sm">Result: {activeDetail.result_status}</p>
            <p className="mt-1 text-sm">Overall score: {activeDetail.overall_score}</p>
            <p className="mt-1 text-sm">Status: {activeDetail.status}</p>
            <p className="mt-1 text-sm">Objective: {activeDetail.objective}</p>
          </article>
        </section>
      ) : null}
    </div>
  );
}
