"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  getDecisionCounterfactualDetail,
  getDecisionCounterfactuals,
  getDecisionExplainability,
  getDecisionQuality,
  getDecisionRecommendations,
  getDecisionTimeline,
  type CounterfactualDetail,
  type CounterfactualListItem,
  type DecisionExplainability,
  type DecisionQualityItem,
  type DecisionReadFilters,
  type DecisionRecommendationItem,
  type KnownState,
  type PaginatedResponse,
  type TimelineItem,
  type TimelineStateField,
} from "@/lib/api/decisions";

type FilterDraft = {
  account_id: string;
  portfolio_id: string;
  strategy_id: string;
  asset_id: string;
  status: string;
  start_time: string;
  end_time: string;
  page_size: string;
};

type LoadingState = {
  timeline: boolean;
  explainability: boolean;
  counterfactuals: boolean;
  quality: boolean;
  recommendations: boolean;
  counterfactualDetail: boolean;
};

type ErrorState = {
  timeline: string | null;
  explainability: string | null;
  counterfactuals: string | null;
  quality: string | null;
  recommendations: string | null;
  counterfactualDetail: string | null;
};

const STATUS_OPTIONS = ["", "approved", "resized", "rejected", "wait", "unknown"] as const;

const INITIAL_FILTER_DRAFT: FilterDraft = {
  account_id: "",
  portfolio_id: "",
  strategy_id: "",
  asset_id: "",
  status: "",
  start_time: "",
  end_time: "",
  page_size: "20",
};

const INITIAL_LOADING: LoadingState = {
  timeline: false,
  explainability: false,
  counterfactuals: false,
  quality: false,
  recommendations: false,
  counterfactualDetail: false,
};

const INITIAL_ERRORS: ErrorState = {
  timeline: null,
  explainability: null,
  counterfactuals: null,
  quality: null,
  recommendations: null,
  counterfactualDetail: null,
};

function toApiErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

function toIsoFromDateTimeLocal(value: string): string | undefined {
  if (!value.trim()) {
    return undefined;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return undefined;
  }

  return date.toISOString();
}

function toLocalDateTimeInput(value: string | null | undefined): string {
  if (!value) {
    return "Not available";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }

  return parsed.toLocaleString();
}

function parsePageSize(value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return 20;
  }
  return Math.min(200, Math.floor(parsed));
}

function compactJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function stateBadgeClass(state: KnownState): string {
  if (state === "known") {
    return "bg-emerald-500/20 text-emerald-200 border-emerald-400/40";
  }
  if (state === "unknown") {
    return "bg-amber-500/20 text-amber-200 border-amber-400/40";
  }
  return "bg-slate-500/20 text-slate-200 border-slate-400/40";
}

function AvailabilityBadge({ state, reason }: { state: KnownState; reason?: string | null }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${stateBadgeClass(state)}`}
      title={reason ?? state}
    >
      {state}
    </span>
  );
}

function TimelineStateValue({ value }: { value: TimelineStateField }) {
  if (value.state === "known") {
    return <span className="font-mono text-xs text-foreground/90">{value.value}</span>;
  }

  return (
    <span className="inline-flex items-center gap-2">
      <AvailabilityBadge state={value.state} reason={null} />
    </span>
  );
}

function buildFilters(draft: FilterDraft, page: number): DecisionReadFilters {
  return {
    account_id: draft.account_id || undefined,
    portfolio_id: draft.portfolio_id || undefined,
    strategy_id: draft.strategy_id || undefined,
    asset_id: draft.asset_id || undefined,
    status: draft.status || undefined,
    start_time: toIsoFromDateTimeLocal(draft.start_time),
    end_time: toIsoFromDateTimeLocal(draft.end_time),
    page,
    page_size: parsePageSize(draft.page_size),
  };
}

function PaginationControls({
  label,
  page,
  pageSize,
  total,
  onPageChange,
  disabled,
}: {
  label: string;
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (next: number) => void;
  disabled?: boolean;
}) {
  const maxPage = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-foreground/70">
      <p aria-live="polite">
        {label}: page {page} of {maxPage} ({total} total)
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded-md border border-border px-2 py-1 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={disabled || page <= 1}
          aria-label={`${label} previous page`}
        >
          Previous
        </button>
        <button
          type="button"
          className="rounded-md border border-border px-2 py-1 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => onPageChange(Math.min(maxPage, page + 1))}
          disabled={disabled || page >= maxPage}
          aria-label={`${label} next page`}
        >
          Next
        </button>
      </div>
    </div>
  );
}

function DecisionIntelligenceFilters({
  draft,
  onChange,
  onApply,
  disabled,
}: {
  draft: FilterDraft;
  onChange: (next: FilterDraft) => void;
  onApply: () => void;
  disabled: boolean;
}) {
  return (
    <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-labelledby="filters-heading">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="filters-heading" className="text-lg font-semibold">
            Read-only Filters
          </h2>
          <p className="mt-1 text-xs text-foreground/70">
            Filters map directly to Decision Intelligence API query parameters and do not mutate platform state.
          </p>
        </div>
        <button
          type="button"
          onClick={onApply}
          disabled={disabled}
          className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm font-medium hover:bg-background/80 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Apply Filters
        </button>
      </div>

      <fieldset className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <legend className="sr-only">Decision query filters</legend>

        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Account ID</span>
          <input
            value={draft.account_id}
            onChange={(event) => onChange({ ...draft, account_id: event.target.value })}
            placeholder="uuid"
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Portfolio ID</span>
          <input
            value={draft.portfolio_id}
            onChange={(event) => onChange({ ...draft, portfolio_id: event.target.value })}
            placeholder="uuid"
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Strategy ID</span>
          <input
            value={draft.strategy_id}
            onChange={(event) => onChange({ ...draft, strategy_id: event.target.value })}
            placeholder="uuid"
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Asset ID</span>
          <input
            value={draft.asset_id}
            onChange={(event) => onChange({ ...draft, asset_id: event.target.value })}
            placeholder="uuid"
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Status</span>
          <select
            value={draft.status}
            onChange={(event) => onChange({ ...draft, status: event.target.value })}
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option || "all"}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Start Time</span>
          <input
            type="datetime-local"
            value={draft.start_time}
            onChange={(event) => onChange({ ...draft, start_time: event.target.value })}
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">End Time</span>
          <input
            type="datetime-local"
            value={draft.end_time}
            onChange={(event) => onChange({ ...draft, end_time: event.target.value })}
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
          />
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-foreground/80">Page Size</span>
          <input
            type="number"
            min={1}
            max={200}
            value={draft.page_size}
            onChange={(event) => onChange({ ...draft, page_size: event.target.value })}
            className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
          />
        </label>
      </fieldset>
    </section>
  );
}

export default function DecisionIntelligencePage() {
  const [filterDraft, setFilterDraft] = useState<FilterDraft>(INITIAL_FILTER_DRAFT);

  const [timelinePage, setTimelinePage] = useState(1);
  const [counterfactualPage, setCounterfactualPage] = useState(1);
  const [qualityPage, setQualityPage] = useState(1);
  const [recommendationPage, setRecommendationPage] = useState(1);

  const [timeline, setTimeline] = useState<PaginatedResponse<TimelineItem> | null>(null);
  const [counterfactuals, setCounterfactuals] = useState<PaginatedResponse<CounterfactualListItem> | null>(null);
  const [quality, setQuality] = useState<PaginatedResponse<DecisionQualityItem> | null>(null);
  const [recommendations, setRecommendations] = useState<PaginatedResponse<DecisionRecommendationItem> | null>(null);

  const [selectedDecisionId, setSelectedDecisionId] = useState<string | null>(null);
  const [explainability, setExplainability] = useState<DecisionExplainability | null>(null);
  const [counterfactualDetail, setCounterfactualDetail] = useState<CounterfactualDetail | null>(null);

  const [loading, setLoading] = useState<LoadingState>(INITIAL_LOADING);
  const [errors, setErrors] = useState<ErrorState>(INITIAL_ERRORS);

  const activePageSize = parsePageSize(filterDraft.page_size);

  const commonFilterPayload = useMemo(() => {
    return {
      account_id: filterDraft.account_id || undefined,
      portfolio_id: filterDraft.portfolio_id || undefined,
      strategy_id: filterDraft.strategy_id || undefined,
      asset_id: filterDraft.asset_id || undefined,
      status: filterDraft.status || undefined,
      start_time: toIsoFromDateTimeLocal(filterDraft.start_time),
      end_time: toIsoFromDateTimeLocal(filterDraft.end_time),
      page_size: activePageSize,
    };
  }, [activePageSize, filterDraft]);

  const loadTimeline = useCallback(async (page: number) => {
    setLoading((current) => ({ ...current, timeline: true }));
    setErrors((current) => ({ ...current, timeline: null }));

    try {
      const payload = await getDecisionTimeline({
        ...commonFilterPayload,
        page,
      });
      setTimeline(payload);
      if (!selectedDecisionId && payload.items.length > 0) {
        setSelectedDecisionId(payload.items[0].decision_id);
      }
    } catch (error) {
      setErrors((current) => ({
        ...current,
        timeline: toApiErrorMessage(error, "Failed to load decision timeline."),
      }));
      setTimeline(null);
    } finally {
      setLoading((current) => ({ ...current, timeline: false }));
    }
  }, [commonFilterPayload, selectedDecisionId]);

  const loadCounterfactuals = useCallback(async (page: number) => {
    setLoading((current) => ({ ...current, counterfactuals: true }));
    setErrors((current) => ({ ...current, counterfactuals: null }));

    try {
      const payload = await getDecisionCounterfactuals({
        ...commonFilterPayload,
        page,
      });
      setCounterfactuals(payload);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        counterfactuals: toApiErrorMessage(error, "Failed to load counterfactual outcomes."),
      }));
      setCounterfactuals(null);
    } finally {
      setLoading((current) => ({ ...current, counterfactuals: false }));
    }
  }, [commonFilterPayload]);

  const loadQuality = useCallback(async (page: number) => {
    setLoading((current) => ({ ...current, quality: true }));
    setErrors((current) => ({ ...current, quality: null }));

    try {
      const payload = await getDecisionQuality({
        ...commonFilterPayload,
        page,
      });
      setQuality(payload);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        quality: toApiErrorMessage(error, "Failed to load decision quality."),
      }));
      setQuality(null);
    } finally {
      setLoading((current) => ({ ...current, quality: false }));
    }
  }, [commonFilterPayload]);

  const loadRecommendations = useCallback(async (page: number) => {
    setLoading((current) => ({ ...current, recommendations: true }));
    setErrors((current) => ({ ...current, recommendations: null }));

    try {
      const payload = await getDecisionRecommendations({
        ...commonFilterPayload,
        page,
      });
      setRecommendations(payload);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        recommendations: toApiErrorMessage(error, "Failed to load recommendations."),
      }));
      setRecommendations(null);
    } finally {
      setLoading((current) => ({ ...current, recommendations: false }));
    }
  }, [commonFilterPayload]);

  const loadExplainability = useCallback(async (decisionId: string) => {
    setLoading((current) => ({ ...current, explainability: true }));
    setErrors((current) => ({ ...current, explainability: null }));

    try {
      const payload = await getDecisionExplainability(decisionId);
      setExplainability(payload);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        explainability: toApiErrorMessage(error, "Failed to load explainability view."),
      }));
      setExplainability(null);
    } finally {
      setLoading((current) => ({ ...current, explainability: false }));
    }
  }, []);

  const loadCounterfactualDetail = useCallback(async (decisionId: string) => {
    setLoading((current) => ({ ...current, counterfactualDetail: true }));
    setErrors((current) => ({ ...current, counterfactualDetail: null }));

    try {
      const payload = await getDecisionCounterfactualDetail(decisionId);
      setCounterfactualDetail(payload);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        counterfactualDetail: toApiErrorMessage(error, "Failed to load decision counterfactual detail."),
      }));
      setCounterfactualDetail(null);
    } finally {
      setLoading((current) => ({ ...current, counterfactualDetail: false }));
    }
  }, []);

  const loadAllReadModels = useCallback(async () => {
    await Promise.all([
      loadTimeline(timelinePage),
      loadCounterfactuals(counterfactualPage),
      loadQuality(qualityPage),
      loadRecommendations(recommendationPage),
    ]);
  }, [counterfactualPage, loadCounterfactuals, loadQuality, loadRecommendations, loadTimeline, qualityPage, recommendationPage, timelinePage]);

  useEffect(() => {
    void loadAllReadModels();
  }, [loadAllReadModels]);

  useEffect(() => {
    if (!selectedDecisionId) {
      setExplainability(null);
      setCounterfactualDetail(null);
      return;
    }

    void Promise.all([loadExplainability(selectedDecisionId), loadCounterfactualDetail(selectedDecisionId)]);
  }, [loadCounterfactualDetail, loadExplainability, selectedDecisionId]);

  const onApplyFilters = useCallback(() => {
    setTimelinePage(1);
    setCounterfactualPage(1);
    setQualityPage(1);
    setRecommendationPage(1);

    void Promise.all([
      loadTimeline(1),
      loadCounterfactuals(1),
      loadQuality(1),
      loadRecommendations(1),
    ]);
  }, [loadCounterfactuals, loadQuality, loadRecommendations, loadTimeline]);

  const onSelectDecision = useCallback((decisionId: string) => {
    setSelectedDecisionId(decisionId);
  }, []);

  useEffect(() => {
    void loadTimeline(timelinePage);
  }, [loadTimeline, timelinePage]);

  useEffect(() => {
    void loadCounterfactuals(counterfactualPage);
  }, [counterfactualPage, loadCounterfactuals]);

  useEffect(() => {
    void loadQuality(qualityPage);
  }, [loadQuality, qualityPage]);

  useEffect(() => {
    void loadRecommendations(recommendationPage);
  }, [loadRecommendations, recommendationPage]);

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5">
        <h1 className="text-2xl font-semibold">Decision Intelligence Dashboard</h1>
        <p className="mt-2 text-sm text-foreground/80">
          Read-only Phase 7 explorer for timeline, explainability, counterfactuals, quality, and recommendations.
        </p>
        <p className="mt-1 text-xs text-foreground/65">
          Observational only: no execution controls, no risk controls, no strategy editing, and no recommendation approval actions.
        </p>
      </section>

      <DecisionIntelligenceFilters
        draft={filterDraft}
        onChange={setFilterDraft}
        onApply={onApplyFilters}
        disabled={Object.values(loading).some(Boolean)}
      />

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-labelledby="timeline-heading">
        <h2 id="timeline-heading" className="text-lg font-semibold">
          Decision Timeline
        </h2>
        <p className="mt-1 text-xs text-foreground/70">Select a decision row to load explainability and per-decision counterfactual detail.</p>

        {errors.timeline ? <p className="mt-3 text-sm text-red-300" role="alert">{errors.timeline}</p> : null}

        <div className="mt-3 overflow-x-auto rounded-lg border border-border">
          <table className="min-w-full text-left text-sm">
            <caption className="sr-only">Decision timeline results</caption>
            <thead className="bg-background/60 text-xs uppercase tracking-wide text-foreground/70">
              <tr>
                <th scope="col" className="px-3 py-2">Time</th>
                <th scope="col" className="px-3 py-2">Status</th>
                <th scope="col" className="px-3 py-2">Account</th>
                <th scope="col" className="px-3 py-2">Asset</th>
                <th scope="col" className="px-3 py-2">Strategy</th>
                <th scope="col" className="px-3 py-2">Narrative</th>
              </tr>
            </thead>
            <tbody>
              {timeline?.items.map((item) => {
                const selected = item.decision_id === selectedDecisionId;
                return (
                  <tr
                    key={item.decision_id}
                    className={`border-t border-border ${selected ? "bg-blue-500/10" : "bg-transparent"}`}
                  >
                    <td className="px-3 py-2 align-top">
                      <button
                        type="button"
                        onClick={() => onSelectDecision(item.decision_id)}
                        className="rounded border border-border px-2 py-1 text-left font-mono text-xs hover:bg-background/60"
                        aria-label={`Select decision ${item.decision_id}`}
                      >
                        {toLocalDateTimeInput(item.timestamp)}
                      </button>
                    </td>
                    <td className="px-3 py-2 align-top">{item.status}</td>
                    <td className="px-3 py-2 align-top"><TimelineStateValue value={item.account_id} /></td>
                    <td className="px-3 py-2 align-top"><TimelineStateValue value={item.asset_id} /></td>
                    <td className="px-3 py-2 align-top"><TimelineStateValue value={item.strategy_id} /></td>
                    <td className="max-w-xl px-3 py-2 align-top text-foreground/85">{item.narrative}</td>
                  </tr>
                );
              })}
              {!loading.timeline && (!timeline || timeline.items.length === 0) ? (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-sm text-foreground/70">
                    No decisions found for current filters.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        {loading.timeline ? <p className="mt-3 text-xs text-foreground/70" aria-live="polite">Loading timeline...</p> : null}

        <PaginationControls
          label="Timeline"
          page={timelinePage}
          pageSize={timeline?.page_size ?? activePageSize}
          total={timeline?.total ?? 0}
          onPageChange={setTimelinePage}
          disabled={loading.timeline}
        />
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-labelledby="explainability-heading">
          <h2 id="explainability-heading" className="text-lg font-semibold">Explainability View</h2>
          <p className="mt-1 text-xs text-foreground/70">Decision-level supporting, opposing, confidence, and risk-adjustment evidence.</p>

          {selectedDecisionId ? <p className="mt-2 font-mono text-xs text-foreground/75">Decision: {selectedDecisionId}</p> : null}
          {errors.explainability ? <p className="mt-3 text-sm text-red-300" role="alert">{errors.explainability}</p> : null}
          {loading.explainability ? <p className="mt-3 text-xs text-foreground/70" aria-live="polite">Loading explainability...</p> : null}

          {!loading.explainability && explainability ? (
            <div className="mt-4 space-y-4">
              <div className="rounded-md border border-border bg-background/40 p-3">
                <p className="text-sm"><span className="font-semibold">Status:</span> {explainability.decision_status}</p>
                <p className="mt-2 text-sm text-foreground/85">{explainability.explanation}</p>
              </div>

              {([
                ["Supporting", explainability.supporting_evidence],
                ["Opposing", explainability.opposing_evidence],
                ["Confidence", explainability.confidence_factors],
                ["Risk Adjustments", explainability.risk_adjustments],
              ] as Array<[string, typeof explainability.supporting_evidence]>).map(([label, items]) => (
                <div key={label} className="rounded-md border border-border bg-background/40 p-3">
                  <h3 className="text-sm font-semibold">{label}</h3>
                  <ul className="mt-2 space-y-2">
                    {items.map((item, index) => (
                      <li key={`${label}-${item.evidence_name}-${index}`} className="rounded border border-border bg-background/60 p-2">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs font-semibold">{item.evidence_name}</p>
                          <AvailabilityBadge state={item.availability_state} reason={item.state_reason} />
                        </div>
                        {item.state_reason ? <p className="mt-1 text-xs text-foreground/70">Reason: {item.state_reason}</p> : null}
                        <details className="mt-2">
                          <summary className="cursor-pointer text-xs text-foreground/80">View payload and provenance</summary>
                          <pre className="mt-2 overflow-auto rounded bg-background p-2 text-[11px] text-foreground/80">
                            {compactJson({ payload: item.evidence_payload, provenance: item.provenance })}
                          </pre>
                        </details>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : null}
        </section>

        <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-labelledby="counterfactual-detail-heading">
          <h2 id="counterfactual-detail-heading" className="text-lg font-semibold">Counterfactual Decision Detail</h2>
          <p className="mt-1 text-xs text-foreground/70">Per-decision detail preserves known or unavailable states without hidden defaults.</p>

          {errors.counterfactualDetail ? <p className="mt-3 text-sm text-red-300" role="alert">{errors.counterfactualDetail}</p> : null}
          {loading.counterfactualDetail ? <p className="mt-3 text-xs text-foreground/70" aria-live="polite">Loading decision counterfactual detail...</p> : null}

          {!loading.counterfactualDetail && counterfactualDetail ? (
            <div className="mt-3 space-y-3">
              <div className="flex items-center gap-2">
                <AvailabilityBadge state={counterfactualDetail.availability_state} reason={counterfactualDetail.state_reason} />
                {counterfactualDetail.state_reason ? (
                  <p className="text-xs text-foreground/75">Reason: {counterfactualDetail.state_reason}</p>
                ) : null}
              </div>

              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="min-w-full text-left text-sm">
                  <caption className="sr-only">Decision-level counterfactual detail</caption>
                  <thead className="bg-background/60 text-xs uppercase tracking-wide text-foreground/70">
                    <tr>
                      <th scope="col" className="px-3 py-2">Horizon</th>
                      <th scope="col" className="px-3 py-2">State</th>
                      <th scope="col" className="px-3 py-2">Actual</th>
                      <th scope="col" className="px-3 py-2">Best</th>
                      <th scope="col" className="px-3 py-2">Correct</th>
                    </tr>
                  </thead>
                  <tbody>
                    {counterfactualDetail.items.map((item) => (
                      <tr key={item.id} className="border-t border-border">
                        <td className="px-3 py-2">{item.horizon_label} ({item.horizon_minutes}m)</td>
                        <td className="px-3 py-2">{item.evaluation_state}</td>
                        <td className="px-3 py-2">{item.actual_action}</td>
                        <td className="px-3 py-2">{item.best_action ?? "n/a"}</td>
                        <td className="px-3 py-2">{String(item.actual_action_correct)}</td>
                      </tr>
                    ))}
                    {counterfactualDetail.items.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="px-3 py-6 text-center text-sm text-foreground/70">No detail items for this decision.</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </section>
      </div>

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-labelledby="counterfactual-list-heading">
        <h2 id="counterfactual-list-heading" className="text-lg font-semibold">Counterfactual Views</h2>
        <p className="mt-1 text-xs text-foreground/70">Cross-decision horizon outcomes with deterministic ordering and pagination.</p>

        {errors.counterfactuals ? <p className="mt-3 text-sm text-red-300" role="alert">{errors.counterfactuals}</p> : null}
        {loading.counterfactuals ? <p className="mt-3 text-xs text-foreground/70" aria-live="polite">Loading counterfactual outcomes...</p> : null}

        <div className="mt-3 overflow-x-auto rounded-lg border border-border">
          <table className="min-w-full text-left text-sm">
            <caption className="sr-only">Counterfactual results across decisions</caption>
            <thead className="bg-background/60 text-xs uppercase tracking-wide text-foreground/70">
              <tr>
                <th scope="col" className="px-3 py-2">Decision</th>
                <th scope="col" className="px-3 py-2">Horizon</th>
                <th scope="col" className="px-3 py-2">State</th>
                <th scope="col" className="px-3 py-2">Best</th>
                <th scope="col" className="px-3 py-2">Correct</th>
                <th scope="col" className="px-3 py-2">Returns (B/S/W)</th>
              </tr>
            </thead>
            <tbody>
              {counterfactuals?.items.map((item) => (
                <tr key={item.id} className="border-t border-border">
                  <td className="px-3 py-2 font-mono text-xs">{item.decision_id}</td>
                  <td className="px-3 py-2">{item.horizon_label} ({item.horizon_minutes}m)</td>
                  <td className="px-3 py-2">{item.evaluation_state}</td>
                  <td className="px-3 py-2">{item.best_action ?? "n/a"}</td>
                  <td className="px-3 py-2">{String(item.actual_action_correct)}</td>
                  <td className="px-3 py-2 font-mono text-xs">
                    {item.shadow_buy_return_pct ?? "n/a"} / {item.shadow_sell_return_pct ?? "n/a"} / {item.shadow_wait_return_pct ?? "n/a"}
                  </td>
                </tr>
              ))}
              {!loading.counterfactuals && (!counterfactuals || counterfactuals.items.length === 0) ? (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-sm text-foreground/70">No counterfactual outcomes found.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <PaginationControls
          label="Counterfactuals"
          page={counterfactualPage}
          pageSize={counterfactuals?.page_size ?? activePageSize}
          total={counterfactuals?.total ?? 0}
          onPageChange={setCounterfactualPage}
          disabled={loading.counterfactuals}
        />
      </section>

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-labelledby="quality-heading">
        <h2 id="quality-heading" className="text-lg font-semibold">Decision Quality Views</h2>
        <p className="mt-1 text-xs text-foreground/70">Quality read model with explicit known and unavailable outcomes.</p>

        {errors.quality ? <p className="mt-3 text-sm text-red-300" role="alert">{errors.quality}</p> : null}

        <div className="mt-3 overflow-x-auto rounded-lg border border-border">
          <table className="min-w-full text-left text-sm">
            <caption className="sr-only">Decision quality table</caption>
            <thead className="bg-background/60 text-xs uppercase tracking-wide text-foreground/70">
              <tr>
                <th scope="col" className="px-3 py-2">Decision</th>
                <th scope="col" className="px-3 py-2">Availability</th>
                <th scope="col" className="px-3 py-2">Composite</th>
                <th scope="col" className="px-3 py-2">Model</th>
                <th scope="col" className="px-3 py-2">Created</th>
              </tr>
            </thead>
            <tbody>
              {quality?.items.map((item) => (
                <tr key={item.decision_id} className="border-t border-border">
                  <td className="px-3 py-2 font-mono text-xs">{item.decision_id}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-col gap-1">
                      <AvailabilityBadge state={item.availability_state} reason={item.state_reason} />
                      {item.state_reason ? <span className="text-xs text-foreground/70">{item.state_reason}</span> : null}
                    </div>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{item.composite_score ?? "n/a"}</td>
                  <td className="px-3 py-2">{item.scoring_model_version ?? "n/a"}</td>
                  <td className="px-3 py-2">{toLocalDateTimeInput(item.created_at)}</td>
                </tr>
              ))}
              {!loading.quality && (!quality || quality.items.length === 0) ? (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-sm text-foreground/70">No quality rows found for current filters.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        {loading.quality ? <p className="mt-3 text-xs text-foreground/70" aria-live="polite">Loading decision quality...</p> : null}

        <PaginationControls
          label="Quality"
          page={qualityPage}
          pageSize={quality?.page_size ?? activePageSize}
          total={quality?.total ?? 0}
          onPageChange={setQualityPage}
          disabled={loading.quality}
        />
      </section>

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-labelledby="recommendations-heading">
        <h2 id="recommendations-heading" className="text-lg font-semibold">Recommendation Views</h2>
        <p className="mt-1 text-xs text-foreground/70">Advisory-only recommendation evidence, with no approval or execution actions.</p>

        {errors.recommendations ? <p className="mt-3 text-sm text-red-300" role="alert">{errors.recommendations}</p> : null}

        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {recommendations?.items.map((item) => (
            <article key={item.id} className="rounded-lg border border-border bg-background/40 p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold">{item.recommendation_type}</h3>
                  <p className="text-xs text-foreground/70">{item.recommendation_category}</p>
                </div>
                <AvailabilityBadge state={item.availability_state} reason={item.state_reason} />
              </div>

              <p className="mt-2 text-xs text-foreground/80">{item.explanation}</p>

              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <dt className="text-foreground/60">Confidence</dt>
                  <dd>{item.confidence_level}</dd>
                </div>
                <div>
                  <dt className="text-foreground/60">Impact</dt>
                  <dd>{item.expected_impact}</dd>
                </div>
                <div>
                  <dt className="text-foreground/60">Review</dt>
                  <dd>{item.required_human_review_level}</dd>
                </div>
                <div>
                  <dt className="text-foreground/60">Advisory only</dt>
                  <dd>{String(item.advisory_only)}</dd>
                </div>
              </dl>

              {item.state_reason ? <p className="mt-2 text-xs text-foreground/70">Reason: {item.state_reason}</p> : null}

              <details className="mt-3">
                <summary className="cursor-pointer text-xs text-foreground/80">View provenance and experiment details</summary>
                <pre className="mt-2 overflow-auto rounded bg-background p-2 text-[11px] text-foreground/80">
                  {compactJson({
                    originating_decision_ids: item.originating_decision_ids,
                    suggested_experiment: item.suggested_experiment,
                    supporting_evidence_refs: item.supporting_evidence_refs,
                    provenance: item.provenance,
                  })}
                </pre>
              </details>
            </article>
          ))}
          {!loading.recommendations && (!recommendations || recommendations.items.length === 0) ? (
            <p className="rounded-lg border border-border bg-background/40 p-6 text-sm text-foreground/70 sm:col-span-2 xl:col-span-3">
              No recommendations found for current filters.
            </p>
          ) : null}
        </div>

        {loading.recommendations ? <p className="mt-3 text-xs text-foreground/70" aria-live="polite">Loading recommendations...</p> : null}

        <PaginationControls
          label="Recommendations"
          page={recommendationPage}
          pageSize={recommendations?.page_size ?? activePageSize}
          total={recommendations?.total ?? 0}
          onPageChange={setRecommendationPage}
          disabled={loading.recommendations}
        />
      </section>
    </div>
  );
}
