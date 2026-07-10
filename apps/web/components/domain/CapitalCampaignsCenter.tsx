"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiRequestError } from "@/lib/api/arena";
import { listCapitalCampaigns, type CapitalCampaign, type CapitalCampaignStatus } from "@/lib/api/capital-campaigns";

const STATUS_OPTIONS: Array<{ value: "all" | CapitalCampaignStatus; label: string }> = [
  { value: "all", label: "All" },
  { value: "DRAFT", label: "Draft" },
  { value: "READY", label: "Ready" },
  { value: "RUNNING", label: "Running" },
  { value: "PAUSED", label: "Paused" },
  { value: "TARGET_REACHED", label: "Target Reached" },
  { value: "COMPLETED", label: "Completed" },
  { value: "ARCHIVED", label: "Archived" },
];

function formatCurrency(value: string): string {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(numeric);
}

function formatPercent(value: string): string {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return `${numeric.toFixed(2)}%`;
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown";
  }
  return parsed.toLocaleDateString();
}

function statusBadgeClass(status: CapitalCampaignStatus): string {
  if (status === "RUNNING" || status === "READY") {
    return "border-emerald-500/40 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "PAUSED" || status === "TARGET_REACHED") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-100";
  }
  if (status === "COMPLETED" || status === "ARCHIVED") {
    return "border-sky-500/40 bg-sky-500/10 text-sky-100";
  }
  return "border-slate-500/40 bg-slate-500/10 text-slate-100";
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load capital campaigns.";
}

export default function CapitalCampaignsCenter() {
  const [campaigns, setCampaigns] = useState<CapitalCampaign[]>([]);
  const [statusFilter, setStatusFilter] = useState<"all" | CapitalCampaignStatus>("all");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const items = await listCapitalCampaigns({
          status: statusFilter === "all" ? undefined : statusFilter,
          owner: ownerFilter.trim() || undefined,
        });
        if (!active) {
          return;
        }
        setCampaigns(items);
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
  }, [statusFilter, ownerFilter]);

  const totalManaged = useMemo(() => {
    return campaigns
      .filter((item) => ["READY", "RUNNING", "PAUSED", "TARGET_REACHED"].includes(item.status))
      .reduce((acc, item) => acc + Number(item.starting_capital || "0"), 0);
  }, [campaigns]);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">Capital Campaigns</h1>
        <p className="max-w-3xl text-sm text-foreground/75">
          Every allocation is represented as a campaign with deterministic status tracking and accounting fields.
        </p>
      </header>

      {error ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          {error}
        </section>
      ) : null}

      <section className="rounded-2xl border border-border bg-slate-950/50 p-4">
        <div className="grid gap-3 md:grid-cols-3">
          <article className="rounded-xl border border-border bg-background/50 p-3">
            <p className="text-[11px] uppercase tracking-wide text-foreground/65">Total Campaigns</p>
            <p className="mt-1 text-2xl font-semibold text-foreground">{campaigns.length}</p>
          </article>
          <article className="rounded-xl border border-border bg-background/50 p-3">
            <p className="text-[11px] uppercase tracking-wide text-foreground/65">Total Managed Capital</p>
            <p className="mt-1 text-2xl font-semibold text-foreground">{formatCurrency(String(totalManaged))}</p>
          </article>
          <article className="rounded-xl border border-border bg-background/50 p-3">
            <p className="text-[11px] uppercase tracking-wide text-foreground/65">Running Campaigns</p>
            <p className="mt-1 text-2xl font-semibold text-foreground">{campaigns.filter((item) => item.status === "RUNNING").length}</p>
          </article>
        </div>
      </section>

      <section className="rounded-2xl border border-border bg-muted/20 p-4">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm text-foreground/80">
            Status
            <select
              className="mt-1 w-full rounded border border-border bg-background/60 px-2 py-2 text-sm"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as "all" | CapitalCampaignStatus)}
            >
              {STATUS_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
          </label>
          <label className="text-sm text-foreground/80">
            Owner
            <input
              className="mt-1 w-full rounded border border-border bg-background/60 px-2 py-2 text-sm"
              value={ownerFilter}
              onChange={(event) => setOwnerFilter(event.target.value)}
              placeholder="owner identifier"
            />
          </label>
        </div>
      </section>

      {loading ? (
        <section className="rounded-2xl border border-border bg-muted/30 p-3 text-sm text-foreground/80">Loading campaigns...</section>
      ) : null}

      {!loading && campaigns.length === 0 ? (
        <section className="rounded-2xl border border-border bg-background/60 p-6 text-sm text-foreground/75">
          <h2 className="text-base font-semibold text-foreground">No capital campaigns yet</h2>
          <p className="mt-2">
            Create the first campaign through the Capital Campaigns API to begin campaign-scoped capital tracking.
          </p>
        </section>
      ) : null}

      {!loading && campaigns.length > 0 ? (
        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {campaigns.map((campaign) => (
            <Link
              key={campaign.uuid}
              href={`/capital-campaigns/${campaign.uuid}`}
              className="block rounded-2xl border border-border bg-background/55 p-4 transition hover:border-cyan-500/50"
            >
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-base font-semibold text-foreground">{campaign.name}</h2>
                <span className={`rounded-full border px-2 py-1 text-xs ${statusBadgeClass(campaign.status)}`}>
                  {campaign.status.replaceAll("_", " ")}
                </span>
              </div>
              <p className="mt-2 text-sm text-foreground/75">{campaign.description ?? "No description."}</p>
              <dl className="mt-3 space-y-1 text-sm text-foreground/80">
                <div className="flex justify-between gap-3"><dt>Owner</dt><dd>{campaign.owner}</dd></div>
                <div className="flex justify-between gap-3"><dt>Type</dt><dd>{campaign.campaign_type}</dd></div>
                <div className="flex justify-between gap-3"><dt>Starting Capital</dt><dd>{formatCurrency(campaign.starting_capital)}</dd></div>
                <div className="flex justify-between gap-3"><dt>Current Equity</dt><dd>{formatCurrency(campaign.current_equity)}</dd></div>
                <div className="flex justify-between gap-3"><dt>Realized Profit</dt><dd>{formatCurrency(campaign.realized_profit)}</dd></div>
                <div className="flex justify-between gap-3"><dt>Unrealized Profit</dt><dd>{formatCurrency(campaign.unrealized_profit)}</dd></div>
                <div className="flex justify-between gap-3"><dt>ROI</dt><dd>{formatPercent(campaign.roi)}</dd></div>
                <div className="flex justify-between gap-3"><dt>Created</dt><dd>{formatDate(campaign.created_at)}</dd></div>
              </dl>
              <div className="mt-4">
                <span className="inline-flex rounded-full border border-cyan-400/40 bg-cyan-500/15 px-4 py-2 text-sm font-semibold text-cyan-100">
                  View Campaign
                </span>
              </div>
            </Link>
          ))}
        </section>
      ) : null}
    </div>
  );
}
