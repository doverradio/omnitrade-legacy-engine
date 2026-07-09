"use client";

import { useEffect, useMemo, useState } from "react";

import ReplayAgentsPanel from "@/components/domain/ReplayAgentsPanel";
import {
  ApiRequestError,
  coachReviewDecisionQuality,
  evaluateCandidates,
  getCapitalAllocationRecommendation,
  getDecisionArenaTournament,
  getResearchAgents,
  getResearchCandidates,
  getResearchLaboratoryStatus,
  evaluateReplayResult,
  getDecisionIntelligenceRecommendation,
  replayDecisionPackage,
  runResearchLaboratory,
  type AICoachObservation,
  type CandidateBatchEvaluationResponse,
  type CapitalAllocationRecommendation,
  type CandidateEvaluation,
  type DecisionIntelligenceRecommendation,
  type DecisionQualityResult,
  type ReplayResult,
  type ResearchAgent,
  type StrategyCandidate,
  type ResearchLaboratoryStatus,
  type TournamentResponse,
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

function formatNumber(value: string): string {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return numeric.toFixed(2);
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
  const [tournament, setTournament] = useState<TournamentResponse | null>(null);
  const [replayLoadingPackageId, setReplayLoadingPackageId] = useState<string | null>(null);
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null);
  const [qualityResult, setQualityResult] = useState<DecisionQualityResult | null>(null);
  const [coachObservation, setCoachObservation] = useState<AICoachObservation | null>(null);
  const [decisionIntelligence, setDecisionIntelligence] = useState<DecisionIntelligenceRecommendation | null>(null);
  const [capitalAllocation, setCapitalAllocation] = useState<CapitalAllocationRecommendation | null>(null);
  const [researchAgents, setResearchAgents] = useState<ResearchAgent[]>([]);
  const [researchCandidates, setResearchCandidates] = useState<StrategyCandidate[]>([]);
  const [candidateEvaluations, setCandidateEvaluations] = useState<CandidateEvaluation[]>([]);
  const [candidateBatchSummary, setCandidateBatchSummary] = useState<CandidateBatchEvaluationResponse | null>(null);
  const [evaluatingCandidates, setEvaluatingCandidates] = useState(false);
  const [candidateEvaluationError, setCandidateEvaluationError] = useState<string | null>(null);
  const [laboratoryStatus, setLaboratoryStatus] = useState<ResearchLaboratoryStatus | null>(null);
  const [laboratoryRunning, setLaboratoryRunning] = useState(false);
  const [laboratoryError, setLaboratoryError] = useState<string | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [payload, recommendation, allocation, agents, candidates, laboratory] = await Promise.all([
          getDecisionArenaTournament(),
          getDecisionIntelligenceRecommendation(),
          getCapitalAllocationRecommendation(),
          getResearchAgents(),
          getResearchCandidates(),
          getResearchLaboratoryStatus(),
        ]);

        if (active) {
          setTournament(payload);
          setDecisionIntelligence(recommendation);
          setCapitalAllocation(allocation);
          setResearchAgents(agents);
          setResearchCandidates(candidates);
          setCandidateEvaluations([]);
          setCandidateBatchSummary(null);
          setLaboratoryStatus(laboratory);
        }
      } catch (fetchError) {
        if (active) {
          setError(errorMessage(fetchError, "Failed to load Decision Arena Tournament."));
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

  const ranking = useMemo(() => tournament?.ranking ?? [], [tournament]);
  const champion = ranking[0] ?? null;
  const runnerUp = ranking[1] ?? null;

  async function handleReplay(decisionPackageId: string | null) {
    if (!decisionPackageId) {
      setReplayError("No replay package is available for this strategy yet.");
      return;
    }

    setReplayLoadingPackageId(decisionPackageId);
    setReplayError(null);
    setReplayResult(null);
    setQualityResult(null);
    setCoachObservation(null);

    try {
      const result = await replayDecisionPackage({ decision_package_id: decisionPackageId });
      setReplayResult(result);
      const quality = await evaluateReplayResult(result);
      setQualityResult(quality);
      const observation = await coachReviewDecisionQuality(quality);
      setCoachObservation(observation);
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

  async function handleEvaluateCandidates() {
    setCandidateEvaluationError(null);
    setEvaluatingCandidates(true);
    try {
      const response = await evaluateCandidates({
        candidate_ids: researchCandidates.map((candidate) => candidate.candidate_id),
      });
      setCandidateBatchSummary(response);
      setCandidateEvaluations(response.evaluations);
    } catch (batchError) {
      setCandidateBatchSummary(null);
      setCandidateEvaluations([]);
      setCandidateEvaluationError(errorMessage(batchError, "Failed to evaluate candidates."));
    } finally {
      setEvaluatingCandidates(false);
    }
  }

  async function handleRunLaboratory() {
    setLaboratoryError(null);
    setLaboratoryRunning(true);
    try {
      await runResearchLaboratory();
      const refreshedStatus = await getResearchLaboratoryStatus();
      setLaboratoryStatus(refreshedStatus);
    } catch (runError) {
      setLaboratoryError(errorMessage(runError, "Failed to run research laboratory."));
    } finally {
      setLaboratoryRunning(false);
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

      <section className="rounded-md border border-border bg-background/60 px-3 py-3 text-sm text-foreground/85">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-foreground/60">Deterministic AI Coach (Rule-Based)</p>
            <p className="text-xs text-foreground/45">No AI model is being used.</p>
          </div>
        </div>

        {coachObservation ? (
          <div className="mt-3 space-y-3">
            <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2">
              <p className="text-sm font-medium text-emerald-100">{coachObservation.summary}</p>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <ObservationList title="Strengths" items={coachObservation.strengths} emptyLabel="None" />
              <ObservationList title="Weaknesses" items={coachObservation.weaknesses} emptyLabel="None" />
            </div>

            <div className="grid gap-3 lg:grid-cols-3">
              <Metric label="Confidence Note" value={coachObservation.confidence_note} />
              <Metric label="Reproducibility Note" value={coachObservation.reproducibility_note} />
              <Metric label="Suggested Follow-up" value={coachObservation.suggested_follow_up} />
            </div>
          </div>
        ) : (
          <div className="mt-3 rounded-md border border-dashed border-border/70 bg-background/40 px-3 py-3 text-sm text-foreground/55">
            No coach observation yet. Run Replay to generate a deterministic AI Coach review.
          </div>
        )}
      </section>

      <section className="rounded-xl border border-border bg-background/60 p-4 sm:p-5" aria-labelledby="capital-allocation-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 id="capital-allocation-heading" className="text-base font-semibold">Capital Allocation</h3>
            <p className="mt-1 text-xs text-foreground/70">Deterministic recommendation-only paper capital allocation by tournament rank.</p>
          </div>
          <div className="text-right text-xs">
            <p className="font-semibold uppercase tracking-wide text-foreground/60">Rule-Based Capital Allocation</p>
            <p className="text-amber-200">Human Approval Required</p>
          </div>
        </div>

        {capitalAllocation && capitalAllocation.allocations.length > 0 ? (
          <div className="mt-4 space-y-3">
            <div className="rounded-md border border-border/70 bg-background/40 px-3 py-2 text-sm">
              <p className="text-xs uppercase tracking-wide text-foreground/55">Total Paper Capital</p>
              <p className="mt-1 font-semibold text-foreground/90">${formatNumber(capitalAllocation.total_paper_capital)}</p>
            </div>

            <div className="overflow-x-auto">
              <table className="min-w-[760px] w-full text-left text-sm" aria-label="Recommended Allocation">
                <thead>
                  <tr className="border-b border-border text-foreground/70">
                    <th className="px-3 py-2">Strategy</th>
                    <th className="px-3 py-2">%</th>
                    <th className="px-3 py-2">$</th>
                    <th className="px-3 py-2">Rationale</th>
                  </tr>
                </thead>
                <tbody>
                  {capitalAllocation.allocations.map((item) => (
                    <tr key={item.strategy_name} className="border-b border-border/60">
                      <td className="px-3 py-3 font-semibold text-foreground/90">{item.strategy_name}</td>
                      <td className="px-3 py-3">{formatNumber(item.allocation_percent)}%</td>
                      <td className="px-3 py-3">${formatNumber(item.allocation_amount)}</td>
                      <td className="px-3 py-3 text-xs text-foreground/75">{item.rationale}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="mt-4 rounded-md border border-dashed border-border/70 bg-background/40 px-3 py-3 text-sm text-foreground/60">
            No capital allocation recommendation available yet.
          </div>
        )}
      </section>

      <section className="rounded-xl border border-border bg-background/60 p-4 sm:p-5" aria-labelledby="research-agents-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 id="research-agents-heading" className="text-base font-semibold">Research Agents</h3>
            <p className="mt-1 text-xs text-foreground/70">Framework for deterministic candidate strategy generation.</p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded-full border border-sky-500/40 bg-sky-500/10 px-3 py-1 font-semibold text-sky-100">RESEARCH ONLY</span>
            <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 font-semibold text-amber-100">NO PRODUCTION CHANGES</span>
            <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 font-semibold text-emerald-100">HUMAN REVIEW REQUIRED</span>
          </div>
        </div>

        {researchAgents.length > 0 || researchCandidates.length > 0 ? (
          <div className="mt-4 space-y-3">
            <div className="rounded-md border border-border/70 bg-background/40 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-foreground/60">Registered Agents</p>
              <div className="mt-2 space-y-2">
                {researchAgents.map((agent) => (
                  <article key={agent.agent_id} className="rounded border border-border/60 bg-background/60 p-2">
                    <p className="text-sm font-semibold text-foreground/90">{agent.agent_name}</p>
                    <p className="mt-1 text-xs text-foreground/65">Capabilities</p>
                    <ul className="mt-1 space-y-1 text-xs text-foreground/80">
                      {agent.capabilities.map((capability) => (
                        <li key={`${agent.agent_id}-${capability}`}>{capability}</li>
                      ))}
                    </ul>
                  </article>
                ))}
              </div>
            </div>

            <div className="rounded-md border border-border/70 bg-background/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-foreground/60">Candidate Strategies</p>
                <button
                  type="button"
                  onClick={() => {
                    void handleEvaluateCandidates();
                  }}
                  disabled={researchCandidates.length === 0 || evaluatingCandidates}
                  className="rounded border border-sky-500/40 bg-sky-500/10 px-3 py-1 text-xs font-semibold text-sky-100 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {evaluatingCandidates ? "Evaluating..." : "Evaluate Candidates"}
                </button>
              </div>
              {researchCandidates.length > 0 ? (
                <div className="mt-2 overflow-x-auto">
                  <table className="min-w-[760px] w-full text-left text-sm" aria-label="Candidate Strategies">
                    <thead>
                      <tr className="border-b border-border text-foreground/70">
                        <th className="px-3 py-2">Strategy</th>
                        <th className="px-3 py-2">Description</th>
                        <th className="px-3 py-2">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {researchCandidates.map((candidate) => (
                        <tr key={candidate.candidate_id} className="border-b border-border/60">
                          <td className="px-3 py-3 font-semibold text-foreground/90">{candidate.strategy_name}</td>
                          <td className="px-3 py-3 text-xs text-foreground/75">{candidate.description}</td>
                          <td className="px-3 py-3">
                            <span className="rounded border border-sky-500/40 bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-100">
                              {candidate.status}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="mt-2 text-sm text-foreground/65">No candidate strategies available.</p>
              )}
            </div>

            <div className="rounded-md border border-border/70 bg-background/40 p-3" aria-labelledby="candidate-evaluations-heading">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h4 id="candidate-evaluations-heading" className="text-xs font-semibold uppercase tracking-wide text-foreground/60">Candidate Evaluations</h4>
                <p className="text-xs text-foreground/65">
                  {candidateBatchSummary ? `Evaluated ${candidateBatchSummary.evaluated_count} candidates.` : "No batch evaluated yet."}
                </p>
              </div>

              {candidateEvaluationError ? (
                <div className="mt-2 rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-xs text-red-100" role="alert">
                  {candidateEvaluationError}
                </div>
              ) : null}

              {candidateEvaluations.length > 0 ? (
                <div className="mt-2 overflow-x-auto">
                  <table className="min-w-[980px] w-full text-left text-sm" aria-label="Candidate Evaluations">
                    <thead>
                      <tr className="border-b border-border text-foreground/70">
                        <th className="px-3 py-2">Candidate</th>
                        <th className="px-3 py-2">Quality</th>
                        <th className="px-3 py-2">Coach Summary</th>
                        <th className="px-3 py-2">Tournament Rank</th>
                        <th className="px-3 py-2">Promotion Eligible</th>
                        <th className="px-3 py-2">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {candidateEvaluations.map((evaluation) => {
                        const candidate = researchCandidates.find((item) => item.candidate_id === evaluation.candidate_id);
                        return (
                          <tr key={evaluation.evaluation_id} className="border-b border-border/60">
                            <td className="px-3 py-3 font-semibold text-foreground/90">{candidate?.strategy_name ?? evaluation.candidate_id}</td>
                            <td className="px-3 py-3">{evaluation.decision_quality_score}</td>
                            <td className="px-3 py-3 text-xs text-foreground/75">{evaluation.ai_coach_summary}</td>
                            <td className="px-3 py-3">{evaluation.tournament_rank ?? "n/a"}</td>
                            <td className="px-3 py-3">{evaluation.promotion_eligible ? "Yes" : "No"}</td>
                            <td className="px-3 py-3">{candidate?.status ?? "PROPOSED"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="mt-2 text-sm text-foreground/65">No candidate evaluations available yet.</p>
              )}
            </div>
          </div>
        ) : (
          <div className="mt-4 rounded-md border border-dashed border-border/70 bg-background/40 px-3 py-3 text-sm text-foreground/60">
            No research agents or candidate strategies are available yet.
          </div>
        )}
      </section>

      <section className="rounded-xl border border-border bg-background/60 p-4 sm:p-5" aria-labelledby="research-laboratory-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 id="research-laboratory-heading" className="text-base font-semibold">Research Laboratory</h3>
            <p className="mt-1 text-xs text-foreground/70">Central deterministic coordinator for multi-agent research runs.</p>
          </div>
          <button
            type="button"
            onClick={() => {
              void handleRunLaboratory();
            }}
            disabled={laboratoryRunning}
            className="rounded border border-indigo-500/40 bg-indigo-500/10 px-3 py-1 text-xs font-semibold text-indigo-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {laboratoryRunning ? "Running..." : "Run Laboratory"}
          </button>
        </div>

        {laboratoryError ? (
          <div className="mt-3 rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-xs text-red-100" role="alert">
            {laboratoryError}
          </div>
        ) : null}

        {laboratoryStatus ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Metric label="Laboratory Status" value={laboratoryStatus.status} />
            <Metric
              label="Registered Agents"
              value={
                laboratoryStatus.registered_agents.length > 0
                  ? laboratoryStatus.registered_agents.join(", ")
                  : "None"
              }
            />
            <Metric label="Last Run" value={formatWhen(laboratoryStatus.last_run?.completed_at ?? null)} />
            <Metric label="Candidates Generated" value={String(laboratoryStatus.candidates_generated)} />
            <Metric label="Candidates Evaluated" value={String(laboratoryStatus.candidates_evaluated)} />
            <Metric label="Success Rate" value={laboratoryStatus.success_rate} />
          </div>
        ) : (
          <div className="mt-3 rounded-md border border-dashed border-border/70 bg-background/40 px-3 py-3 text-sm text-foreground/60">
            No laboratory status available yet.
          </div>
        )}

        {laboratoryStatus?.last_run ? (
          <div className="mt-3 rounded-md border border-border/70 bg-background/40 px-3 py-2 text-xs text-foreground/70">
            Last run included {laboratoryStatus.last_run.participating_agents.length} agent(s) and completed with status {laboratoryStatus.last_run.status}.
          </div>
        ) : (
          <div className="mt-3 rounded-md border border-dashed border-border/70 bg-background/40 px-3 py-2 text-xs text-foreground/65">
            No laboratory run has completed yet.
          </div>
        )}
      </section>

      <section className="rounded-md border border-border bg-background/60 px-3 py-3 text-sm text-foreground/85">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-foreground/60">Rule-Based Decision Intelligence</p>
            <p className="text-xs text-foreground/45">No AI model is used.</p>
          </div>
        </div>

        {decisionIntelligence ? (
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Metric
              label="Compared Strategies"
              value={decisionIntelligence.compared_strategies.length > 0 ? decisionIntelligence.compared_strategies.join(", ") : "None"}
            />
            <Metric label="Best Current Strategy" value={decisionIntelligence.highest_quality_strategy ?? "None"} />
            <Metric label="Evidence Summary" value={decisionIntelligence.evidence_summary} />
            <Metric label="Confidence Summary" value={decisionIntelligence.confidence_summary} />
            <Metric label="Recommendation Summary" value={decisionIntelligence.recommendation_summary} />
            <Metric label="Human Review Required" value={decisionIntelligence.human_review_required ? "Yes" : "No"} />
            <Metric label="Promotion Recommended" value={decisionIntelligence.promotion_recommended ? "Yes" : "No"} />
          </div>
        ) : (
          <div className="mt-3 rounded-md border border-dashed border-border/70 bg-background/40 px-3 py-3 text-sm text-foreground/55">
            No deterministic recommendation available yet.
          </div>
        )}
      </section>

      <section className="rounded-xl border border-border bg-muted/20 p-4 sm:p-5" aria-labelledby="tournament-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="tournament-heading" className="text-lg font-semibold">
              Decision Arena Tournament
            </h2>
            <p className="mt-1 text-xs text-foreground/70">
              Deterministic, read-only strategy competition based on replay evidence and quality.
            </p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-foreground/75">
            <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1">🥇 First</span>
            <span className="rounded-full border border-slate-400/40 bg-slate-400/10 px-3 py-1">🥈 Second</span>
            <span className="rounded-full border border-orange-700/40 bg-orange-700/10 px-3 py-1">🥉 Third</span>
          </div>
        </div>

        <div className="mt-4 overflow-x-auto">
          {loading ? (
            <p className="py-8 text-sm text-foreground/70">Loading tournament rankings...</p>
          ) : ranking.length > 0 ? (
            <table className="min-w-[1180px] w-full text-left text-sm" aria-label="Tournament Ranking">
              <thead>
                <tr className="border-b border-border text-foreground/70">
                  <th className="px-3 py-2">Rank</th>
                  <th className="px-3 py-2">Strategy</th>
                  <th className="px-3 py-2">Quality</th>
                  <th className="px-3 py-2">Replay Variance</th>
                  <th className="px-3 py-2">Replay Count</th>
                  <th className="px-3 py-2">Paper Trades</th>
                  <th className="px-3 py-2">Realized PnL</th>
                  <th className="px-3 py-2">Unrealized PnL</th>
                  <th className="px-3 py-2">Win Rate</th>
                </tr>
              </thead>
              <tbody>
                {ranking.map((item) => (
                  <tr key={item.strategy_name} className={`border-b border-border/60 ${item.overall_rank === 1 ? "bg-emerald-500/10" : ""}`}>
                    <td className="px-3 py-3 font-semibold">
                      {item.overall_rank === 1 ? "🥇" : item.overall_rank === 2 ? "🥈" : item.overall_rank === 3 ? "🥉" : "#"}
                      {item.overall_rank}
                    </td>
                    <td className="px-3 py-3">
                      <div className="font-semibold text-foreground/90">
                        {item.strategy_name}
                        {item.overall_rank === 1 ? <span className="ml-2 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-100">Leader</span> : null}
                      </div>
                    </td>
                    <td className="px-3 py-3">{item.quality_score}</td>
                    <td className="px-3 py-3">{formatNumber(item.replay_variance)}</td>
                    <td className="px-3 py-3">{item.replay_count}</td>
                    <td className="px-3 py-3">{item.paper_trades}</td>
                    <td className={`px-3 py-3 font-semibold ${returnStyles(item.realized_pnl)}`}>{formatNumber(item.realized_pnl)}</td>
                    <td className={`px-3 py-3 font-semibold ${returnStyles(item.unrealized_pnl)}`}>{formatNumber(item.unrealized_pnl)}</td>
                    <td className="px-3 py-3">{item.win_rate ? formatPercent(item.win_rate) : "n/a"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="rounded-lg border border-dashed border-border bg-background/40 p-6 text-sm text-foreground/70">
              No active strategies are available for tournament comparison yet.
            </div>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-border bg-background/50 p-4 sm:p-5" aria-labelledby="tournament-summary-heading">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 id="tournament-summary-heading" className="text-base font-semibold">Tournament Summary</h3>
          <p className="text-xs text-foreground/60">Generated {tournament ? formatWhen(tournament.generated_at) : "Not available"}</p>
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Current Champion" value={champion?.strategy_name ?? "None"} />
          <Metric label="Runner Up" value={runnerUp?.strategy_name ?? "None"} />
          <Metric
            label="Evidence Summary"
            value={
              tournament && tournament.compared_strategies.length > 0
                ? `Compared ${tournament.compared_strategies.length} active strategies using deterministic tournament ranking rules.`
                : "No active strategies available for deterministic tournament evidence."
            }
          />
          <Metric label="Human Review Required" value="Yes" />
        </div>
      </section>

      <section className="rounded-xl border border-dashed border-border bg-background/40 p-4 sm:p-5" aria-labelledby="history-placeholder-heading">
        <h3 id="history-placeholder-heading" className="text-base font-semibold">History Placeholder</h3>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <article className="rounded-md border border-border/60 bg-background/40 p-3">
            <h4 className="text-sm font-semibold">Future: Tournament History</h4>
            <p className="mt-1 text-xs text-foreground/65">Historical tournament snapshots will appear here.</p>
          </article>
          <article className="rounded-md border border-border/60 bg-background/40 p-3">
            <h4 className="text-sm font-semibold">Future: Champion History</h4>
            <p className="mt-1 text-xs text-foreground/65">Champion progression by tournament cycle will appear here.</p>
          </article>
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

function ObservationList({ title, items, emptyLabel }: { title: string; items: string[]; emptyLabel: string }) {
  return (
    <div className="rounded-md border border-border/70 bg-background/40 px-3 py-2">
      <p className="text-xs uppercase tracking-wide text-foreground/55">{title}</p>
      {items.length > 0 ? (
        <ul className="mt-2 space-y-1 text-sm text-foreground/90">
          {items.map((item) => (
            <li key={item} className="rounded-md border border-border/50 bg-background/40 px-2 py-1">
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm font-medium text-foreground/45">{emptyLabel}</p>
      )}
    </div>
  );
}
