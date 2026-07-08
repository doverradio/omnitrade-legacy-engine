"use client";

import { useMemo, useState } from "react";

import {
  ApiRequestError,
  generateCoachReviews,
  getDecisionRecommendations,
  getDecisionRecords,
  type CoachReviewGenerationResponse,
  type DecisionRecommendationItem,
  type DecisionRecordItem,
  type PaginatedResponse,
} from "@/lib/api/decisions";

type FilterState = {
  asset_id: string;
  strategy_id: string;
  action: string;
  trade_accepted: string;
  review_status: string;
  start_time: string;
  end_time: string;
  page_size: string;
};

const INITIAL_FILTERS: FilterState = {
  asset_id: "",
  strategy_id: "",
  action: "",
  trade_accepted: "",
  review_status: "",
  start_time: "",
  end_time: "",
  page_size: "50",
};

function asIso(value: string): string | undefined {
  if (!value.trim()) {
    return undefined;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return undefined;
  }
  return parsed.toISOString();
}

function localDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "-";
  }
  return parsed.toLocaleString();
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Request failed";
}

function qualityText(item: DecisionRecordItem): string {
  if (item.quality_score.availability_state !== "known" || !item.quality_score.composite_score) {
    return item.quality_score.availability_state;
  }
  return `${item.quality_score.composite_score} (${item.quality_score.scoring_model_version ?? "unknown_model"})`;
}

function futureOutcomeText(item: DecisionRecordItem): string {
  if (item.future_outcome_tracking.availability_state !== "known") {
    return item.future_outcome_tracking.availability_state;
  }

  const latestState = item.future_outcome_tracking.latest_evaluation_state ?? "unknown";
  const latestHorizon = item.future_outcome_tracking.latest_horizon_label ?? "-";
  return `${item.future_outcome_tracking.resolved_horizons}/${item.future_outcome_tracking.total_horizons} resolved (${latestState} @ ${latestHorizon})`;
}

function recommendationText(item: DecisionRecordItem): string {
  if (item.recommendation_history.count === 0) {
    return "none";
  }
  return `${item.recommendation_history.count} (${item.recommendation_history.latest_recommendation_type ?? "unknown"})`;
}

export default function DecisionRecordsPage() {
  const [filters, setFilters] = useState<FilterState>(INITIAL_FILTERS);
  const [page, setPage] = useState(1);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [records, setRecords] = useState<PaginatedResponse<DecisionRecordItem> | null>(null);

  const [coachLoading, setCoachLoading] = useState(false);
  const [coachResult, setCoachResult] = useState<CoachReviewGenerationResponse | null>(null);
  const [coachError, setCoachError] = useState<string | null>(null);

  const [recommendationsLoading, setRecommendationsLoading] = useState(false);
  const [recommendationsError, setRecommendationsError] = useState<string | null>(null);
  const [recommendations, setRecommendations] = useState<DecisionRecommendationItem[]>([]);

  const pageSize = useMemo(() => {
    const value = Number(filters.page_size);
    if (!Number.isFinite(value) || value < 1) {
      return 50;
    }
    return Math.min(200, Math.floor(value));
  }, [filters.page_size]);

  const maxPage = useMemo(() => {
    if (!records) {
      return 1;
    }
    return Math.max(1, Math.ceil(records.total / records.page_size));
  }, [records]);

  async function loadRecords(nextPage: number) {
    setLoading(true);
    setError(null);
    try {
      const payload = await getDecisionRecords({
        asset_id: filters.asset_id || undefined,
        strategy_id: filters.strategy_id || undefined,
        action: filters.action || undefined,
        trade_accepted:
          filters.trade_accepted === ""
            ? undefined
            : filters.trade_accepted === "true"
              ? true
              : false,
        review_status: filters.review_status || undefined,
        start_time: asIso(filters.start_time),
        end_time: asIso(filters.end_time),
        page: nextPage,
        page_size: pageSize,
      });
      setRecords(payload);
      setPage(nextPage);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }

  async function runCoachReview() {
    setCoachLoading(true);
    setCoachError(null);
    setCoachResult(null);
    try {
      const payload = await generateCoachReviews({ lookback_hours: 24, limit: 250 });
      setCoachResult(payload);
      await loadCoachRecommendations();
    } catch (requestError) {
      setCoachError(errorMessage(requestError));
    } finally {
      setCoachLoading(false);
    }
  }

  async function loadCoachRecommendations() {
    setRecommendationsLoading(true);
    setRecommendationsError(null);
    try {
      const payload = await getDecisionRecommendations({ page: 1, page_size: 20 });
      const coachItems = payload.items.filter((item) => item.recommendation_type === "recurring_decision_pattern");
      setRecommendations(coachItems);
    } catch (requestError) {
      setRecommendationsError(errorMessage(requestError));
    } finally {
      setRecommendationsLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Decision Records</h1>
        <p className="text-sm text-foreground/75">
          Paper-mode decision browser for recent decision records, linked signal context, and explanation fields.
        </p>
      </header>

      <section className="rounded-xl border border-border bg-muted/20 p-4 sm:p-5" aria-labelledby="decision-record-filters">
        <h2 id="decision-record-filters" className="text-lg font-semibold">
          Filters
        </h2>
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Asset ID</span>
            <input
              value={filters.asset_id}
              onChange={(event) => setFilters((current) => ({ ...current, asset_id: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
              placeholder="uuid"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Strategy ID</span>
            <input
              value={filters.strategy_id}
              onChange={(event) => setFilters((current) => ({ ...current, strategy_id: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
              placeholder="uuid"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Action</span>
            <select
              value={filters.action}
              onChange={(event) => setFilters((current) => ({ ...current, action: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            >
              <option value="">all</option>
              <option value="buy">buy</option>
              <option value="sell">sell</option>
              <option value="hold">hold</option>
            </select>
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Trade Accepted</span>
            <select
              value={filters.trade_accepted}
              onChange={(event) => setFilters((current) => ({ ...current, trade_accepted: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            >
              <option value="">all</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Review Status</span>
            <input
              value={filters.review_status}
              onChange={(event) => setFilters((current) => ({ ...current, review_status: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
              placeholder="unreviewed"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Start Time</span>
            <input
              type="datetime-local"
              value={filters.start_time}
              onChange={(event) => setFilters((current) => ({ ...current, start_time: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">End Time</span>
            <input
              type="datetime-local"
              value={filters.end_time}
              onChange={(event) => setFilters((current) => ({ ...current, end_time: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Page Size</span>
            <input
              type="number"
              min={1}
              max={200}
              value={filters.page_size}
              onChange={(event) => setFilters((current) => ({ ...current, page_size: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              void loadRecords(1);
            }}
            disabled={loading}
            className="rounded-md border border-border bg-background/70 px-3 py-2 text-sm font-medium hover:bg-background disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Loading..." : "Load Decision Records"}
          </button>

          <button
            type="button"
            onClick={() => {
              void runCoachReview();
            }}
            disabled={coachLoading}
            className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm font-medium text-emerald-100 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {coachLoading ? "Generating..." : "Generate AI Coach Batch Review"}
          </button>

          <button
            type="button"
            onClick={() => {
              void loadCoachRecommendations();
            }}
            disabled={recommendationsLoading}
            className="rounded-md border border-border bg-background/70 px-3 py-2 text-sm font-medium hover:bg-background disabled:cursor-not-allowed disabled:opacity-60"
          >
            {recommendationsLoading ? "Loading coach queue..." : "Load AI Coach Learn Queue"}
          </button>
        </div>

        {error && <p className="mt-3 text-sm text-red-300">{error}</p>}
        {coachError && <p className="mt-3 text-sm text-red-300">{coachError}</p>}
        {recommendationsError && <p className="mt-3 text-sm text-red-300">{recommendationsError}</p>}

        {coachResult && (
          <p className="mt-3 text-xs text-emerald-200">
            Coach review complete: scanned {coachResult.scanned_records}, inserted {coachResult.inserted_recommendations}, skipped {coachResult.skipped_existing}. Advisory-only.
          </p>
        )}
      </section>

      <section className="rounded-xl border border-border bg-muted/20 p-4 sm:p-5" aria-labelledby="coach-learn-queue">
        <div className="flex items-center justify-between gap-3">
          <h2 id="coach-learn-queue" className="text-lg font-semibold">
            AI Coach Learn Queue
          </h2>
          <p className="text-xs text-foreground/70">Advisory-only, paper-mode evidence</p>
        </div>

        {recommendations.length === 0 ? (
          <p className="mt-3 text-sm text-foreground/75">No AI Coach recommendations loaded yet.</p>
        ) : (
          <ul className="mt-4 space-y-3">
            {recommendations.map((item) => {
              const suggestedName =
                typeof item.suggested_experiment.name === "string"
                  ? item.suggested_experiment.name
                  : "unnamed_experiment";

              return (
                <li key={item.id} className="rounded-lg border border-border/80 bg-background/30 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-foreground/75">
                    <span className="rounded bg-foreground/10 px-2 py-0.5">{item.recommendation_type}</span>
                    <span>{new Date(item.created_at).toLocaleString()}</span>
                    <span>confidence: {item.confidence_level}</span>
                    <span>impact: {item.expected_impact}</span>
                  </div>
                  <p className="mt-2 text-sm text-foreground/90">{item.explanation}</p>
                  <p className="mt-2 text-xs text-foreground/75">Suggested experiment: {suggestedName}</p>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="rounded-xl border border-border bg-muted/20 p-4 sm:p-5">
        <div className="mb-3 flex items-center justify-between gap-3 text-xs text-foreground/70">
          <p>
            Page {page} of {maxPage}
            {records ? ` (${records.total} total)` : ""}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              className="rounded-md border border-border px-2 py-1 disabled:opacity-50"
              disabled={!records || loading || page <= 1}
              onClick={() => {
                void loadRecords(Math.max(1, page - 1));
              }}
            >
              Previous
            </button>
            <button
              type="button"
              className="rounded-md border border-border px-2 py-1 disabled:opacity-50"
              disabled={!records || loading || page >= maxPage}
              onClick={() => {
                void loadRecords(Math.min(maxPage, page + 1));
              }}
            >
              Next
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-foreground/70">
                <th className="px-3 py-2">Time</th>
                <th className="px-3 py-2">Decision</th>
                <th className="px-3 py-2">Asset</th>
                <th className="px-3 py-2">Strategy</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2">Signal Status</th>
                <th className="px-3 py-2">Trade Accepted</th>
                <th className="px-3 py-2">Review Status</th>
                <th className="px-3 py-2">Quality Score</th>
                <th className="px-3 py-2">Future Outcome</th>
                <th className="px-3 py-2">Recommendations</th>
                <th className="px-3 py-2">Explanation</th>
              </tr>
            </thead>
            <tbody>
              {!records || records.items.length === 0 ? (
                <tr>
                  <td className="px-3 py-4 text-foreground/70" colSpan={12}>
                    No decision records loaded yet.
                  </td>
                </tr>
              ) : (
                records.items.map((item) => (
                  <tr key={item.decision_id} className="border-b border-border/70 align-top">
                    <td className="px-3 py-2">{localDate(item.timestamp)}</td>
                    <td className="px-3 py-2 font-mono text-xs">{item.decision_id}</td>
                    <td className="px-3 py-2 font-mono text-xs">{item.linked_signal.asset_id ?? item.asset_id ?? "-"}</td>
                    <td className="px-3 py-2 font-mono text-xs">{item.linked_signal.strategy_id ?? "-"}</td>
                    <td className="px-3 py-2">{item.linked_signal.action ?? item.action ?? "-"}</td>
                    <td className="px-3 py-2">{item.linked_signal.status ?? "-"}</td>
                    <td className="px-3 py-2">{item.trade_accepted ? "true" : "false"}</td>
                    <td className="px-3 py-2">{item.review_status ?? "-"}</td>
                    <td className="px-3 py-2 text-xs">{qualityText(item)}</td>
                    <td className="px-3 py-2 text-xs">{futureOutcomeText(item)}</td>
                    <td className="px-3 py-2 text-xs">{recommendationText(item)}</td>
                    <td className="px-3 py-2 text-xs text-foreground/80">
                      {item.decision_explanation.human_notes ??
                        item.decision_explanation.trade_rejected_reason ??
                        (item.decision_explanation.ai_reflection ? "AI reflection available" : "-")}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
