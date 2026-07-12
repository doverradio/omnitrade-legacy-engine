"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  getDecisionExplorerSummary,
  getDecisionRecords,
  type DecisionExplorerSummary,
  type DecisionRecordFilters,
  type DecisionRecordItem,
  type PaginatedResponse,
} from "@/lib/api/decisions";

type FilterState = {
  q: string;
  action: string;
  tradeAccepted: string;
  reviewStatus: string;
  provider: string;
  environment: string;
  productId: string;
  sort: DecisionRecordFilters["sort"];
  pageSize: string;
};

const INITIAL_FILTERS: FilterState = {
  q: "",
  action: "",
  tradeAccepted: "",
  reviewStatus: "",
  provider: "",
  environment: "",
  productId: "",
  sort: "newest",
  pageSize: "20",
};

const EMPTY_SUMMARY: DecisionExplorerSummary = {
  total_decisions: 0,
  accepted: 0,
  risk_rejected: 0,
  hold_wait: 0,
  preview_ready: 0,
  submitted: 0,
  executed: 0,
  needs_review: 0,
  missing_linkage: 0,
};

function messageFromError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Request failed";
}

function asFilters(filters: FilterState, page: number): DecisionRecordFilters {
  return {
    q: filters.q || undefined,
    action: filters.action || undefined,
    trade_accepted:
      filters.tradeAccepted === "" ? undefined : filters.tradeAccepted === "true" ? true : false,
    review_status: filters.reviewStatus || undefined,
    provider: filters.provider || undefined,
    environment: filters.environment || undefined,
    product_id: filters.productId || undefined,
    sort: filters.sort,
    page,
    page_size: Number(filters.pageSize) > 0 ? Math.min(200, Number(filters.pageSize)) : 20,
  };
}

function statusTone(value: string): string {
  if (value === "approved" || value === "ready" || value === "filled") {
    return "border-emerald-500/50 bg-emerald-500/15 text-emerald-100";
  }
  if (value === "rejected" || value === "missing_linkage") {
    return "border-rose-500/50 bg-rose-500/15 text-rose-100";
  }
  return "border-slate-500/50 bg-slate-500/15 text-slate-100";
}

function summaryEntries(summary: DecisionExplorerSummary): Array<{ label: string; value: number }> {
  return [
    { label: "Total decisions", value: summary.total_decisions },
    { label: "Accepted", value: summary.accepted },
    { label: "Risk rejected", value: summary.risk_rejected },
    { label: "Hold/Wait", value: summary.hold_wait },
    { label: "Preview ready", value: summary.preview_ready },
    { label: "Submitted", value: summary.submitted },
    { label: "Executed", value: summary.executed },
    { label: "Needs review", value: summary.needs_review },
    { label: "Missing linkage", value: summary.missing_linkage },
  ];
}

export default function DecisionExplorerPage() {
  const [filters, setFilters] = useState<FilterState>(INITIAL_FILTERS);
  const [records, setRecords] = useState<PaginatedResponse<DecisionRecordItem> | null>(null);
  const [summary, setSummary] = useState<DecisionExplorerSummary>(EMPTY_SUMMARY);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pageSize = useMemo(() => {
    const value = Number(filters.pageSize);
    if (!Number.isFinite(value) || value <= 0) {
      return 20;
    }
    return Math.min(200, Math.floor(value));
  }, [filters.pageSize]);

  const maxPage = useMemo(() => {
    if (!records) {
      return 1;
    }
    return Math.max(1, Math.ceil(records.total / records.page_size));
  }, [records]);

  async function loadData(nextPage: number): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const baseFilters = asFilters(filters, nextPage);
      const [recordsPayload, summaryPayload] = await Promise.all([
        getDecisionRecords(baseFilters),
        getDecisionExplorerSummary(baseFilters),
      ]);
      setRecords(recordsPayload);
      setSummary(summaryPayload);
      setPage(nextPage);
    } catch (requestError) {
      setError(messageFromError(requestError));
      setRecords(null);
      setSummary(EMPTY_SUMMARY);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadData(1);
  }, []);

  return (
    <div className="space-y-6">
      <header className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">Decision Explorer</h1>
            <p className="mt-1 text-sm text-foreground/80">Search and inspect every governed market decision.</p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded-full border border-slate-500/50 bg-slate-500/20 px-2 py-1">Read-only</span>
            <span className="rounded-full border border-slate-500/50 bg-slate-500/20 px-2 py-1">Environment: paper</span>
          </div>
        </div>
      </header>

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Decision summary strip">
        <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-9">
          {summaryEntries(summary).map((item) => (
            <div key={item.label} className="rounded-md border border-border bg-background/40 p-2">
              <p className="text-[11px] uppercase tracking-wide text-foreground/70">{item.label}</p>
              <p className="mt-1 text-lg font-semibold">{item.value}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Decision filters">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Search</span>
            <input
              value={filters.q}
              onChange={(event) => setFilters((current) => ({ ...current, q: event.target.value }))}
              placeholder="decision id, provider, reason, notes"
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
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
              <option value="wait">wait</option>
            </select>
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Trade accepted</span>
            <select
              value={filters.tradeAccepted}
              onChange={(event) => setFilters((current) => ({ ...current, tradeAccepted: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            >
              <option value="">all</option>
              <option value="true">yes</option>
              <option value="false">no</option>
            </select>
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Review status</span>
            <input
              value={filters.reviewStatus}
              onChange={(event) => setFilters((current) => ({ ...current, reviewStatus: event.target.value }))}
              placeholder="unreviewed"
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Provider</span>
            <input
              value={filters.provider}
              onChange={(event) => setFilters((current) => ({ ...current, provider: event.target.value }))}
              placeholder="kraken_spot"
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Environment</span>
            <input
              value={filters.environment}
              onChange={(event) => setFilters((current) => ({ ...current, environment: event.target.value }))}
              placeholder="production"
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Product</span>
            <input
              value={filters.productId}
              onChange={(event) => setFilters((current) => ({ ...current, productId: event.target.value }))}
              placeholder="BTC-USD"
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            />
          </label>

          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Sort</span>
            <select
              value={filters.sort}
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  sort: event.target.value as FilterState["sort"],
                }))
              }
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            >
              <option value="newest">newest first</option>
              <option value="oldest">oldest first</option>
              <option value="highest_confidence">highest confidence</option>
              <option value="lowest_confidence">lowest confidence</option>
              <option value="highest_quality">highest quality</option>
              <option value="lowest_quality">lowest quality</option>
              <option value="largest_requested_notional">largest requested notional</option>
              <option value="largest_approved_notional">largest approved notional</option>
              <option value="most_recently_reviewed">most recently reviewed</option>
            </select>
          </label>
        </div>

        <div className="mt-3 flex items-center gap-2">
          <label className="text-sm">
            <span className="mb-1 block text-foreground/80">Page size</span>
            <input
              type="number"
              min={1}
              max={200}
              value={filters.pageSize}
              onChange={(event) => setFilters((current) => ({ ...current, pageSize: event.target.value }))}
              className="w-28 rounded-md border border-border bg-background/70 px-3 py-2"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              void loadData(1);
            }}
            disabled={loading}
            className="mt-5 rounded-md border border-border bg-background/70 px-3 py-2 text-sm font-medium hover:bg-background disabled:opacity-60"
          >
            {loading ? "Loading..." : "Apply filters"}
          </button>
        </div>

        {error ? <p className="mt-3 text-sm text-rose-200">{error}</p> : null}
      </section>

      <section className="space-y-3" aria-label="Decision results">
        <div className="flex items-center justify-between text-xs text-foreground/70">
          <p>
            Page {page} of {maxPage} ({records?.total ?? 0} matches)
          </p>
          <p>Data freshness: {new Date().toLocaleString()}</p>
        </div>

        {!records || records.items.length === 0 ? (
          <div className="rounded-xl border border-border bg-muted/30 p-5 text-sm text-foreground/80">
            No decisions match this query.
          </div>
        ) : (
          records.items.map((item) => (
            <article key={item.decision_id} className="rounded-xl border border-border bg-muted/30 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-xs text-foreground/65">{new Date(item.timestamp).toLocaleString()}</p>
                  <p className="font-mono text-xs text-foreground/80">{item.decision_id}</p>
                  <p className="mt-1 text-sm text-foreground/90">{item.product_id ?? "unknown product"} • {item.provider ?? "unknown provider"}</p>
                </div>
                <Link
                  href={`/decisions/${item.decision_id}`}
                  className="rounded-md border border-blue-400/50 bg-blue-500/20 px-3 py-1.5 text-xs font-semibold text-blue-100 hover:bg-blue-500/30"
                >
                  Open Inspector
                </Link>
              </div>

              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4 text-xs">
                <p><span className="text-foreground/70">Action:</span> {item.action ?? "unknown"}</p>
                <p><span className="text-foreground/70">Confidence:</span> {item.confidence ?? "n/a"}</p>
                <p><span className="text-foreground/70">Requested:</span> {item.requested_notional ?? "n/a"}</p>
                <p><span className="text-foreground/70">Approved:</span> {item.approved_notional ?? "n/a"}</p>
              </div>

              <div className="mt-3 flex flex-wrap gap-2 text-[11px] uppercase tracking-wide">
                <span className={`rounded-full border px-2 py-1 ${statusTone(item.risk_verdict)}`}>Risk {item.risk_verdict}</span>
                <span className={`rounded-full border px-2 py-1 ${statusTone(item.preview_status)}`}>Preview {item.preview_status}</span>
                <span className={`rounded-full border px-2 py-1 ${statusTone(item.execution_status)}`}>Execution {item.execution_status}</span>
                <span className={`rounded-full border px-2 py-1 ${statusTone(item.evidence_completeness)}`}>{item.evidence_completeness}</span>
                <span className="rounded-full border border-slate-500/50 bg-slate-500/15 px-2 py-1">Review {item.review_status ?? "unknown"}</span>
              </div>

              {item.first_failing_risk_rule ? (
                <p className="mt-2 text-xs text-rose-200">First failing rule: {item.first_failing_risk_rule}</p>
              ) : null}
            </article>
          ))
        )}

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              void loadData(Math.max(1, page - 1));
            }}
            disabled={loading || page <= 1}
            className="rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => {
              void loadData(Math.min(maxPage, page + 1));
            }}
            disabled={loading || page >= maxPage}
            className="rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Next
          </button>
          <span className="text-xs text-foreground/70">page size {pageSize}</span>
        </div>
      </section>
    </div>
  );
}
