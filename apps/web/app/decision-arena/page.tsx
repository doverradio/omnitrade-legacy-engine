"use client";

import { useCallback, useMemo, useState } from "react";

import {
  ApiRequestError,
  getArenaComparisonLatest,
  getArenaLeaderboardLatest,
  getArenaTournamentHistory,
  type ArenaComparisonResponse,
  type ArenaLeaderboardResponse,
  type ArenaMetric,
  type ArenaTournamentHistoryResponse,
} from "@/lib/api/arena";

type Filters = {
  competitionId: string;
  tournamentId: string;
  cycleId: string;
  availabilityMode: "all" | "known_only";
};

const INITIAL_FILTERS: Filters = {
  competitionId: "",
  tournamentId: "",
  cycleId: "",
  availabilityMode: "all",
};

function metricText(metric: ArenaMetric): string {
  if (metric.value === null) {
    return `${metric.status}${metric.reason ? ` (${metric.reason})` : ""}`;
  }
  return `${metric.value} (${metric.status})`;
}

function toLocal(value: string | null): string {
  if (!value) {
    return "Not available";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return fallback;
}

export default function DecisionArenaPage() {
  const [filters, setFilters] = useState<Filters>(INITIAL_FILTERS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [leaderboard, setLeaderboard] = useState<ArenaLeaderboardResponse | null>(null);
  const [comparison, setComparison] = useState<ArenaComparisonResponse | null>(null);
  const [history, setHistory] = useState<ArenaTournamentHistoryResponse | null>(null);
  const [selectedReplayIndex, setSelectedReplayIndex] = useState(0);

  const canLoad = filters.competitionId.trim().length > 0;

  const loadArenaReadModels = useCallback(async () => {
    if (!filters.competitionId.trim()) {
      setError("Competition ID is required.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const [leaderboardPayload, comparisonPayload, historyPayload] = await Promise.all([
        getArenaLeaderboardLatest({
          competitionId: filters.competitionId,
          tournamentId: filters.tournamentId || undefined,
          cycleId: filters.cycleId || undefined,
          availabilityMode: filters.availabilityMode,
        }),
        getArenaComparisonLatest({
          competitionId: filters.competitionId,
          tournamentId: filters.tournamentId || undefined,
          cycleId: filters.cycleId || undefined,
        }),
        filters.tournamentId
          ? getArenaTournamentHistory({
              competitionId: filters.competitionId,
              tournamentId: filters.tournamentId,
            })
          : Promise.resolve(null),
      ]);

      setLeaderboard(leaderboardPayload);
      setComparison(comparisonPayload);
      setHistory(historyPayload);
      setSelectedReplayIndex(0);
    } catch (fetchError) {
      setError(errorMessage(fetchError, "Failed to load Decision Arena read models."));
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const selectedReplayEvent = useMemo(() => {
    if (!history || history.history.length === 0) {
      return null;
    }
    const safeIndex = Math.min(selectedReplayIndex, history.history.length - 1);
    return history.history[safeIndex];
  }, [history, selectedReplayIndex]);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Decision Arena Dashboard</h1>
        <p className="max-w-3xl text-sm text-foreground/75">
          Read-only Decision Arena explorer for tournaments, leaderboard rankings, comparisons, and replay metadata.
        </p>
        <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-100">
          Observational only: no trading, no portfolio modification, no strategy mutation, no capital allocation, and no promotion controls.
        </p>
      </header>

      <section className="rounded-xl border border-border bg-muted/20 p-4 sm:p-5" aria-labelledby="arena-filters-heading">
        <h2 id="arena-filters-heading" className="text-lg font-semibold">
          Arena Query Filters
        </h2>
        <p className="mt-1 text-xs text-foreground/70">
          All fields map to read-only API query parameters. Tournament history and replay viewer require both competition and tournament IDs.
        </p>

        <form
          className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
          onSubmit={(event) => {
            event.preventDefault();
            void loadArenaReadModels();
          }}
        >
          <label className="text-sm" htmlFor="competition-id">
            <span className="mb-1 block text-foreground/80">Competition ID</span>
            <input
              id="competition-id"
              value={filters.competitionId}
              onChange={(event) => setFilters((current) => ({ ...current, competitionId: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
              placeholder="required UUID"
              aria-required="true"
            />
          </label>

          <label className="text-sm" htmlFor="tournament-id">
            <span className="mb-1 block text-foreground/80">Tournament ID</span>
            <input
              id="tournament-id"
              value={filters.tournamentId}
              onChange={(event) => setFilters((current) => ({ ...current, tournamentId: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
              placeholder="optional UUID"
            />
          </label>

          <label className="text-sm" htmlFor="cycle-id">
            <span className="mb-1 block text-foreground/80">Cycle ID</span>
            <input
              id="cycle-id"
              value={filters.cycleId}
              onChange={(event) => setFilters((current) => ({ ...current, cycleId: event.target.value }))}
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
              placeholder="optional UUID"
            />
          </label>

          <label className="text-sm" htmlFor="availability-mode">
            <span className="mb-1 block text-foreground/80">Availability Mode</span>
            <select
              id="availability-mode"
              value={filters.availabilityMode}
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  availabilityMode: event.target.value as "all" | "known_only",
                }))
              }
              className="w-full rounded-md border border-border bg-background/70 px-3 py-2"
            >
              <option value="all">all</option>
              <option value="known_only">known_only</option>
            </select>
          </label>

          <div className="sm:col-span-2 lg:col-span-4">
            <button
              type="submit"
              className="rounded-md border border-border bg-background px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60"
              disabled={loading || !canLoad}
            >
              {loading ? "Loading..." : "Load Arena Dashboard"}
            </button>
          </div>
        </form>
      </section>

      {error ? (
        <section className="rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm text-red-100" role="alert">
          {error}
        </section>
      ) : null}

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3" aria-live="polite">
        <article className="rounded-xl border border-border bg-background/40 p-4">
          <h2 className="text-lg font-semibold">Leaderboard</h2>
          {leaderboard ? (
            <>
              <p className="mt-1 text-xs text-foreground/70">
                State: {leaderboard.availability_state} | Scope: {leaderboard.snapshot_scope}
              </p>
              <div className="mt-3 overflow-x-auto">
                <table className="min-w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-border text-foreground/70">
                      <th className="px-2 py-1">Rank</th>
                      <th className="px-2 py-1">Agent</th>
                      <th className="px-2 py-1">Composite</th>
                      <th className="px-2 py-1">Profit</th>
                      <th className="px-2 py-1">DQ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leaderboard.entries.map((entry) => (
                      <tr key={entry.agent_id} className="border-b border-border/60">
                        <td className="px-2 py-1">{entry.rank}</td>
                        <td className="px-2 py-1 font-mono">{entry.agent_id}</td>
                        <td className="px-2 py-1">{metricText(entry.composite_rank_score)}</td>
                        <td className="px-2 py-1">{metricText(entry.profit)}</td>
                        <td className="px-2 py-1">{metricText(entry.decision_quality)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <p className="mt-2 text-sm text-foreground/70">Load filters to fetch leaderboard read model.</p>
          )}
        </article>

        <article className="rounded-xl border border-border bg-background/40 p-4">
          <h2 className="text-lg font-semibold">Comparisons</h2>
          {comparison ? (
            <>
              <p className="mt-1 text-xs text-foreground/70">
                State: {comparison.availability_state} | Agents: {comparison.compared_agent_ids.length}
              </p>
              <ul className="mt-3 space-y-2 text-xs">
                {comparison.agent_summaries.map((summary) => (
                  <li key={summary.agent_id} className="rounded-md border border-border p-2">
                    <p className="font-mono">{summary.agent_id}</p>
                    <p>Decision Quality: {metricText(summary.decision_quality)}</p>
                    <p>Explainability: {metricText(summary.explainability_support_ratio)}</p>
                    <p>Counterfactual: {metricText(summary.counterfactual_correctness)}</p>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="mt-2 text-sm text-foreground/70">Load filters to fetch comparison read model.</p>
          )}
        </article>

        <article className="rounded-xl border border-border bg-background/40 p-4">
          <h2 className="text-lg font-semibold">Tournament Lifecycle</h2>
          {history ? (
            <>
              <p className="mt-1 text-xs text-foreground/70">
                State: {history.availability_state} | Current: {history.current_state ?? "n/a"}
              </p>
              <p className="mt-1 text-xs text-foreground/70">
                Latest event: {history.latest_event_type ?? "n/a"} at {toLocal(history.latest_event_timestamp)}
              </p>
              <p className="mt-1 text-xs text-foreground/70">History count: {history.history_count}</p>
              <pre className="mt-3 overflow-x-auto rounded-md border border-border/70 bg-background/50 p-2 text-[11px] leading-5">
                {JSON.stringify(history.latest_schedule_payload, null, 2)}
              </pre>
            </>
          ) : (
            <p className="mt-2 text-sm text-foreground/70">
              Enter a Tournament ID and load filters to fetch lifecycle history and replay metadata.
            </p>
          )}
        </article>
      </section>

      <section className="rounded-xl border border-border bg-background/40 p-4" aria-labelledby="replay-heading">
        <h2 id="replay-heading" className="text-lg font-semibold">
          Tournament Replay Viewer
        </h2>
        <p className="mt-1 text-xs text-foreground/70">
          Replay viewer is read-only and reconstructs ordering, tie-breaks, schedule payloads, and replay metadata from append-only tournament history events.
        </p>

        {history && history.history.length > 0 ? (
          <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
            <div className="space-y-2" role="list" aria-label="Replay events">
              {history.history.map((item, index) => (
                <button
                  key={item.history_record_id}
                  type="button"
                  className={`w-full rounded-md border px-3 py-2 text-left text-xs ${
                    selectedReplayIndex === index
                      ? "border-emerald-400/50 bg-emerald-500/10"
                      : "border-border bg-background/50"
                  }`}
                  onClick={() => setSelectedReplayIndex(index)}
                >
                  <p className="font-semibold">#{item.sequence_number} {item.event_type}</p>
                  <p className="text-foreground/70">{toLocal(item.event_timestamp)}</p>
                </button>
              ))}
            </div>

            <div className="space-y-3 rounded-md border border-border p-3 text-xs">
              {selectedReplayEvent ? (
                <>
                  <p>
                    <span className="font-semibold">Lifecycle:</span> {selectedReplayEvent.lifecycle_state}
                  </p>
                  <p>
                    <span className="font-semibold">Event hash:</span> <span className="font-mono">{selectedReplayEvent.event_hash}</span>
                  </p>
                  <div>
                    <p className="font-semibold">Tie-break rules</p>
                    <ul className="ml-4 list-disc">
                      {selectedReplayEvent.tie_break_rules.map((rule) => (
                        <li key={rule}>{rule}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <p className="font-semibold">Ordering rules</p>
                    <ul className="ml-4 list-disc">
                      {selectedReplayEvent.ordering_rules.map((rule) => (
                        <li key={rule}>{rule}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <div>
                      <p className="font-semibold">Replay metadata</p>
                      <pre className="overflow-x-auto rounded-md border border-border/70 bg-background/60 p-2 text-[11px] leading-5">
                        {JSON.stringify(selectedReplayEvent.replay_metadata, null, 2)}
                      </pre>
                    </div>
                    <div>
                      <p className="font-semibold">Schedule payload</p>
                      <pre className="overflow-x-auto rounded-md border border-border/70 bg-background/60 p-2 text-[11px] leading-5">
                        {JSON.stringify(selectedReplayEvent.schedule_payload, null, 2)}
                      </pre>
                    </div>
                  </div>
                </>
              ) : (
                <p>No replay event selected.</p>
              )}
            </div>
          </div>
        ) : (
          <p className="mt-3 text-sm text-foreground/70">No replay events available.</p>
        )}
      </section>
    </div>
  );
}
