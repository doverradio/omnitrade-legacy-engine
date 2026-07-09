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
