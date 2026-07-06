"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  disableKillSwitch,
  enableKillSwitch,
  getRiskRules,
  getRiskStatus,
  patchRiskRules,
  type RiskRules,
  type RiskRulesResponse,
  type RiskStatusResponse,
} from "@/lib/api/risk";

type KillSwitchIntent = {
  action: "enable" | "disable";
  scope: "global" | "account";
};

type RuleFieldKey = keyof RiskRules;

type RulesDraft = {
  max_position_size_pct: string;
  max_daily_loss_pct: string;
  max_drawdown_pct: string;
  default_stop_loss_pct: string;
  cooldown_after_losses: string;
  cooldown_duration_hours: string;
};

const DEFAULT_ACCOUNT_PLACEHOLDER = "Enter paper account UUID";

function parseNumber(value: string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function percentLabel(ratio: string): string {
  const numeric = parseNumber(ratio);
  return `${(numeric * 100).toFixed(1)}%`;
}

function toRulesDraft(rules: RiskRules): RulesDraft {
  return {
    max_position_size_pct: rules.max_position_size_pct,
    max_daily_loss_pct: rules.max_daily_loss_pct,
    max_drawdown_pct: rules.max_drawdown_pct,
    default_stop_loss_pct: rules.default_stop_loss_pct,
    cooldown_after_losses: String(rules.cooldown_after_losses),
    cooldown_duration_hours: String(rules.cooldown_duration_hours),
  };
}

function toPatchRules(next: RulesDraft): Partial<RiskRules> {
  return {
    max_position_size_pct: next.max_position_size_pct,
    max_daily_loss_pct: next.max_daily_loss_pct,
    max_drawdown_pct: next.max_drawdown_pct,
    default_stop_loss_pct: next.default_stop_loss_pct,
    cooldown_after_losses: Number(next.cooldown_after_losses),
    cooldown_duration_hours: Number(next.cooldown_duration_hours),
  };
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

function isoDateTime(value: string | null | undefined): string {
  if (!value) {
    return "Not available";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function progressWidth(pctUsed: string): string {
  const ratio = Math.max(0, Math.min(1, parseNumber(pctUsed)));
  return `${(ratio * 100).toFixed(1)}%`;
}

function isLoosening(currentRules: RiskRules, draftRules: RulesDraft): boolean {
  const currentCooldownAfterLosses = Number(currentRules.cooldown_after_losses);
  const currentCooldownDuration = Number(currentRules.cooldown_duration_hours);
  const nextCooldownAfterLosses = Number(draftRules.cooldown_after_losses);
  const nextCooldownDuration = Number(draftRules.cooldown_duration_hours);

  if (parseNumber(draftRules.max_position_size_pct) > parseNumber(currentRules.max_position_size_pct)) {
    return true;
  }
  if (parseNumber(draftRules.max_daily_loss_pct) > parseNumber(currentRules.max_daily_loss_pct)) {
    return true;
  }
  if (parseNumber(draftRules.max_drawdown_pct) > parseNumber(currentRules.max_drawdown_pct)) {
    return true;
  }
  if (parseNumber(draftRules.default_stop_loss_pct) > parseNumber(currentRules.default_stop_loss_pct)) {
    return true;
  }
  if (nextCooldownAfterLosses < currentCooldownAfterLosses) {
    return true;
  }
  if (nextCooldownDuration < currentCooldownDuration) {
    return true;
  }

  return false;
}

function buildDiffSummary(currentRules: RiskRules, draftRules: RulesDraft): string[] {
  const messages: string[] = [];
  const mapping: Array<{ key: RuleFieldKey; label: string }> = [
    { key: "max_position_size_pct", label: "Max position size" },
    { key: "max_daily_loss_pct", label: "Max daily loss" },
    { key: "max_drawdown_pct", label: "Max drawdown" },
    { key: "default_stop_loss_pct", label: "Default stop loss" },
    { key: "cooldown_after_losses", label: "Cooldown after losses" },
    { key: "cooldown_duration_hours", label: "Cooldown duration (hours)" },
  ];

  for (const field of mapping) {
    const beforeValue = String(currentRules[field.key]);
    const afterValue = String(draftRules[field.key]);
    if (beforeValue !== afterValue) {
      messages.push(`${field.label}: ${beforeValue} -> ${afterValue}`);
    }
  }

  return messages;
}

export default function RiskMonitorPage() {
  const [accountIdInput, setAccountIdInput] = useState("");
  const [loadedAccountId, setLoadedAccountId] = useState<string | null>(null);

  const [statusData, setStatusData] = useState<RiskStatusResponse | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusUnknown, setStatusUnknown] = useState(false);

  const [rulesData, setRulesData] = useState<RiskRulesResponse | null>(null);
  const [rulesDraft, setRulesDraft] = useState<RulesDraft | null>(null);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesSaving, setRulesSaving] = useState(false);
  const [rulesError, setRulesError] = useState<string | null>(null);

  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);
  const [confirmIntent, setConfirmIntent] = useState<KillSwitchIntent | "rules-save" | null>(null);
  const [confirmReason, setConfirmReason] = useState("");
  const [confirmLoosening, setConfirmLoosening] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [isMutating, setIsMutating] = useState(false);

  const canLoad = accountIdInput.trim().length > 0;

  const loadStatus = useCallback(async (accountId: string) => {
    setStatusLoading(true);
    setStatusError(null);

    try {
      const payload = await getRiskStatus(accountId);
      setStatusData(payload);
      setStatusUnknown(false);
    } catch (error) {
      const message = getErrorMessage(error, "Failed to load risk status.");
      setStatusError(message);
      setStatusData(null);
      if (error instanceof ApiRequestError && error.status === 503) {
        setStatusUnknown(true);
      } else {
        setStatusUnknown(false);
      }
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const loadRules = useCallback(async (accountId: string) => {
    setRulesLoading(true);
    setRulesError(null);

    try {
      const payload = await getRiskRules(accountId);
      setRulesData(payload);
      setRulesDraft(toRulesDraft(payload.rules));
    } catch (error) {
      const message = getErrorMessage(error, "Failed to load risk rules.");
      setRulesError(message);
      setRulesData(null);
      setRulesDraft(null);
    } finally {
      setRulesLoading(false);
    }
  }, []);

  const refreshAll = useCallback(async (accountId: string) => {
    await Promise.all([loadStatus(accountId), loadRules(accountId)]);
  }, [loadRules, loadStatus]);

  useEffect(() => {
    if (!loadedAccountId) {
      return;
    }

    const timer = window.setInterval(() => {
      void loadStatus(loadedAccountId);
    }, 20000);

    return () => {
      window.clearInterval(timer);
    };
  }, [loadedAccountId, loadStatus]);

  const onLoadAccount = useCallback(async () => {
    const accountId = accountIdInput.trim();
    if (!accountId) {
      return;
    }
    setLoadedAccountId(accountId);
    await refreshAll(accountId);
  }, [accountIdInput, refreshAll]);

  const openKillSwitchConfirm = useCallback((intent: KillSwitchIntent) => {
    setConfirmIntent(intent);
    setConfirmReason("");
    setConfirmLoosening(false);
    setConfirmError(null);
    setConfirmDialogOpen(true);
  }, []);

  const openRulesSaveConfirm = useCallback(() => {
    setConfirmIntent("rules-save");
    setConfirmReason("");
    setConfirmLoosening(false);
    setConfirmError(null);
    setConfirmDialogOpen(true);
  }, []);

  const closeConfirmDialog = useCallback(() => {
    if (isMutating) {
      return;
    }
    setConfirmDialogOpen(false);
    setConfirmIntent(null);
    setConfirmReason("");
    setConfirmLoosening(false);
    setConfirmError(null);
  }, [isMutating]);

  const saveRules = useCallback(async () => {
    if (!loadedAccountId || !rulesData || !rulesDraft) {
      return;
    }

    const loosening = isLoosening(rulesData.rules, rulesDraft);
    if (loosening && !confirmLoosening) {
      setConfirmError("You must explicitly confirm loosening before saving these rule changes.");
      return;
    }
    if (!confirmReason.trim()) {
      setConfirmError("Please provide a short reason for this rules update.");
      return;
    }

    setIsMutating(true);
    setRulesSaving(true);
    setRulesError(null);

    try {
      const updated = await patchRiskRules({
        account_id: loadedAccountId,
        rules: toPatchRules(rulesDraft),
        confirm_loosening: loosening ? true : undefined,
        actor: "user:risk-monitor",
      });
      setRulesData(updated);
      setRulesDraft(toRulesDraft(updated.rules));
      setConfirmDialogOpen(false);
      setConfirmIntent(null);
    } catch (error) {
      setRulesError(getErrorMessage(error, "Failed to save risk rules."));
    } finally {
      setRulesSaving(false);
      setIsMutating(false);
    }
  }, [confirmLoosening, confirmReason, loadedAccountId, rulesData, rulesDraft]);

  const applyKillSwitch = useCallback(async () => {
    if (!loadedAccountId || !confirmIntent || confirmIntent === "rules-save") {
      return;
    }
    if (!confirmReason.trim()) {
      setConfirmError("Please provide a short reason before submitting.");
      return;
    }

    setIsMutating(true);
    setConfirmError(null);

    try {
      const payload = {
        scope: confirmIntent.scope,
        account_id: confirmIntent.scope === "account" ? loadedAccountId : null,
        reason: confirmReason.trim(),
        confirm: true as const,
        actor: "user:risk-monitor",
      };

      if (confirmIntent.action === "enable") {
        await enableKillSwitch(payload);
      } else {
        await disableKillSwitch(payload);
      }

      setConfirmDialogOpen(false);
      setConfirmIntent(null);
      await loadStatus(loadedAccountId);
    } catch (error) {
      setConfirmError(getErrorMessage(error, "Failed to update kill switch state."));
    } finally {
      setIsMutating(false);
    }
  }, [confirmIntent, confirmReason, loadedAccountId, loadStatus]);

  const onConfirmSubmit = useCallback(async () => {
    if (confirmIntent === "rules-save") {
      await saveRules();
      return;
    }
    await applyKillSwitch();
  }, [applyKillSwitch, confirmIntent, saveRules]);

  const rulesDiff = useMemo(() => {
    if (!rulesData || !rulesDraft) {
      return [];
    }
    return buildDiffSummary(rulesData.rules, rulesDraft);
  }, [rulesData, rulesDraft]);

  const rulesAreLoosening = useMemo(() => {
    if (!rulesData || !rulesDraft) {
      return false;
    }
    return isLoosening(rulesData.rules, rulesDraft);
  }, [rulesData, rulesDraft]);

  const disableButtons = !loadedAccountId || isMutating || statusLoading;

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5">
        <h1 className="text-2xl font-semibold">Risk Monitor</h1>
        <p className="mt-2 text-sm text-foreground/80">
          Paper-trading safety controls. This page reads risk state from the Risk Monitor API and never computes risk decisions in the browser.
        </p>
        <p className="mt-1 text-xs text-foreground/65">Paper mode only. No live trading actions are available.</p>

        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex-1 text-sm">
            <span className="mb-1 block text-foreground/80">Paper account ID</span>
            <input
              value={accountIdInput}
              onChange={(event) => setAccountIdInput(event.target.value)}
              placeholder={DEFAULT_ACCOUNT_PLACEHOLDER}
              className="w-full rounded-md border border-border bg-background/60 px-3 py-2 text-sm"
              aria-label="Paper account ID"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              void onLoadAccount();
            }}
            disabled={!canLoad || statusLoading || rulesLoading}
            className="rounded-md border border-accent/70 bg-accent/20 px-4 py-2 text-sm font-semibold text-foreground disabled:cursor-not-allowed disabled:opacity-60"
          >
            {statusLoading || rulesLoading ? "Loading..." : "Load Risk Status"}
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-border bg-muted/25 p-4 sm:p-5" aria-live="polite">
        <h2 className="text-lg font-semibold">Status Strip</h2>
        {statusLoading ? (
          <div className="mt-3 rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/80">
            Checking risk status... status is not assumed safe while loading.
          </div>
        ) : null}

        {statusUnknown ? (
          <div role="alert" className="mt-3 rounded-md border border-amber-400/70 bg-amber-500/20 p-3 text-sm text-amber-100">
            STATUS UNKNOWN - trading state is unavailable/unsafe. Do not assume this account is clear to trade.
          </div>
        ) : null}

        {statusError && !statusUnknown ? (
          <div role="alert" className="mt-3 rounded-md border border-red-400/70 bg-red-500/20 p-3 text-sm text-red-100">
            {statusError}
          </div>
        ) : null}

        {statusData ? (
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <article className="rounded-md border border-border bg-background/40 p-3">
              <p className="text-xs uppercase tracking-wide text-foreground/65">Global kill switch</p>
              <p className={`mt-1 text-base font-semibold ${statusData.global_kill_switch.engaged ? "text-red-200" : "text-emerald-200"}`}>
                {statusData.global_kill_switch.engaged ? "Engaged" : "Disengaged"}
              </p>
              <p className="mt-1 text-xs text-foreground/70">Changed: {isoDateTime(statusData.global_kill_switch.engaged_at)}</p>
              <p className="mt-1 text-xs text-foreground/70">Reason: {statusData.global_kill_switch.reason ?? "No active reason"}</p>
            </article>

            <article className="rounded-md border border-border bg-background/40 p-3">
              <p className="text-xs uppercase tracking-wide text-foreground/65">Account trading state</p>
              <p className={`mt-1 text-base font-semibold ${statusData.account.trading_paused ? "text-red-200" : "text-emerald-200"}`}>
                {statusData.account.trading_paused ? "Paused" : "Active"}
              </p>
              <p className="mt-1 text-xs text-foreground/70">Reason: {statusData.account.paused_reason ?? "No pause reason"}</p>
              <p className="mt-1 text-xs text-foreground/70">Account: {statusData.account.account_id}</p>
            </article>
          </div>
        ) : (
          !statusLoading && !statusError && <p className="mt-3 text-sm text-foreground/70">Load an account to view risk status.</p>
        )}
      </section>

      <section className="rounded-xl border border-border bg-muted/25 p-4 sm:p-5">
        <h2 className="text-lg font-semibold">Kill Switch Controls</h2>
        <p className="mt-2 text-sm text-foreground/75">
          These controls are immediate paper-risk safety actions. Every change requires confirmation and an audit reason.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <button
            type="button"
            disabled={disableButtons}
            onClick={() => openKillSwitchConfirm({ action: "enable", scope: "global" })}
            className="rounded-md border border-red-400/70 bg-red-500/20 px-3 py-2 text-sm font-semibold text-red-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Engage Global Kill Switch
          </button>
          <button
            type="button"
            disabled={disableButtons}
            onClick={() => openKillSwitchConfirm({ action: "disable", scope: "global" })}
            className="rounded-md border border-emerald-400/70 bg-emerald-500/20 px-3 py-2 text-sm font-semibold text-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Disable Global Kill Switch
          </button>
          <button
            type="button"
            disabled={disableButtons}
            onClick={() => openKillSwitchConfirm({ action: "enable", scope: "account" })}
            className="rounded-md border border-red-400/70 bg-red-500/15 px-3 py-2 text-sm font-semibold text-red-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Pause This Account
          </button>
          <button
            type="button"
            disabled={disableButtons}
            onClick={() => openKillSwitchConfirm({ action: "disable", scope: "account" })}
            className="rounded-md border border-emerald-400/70 bg-emerald-500/15 px-3 py-2 text-sm font-semibold text-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Resume This Account
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-border bg-muted/25 p-4 sm:p-5">
        <h2 className="text-lg font-semibold">Risk Limit Usage</h2>
        {statusData ? (
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <article className="rounded-md border border-border bg-background/40 p-3">
              <p className="text-sm font-semibold">Daily Loss Usage</p>
              <p className="mt-1 text-xs text-foreground/70">
                Used {statusData.account.daily_loss.used} of {statusData.account.daily_loss.limit} ({percentLabel(statusData.account.daily_loss.pct_used)})
              </p>
              <div className="mt-3 h-2 rounded bg-foreground/10">
                <div
                  className="h-2 rounded bg-amber-300"
                  style={{ width: progressWidth(statusData.account.daily_loss.pct_used) }}
                  aria-label="Daily loss progress"
                />
              </div>
            </article>

            <article className="rounded-md border border-border bg-background/40 p-3">
              <p className="text-sm font-semibold">Drawdown Usage</p>
              <p className="mt-1 text-xs text-foreground/70">
                Used {statusData.account.drawdown.used} of {statusData.account.drawdown.limit} ({percentLabel(statusData.account.drawdown.pct_used)})
              </p>
              <div className="mt-3 h-2 rounded bg-foreground/10">
                <div
                  className="h-2 rounded bg-orange-300"
                  style={{ width: progressWidth(statusData.account.drawdown.pct_used) }}
                  aria-label="Drawdown progress"
                />
              </div>
            </article>
          </div>
        ) : (
          <p className="mt-3 text-sm text-foreground/70">No risk usage data yet. Load an account first.</p>
        )}
      </section>

      <section className="rounded-xl border border-border bg-muted/25 p-4 sm:p-5">
        <h2 className="text-lg font-semibold">Current Active Risk Events</h2>
        <p className="mt-1 text-xs text-foreground/70">
          This view shows currently active cooldowns and no-trade zones from the status API. Historical event history is not available in this page scope.
        </p>
        {!statusData ? (
          <p className="mt-3 text-sm text-foreground/70">No risk events recorded yet.</p>
        ) : (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <article className="rounded-md border border-border bg-background/40 p-3">
              <h3 className="text-sm font-semibold">Active Cooldowns</h3>
              {statusData.account.active_cooldowns.length === 0 ? (
                <p className="mt-2 text-sm text-foreground/70">No active cooldowns.</p>
              ) : (
                <ul className="mt-2 space-y-2 text-sm text-foreground/85">
                  {statusData.account.active_cooldowns.map((cooldown) => (
                    <li key={`${cooldown.strategy_id}-${cooldown.asset_id}`} className="rounded border border-border bg-background/50 p-2">
                      <p>Strategy: {cooldown.strategy_id}</p>
                      <p>Asset: {cooldown.asset_id}</p>
                      <p>Until: {isoDateTime(cooldown.cooldown_until)}</p>
                      <p>Reason: {cooldown.reason}</p>
                    </li>
                  ))}
                </ul>
              )}
            </article>

            <article className="rounded-md border border-border bg-background/40 p-3">
              <h3 className="text-sm font-semibold">Active No-Trade Zones</h3>
              {statusData.account.active_no_trade_zones.length === 0 ? (
                <p className="mt-2 text-sm text-foreground/70">No active no-trade zones.</p>
              ) : (
                <ul className="mt-2 space-y-2 text-sm text-foreground/85">
                  {statusData.account.active_no_trade_zones.map((zone) => (
                    <li key={`${zone.asset_id}-${zone.since}`} className="rounded border border-border bg-background/50 p-2">
                      <p>Asset: {zone.asset_id}</p>
                      <p>Since: {isoDateTime(zone.since)}</p>
                      <p>Reason: {zone.reason}</p>
                    </li>
                  ))}
                </ul>
              )}
            </article>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-border bg-muted/25 p-4 sm:p-5">
        <h2 className="text-lg font-semibold">Risk Rules Configuration</h2>
        <p className="mt-2 text-sm text-foreground/75">
          Update per-account risk limits from the API. Loosening changes require explicit acknowledgement.
        </p>

        {rulesLoading ? <p className="mt-3 text-sm text-foreground/75">Loading rules...</p> : null}
        {rulesError ? (
          <div role="alert" className="mt-3 rounded-md border border-red-400/70 bg-red-500/20 p-3 text-sm text-red-100">
            {rulesError}
          </div>
        ) : null}

        {!rulesLoading && !rulesData ? (
          <p className="mt-3 text-sm text-foreground/70">No rules loaded yet. Load an account first.</p>
        ) : null}

        {rulesData && rulesDraft ? (
          <>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <label className="text-sm">
                <span className="mb-1 block text-foreground/80">Max position size (ratio)</span>
                <input
                  value={rulesDraft.max_position_size_pct}
                  onChange={(event) => setRulesDraft((previous) => previous ? { ...previous, max_position_size_pct: event.target.value } : previous)}
                  className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                  inputMode="decimal"
                />
              </label>

              <label className="text-sm">
                <span className="mb-1 block text-foreground/80">Max daily loss (ratio)</span>
                <input
                  value={rulesDraft.max_daily_loss_pct}
                  onChange={(event) => setRulesDraft((previous) => previous ? { ...previous, max_daily_loss_pct: event.target.value } : previous)}
                  className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                  inputMode="decimal"
                />
              </label>

              <label className="text-sm">
                <span className="mb-1 block text-foreground/80">Max drawdown (ratio)</span>
                <input
                  value={rulesDraft.max_drawdown_pct}
                  onChange={(event) => setRulesDraft((previous) => previous ? { ...previous, max_drawdown_pct: event.target.value } : previous)}
                  className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                  inputMode="decimal"
                />
              </label>

              <label className="text-sm">
                <span className="mb-1 block text-foreground/80">Default stop loss (ratio)</span>
                <input
                  value={rulesDraft.default_stop_loss_pct}
                  onChange={(event) => setRulesDraft((previous) => previous ? { ...previous, default_stop_loss_pct: event.target.value } : previous)}
                  className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                  inputMode="decimal"
                />
              </label>

              <label className="text-sm">
                <span className="mb-1 block text-foreground/80">Cooldown after losses (count)</span>
                <input
                  value={rulesDraft.cooldown_after_losses}
                  onChange={(event) => setRulesDraft((previous) => previous ? { ...previous, cooldown_after_losses: event.target.value } : previous)}
                  className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                  inputMode="numeric"
                />
              </label>

              <label className="text-sm">
                <span className="mb-1 block text-foreground/80">Cooldown duration (hours)</span>
                <input
                  value={rulesDraft.cooldown_duration_hours}
                  onChange={(event) => setRulesDraft((previous) => previous ? { ...previous, cooldown_duration_hours: event.target.value } : previous)}
                  className="w-full rounded-md border border-border bg-background/60 px-3 py-2"
                  inputMode="numeric"
                />
              </label>
            </div>

            <div className="mt-4 rounded-md border border-border bg-background/40 p-3 text-sm text-foreground/80">
              <p className="font-semibold">Beginner note</p>
              <p className="mt-1">
                Lower max values and longer cooldowns generally make the account safer but can reduce trade frequency. Higher max values and shorter cooldowns allow more risk and require extra caution.
              </p>
            </div>

            {rulesAreLoosening ? (
              <div className="mt-4 rounded-md border border-amber-400/70 bg-amber-500/20 p-3 text-sm text-amber-100">
                This draft loosens one or more limits. Explicit confirmation will be required before saving.
              </div>
            ) : null}

            <div className="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={openRulesSaveConfirm}
                disabled={rulesSaving || isMutating}
                className="rounded-md border border-accent/70 bg-accent/20 px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60"
              >
                {rulesSaving ? "Saving..." : "Save Rule Changes"}
              </button>
              <button
                type="button"
                onClick={() => setRulesDraft(toRulesDraft(rulesData.rules))}
                disabled={rulesSaving || isMutating}
                className="rounded-md border border-border bg-background/50 px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60"
              >
                Revert Draft
              </button>
            </div>
          </>
        ) : null}
      </section>

      {confirmDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="risk-confirm-title">
          <div className="w-full max-w-lg rounded-lg border border-border bg-background p-4 sm:p-5">
            <h3 id="risk-confirm-title" className="text-lg font-semibold">
              Confirm Risk Action
            </h3>
            <p className="mt-2 text-sm text-foreground/80">
              {confirmIntent === "rules-save"
                ? "You are about to update risk rules. This is a state-changing action and will be audited."
                : "You are about to change kill switch state. This is a state-changing action and will be audited."}
            </p>

            {confirmIntent === "rules-save" && rulesDiff.length > 0 ? (
              <div className="mt-3 max-h-36 overflow-auto rounded border border-border bg-muted/30 p-2 text-sm">
                <p className="mb-1 font-semibold">Planned changes:</p>
                <ul className="space-y-1">
                  {rulesDiff.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <label className="mt-3 block text-sm">
              <span className="mb-1 block text-foreground/80">Reason (required)</span>
              <textarea
                value={confirmReason}
                onChange={(event) => setConfirmReason(event.target.value)}
                className="min-h-20 w-full rounded-md border border-border bg-background/60 px-3 py-2"
                placeholder="Describe why this change is necessary"
              />
            </label>

            {confirmIntent === "rules-save" && rulesAreLoosening ? (
              <label className="mt-3 flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={confirmLoosening}
                  onChange={(event) => setConfirmLoosening(event.target.checked)}
                  className="mt-1"
                />
                <span>
                  I confirm this update loosens risk limits and I understand this increases paper-trading risk exposure.
                </span>
              </label>
            ) : null}

            {confirmError ? (
              <div role="alert" className="mt-3 rounded-md border border-red-400/70 bg-red-500/20 p-2 text-sm text-red-100">
                {confirmError}
              </div>
            ) : null}

            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={closeConfirmDialog}
                disabled={isMutating}
                className="rounded-md border border-border bg-background/50 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  void onConfirmSubmit();
                }}
                disabled={isMutating}
                className="rounded-md border border-accent/70 bg-accent/20 px-3 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isMutating ? "Submitting..." : "Confirm and Submit"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
