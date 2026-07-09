export class ApiRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type ErrorEnvelope = {
  error?: {
    message?: string;
  };
};

export type ArenaMetric = {
  value: string | null;
  status: string;
  reason: string | null;
};

export type ArenaLeaderboardEntry = {
  rank: number;
  agent_id: string;
  composite_rank_score: ArenaMetric;
  decision_quality: ArenaMetric;
  profit: ArenaMetric;
  drawdown: ArenaMetric;
  fee_drag: ArenaMetric;
  consistency: ArenaMetric;
  risk_discipline: ArenaMetric;
  explainability: ArenaMetric;
  evidence_provenance: Record<string, unknown>;
};

export type ArenaLeaderboardResponse = {
  snapshot_scope: string;
  competition_id: string;
  tournament_id: string | null;
  cycle_id: string | null;
  availability_state: "known" | "unavailable";
  state_reason: string | null;
  ranking_hash: string | null;
  ranking_methodology_version: string | null;
  snapshot_timestamp: string | null;
  filters: {
    included_agent_ids: string[] | null;
    limit: number | null;
    availability_mode: "all" | "known_only";
  };
  entries: ArenaLeaderboardEntry[];
  evidence_sources: Record<string, unknown>;
  provenance: Record<string, unknown>;
};

export type ArenaComparisonAgentSummary = {
  agent_id: string;
  decision_quality: ArenaMetric;
  explainability_support_ratio: ArenaMetric;
  counterfactual_correctness: ArenaMetric;
  evidence_provenance: Record<string, unknown>;
};

export type ArenaComparisonResponse = {
  comparison_scope: string;
  competition_id: string;
  tournament_id: string | null;
  cycle_id: string | null;
  availability_state: "known" | "unavailable";
  state_reason: string | null;
  comparison_hash: string | null;
  compared_agent_ids: string[];
  comparison_timestamp: string | null;
  agent_summaries: ArenaComparisonAgentSummary[];
  portfolio_dimensions: Record<string, ArenaMetric>;
  evidence_sources: Record<string, unknown>;
  provenance: Record<string, unknown>;
};

export type ArenaTournamentStanding = {
  rank: number;
  agent_id: string;
  composite_score: ArenaMetric;
  decision_quality: ArenaMetric;
  risk_discipline: ArenaMetric;
  drawdown: ArenaMetric;
  fee_drag: ArenaMetric;
  profit: ArenaMetric;
  evidence_provenance: Record<string, unknown>;
};

export type ArenaTournamentHistoryItem = {
  history_record_id: string;
  event_hash: string;
  sequence_number: number;
  event_type: string;
  lifecycle_state: string;
  event_timestamp: string;
  schedule_payload: Record<string, unknown>;
  replay_metadata: Record<string, unknown>;
  tie_break_rules: string[];
  ordering_rules: string[];
  standings: ArenaTournamentStanding[];
  provenance: Record<string, unknown>;
};

export type ArenaTournamentHistoryResponse = {
  competition_id: string;
  tournament_id: string;
  availability_state: "known" | "unavailable";
  state_reason: string | null;
  current_state: string | null;
  latest_event_type: string | null;
  latest_event_timestamp: string | null;
  history_count: number;
  replay_metadata: Record<string, unknown>;
  latest_schedule_payload: Record<string, unknown>;
  latest_standings: ArenaTournamentStanding[];
  history: ArenaTournamentHistoryItem[];
};

export type StrategyArenaScoreboardItem = {
  strategy_id: string;
  strategy_name: string;
  enabled: boolean;
  status: string;
  signals_generated: number;
  buy_signals: number;
  sell_signals: number;
  hold_signals: number;
  paper_trades: number;
  open_positions: number;
  realized_pnl: string;
  unrealized_pnl: string;
  total_return_pct: string;
  decision_records: number;
  last_signal_timestamp: string | null;
  last_trade_timestamp: string | null;
  latest_decision_package_id: string | null;
};

export type StrategyArenaScoreboardResponse = {
  items: StrategyArenaScoreboardItem[];
};

export type ReplayAgentCapability = {
  name: string;
  description: string;
};

export type ReplayAgentRegistration = {
  replay_agent_id: string;
  name: string;
  status: string;
  capabilities: ReplayAgentCapability[];
  decision_package_consumer: boolean;
  execution_logic: boolean;
  processing_enabled: boolean;
  scheduling_enabled: boolean;
  writes_enabled: boolean;
};

export type ReplayRequest = {
  decision_package_id: string;
};

export type ReplayResult = {
  replay_id: string;
  replay_agent_id: string;
  decision_package_id: string;
  replay_timestamp: string;
  reconstructed_action: "BUY" | "SELL" | "HOLD";
  reconstructed_confidence: string | null;
  supporting_evidence: Array<Record<string, unknown>>;
  explanation: string | null;
  metadata: Record<string, unknown>;
};

export type DecisionQualityResult = {
  quality_score: number;
  decision_reproduced: boolean;
  action_matches_original: boolean;
  confidence_matches_original: boolean;
  replay_duration_ms: number | null;
  evaluation_timestamp: string;
  calibration: string | null;
  opportunity_cost: string | null;
  drawdown: string | null;
  risk_adjusted_return: string | null;
  explanation_quality: string | null;
};

export type AICoachObservation = {
  observation_id: string;
  evaluation_timestamp: string;
  summary: string;
  strengths: string[];
  weaknesses: string[];
  confidence_note: string;
  reproducibility_note: string;
  suggested_follow_up: string;
};

export type DecisionIntelligenceRecommendation = {
  recommendation_id: string;
  generated_at: string;
  compared_strategies: string[];
  highest_quality_strategy: string | null;
  evidence_summary: string;
  confidence_summary: string;
  recommendation_summary: string;
  human_review_required: boolean;
  promotion_recommended: boolean;
};

export type TournamentRankingEntry = {
  strategy_name: string;
  quality_score: number;
  replay_variance: string;
  replay_count: number;
  paper_trades: number;
  realized_pnl: string;
  unrealized_pnl: string;
  win_rate: string | null;
  overall_rank: number;
};

export type TournamentResponse = {
  tournament_id: string;
  generated_at: string;
  compared_strategies: string[];
  ranking: TournamentRankingEntry[];
};

export type CapitalAllocationEntry = {
  strategy_name: string;
  allocation_percent: string;
  allocation_amount: string;
  rationale: string;
};

export type CapitalAllocationRecommendation = {
  recommendation_id: string;
  generated_at: string;
  total_paper_capital: string;
  allocations: CapitalAllocationEntry[];
};

export type StrategyHealthItem = {
  strategy_name: string;
  enabled: boolean;
  last_signal_time: string | null;
  last_trade_time: string | null;
  signals_today: number;
  decision_records_today: number;
  status: string;
};

export type StrategyHealthResponse = {
  items: StrategyHealthItem[];
};

export type ResearchAgent = {
  agent_id: string;
  agent_name: string;
  capabilities: string[];
};

export type LLMResearchAdapter = {
  adapter_id: string;
  adapter_name: string;
  provider: string;
  capabilities: string[];
  status: string;
};

export type OpenAIResearchGenerationResponse = {
  status: string;
  generated_candidates: StrategyCandidate[];
  evaluations: CandidateEvaluation[];
  generation_timestamp: string | null;
  prompt_version: string | null;
  response_duration_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
};

export type StrategyCandidate = {
  candidate_id: string;
  generated_at: string;
  originating_agent: string;
  strategy_name: string;
  description: string;
  parameter_set: Record<string, unknown>;
  rationale: string;
  status: string;
};

export type CandidateEvaluationRequest = {
  candidate_id: string;
};

export type CandidateEvaluation = {
  evaluation_id: string;
  candidate_id: string;
  replay_status: string;
  decision_quality_score: number;
  ai_coach_summary: string;
  decision_intelligence_summary: string;
  tournament_rank: number | null;
  promotion_eligible: boolean;
};

export type CandidateBatchEvaluationRequest = {
  candidate_ids?: string[];
  limit?: number;
};

export type CandidateBatchEvaluationResponse = {
  evaluated_count: number;
  evaluations: CandidateEvaluation[];
};

export type ResearchLaboratoryRun = {
  laboratory_run_id: string;
  started_at: string;
  completed_at: string | null;
  participating_agents: string[];
  generated_candidates: number;
  evaluated_candidates: number;
  status: string;
};

export type ResearchLaboratoryStatus = {
  status: string;
  registered_agents: string[];
  last_run: ResearchLaboratoryRun | null;
  candidates_generated: number;
  candidates_evaluated: number;
  success_rate: string;
};

export type ResearchMemoryLaboratoryRun = {
  laboratory_run_id: string;
  started_at: string;
  completed_at: string | null;
  participating_agents: string[];
  candidates_generated: number;
  candidates_evaluated: number;
};

export type ResearchMemoryParameterDiff = {
  parameter_name: string;
  previous_value: number;
  new_value: number;
};

export type ResearchMemoryCandidate = {
  laboratory_run_id: string;
  candidate_id: string;
  originating_agent: string;
  parameter_set: Record<string, unknown>;
  evaluation_summary: string | null;
  quality_score: number | null;
  tournament_rank: number | null;
  status: string;
  parent_candidate_id: string | null;
  generation: number;
  mutation_reason: string | null;
  parameter_diff: ResearchMemoryParameterDiff[];
};

export type ResearchMemorySummary = {
  total_laboratory_runs: number;
  total_candidates: number;
  highest_quality_candidate: ResearchMemoryCandidate | null;
  average_quality_score: number | null;
  latest_laboratory_run: ResearchMemoryLaboratoryRun | null;
};

export type ResearchCampaign = {
  campaign_id: string;
  name: string;
  objective: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  participating_agents: string[];
  laboratory_runs: number;
  candidates_generated: number;
  candidates_evaluated: number;
  best_candidate: string | null;
  best_quality_score: number | null;
  current_champion: string | null;
};

export type ResearchCampaignCreateRequest = {
  name: string;
  objective: string;
};

export type EvolutionMutation = {
  parameter_name: string;
  previous_value: number;
  new_value: number;
};

export type EvolvedCandidate = {
  candidate_id: string;
  parent_candidate_id: string;
  generation: number;
  mutation_reason: string;
  parameter_diff: EvolutionMutation[];
  parameter_set: Record<string, unknown>;
  generated_at: string;
  quality_score: number | null;
  tournament_rank: number | null;
  status: string;
};

export type EvolutionRequest = {
  parent_candidate_id?: string;
  generation_limit?: number;
};

export type EvolutionResponse = {
  generated_count: number;
  descendants: EvolvedCandidate[];
};

export type EvolutionAnalyticsGenerationDistributionItem = {
  generation: number;
  count: number;
};

export type EvolutionAnalyticsQualityPoint = {
  sequence: number;
  quality_score: number;
};

export type EvolutionAnalyticsRunPoint = {
  laboratory_run_id: string;
  candidates_generated: number;
};

export type EvolutionAnalyticsMutationSuccessRate = {
  successful_mutations: number;
  unsuccessful_mutations: number;
  success_rate_percent: number;
};

export type EvolutionAnalyticsAgentLeaderboardItem = {
  agent_name: string;
  average_quality_score: number | null;
  best_quality_score: number | null;
  total_candidates: number;
};

export type EvolutionAnalyticsLargestLineageTree = {
  root_candidate_id: string | null;
  lineage_depth: number;
  descendant_count: number;
};

export type EvolutionAnalytics = {
  total_laboratory_runs: number;
  total_candidates_generated: number;
  total_evolved_candidates: number;
  average_quality_score: number | null;
  best_quality_score: number | null;
  best_candidate: {
    candidate_id: string;
    quality_score: number;
    tournament_rank: number | null;
    originating_agent: string;
  } | null;
  successful_mutations: number;
  unsuccessful_mutations: number;
  generation_distribution: EvolutionAnalyticsGenerationDistributionItem[];
  lineage_depth: number;
  top_research_agent: string | null;
  quality_score_over_time: EvolutionAnalyticsQualityPoint[];
  candidates_generated_per_laboratory_run: EvolutionAnalyticsRunPoint[];
  mutation_success_rate: EvolutionAnalyticsMutationSuccessRate;
  research_agent_leaderboard: EvolutionAnalyticsAgentLeaderboardItem[];
  largest_lineage_tree: EvolutionAnalyticsLargestLineageTree;
};

export type OperationalHealthIndicator = {
  state: "green" | "yellow" | "red";
  detail: string;
};

export type OperationalRunStatus = {
  run_id: string;
  started_at: string;
  expected_end: string;
  uptime: string;
  current_phase: string;
  health_status: "green" | "yellow" | "red";
};

export type OperationalMonitoring = {
  candles_processed: number;
  signals_generated: number;
  paper_trades_executed: number;
  decision_records_created: number;
  replay_count: number;
  candidate_count: number;
  campaign_count: number;
  laboratory_runs: number;
  evolution_count: number;
  current_champion: string | null;
  paper_equity: string;
  signals_today: number;
  trades_today: number;
  research_memory_growth: number;
};

export type OperationalAlert = {
  code: string;
  severity: "green" | "yellow" | "red";
  message: string;
};

export type OperationalStatus = {
  overall_health: "green" | "yellow" | "red";
  run_status: OperationalRunStatus;
  system_health: {
    api: OperationalHealthIndicator;
    orchestrator: OperationalHealthIndicator;
    database: OperationalHealthIndicator;
    research_agent: OperationalHealthIndicator;
  };
  research_status: {
    current_campaign: string | null;
    current_champion: string | null;
    campaign_status: string;
  };
  monitoring: OperationalMonitoring;
  alerts: OperationalAlert[];
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers,
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const payload = (await response.json()) as ErrorEnvelope;
      if (payload.error?.message) {
        message = payload.error.message;
      }
    } catch {
      // Keep fallback message.
    }

    throw new ApiRequestError(message, response.status);
  }

  return (await response.json()) as T;
}

function buildQuery(params: Record<string, string | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value && value.trim()) {
      query.set(key, value);
    }
  }
  return query.toString();
}

export async function getArenaLeaderboardLatest(input: {
  competitionId: string;
  tournamentId?: string;
  cycleId?: string;
  availabilityMode?: "all" | "known_only";
}): Promise<ArenaLeaderboardResponse> {
  const query = buildQuery({
    competition_id: input.competitionId,
    tournament_id: input.tournamentId,
    cycle_id: input.cycleId,
    availability_mode: input.availabilityMode ?? "all",
  });
  return requestJson<ArenaLeaderboardResponse>(`/decisions/arena-leaderboard/latest?${query}`);
}

export async function getArenaComparisonLatest(input: {
  competitionId: string;
  tournamentId?: string;
  cycleId?: string;
}): Promise<ArenaComparisonResponse> {
  const query = buildQuery({
    competition_id: input.competitionId,
    tournament_id: input.tournamentId,
    cycle_id: input.cycleId,
  });
  return requestJson<ArenaComparisonResponse>(`/decisions/arena-comparisons/latest?${query}`);
}

export async function getArenaTournamentHistory(input: {
  competitionId: string;
  tournamentId: string;
}): Promise<ArenaTournamentHistoryResponse> {
  const query = buildQuery({
    competition_id: input.competitionId,
    tournament_id: input.tournamentId,
  });
  return requestJson<ArenaTournamentHistoryResponse>(`/decisions/arena-tournaments/history?${query}`);
}

export async function getStrategyArenaScoreboard(): Promise<StrategyArenaScoreboardResponse> {
  return requestJson<StrategyArenaScoreboardResponse>("/arena/strategy-scoreboard");
}

export async function getReplayAgents(): Promise<ReplayAgentRegistration[]> {
  return requestJson<ReplayAgentRegistration[]>("/arena/replay-agents");
}

export async function replayDecisionPackage(request: ReplayRequest): Promise<ReplayResult> {
  return requestJson<ReplayResult>("/arena/replay", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function evaluateReplayResult(request: ReplayResult): Promise<DecisionQualityResult> {
  return requestJson<DecisionQualityResult>("/arena/evaluate-replay", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function coachReviewDecisionQuality(request: DecisionQualityResult): Promise<AICoachObservation> {
  return requestJson<AICoachObservation>("/arena/coach-review", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function getDecisionIntelligenceRecommendation(): Promise<DecisionIntelligenceRecommendation> {
  return requestJson<DecisionIntelligenceRecommendation>("/arena/decision-intelligence");
}

export async function getDecisionArenaTournament(): Promise<TournamentResponse> {
  return requestJson<TournamentResponse>("/arena/tournament");
}

export async function getCapitalAllocationRecommendation(): Promise<CapitalAllocationRecommendation> {
  return requestJson<CapitalAllocationRecommendation>("/arena/capital-allocation");
}

export async function getStrategyHealth(): Promise<StrategyHealthResponse> {
  return requestJson<StrategyHealthResponse>("/arena/strategy-health");
}

export async function getResearchAgents(): Promise<ResearchAgent[]> {
  return requestJson<ResearchAgent[]>("/research/agents");
}

export async function getLLMResearchAdapters(): Promise<LLMResearchAdapter[]> {
  return requestJson<LLMResearchAdapter[]>("/research/llm-adapters");
}

export async function generateOpenAIResearchCandidates(): Promise<OpenAIResearchGenerationResponse> {
  return requestJson<OpenAIResearchGenerationResponse>("/research/llm-adapters/openai/generate-candidates", {
    method: "POST",
  });
}

export async function getResearchCandidates(): Promise<StrategyCandidate[]> {
  return requestJson<StrategyCandidate[]>("/research/candidates");
}

export async function evaluateCandidate(request: CandidateEvaluationRequest): Promise<CandidateEvaluation> {
  return requestJson<CandidateEvaluation>("/research/evaluate-candidate", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function evaluateCandidates(request: CandidateBatchEvaluationRequest): Promise<CandidateBatchEvaluationResponse> {
  return requestJson<CandidateBatchEvaluationResponse>("/research/evaluate-candidates", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function getResearchLaboratoryStatus(): Promise<ResearchLaboratoryStatus> {
  return requestJson<ResearchLaboratoryStatus>("/research/laboratory");
}

export async function runResearchLaboratory(): Promise<ResearchLaboratoryRun> {
  return requestJson<ResearchLaboratoryRun>("/research/laboratory/run", {
    method: "POST",
  });
}

export async function getResearchMemorySummary(): Promise<ResearchMemorySummary> {
  return requestJson<ResearchMemorySummary>("/research/memory");
}

export async function getResearchMemoryRuns(): Promise<ResearchMemoryLaboratoryRun[]> {
  return requestJson<ResearchMemoryLaboratoryRun[]>("/research/memory/runs");
}

export async function getResearchMemoryCandidates(): Promise<ResearchMemoryCandidate[]> {
  return requestJson<ResearchMemoryCandidate[]>("/research/memory/candidates");
}

export async function getResearchCampaigns(): Promise<ResearchCampaign[]> {
  return requestJson<ResearchCampaign[]>("/research/campaigns");
}

export async function getResearchCampaign(campaignId: string): Promise<ResearchCampaign> {
  return requestJson<ResearchCampaign>(`/research/campaigns/${campaignId}`);
}

export async function createResearchCampaign(request: ResearchCampaignCreateRequest): Promise<ResearchCampaign> {
  return requestJson<ResearchCampaign>("/research/campaigns", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function runResearchCampaign(campaignId: string): Promise<ResearchCampaign> {
  return requestJson<ResearchCampaign>(`/research/campaigns/${campaignId}/run`, {
    method: "POST",
  });
}

export async function evolveResearchCandidates(request: EvolutionRequest): Promise<EvolutionResponse> {
  return requestJson<EvolutionResponse>("/research/evolve", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function getEvolutionAnalytics(): Promise<EvolutionAnalytics> {
  return requestJson<EvolutionAnalytics>("/research/evolution-analytics");
}

export async function getOperationsStatus(): Promise<OperationalStatus> {
  return requestJson<OperationalStatus>("/operations/status");
}
