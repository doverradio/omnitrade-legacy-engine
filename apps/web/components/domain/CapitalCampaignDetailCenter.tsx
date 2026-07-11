"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiRequestError } from "@/lib/api/arena";
import {
  approveCapitalCampaignProfitCycle,
  evaluateCapitalCampaignProfitCycle,
  getCapitalCampaign,
  getCapitalCampaignProfitPolicy,
  listCapitalCampaignProfitCycles,
  rejectCapitalCampaignProfitCycle,
  upsertCapitalCampaignProfitPolicy,
  type CapitalCampaign,
  type CapitalCampaignProfitCycle,
  type CapitalCampaignProfitPolicy,
  type ProfitPolicyType,
} from "@/lib/api/capital-campaigns";

const POLICY_OPTIONS: Array<{ value: ProfitPolicyType; label: string }> = [
  { value: "HOLD_PROFIT", label: "Hold Profit" },
  { value: "FULL_COMPOUND", label: "Full Compound" },
  { value: "PARTIAL_COMPOUND", label: "Partial Compound" },
  { value: "WITHDRAW_PROFIT", label: "Withdraw Profit" },
  { value: "WITHDRAW_AND_COMPOUND", label: "Withdraw and Compound" },
  { value: "PROTECTED_PRINCIPAL", label: "Protected Principal" },
  { value: "MANUAL_REVIEW", label: "Manual Review" },
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
  const [policy, setPolicy] = useState<CapitalCampaignProfitPolicy | null>(null);
  const [cycles, setCycles] = useState<CapitalCampaignProfitCycle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [evaluating, setEvaluating] = useState(false);

  const [policyType, setPolicyType] = useState<ProfitPolicyType>("MANUAL_REVIEW");
  const [targetAmount, setTargetAmount] = useState("");
  const [targetPercent, setTargetPercent] = useState("");
  const [compoundPercent, setCompoundPercent] = useState("0");
  const [withdrawPercent, setWithdrawPercent] = useState("0");
  const [protectedPrincipal, setProtectedPrincipal] = useState("");
  const [minRealizedProfit, setMinRealizedProfit] = useState("0");
  const [maxCampaignCapital, setMaxCampaignCapital] = useState("");
  const [minimumCashReserve, setMinimumCashReserve] = useState("0");
  const [feeReservePercent, setFeeReservePercent] = useState("0");
  const [taxReservePercent, setTaxReservePercent] = useState("0");
  const [cooldownHours, setCooldownHours] = useState("0");
  const [requireApproval, setRequireApproval] = useState(true);

  const latestCycle = cycles.length > 0 ? cycles[0] : null;

  function syncPolicyForm(nextPolicy: CapitalCampaignProfitPolicy | null) {
    if (!nextPolicy) {
      return;
    }
    setPolicyType(nextPolicy.policy_type);
    setTargetAmount(nextPolicy.profit_target_amount ?? "");
    setTargetPercent(nextPolicy.profit_target_percent ?? "");
    setCompoundPercent(nextPolicy.compound_percent ?? "0");
    setWithdrawPercent(nextPolicy.withdraw_percent ?? "0");
    setProtectedPrincipal(nextPolicy.protected_principal_amount ?? "");
    setMinRealizedProfit(nextPolicy.minimum_realized_profit ?? "0");
    setMaxCampaignCapital(nextPolicy.maximum_campaign_capital ?? "");
    setMinimumCashReserve(nextPolicy.minimum_cash_reserve ?? "0");
    setFeeReservePercent(nextPolicy.fee_reserve_percent ?? "0");
    setTaxReservePercent(nextPolicy.tax_reserve_percent ?? "0");
    setCooldownHours(String(nextPolicy.cooldown_hours));
    setRequireApproval(nextPolicy.require_operator_approval);
  }

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [item, activePolicy, cycleItems] = await Promise.all([
          getCapitalCampaign(campaignUuid),
          getCapitalCampaignProfitPolicy(campaignUuid).catch(() => null),
          listCapitalCampaignProfitCycles(campaignUuid).catch(() => [] as CapitalCampaignProfitCycle[]),
        ]);
        if (!active) {
          return;
        }
        setCampaign(item);
        setPolicy(activePolicy);
        syncPolicyForm(activePolicy);
        setCycles(Array.isArray(cycleItems) ? cycleItems : []);
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

  async function savePolicy() {
    setSavingPolicy(true);
    setError(null);
    try {
      const updated = await upsertCapitalCampaignProfitPolicy(campaignUuid, {
        policy_type: policyType,
        profit_target_amount: targetAmount.trim() ? targetAmount.trim() : null,
        profit_target_percent: targetPercent.trim() ? targetPercent.trim() : null,
        compound_percent: compoundPercent,
        withdraw_percent: withdrawPercent,
        protected_principal_amount: protectedPrincipal.trim() ? protectedPrincipal.trim() : null,
        minimum_realized_profit: minRealizedProfit,
        maximum_campaign_capital: maxCampaignCapital.trim() ? maxCampaignCapital.trim() : null,
        minimum_cash_reserve: minimumCashReserve,
        fee_reserve_percent: feeReservePercent,
        tax_reserve_percent: taxReservePercent,
        cooldown_hours: Number(cooldownHours),
        require_operator_approval: requireApproval,
        is_active: true,
      }, policy ? "PATCH" : "POST");
      setPolicy(updated);
      syncPolicyForm(updated);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setSavingPolicy(false);
    }
  }

  async function evaluateCycle() {
    setEvaluating(true);
    setError(null);
    try {
      await evaluateCapitalCampaignProfitCycle(campaignUuid, { actor: "operator", force_new_cycle: false });
      const refreshed = await listCapitalCampaignProfitCycles(campaignUuid);
      setCycles(Array.isArray(refreshed) ? refreshed : []);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setEvaluating(false);
    }
  }

  async function approveCycle(cycleUuid: string) {
    try {
      await approveCapitalCampaignProfitCycle(campaignUuid, cycleUuid);
      const refreshed = await listCapitalCampaignProfitCycles(campaignUuid);
      setCycles(Array.isArray(refreshed) ? refreshed : []);
    } catch (requestError) {
      setError(errorMessage(requestError));
    }
  }

  async function rejectCycle(cycleUuid: string) {
    try {
      await rejectCapitalCampaignProfitCycle(campaignUuid, cycleUuid, "Operator rejected recommendation");
      const refreshed = await listCapitalCampaignProfitCycles(campaignUuid);
      setCycles(Array.isArray(refreshed) ? refreshed : []);
    } catch (requestError) {
      setError(errorMessage(requestError));
    }
  }

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

      {campaign ? (
        <section className="rounded-2xl border border-border bg-background/55 p-4 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold text-foreground">Profit Policy</h2>
            <button
              type="button"
              onClick={savePolicy}
              disabled={savingPolicy}
              className="rounded-full border border-cyan-400/40 bg-cyan-500/15 px-4 py-2 text-sm font-semibold text-cyan-100"
            >
              {savingPolicy ? "Saving..." : "Save Policy"}
            </button>
          </div>

          <div className="grid gap-3 md:grid-cols-3 text-sm">
            <label className="space-y-1">
              <span className="text-foreground/70">Policy Type</span>
              <select className="w-full rounded border border-border bg-background/60 px-2 py-2" value={policyType} onChange={(event) => setPolicyType(event.target.value as ProfitPolicyType)}>
                {POLICY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Target Amount</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={targetAmount} onChange={(event) => setTargetAmount(event.target.value)} placeholder="5.00" />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Target Percent</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={targetPercent} onChange={(event) => setTargetPercent(event.target.value)} placeholder="10" />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Compound Percent</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={compoundPercent} onChange={(event) => setCompoundPercent(event.target.value)} />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Withdraw Percent</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={withdrawPercent} onChange={(event) => setWithdrawPercent(event.target.value)} />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Protected Principal</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={protectedPrincipal} onChange={(event) => setProtectedPrincipal(event.target.value)} />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Minimum Realized Profit</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={minRealizedProfit} onChange={(event) => setMinRealizedProfit(event.target.value)} />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Maximum Campaign Capital</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={maxCampaignCapital} onChange={(event) => setMaxCampaignCapital(event.target.value)} />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Minimum Cash Reserve</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={minimumCashReserve} onChange={(event) => setMinimumCashReserve(event.target.value)} />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Fee Reserve Percent</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={feeReservePercent} onChange={(event) => setFeeReservePercent(event.target.value)} />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Tax Reserve Percent</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={taxReservePercent} onChange={(event) => setTaxReservePercent(event.target.value)} />
            </label>
            <label className="space-y-1">
              <span className="text-foreground/70">Cooldown Hours</span>
              <input className="w-full rounded border border-border bg-background/60 px-2 py-2" value={cooldownHours} onChange={(event) => setCooldownHours(event.target.value)} />
            </label>
          </div>

          <label className="inline-flex items-center gap-2 text-sm text-foreground/80">
            <input type="checkbox" checked={requireApproval} onChange={(event) => setRequireApproval(event.target.checked)} />
            Require Operator Approval
          </label>

          <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
            This is an accounting recommendation only. No funds will move.
          </div>

          <div className="grid gap-3 md:grid-cols-3 text-sm">
            <div className="rounded-xl border border-border bg-background/50 p-3">
              <p className="text-foreground/65">Realized Profit</p>
              <p className="font-semibold">{formatCurrency(campaign.realized_profit)}</p>
            </div>
            <div className="rounded-xl border border-border bg-background/50 p-3">
              <p className="text-foreground/65">Unrealized Profit</p>
              <p className="font-semibold">{formatCurrency(campaign.unrealized_profit)}</p>
            </div>
            <div className="rounded-xl border border-border bg-background/50 p-3">
              <p className="text-foreground/65">Target Progress</p>
              <p className="font-semibold">{String(latestCycle?.calculation_snapshot?.target_progress_percent ?? "Not evaluated")}</p>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={evaluateCycle}
              disabled={evaluating}
              className="rounded-full border border-emerald-500/40 bg-emerald-500/15 px-4 py-2 text-sm font-semibold text-emerald-100"
            >
              {evaluating ? "Evaluating..." : "Evaluate Profit Cycle"}
            </button>
          </div>

          <section className="rounded-xl border border-border bg-background/50 p-3 space-y-2 text-sm">
            <h3 className="font-semibold">Compounding Preview</h3>
            <p>Current campaign capital: {formatCurrency(campaign.starting_capital)}</p>
            <p>Eligible realized profit: {formatCurrency(latestCycle?.eligible_profit ?? "0")}</p>
            <p>Fees and reserves: {formatCurrency(latestCycle?.reserve_amount ?? "0")}</p>
            <p>Recommended compounding: {formatCurrency(latestCycle?.compound_amount ?? "0")}</p>
            <p>Recommended withdrawal: {formatCurrency(latestCycle?.withdrawal_amount ?? "0")}</p>
            <p>Resulting campaign capital: {formatCurrency(latestCycle?.closing_campaign_capital ?? campaign.starting_capital)}</p>
            <p>Remaining protected principal: {formatCurrency(policy?.protected_principal_amount ?? "0")}</p>
            <p className="text-amber-200">This is an accounting recommendation only. No funds will move.</p>
          </section>

          <section className="space-y-2">
            <h3 className="font-semibold">Profit Cycles</h3>
            {cycles.length === 0 ? (
              <p className="text-sm text-foreground/70">No profit cycles yet.</p>
            ) : (
              <div className="space-y-2">
                {cycles.map((cycle) => (
                  <article key={cycle.cycle_uuid} className="rounded-xl border border-border bg-background/50 p-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="font-semibold">Cycle #{cycle.cycle_number}</p>
                      <p>{cycle.status.replaceAll("_", " ")}</p>
                    </div>
                    <div className="grid gap-1 md:grid-cols-2">
                      <p>Target Reached: {cycle.target_reached ? "Yes" : "No"}</p>
                      <p>Realized Profit: {formatCurrency(cycle.realized_profit)}</p>
                      <p>Eligible Profit: {formatCurrency(cycle.eligible_profit)}</p>
                      <p>Compound Recommendation: {formatCurrency(cycle.compound_amount)}</p>
                      <p>Withdrawal Recommendation: {formatCurrency(cycle.withdrawal_amount)}</p>
                      <p>Calculated: {formatTimestamp(cycle.calculated_at)}</p>
                      <p>Settlement: {cycle.settlement_state.replaceAll("_", " ")}</p>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button
                        type="button"
                        className="rounded-full border border-emerald-500/40 bg-emerald-500/15 px-3 py-1 text-xs font-semibold text-emerald-100"
                        onClick={() => approveCycle(cycle.cycle_uuid)}
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        className="rounded-full border border-rose-500/40 bg-rose-500/15 px-3 py-1 text-xs font-semibold text-rose-100"
                        onClick={() => rejectCycle(cycle.cycle_uuid)}
                      >
                        Reject
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
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
