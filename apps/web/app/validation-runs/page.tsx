"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

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
import { useStablePolling } from "@/lib/useStablePolling";

type AccordionKey = "new" | "active" | "scorecard" | "history" | "timeline";

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
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-muted/30">
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
  const [activeDetail, setActiveDetail] = useState<ValidationRunDetail | null>(null);
  const [metricsByRunId, setMetricsByRunId] = useState<Record<string, ValidationRunMetrics>>({});
  const [timelineEvents, setTimelineEvents] = useState<ValidationRunEvent[]>([]);
  const [timelinePage, setTimelinePage] = useState(1);
  const [timelineHasMore, setTimelineHasMore] = useState(false);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineQuery, setTimelineQuery] = useState<TimelineQuery>({
    order: "newest",
    window: "entire_run",
    category: "all",
    severity: "all",
    search: "",
  });
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [openSections, setOpenSections] = useState<Record<AccordionKey, boolean>>({
    new: false,
    active: true,
    scorecard: false,
    history: false,
    timeline: false,
  });

  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const activeRunPanelRef = useRef<HTMLDivElement | null>(null);
  const selectedRunIdRef = useRef<string | null>(null);

  const activeRuns = useMemo(() => runs.filter((item) => item.status === "RUNNING"), [runs]);
  const historyRuns = useMemo(() => runs.filter((item) => item.status !== "RUNNING"), [runs]);
  const selectedRun = useMemo(() => {
    if (!selectedRunId) {
      return null;
    }
    return runs.find((item) => item.validation_run_id === selectedRunId) ?? null;
  }, [runs, selectedRunId]);

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId;
  }, [selectedRunId]);

  const loadTimeline = useCallback(async (
    runId: string,
    options: { reset: boolean; silent?: boolean },
  ): Promise<ValidationRunEventListResponse> => {
    const { reset, silent = false } = options;
    if (!silent) {
      setTimelineLoading(true);
    }
    try {
      const page = reset ? 1 : timelinePage + 1;
      const result = await getValidationRunEvents(runId, {
        page,
        pageSize: 30,
        order: timelineQuery.order,
        window: timelineQuery.window,
        category: timelineQuery.category,
        severity: timelineQuery.severity,
        search: timelineQuery.search,
      });

      setTimelinePage(result.page);
      setTimelineHasMore(result.has_more);
      setTimelineEvents((previous) => (reset ? result.items : [...previous, ...result.items]));
      return result;
    } finally {
      if (!silent) {
        setTimelineLoading(false);
      }
    }
  }, [timelinePage, timelineQuery]);

  const refreshAll = useCallback(async () => {
    const list = await getValidationRuns();
    setRuns(list.items);

    const running = list.items.filter((item) => item.status === "RUNNING");
    const metricsResults = await Promise.all(
      running.map(async (item) => ({
        id: item.validation_run_id,
        metrics: await getValidationRunMetrics(item.validation_run_id),
      })),
    );
    const nextMetricsByRunId: Record<string, ValidationRunMetrics> = {};
    for (const row of metricsResults) {
      nextMetricsByRunId[row.id] = row.metrics;
    }
    setMetricsByRunId(nextMetricsByRunId);

    const currentSelectedRunId = selectedRunIdRef.current;
    const selectedRunStillExists = currentSelectedRunId
      ? list.items.find((item) => item.validation_run_id === currentSelectedRunId) ?? null
      : null;
    const selectedRunIsTerminal = selectedRunStillExists
      ? selectedRunStillExists.status === "COMPLETED" || selectedRunStillExists.status === "CANCELLED"
      : false;
    const preferredRunId =
      selectedRunStillExists && !selectedRunIsTerminal
        ? currentSelectedRunId
        : (running[0]?.validation_run_id ?? list.items[0]?.validation_run_id ?? null);
    setSelectedRunId(preferredRunId);

    if (preferredRunId) {
      const [detail, events] = await Promise.all([
        getValidationRun(preferredRunId),
        getValidationRunEvents(preferredRunId, {
          page: 1,
          pageSize: 30,
          order: timelineQuery.order,
          window: timelineQuery.window,
          category: timelineQuery.category,
          severity: timelineQuery.severity,
          search: timelineQuery.search,
        }),
      ]);
      setActiveDetail(detail);
      setTimelineEvents(events.items);
      setTimelinePage(events.page);
      setTimelineHasMore(events.has_more);
    } else {
      setActiveDetail(null);
      setTimelineEvents([]);
      setTimelinePage(1);
      setTimelineHasMore(false);
    }
  }, [timelineQuery]);

  const pollValidationRuns = useCallback(async () => {
    await refreshAll();
    return true;
  }, [refreshAll]);

  const polling = useStablePolling(pollValidationRuns, { intervalMs: activeRuns.length > 0 ? 5000 : 15000, enabled: true });

  useEffect(() => {
    setLoading(polling.initialLoading);
    if (polling.error) {
      setError(errorMessage(new Error(polling.error), "Failed to refresh validation runs."));
    }
  }, [polling.error, polling.initialLoading]);

  useEffect(() => {
    if (!selectedRunId) {
      return;
    }

    setError(null);
    void loadTimeline(selectedRunId, { reset: true }).catch((timelineError) => {
      setError(errorMessage(timelineError, "Failed to load validation run timeline."));
    });
  }, [loadTimeline, selectedRunId, timelineQuery]);

  const effectiveDuration = durationPreset === "custom" ? Number(customDuration) : Number(durationPreset);

  function scrollToActiveRunPanel() {
    activeRunPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function handleStartValidationRun() {
    setStarting(true);
    setError(null);
    setSuccess(null);
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
      setSuccess("Validation run started successfully.");
      setOpenSections((previous) => ({ ...previous, active: true }));
      scrollToActiveRunPanel();
    } catch (submitError) {
      setError(errorMessage(submitError, "Failed to start validation run."));
    } finally {
      setStarting(false);
    }
  }

  async function handleCancelRun(runId: string) {
    setCancelling(true);
    setError(null);
    setSuccess(null);
    try {
      await cancelValidationRun(runId);
      await refreshAll();
    } catch (cancelError) {
      setError(errorMessage(cancelError, "Failed to cancel validation run."));
    } finally {
      setCancelling(false);
    }
  }

  async function handleSelectRun(runId: string) {
    selectedRunIdRef.current = runId;
    setSelectedRunId(runId);
    try {
      const detail = await getValidationRun(runId);
      setActiveDetail(detail);
      await loadTimeline(runId, { reset: true });
      if (!metricsByRunId[runId]) {
        const metrics = await getValidationRunMetrics(runId);
        setMetricsByRunId((previous) => ({ ...previous, [runId]: metrics }));
      }
    } catch (detailError) {
      setError(errorMessage(detailError, "Failed to load validation run details."));
    }
  }

  function toggleAccordion(section: AccordionKey) {
    setOpenSections((previous) => ({
      ...previous,
      [section]: !previous[section],
    }));
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

      {success ? (
        <section className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-100" role="status">
          {success}
        </section>
      ) : null}

      <AccordionSection id="new" title="New Validation Run" open={openSections.new} onToggle={toggleAccordion}>
        <div className="grid gap-3 md:grid-cols-2">
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
            <span className="mt-1 block text-xs text-foreground/65">
              Default proving capital is $25 in Small Account Mode. 
              <Link href="/capital" className="font-medium text-cyan-300 hover:text-cyan-200">Open Capital Ledger</Link>
            </span>
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

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            className="rounded-md border border-emerald-500/40 bg-emerald-500/20 px-4 py-2 text-sm font-medium text-emerald-100 disabled:opacity-50"
            onClick={() => void handleStartValidationRun()}
            disabled={starting || effectiveDuration <= 0 || Number.isNaN(effectiveDuration)}
          >
            {starting ? "Starting..." : "Start Validation Run"}
          </button>
          {activeRuns.length > 0 ? (
            <button
              type="button"
              className="rounded-md border border-sky-500/40 bg-sky-500/15 px-3 py-2 text-sm text-sky-100"
              onClick={() => {
                setOpenSections((previous) => ({ ...previous, active: true }));
                scrollToActiveRunPanel();
              }}
            >
              View active run
            </button>
          ) : null}
        </div>
      </AccordionSection>

      <AccordionSection id="active" title="Active Validation Runs" count={activeRuns.length} open={openSections.active} onToggle={toggleAccordion}>
        <div ref={activeRunPanelRef} className="space-y-3">
          {activeRuns.length === 0 ? (
            <p className="rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/75">No active validation runs.</p>
          ) : (
            activeRuns.map((run) => {
              const metrics = metricsByRunId[run.validation_run_id] ?? null;
              const selected = selectedRunId === run.validation_run_id;
              return (
                <article
                  key={run.validation_run_id}
                  className={`rounded-md border p-3 ${selected ? "border-sky-400/55 bg-sky-500/10" : "border-border bg-background/45"}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold">{run.name}</p>
                      <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${statusClass(run.status)}`}>{run.status}</span>
                    </div>
                    <button
                      type="button"
                      className="rounded border border-border bg-background/60 px-2 py-1 text-xs"
                      onClick={() => void handleSelectRun(run.validation_run_id)}
                    >
                      {selected ? "Selected" : "Select run"}
                    </button>
                  </div>

                  <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                    <p>Duration: {run.duration_hours}h</p>
                    <p>Progress: {metrics ? `${metrics.elapsed_percentage.toFixed(2)}%` : "--"}</p>
                    <p>Time remaining: {metrics?.time_remaining ?? "--"}</p>
                    <p>Health score: {run.health_score ?? activeDetail?.overall_score ?? 0}</p>
                    <p>
                      Paper capital: {formatCurrency(run.paper_capital)}{" "}
                      <Link href="/capital" className="font-medium text-cyan-300 hover:text-cyan-200">
                        View Ledger
                      </Link>
                    </p>
                    <p>Paper PnL: {metrics ? formatCurrency(metrics.paper_pnl_during_run) : "--"}</p>
                    <p>Alerts: {metrics?.alerts_count ?? 0}</p>
                  </div>

                  <div className="mt-3">
                    <button
                      className="rounded-md border border-rose-500/40 bg-rose-500/15 px-3 py-1.5 text-xs text-rose-100 disabled:opacity-50"
                      onClick={() => void handleCancelRun(run.validation_run_id)}
                      disabled={cancelling}
                    >
                      Cancel Run
                    </button>
                  </div>
                </article>
              );
            })
          )}
        </div>
      </AccordionSection>

      <AccordionSection id="scorecard" title="Scorecard" open={openSections.scorecard} onToggle={toggleAccordion}>
        {!selectedRunId ? (
          <p className="rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/75">Select a validation run to view scorecard.</p>
        ) : activeDetail?.scorecards?.length ? (
          <div className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
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
            <article className="rounded-md border border-border bg-background/40 p-3">
              <p className="text-xs uppercase tracking-wide text-foreground/70">Selected Run Detail</p>
              <p className="mt-2 text-sm">Result: {activeDetail.result_status}</p>
              <p className="mt-1 text-sm">Overall score: {activeDetail.overall_score}</p>
              <p className="mt-1 text-sm">Status: {activeDetail.status}</p>
              <p className="mt-1 text-sm">Objective: {activeDetail.objective}</p>
            </article>
          </div>
        ) : (
          <p className="rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/75">No scorecard yet.</p>
        )}
      </AccordionSection>

      <AccordionSection id="history" title="Validation Run History" count={historyRuns.length} open={openSections.history} onToggle={toggleAccordion}>
        {loading ? (
          <p className="text-sm text-foreground/75">Loading history...</p>
        ) : historyRuns.length === 0 ? (
          <p className="rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/75">No validation run history yet.</p>
        ) : (
          <div className="space-y-2">
            {historyRuns.map((run) => (
              <article key={run.validation_run_id} className="rounded-md border border-border bg-background/40 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold">{run.name}</p>
                    <p className="text-xs text-foreground/70">{run.duration_hours}h • {run.result_status}</p>
                  </div>
                  <button
                    type="button"
                    className="rounded border border-border bg-background/60 px-2 py-1 text-xs"
                    onClick={() => void handleSelectRun(run.validation_run_id)}
                  >
                    {selectedRunId === run.validation_run_id ? "Selected" : "Select run"}
                  </button>
                </div>
                <p className="mt-2 text-xs text-foreground/70">Started: {formatTime(run.started_at)}</p>
                <p className="mt-1 text-xs text-foreground/70">Completed: {formatTime(run.completed_at)}</p>
              </article>
            ))}
          </div>
        )}
      </AccordionSection>

      <AccordionSection id="timeline" title="Validation Timeline" count={timelineEvents.length} open={openSections.timeline} onToggle={toggleAccordion}>
        <ValidationRunTimeline
          title="Validation Timeline"
          events={timelineEvents}
          query={timelineQuery}
          loading={timelineLoading}
          hasMore={timelineHasMore}
          emptyMessage={selectedRunId ? "No timeline events for this run yet." : "Select a validation run to view timeline."}
          onQueryChange={(next) => setTimelineQuery(next)}
          onLoadMore={() => {
            if (!selectedRunId) {
              return;
            }
            void loadTimeline(selectedRunId, { reset: false }).catch((timelineError) => {
              setError(errorMessage(timelineError, "Failed to load more timeline events."));
            });
          }}
        />
      </AccordionSection>
    </div>
  );
}
