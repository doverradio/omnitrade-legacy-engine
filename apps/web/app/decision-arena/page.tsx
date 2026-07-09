"use client";

import { useEffect, useMemo, useState } from "react";

import ReplayAgentsPanel from "@/components/domain/ReplayAgentsPanel";
import {
  ApiRequestError,
  evaluateReplayResult,
  getStrategyArenaScoreboard,
  replayDecisionPackage,
  type DecisionQualityResult,
  type ReplayResult,
  type StrategyArenaScoreboardResponse,
} from "@/lib/api/arena";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

function formatPercent(value: string): string {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return "0.00%";
  }
  return `${(numeric * 100).toFixed(2)}%`;
}

function formatWhen(value: string | null): string {
  if (!value) {
    return "Not available";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function statusStyles(enabled: boolean): string {
  return enabled
    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
    : "border-slate-500/40 bg-slate-500/10 text-slate-100";
}

function returnStyles(value: string): string {
  return Number(value) >= 0 ? "text-emerald-300" : "text-rose-300";
}

function formatConfidence(value: string | null): string {
  if (value === null) {
    return "n/a";
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return `${(numeric * 100).toFixed(2)}%`;
}

function formatMetric(value: string | null): string {
  if (value === null) {
    return "Planned";
  }
  return value;
}

export default function DecisionArenaPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [scoreboard, setScoreboard] = useState<StrategyArenaScoreboardResponse | null>(null);
  const [replayLoadingPackageId, setReplayLoadingPackageId] = useState<string | null>(null);
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null);
  const [qualityResult, setQualityResult] = useState<DecisionQualityResult | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const payload = await getStrategyArenaScoreboard();
        if (active) {
          setScoreboard(payload);
        }
      } catch (fetchError) {
        if (active) {
          setError(errorMessage(fetchError, "Failed to load Strategy Arena scoreboard."));
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

  const sortedItems = useMemo(() => {
    return [...(scoreboard?.items ?? [])].sort((left, right) => {
      if (left.enabled !== right.enabled) {
        return left.enabled ? -1 : 1;
      }
      return left.strategy_name.localeCompare(right.strategy_name);
    });
  }, [scoreboard]);

  const activeCount = sortedItems.filter((item) => item.enabled).length;
  const disabledCount = sortedItems.length - activeCount;

  async function handleReplay(decisionPackageId: string | null) {
    if (!decisionPackageId) {
      setReplayError("No replay package is available for this strategy yet.");
      return;
    }

    setReplayLoadingPackageId(decisionPackageId);
    setReplayError(null);
    setReplayResult(null);
    setQualityResult(null);

    try {
      const result = await replayDecisionPackage({ decision_package_id: decisionPackageId });
      setReplayResult(result);
      const quality = await evaluateReplayResult(result);
      setQualityResult(quality);
    } catch (replayRequestError) {
      if (replayRequestError instanceof ApiRequestError) {
        setReplayError(replayRequestError.message);
      } else {
        setReplayError("Replay failed.");
      }
    } finally {
      setReplayLoadingPackageId(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Decision Arena</h1>
        <p className="max-w-3xl text-sm text-foreground/75">
          Read-only Strategy Arena scoreboard for comparing production strategies and the evidence they generate.
        </p>
        <p className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-100">
          Observational only: no trading, no strategy mutation, no portfolio modification, and no capital allocation.
        </p>
      </header>

      {error ? (
        <section className="rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm text-red-100" role="alert">
          {error}
        </section>
      ) : null}

      {replayError ? (
        <section className="rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm text-red-100" role="alert">
          {replayError}
        </section>
      ) : null}

      {replayResult ? (
        <div className="space-y-3">
          <section className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-100" role="status">
            <p className="font-medium">Replay completed. Decision reproduced successfully.</p>
            <p className="mt-1 text-xs text-emerald-100/80">
              Reconstructed action: {replayResult.reconstructed_action} | Confidence: {formatConfidence(replayResult.reconstructed_confidence)}
            </p>
          </section>

          {qualityResult ? (
            <section className="rounded-md border border-border bg-background/60 px-3 py-3 text-sm text-foreground/85">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-foreground/60">Replay</p>
                  <p className="font-medium text-foreground/75">↓</p>
                  <p className="text-xs font-semibold uppercase tracking-wide text-foreground/60">Decision Quality</p>
                </div>
                <div className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-base font-semibold text-emerald-100">
                  Quality Score {qualityResult.quality_score}
                </div>
              </div>

              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Metric label="Decision reproduced" value={qualityResult.decision_reproduced ? "Yes" : "No"} />
                <Metric label="Action Match" value={qualityResult.action_matches_original ? "Yes" : "No"} />
                <Metric label="Confidence Match" value={qualityResult.confidence_matches_original ? "Yes" : "No"} />
                <Metric label="Replay Duration" value={qualityResult.replay_duration_ms === null ? null : `${qualityResult.replay_duration_ms} ms`} />
              </div>

              <div className="mt-4 border-t border-border/70 pt-4">
                <div className="flex items-center justify-between gap-2">
                  <h4 className="text-xs font-semibold uppercase tracking-wide text-foreground/60">Future metrics</h4>
                  <span className="text-xs text-foreground/45">Planned placeholders only</span>
                </div>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  <Metric label="Calibration" value={formatMetric(qualityResult.calibration)} muted={qualityResult.calibration === null} />
                  <Metric label="Opportunity Cost" value={formatMetric(qualityResult.opportunity_cost)} muted={qualityResult.opportunity_cost === null} />
                  <Metric label="Drawdown" value={formatMetric(qualityResult.drawdown)} muted={qualityResult.drawdown === null} />
                  <Metric label="Risk-Adjusted Return" value={formatMetric(qualityResult.risk_adjusted_return)} muted={qualityResult.risk_adjusted_return === null} />
                  <Metric label="Explanation Quality" value={formatMetric(qualityResult.explanation_quality)} muted={qualityResult.explanation_quality === null} />
                </div>
              </div>
            </section>
          ) : null}
        </div>
      ) : null}

      <section className="rounded-xl border border-border bg-muted/20 p-4 sm:p-5" aria-labelledby="scoreboard-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="scoreboard-heading" className="text-lg font-semibold">
              Strategy Scoreboard
            </h2>
            <p className="mt-1 text-xs text-foreground/70">
              Built to scale from today&apos;s single MA Crossover strategy to many future strategies without redesign.
            </p>
          </div>
          <div className="flex gap-2 text-xs text-foreground/75">
            <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1">Active {activeCount}</span>
            <span className="rounded-full border border-slate-500/40 bg-slate-500/10 px-3 py-1">Disabled {disabledCount}</span>
          </div>
        </div>

        <div className="mt-4 overflow-x-auto">
          {loading ? (
            <p className="py-8 text-sm text-foreground/70">Loading strategy scoreboard...</p>
          ) : sortedItems.length > 0 ? (
            <table className="min-w-[1180px] w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border text-foreground/70">
                  <th className="px-3 py-2">Strategy</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Signals</th>
                  <th className="px-3 py-2">BUY</th>
                  <th className="px-3 py-2">SELL</th>
                  <th className="px-3 py-2">HOLD</th>
                  <th className="px-3 py-2">Trades</th>
                  <th className="px-3 py-2">Open Positions</th>
                  <th className="px-3 py-2">Return %</th>
                  <th className="px-3 py-2">Decision Records</th>
                  <th className="px-3 py-2">Last Signal</th>
                  <th className="px-3 py-2">Last Trade</th>
                  <th className="px-3 py-2">Replay</th>
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((item) => (
                  <tr key={item.strategy_id} className="border-b border-border/60">
                    <td className="px-3 py-3">
                      <div className="font-semibold text-foreground/90">{item.strategy_name}</div>
                      <div className="text-xs text-foreground/60">{item.strategy_id}</div>
                    </td>
                    <td className="px-3 py-3">
                      <span className={`inline-flex rounded-full border px-2 py-1 text-xs font-medium ${statusStyles(item.enabled)}`}>
                        {item.enabled ? "Active" : "Disabled"}
                      </span>
                    </td>
                    <td className="px-3 py-3 font-medium">{item.signals_generated}</td>
                    <td className="px-3 py-3 text-emerald-300">{item.buy_signals}</td>
                    <td className="px-3 py-3 text-rose-300">{item.sell_signals}</td>
                    <td className="px-3 py-3 text-slate-300">{item.hold_signals}</td>
                    <td className="px-3 py-3">{item.paper_trades}</td>
                    <td className="px-3 py-3">{item.open_positions}</td>
                    <td className={`px-3 py-3 font-semibold ${returnStyles(item.total_return_pct)}`}>
                      {formatPercent(item.total_return_pct)}
                    </td>
                    <td className="px-3 py-3">{item.decision_records}</td>
                    <td className="px-3 py-3 text-xs text-foreground/75">{formatWhen(item.last_signal_timestamp)}</td>
                    <td className="px-3 py-3 text-xs text-foreground/75">{formatWhen(item.last_trade_timestamp)}</td>
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground/90 disabled:cursor-not-allowed disabled:opacity-60"
                        disabled={!item.latest_decision_package_id || replayLoadingPackageId === item.latest_decision_package_id}
                        onClick={() => void handleReplay(item.latest_decision_package_id)}
                      >
                        {replayLoadingPackageId === item.latest_decision_package_id ? "Replaying..." : "Replay"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="rounded-lg border border-dashed border-border bg-background/40 p-6 text-sm text-foreground/70">
              No strategies are registered yet. The arena is ready for MA Crossover today and many strategies tomorrow.
            </div>
          )}
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2" aria-label="Reserved Future Panels">
        <ReplayAgentsPanel />

        {[
          {
            title: "Decision Quality",
            text: "These panels will activate as additional replay agents and research systems are introduced.",
          },
          {
            title: "AI Coach",
            text: "These panels will activate as additional replay agents and research systems are introduced.",
          },
          {
            title: "Capital Allocation",
            text: "These panels will activate as additional replay agents and research systems are introduced.",
          },
        ].map((panel) => (
          <article key={panel.title} className="rounded-xl border border-dashed border-border bg-background/40 p-4">
            <h3 className="text-base font-semibold">{panel.title}</h3>
            <p className="mt-2 text-sm text-foreground/70">{panel.text}</p>
          </article>
        ))}
      </section>
    </div>
  );
}

function Metric({ label, value, muted = false }: { label: string; value: string | null; muted?: boolean }) {
  return (
    <div className="rounded-md border border-border/70 bg-background/40 px-3 py-2">
      <p className="text-xs uppercase tracking-wide text-foreground/55">{label}</p>
      <p className={`mt-1 text-sm font-medium ${muted ? "text-foreground/45" : "text-foreground/90"}`}>{value ?? "Planned"}</p>
    </div>
  );
}
