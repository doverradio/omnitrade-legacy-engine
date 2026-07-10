"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiRequestError } from "@/lib/api/arena";
import {
  getCapitalLedger,
  type CapitalLedgerPool,
  type CapitalLedgerResponse,
  type CapitalLedgerStatus,
  type CapitalLedgerType,
} from "@/lib/api/capital-ledger";
import { listCryptoOrderPreviews, type CryptoOrderPreview } from "@/lib/api/crypto-order-previews";

type AccordionKey =
  | "summary"
  | "active"
  | "pools"
  | "validation"
  | "positions"
  | "archive"
  | "accounting";

type SummaryMetricKey =
  | "managed"
  | "equity"
  | "allocated"
  | "available"
  | "realized"
  | "unrealized"
  | "activePools"
  | "utilization";

const STATUS_OPTIONS: Array<{ value: CapitalLedgerStatus; label: string }> = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
];

const TYPE_OPTIONS: Array<{ value: CapitalLedgerType; label: string }> = [
  { value: "all", label: "All" },
  { value: "paper_account", label: "Paper Account" },
  { value: "validation_run", label: "Validation Run" },
  { value: "research_campaign", label: "Research Campaign" },
  { value: "strategy_allocation", label: "Strategy Allocation" },
  { value: "position", label: "Position" },
];

const DEFAULT_OPEN: Record<AccordionKey, boolean> = {
  summary: true,
  active: true,
  pools: false,
  validation: false,
  positions: false,
  archive: false,
  accounting: false,
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load the capital ledger.";
}

function formatCurrency(value: string | null | undefined): string {
  if (value == null) {
    return "Unavailable";
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(numeric);
}

function formatPercent(value: number | null | undefined): string {
  if (value == null) {
    return "Unavailable";
  }
  return `${value.toFixed(2)}%`;
}

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "Not available";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function statusBadge(status: CapitalLedgerPool["status"]): string {
  if (status === "active") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "completed") {
    return "border-sky-500/40 bg-sky-500/10 text-sky-100";
  }
  if (status === "cancelled") {
    return "border-rose-500/40 bg-rose-500/10 text-rose-100";
  }
  return "border-amber-500/40 bg-amber-500/10 text-amber-100";
}

function AccordionSection({
  id,
  title,
  open,
  count,
  onToggle,
  children,
}: {
  id: AccordionKey;
  title: string;
  open: boolean;
  count?: number;
  onToggle: (key: AccordionKey) => void;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border/80 bg-slate-950/40">
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

export default function CapitalLedgerCenter() {
  const [payload, setPayload] = useState<CapitalLedgerResponse | null>(null);
  const [latestPreview, setLatestPreview] = useState<CryptoOrderPreview | null>(null);
  const [pendingPreviewCount, setPendingPreviewCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<CapitalLedgerStatus>("all");
  const [typeFilter, setTypeFilter] = useState<CapitalLedgerType>("all");
  const [search, setSearch] = useState("");
  const [openSections, setOpenSections] = useState<Record<AccordionKey, boolean>>(DEFAULT_OPEN);
  const [selectedMetric, setSelectedMetric] = useState<SummaryMetricKey | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [next, previewItems] = await Promise.all([
          getCapitalLedger({ status: statusFilter, type: typeFilter, page: 1, pageSize: 200 }),
          listCryptoOrderPreviews(10).catch(() => [] as CryptoOrderPreview[]),
        ]);
        if (!active) {
          return;
        }
        setPayload(next);
        setLatestPreview(previewItems[0] ?? null);
        setPendingPreviewCount(previewItems.filter((item) => ["DRAFT", "VALIDATING", "PREVIEW_REQUESTED"].includes(item.status)).length);
      } catch (requestError) {
        if (active) {
          setError(errorMessage(requestError));
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
  }, [statusFilter, typeFilter]);

  const pools = useMemo(() => payload?.capital_pools ?? [], [payload]);

  const filteredPools = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return pools;
    }
    return pools.filter((item) => {
      return (
        item.name.toLowerCase().includes(needle)
        || item.capital_pool_id.toLowerCase().includes(needle)
        || item.related_entity_id.toLowerCase().includes(needle)
      );
    });
  }, [pools, search]);

  const activeTopLevelPools = useMemo(
    () => filteredPools.filter((item) => item.parent_capital_pool_id == null && item.status === "active"),
    [filteredPools],
  );

  const validationPools = useMemo(
    () => filteredPools.filter((item) => item.capital_pool_type === "validation_run"),
    [filteredPools],
  );

  const positionPools = useMemo(
    () => filteredPools.filter((item) => item.capital_pool_type === "position"),
    [filteredPools],
  );

  const archivePools = useMemo(
    () => filteredPools.filter((item) => item.status !== "active"),
    [filteredPools],
  );

  function toggleAccordion(key: AccordionKey) {
    setOpenSections((previous) => ({ ...previous, [key]: !previous[key] }));
  }

  function onMetricClick(metric: SummaryMetricKey) {
    setSelectedMetric(metric);
    if (metric === "activePools") {
      setStatusFilter("active");
    }
  }

  const metricExplanation = selectedMetric ? {
    managed: "Managed Capital is the sum of distinct top-level funded capital pools (paper accounts and independently funded validation runs). Child positions and strategy allocations are excluded to prevent double counting.",
    equity: "Current Equity sums mark-to-market equity across top-level pools.",
    allocated: "Allocated Capital represents currently committed capital in active pools.",
    available: "Available Capital is uncommitted cash/equity available inside top-level pools.",
    realized: "Realized PnL reflects closed outcomes where available from durable sources.",
    unrealized: "Unrealized PnL reflects mark-to-market deltas in currently open exposures.",
    activePools: "Active Pools filters the ledger to pools with active status.",
    utilization: "Utilization = Allocated Capital / Managed Capital.",
  }[selectedMetric] : null;

  return (
    <div className="space-y-6 overflow-x-hidden">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">Capital Ledger</h1>
        <p className="max-w-4xl text-sm text-foreground/75">
          Track every paper-capital pool, allocation, position, and validation run managed by OmniTrade.
        </p>
      </header>

      {error ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          {error}
        </section>
      ) : null}

      {loading ? (
        <section className="rounded-2xl border border-border bg-muted/30 p-3 text-sm text-foreground/80">Loading capital ledger...</section>
      ) : null}

      {payload ? (
        <div className="space-y-4">
          <section className="rounded-2xl border border-border/80 bg-slate-950/50 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Preview Activity</h2>
                <p className="mt-1 text-sm text-foreground/75">Preview-only activity is visible here and never changes managed capital.</p>
              </div>
              <Link href="/crypto-order-preview" className="rounded-full border border-cyan-400/40 bg-cyan-500/15 px-4 py-2 text-sm font-semibold text-cyan-50">
                Open Preview Workspace
              </Link>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <article className="rounded-2xl border border-border bg-background/55 p-4">
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Pending Previews</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{pendingPreviewCount}</p>
              </article>
              <article className="rounded-2xl border border-border bg-background/55 p-4">
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Latest Preview</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{latestPreview?.status ?? "None"}</p>
              </article>
              <article className="rounded-2xl border border-border bg-background/55 p-4">
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Proposed Amount</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{latestPreview ? formatCurrency(latestPreview.requested_amount) : "Not available"}</p>
              </article>
              <article className="rounded-2xl border border-border bg-background/55 p-4">
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Preview Status</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{latestPreview?.risk_verdict?.toUpperCase().replaceAll("_", " ") ?? "UNKNOWN"}</p>
              </article>
            </div>
            <div className="mt-3 text-sm text-foreground/70">
              Total Managed Capital remains unchanged while previews are generated, refreshed, or cancelled.
            </div>
          </section>

          {payload.capital_pools.length === 0 ? (
            <section className="rounded-2xl border border-border bg-background/60 p-4 text-sm text-foreground/75">
              No managed capital found.
            </section>
          ) : null}

          <AccordionSection id="summary" title="Capital Summary" open={openSections.summary} onToggle={toggleAccordion}>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <button type="button" className="rounded-2xl border border-border bg-background/55 p-4 text-left" onClick={() => onMetricClick("managed")}>
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Total Managed Capital</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{formatCurrency(payload.summary.total_managed_capital)}</p>
              </button>
              <button type="button" className="rounded-2xl border border-border bg-background/55 p-4 text-left" onClick={() => onMetricClick("equity")}>
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Current Equity</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{formatCurrency(payload.summary.total_current_equity)}</p>
              </button>
              <button type="button" className="rounded-2xl border border-border bg-background/55 p-4 text-left" onClick={() => onMetricClick("allocated")}>
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Allocated</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{formatCurrency(payload.summary.total_allocated_capital)}</p>
              </button>
              <button type="button" className="rounded-2xl border border-border bg-background/55 p-4 text-left" onClick={() => onMetricClick("available")}>
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Available</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{formatCurrency(payload.summary.total_available_capital)}</p>
              </button>
              <button type="button" className="rounded-2xl border border-border bg-background/55 p-4 text-left" onClick={() => onMetricClick("realized")}>
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Realized PnL</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{formatCurrency(payload.summary.total_realized_pnl)}</p>
              </button>
              <button type="button" className="rounded-2xl border border-border bg-background/55 p-4 text-left" onClick={() => onMetricClick("unrealized")}>
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Unrealized PnL</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{formatCurrency(payload.summary.total_unrealized_pnl)}</p>
              </button>
              <button type="button" className="rounded-2xl border border-border bg-background/55 p-4 text-left" onClick={() => onMetricClick("activePools")}>
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Active Pools</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{payload.summary.active_capital_pools}</p>
              </button>
              <button type="button" className="rounded-2xl border border-border bg-background/55 p-4 text-left" onClick={() => onMetricClick("utilization")}>
                <p className="text-[11px] uppercase tracking-wide text-foreground/65">Utilization</p>
                <p className="mt-2 text-2xl font-semibold text-foreground">{formatPercent(payload.summary.utilization_percent)}</p>
              </button>
            </div>

            {metricExplanation ? (
              <div className="mt-3 rounded-xl border border-border bg-background/40 p-3 text-sm text-foreground/75" role="note">
                {metricExplanation}
              </div>
            ) : null}

            <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4 text-sm">
              <p>Reserved Capital: {formatCurrency(payload.summary.total_reserved_capital)}</p>
              <p>Active Positions: {payload.summary.active_positions}</p>
              <p>Total Trades: {payload.summary.total_trades}</p>
              <p>Data Completeness: {formatPercent(payload.summary.data_completeness_percent)}</p>
            </div>

            {payload.summary.unavailable_sources.length > 0 ? (
              <div className="mt-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
                <p className="font-medium">Partial data availability</p>
                <ul className="mt-1 list-disc pl-5">
                  {payload.summary.unavailable_sources.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </AccordionSection>

          <AccordionSection id="active" title="Active Capital" count={activeTopLevelPools.length} open={openSections.active} onToggle={toggleAccordion}>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {activeTopLevelPools.map((pool) => (
                <article key={pool.capital_pool_id} className="rounded-2xl border border-border bg-background/55 p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-sm font-semibold text-foreground">{pool.name}</p>
                      <p className="mt-1 text-xs uppercase tracking-wide text-foreground/65">{pool.capital_pool_type.replaceAll("_", " ")}</p>
                    </div>
                    <span className={`rounded-full border px-2 py-1 text-xs font-medium uppercase tracking-wide ${statusBadge(pool.status)}`}>
                      {pool.status}
                    </span>
                  </div>

                  <div className="mt-3 grid gap-1 text-sm text-foreground/80">
                    <p>Starting: {formatCurrency(pool.starting_capital)}</p>
                    <p>Current equity: {formatCurrency(pool.current_equity)}</p>
                    <p>Allocated: {formatCurrency(pool.allocated_capital)}</p>
                    <p>Available: {formatCurrency(pool.available_capital)}</p>
                    <p>PnL: {formatCurrency(pool.unrealized_pnl)}</p>
                    <p>Children: {pool.child_allocations_count}</p>
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-2">
                    <span className="text-xs text-foreground/65">Related subsystem: {pool.related_entity_type.replaceAll("_", " ")}</span>
                    <Link href={pool.related_page_url} className="rounded-md border border-cyan-400/40 bg-cyan-500/20 px-3 py-1 text-xs font-semibold text-cyan-50">
                      View Details
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          </AccordionSection>

          <AccordionSection id="pools" title="Capital Pools" count={filteredPools.length} open={openSections.pools} onToggle={toggleAccordion}>
            <div className="space-y-3">
              <div className="grid gap-2 md:grid-cols-3">
                <select className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as CapitalLedgerStatus)}>
                  {STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <select className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value as CapitalLedgerType)}>
                  {TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <input
                  className="rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
                  placeholder="Search by name, ID, or related entity"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </div>

              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="min-w-[980px] w-full text-left text-sm">
                  <thead className="bg-background/80 text-foreground/75">
                    <tr>
                      <th className="px-3 py-2">Name</th>
                      <th className="px-3 py-2">Type</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Starting</th>
                      <th className="px-3 py-2">Current Equity</th>
                      <th className="px-3 py-2">Allocated</th>
                      <th className="px-3 py-2">Available</th>
                      <th className="px-3 py-2">Realized PnL</th>
                      <th className="px-3 py-2">Unrealized PnL</th>
                      <th className="px-3 py-2">Started</th>
                      <th className="px-3 py-2">Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredPools.map((pool) => (
                      <tr key={pool.capital_pool_id} className="border-t border-border">
                        <td className="px-3 py-2">{pool.name}</td>
                        <td className="px-3 py-2">{pool.capital_pool_type.replaceAll("_", " ")}</td>
                        <td className="px-3 py-2"><span className={`rounded-full border px-2 py-0.5 text-xs ${statusBadge(pool.status)}`}>{pool.status}</span></td>
                        <td className="px-3 py-2">{formatCurrency(pool.starting_capital)}</td>
                        <td className="px-3 py-2">{formatCurrency(pool.current_equity)}</td>
                        <td className="px-3 py-2">{formatCurrency(pool.allocated_capital)}</td>
                        <td className="px-3 py-2">{formatCurrency(pool.available_capital)}</td>
                        <td className="px-3 py-2">{formatCurrency(pool.realized_pnl)}</td>
                        <td className="px-3 py-2">{formatCurrency(pool.unrealized_pnl)}</td>
                        <td className="px-3 py-2">{formatTimestamp(pool.started_at)}</td>
                        <td className="px-3 py-2">
                          <Link href={pool.related_page_url} className="text-cyan-300 hover:text-cyan-200">Open</Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </AccordionSection>

          <AccordionSection id="validation" title="Validation Run Capital" count={validationPools.length} open={openSections.validation} onToggle={toggleAccordion}>
            <div className="space-y-2">
              {validationPools.map((pool) => (
                <article key={pool.capital_pool_id} className="rounded-xl border border-border bg-background/50 p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-medium text-foreground">{pool.name}</p>
                    <span className={`rounded-full border px-2 py-0.5 text-xs ${statusBadge(pool.status)}`}>{pool.status}</span>
                  </div>
                  <div className="mt-2 grid gap-1 sm:grid-cols-2 xl:grid-cols-4 text-foreground/80">
                    <p>Starting: {formatCurrency(pool.starting_capital)}</p>
                    <p>Current equity: {formatCurrency(pool.current_equity)}</p>
                    <p>PnL: {formatCurrency(pool.unrealized_pnl)}</p>
                    <p>Result: {pool.status}</p>
                    <p>Started: {formatTimestamp(pool.started_at)}</p>
                    <p>Completed: {formatTimestamp(pool.completed_at)}</p>
                  </div>
                  <div className="mt-2">
                    <Link href="/validation-runs" className="text-cyan-300 hover:text-cyan-200">View Validation Run</Link>
                  </div>
                </article>
              ))}
            </div>
          </AccordionSection>

          <AccordionSection id="positions" title="Positions and Trades" count={positionPools.length} open={openSections.positions} onToggle={toggleAccordion}>
            <div className="space-y-2">
              <p className="text-sm text-foreground/75">Total trade records in scope: {payload.summary.total_trades}</p>
              {positionPools.length === 0 ? (
                <p className="rounded-xl border border-dashed border-border bg-background/50 p-3 text-sm text-foreground/75">No open position allocations available.</p>
              ) : (
                positionPools.map((pool) => (
                  <article key={pool.capital_pool_id} className="rounded-xl border border-border bg-background/50 p-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="font-medium text-foreground">{pool.name}</p>
                      <span className={`rounded-full border px-2 py-0.5 text-xs ${statusBadge(pool.status)}`}>{pool.status}</span>
                    </div>
                    <div className="mt-2 grid gap-1 sm:grid-cols-2 xl:grid-cols-4 text-foreground/80">
                      <p>Entry value: {formatCurrency(pool.starting_capital)}</p>
                      <p>Current value: {formatCurrency(pool.current_equity)}</p>
                      <p>Unrealized PnL: {formatCurrency(pool.unrealized_pnl)}</p>
                      <p>Related pool: {pool.parent_capital_pool_id ?? "Not available"}</p>
                    </div>
                    <div className="mt-2">
                      <Link href={pool.related_page_url} className="text-cyan-300 hover:text-cyan-200">Open related trade details</Link>
                    </div>
                  </article>
                ))
              )}
            </div>
          </AccordionSection>

          <AccordionSection id="archive" title="Inactive / Archive" count={archivePools.length} open={openSections.archive} onToggle={toggleAccordion}>
            {archivePools.length === 0 ? (
              <p className="rounded-xl border border-dashed border-border bg-background/50 p-3 text-sm text-foreground/75">
                No inactive capital pools currently.
              </p>
            ) : (
              <ul className="space-y-2">
                {archivePools.map((pool) => (
                  <li key={pool.capital_pool_id} className="rounded-xl border border-border bg-background/50 p-3 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-medium text-foreground">{pool.name}</p>
                      <span className={`rounded-full border px-2 py-0.5 text-xs ${statusBadge(pool.status)}`}>{pool.status}</span>
                    </div>
                    <p className="mt-1 text-foreground/75">Type: {pool.capital_pool_type.replaceAll("_", " ")}</p>
                    <p className="text-foreground/75">Completed: {formatTimestamp(pool.completed_at)}</p>
                  </li>
                ))}
              </ul>
            )}
          </AccordionSection>

          <AccordionSection id="accounting" title="Accounting Details" open={openSections.accounting} onToggle={toggleAccordion}>
            <div className="space-y-2 text-sm text-foreground/75">
              <p>Managed Capital counts only top-level funded pools.</p>
              <p>Strategy allocations, positions, and trades are child records and excluded from Managed Capital.</p>
              <p>Completed and cancelled pools remain visible in archive and history.</p>
              <p>Generated at: {formatTimestamp(payload.summary.generated_at)}</p>
            </div>
          </AccordionSection>
        </div>
      ) : null}
    </div>
  );
}
