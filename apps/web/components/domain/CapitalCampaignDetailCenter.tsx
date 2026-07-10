"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiRequestError } from "@/lib/api/arena";
import { getCapitalCampaign, type CapitalCampaign } from "@/lib/api/capital-campaigns";

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

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown";
  }
  return parsed.toLocaleString();
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load campaign detail.";
}

function statusBadge(status: CapitalCampaign["status"]): string {
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

export default function CapitalCampaignDetailCenter({ campaignUuid }: { campaignUuid: string }) {
  const [campaign, setCampaign] = useState<CapitalCampaign | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const item = await getCapitalCampaign(campaignUuid);
        if (!active) {
          return;
        }
        setCampaign(item);
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
  }, [campaignUuid]);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <p className="text-xs uppercase tracking-[0.3em] text-foreground/60">Capital Campaign Detail</p>
        <h1 className="text-2xl font-semibold text-foreground">{campaign?.name ?? "Campaign"}</h1>
        <p className="text-sm text-foreground/75">Campaign-scoped accounting foundation and relationships view.</p>
      </header>

      {error ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          {error}
        </section>
      ) : null}

      {loading ? (
        <section className="rounded-2xl border border-border bg-muted/30 p-3 text-sm text-foreground/80">Loading campaign...</section>
      ) : null}

      {campaign ? (
        <section className="rounded-2xl border border-border bg-background/55 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs text-foreground/65">Campaign UUID</p>
              <p className="font-mono text-sm text-foreground/90">{campaign.uuid}</p>
            </div>
            <span className={`rounded-full border px-3 py-1 text-xs ${statusBadge(campaign.status)}`}>
              {campaign.status.replaceAll("_", " ")}
            </span>
          </div>

          <dl className="mt-4 grid gap-3 text-sm text-foreground/85 md:grid-cols-2">
            <div><dt className="text-foreground/60">Owner</dt><dd>{campaign.owner}</dd></div>
            <div><dt className="text-foreground/60">Campaign Type</dt><dd>{campaign.campaign_type}</dd></div>
            <div><dt className="text-foreground/60">Exchange</dt><dd>{campaign.exchange ?? "Not set"}</dd></div>
            <div><dt className="text-foreground/60">Starting Capital</dt><dd>{formatCurrency(campaign.starting_capital)}</dd></div>
            <div><dt className="text-foreground/60">Current Equity</dt><dd>{formatCurrency(campaign.current_equity)}</dd></div>
            <div><dt className="text-foreground/60">Realized Profit</dt><dd>{formatCurrency(campaign.realized_profit)}</dd></div>
            <div><dt className="text-foreground/60">Unrealized Profit</dt><dd>{formatCurrency(campaign.unrealized_profit)}</dd></div>
            <div><dt className="text-foreground/60">Fees</dt><dd>{formatCurrency(campaign.fees)}</dd></div>
            <div><dt className="text-foreground/60">ROI</dt><dd>{formatPercent(campaign.roi)}</dd></div>
            <div><dt className="text-foreground/60">Validation Run</dt><dd>{campaign.validation_run_id ?? "Not linked"}</dd></div>
            <div><dt className="text-foreground/60">Paper Account</dt><dd>{campaign.paper_account_id ?? "Not linked"}</dd></div>
            <div><dt className="text-foreground/60">Strategy</dt><dd>{campaign.strategy_id ?? "Not linked"}</dd></div>
            <div><dt className="text-foreground/60">Created At</dt><dd>{formatTimestamp(campaign.created_at)}</dd></div>
            <div><dt className="text-foreground/60">Updated At</dt><dd>{formatTimestamp(campaign.updated_at)}</dd></div>
          </dl>

          <div className="mt-5 flex gap-3">
            <Link href="/capital-campaigns" className="rounded-full border border-border bg-background/70 px-4 py-2 text-sm font-semibold text-foreground">
              Back to Campaigns
            </Link>
            <Link href="/mission-control" className="rounded-full border border-cyan-400/40 bg-cyan-500/15 px-4 py-2 text-sm font-semibold text-cyan-100">
              Mission Control
            </Link>
          </div>
        </section>
      ) : null}

      {!loading && !error && !campaign ? (
        <section className="rounded-2xl border border-border bg-background/60 p-4 text-sm text-foreground/75">
          Campaign not found.
        </section>
      ) : null}
    </div>
  );
}
