type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export class ApiRequestError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type KnownState = "known" | "unknown" | "unavailable";

export type TimelineStateField = {
  value: string | null;
  state: KnownState;
};

export type TimelineItem = {
  decision_id: string;
  timestamp: string;
  narrative: string;
  status: string;
  account_id: TimelineStateField;
  asset_id: TimelineStateField;
  strategy_id: TimelineStateField;
  source_lineage: Record<string, string[]>;
};

export type ExplainabilityEvidenceItem = {
  evidence_name: string;
  evidence_payload: Record<string, unknown>;
  provenance: Record<string, unknown>;
  availability_state: KnownState;
  state_reason: string | null;
};

export type DecisionExplainability = {
  decision_id: string;
  decision_status: string;
  explanation: string;
  supporting_evidence: ExplainabilityEvidenceItem[];
  opposing_evidence: ExplainabilityEvidenceItem[];
  confidence_factors: ExplainabilityEvidenceItem[];
  risk_adjustments: ExplainabilityEvidenceItem[];
};

export type CounterfactualListItem = {
  id: string;
  decision_id: string;
  horizon_label: string;
  horizon_minutes: number;
  decision_timestamp: string;
  evaluated_at: string;
  asset_symbol: string;
  actual_action: string;
  shadow_buy_return_pct: string | null;
  shadow_sell_return_pct: string | null;
  shadow_wait_return_pct: string | null;
  best_action: string | null;
  actual_action_correct: boolean | null;
  evaluation_state: string;
  state_reason: string | null;
  lesson_tags: Array<Record<string, string>>;
  feature_snapshot: Record<string, unknown>;
  created_at: string;
};

export type CounterfactualDetail = {
  decision_id: string;
  availability_state: KnownState;
  state_reason: string | null;
  items: Array<{
    id: string;
    horizon_label: string;
    horizon_minutes: number;
    evaluation_state: string;
    actual_action: string;
    best_action: string | null;
    actual_action_correct: boolean | null;
    lesson_tags: Array<Record<string, string>>;
    feature_snapshot: Record<string, unknown>;
  }>;
};

export type DecisionQualityItem = {
  decision_id: string;
  availability_state: KnownState;
  state_reason: string | null;
  scoring_model_version: string | null;
  composite_score: string | null;
  component_scores: Array<Record<string, string>>;
  weight_profile: Record<string, string>;
  provenance: Record<string, unknown>;
  created_at: string | null;
};

export type DecisionRecommendationItem = {
  id: string;
  recommendation_type: string;
  recommendation_category: string;
  confidence_level: string;
  expected_impact: string;
  required_human_review_level: string;
  supporting_evidence_refs: Array<Record<string, unknown>>;
  originating_decision_ids: string[];
  explanation: string;
  suggested_experiment: Record<string, unknown>;
  provenance: Record<string, unknown>;
  availability_state: KnownState;
  state_reason: string | null;
  advisory_only: boolean;
  created_at: string;
};

export type PaginatedResponse<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
};

export type DecisionReadFilters = {
  account_id?: string;
  portfolio_id?: string;
  strategy_id?: string;
  asset_id?: string;
  status?: string;
  start_time?: string;
  end_time?: string;
  page?: number;
  page_size?: number;
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    let code: string | undefined;

    try {
      const payload = (await response.json()) as ErrorEnvelope;
      code = payload.error?.code;
      if (payload.error?.message) {
        message = payload.error.message;
      }
    } catch {
      // Keep generic message when response body is not valid JSON.
    }

    throw new ApiRequestError(message, response.status, code);
  }

  return (await response.json()) as T;
}

function buildQuery(filters: DecisionReadFilters): string {
  const query = new URLSearchParams();

  if (filters.account_id) {
    query.set("account_id", filters.account_id);
  }
  if (filters.portfolio_id) {
    query.set("portfolio_id", filters.portfolio_id);
  }
  if (filters.strategy_id) {
    query.set("strategy_id", filters.strategy_id);
  }
  if (filters.asset_id) {
    query.set("asset_id", filters.asset_id);
  }
  if (filters.status) {
    query.set("status", filters.status);
  }
  if (filters.start_time) {
    query.set("start_time", filters.start_time);
  }
  if (filters.end_time) {
    query.set("end_time", filters.end_time);
  }

  query.set("page", String(filters.page ?? 1));
  query.set("page_size", String(filters.page_size ?? 20));

  return query.toString();
}

export async function getDecisionTimeline(filters: DecisionReadFilters): Promise<PaginatedResponse<TimelineItem>> {
  const query = buildQuery(filters);
  return requestJson<PaginatedResponse<TimelineItem>>(`/decisions/timeline?${query}`);
}

export async function getDecisionExplainability(decisionId: string): Promise<DecisionExplainability> {
  return requestJson<DecisionExplainability>(`/decisions/${decisionId}/explainability`);
}

export async function getDecisionCounterfactuals(filters: DecisionReadFilters): Promise<PaginatedResponse<CounterfactualListItem>> {
  const query = buildQuery(filters);
  return requestJson<PaginatedResponse<CounterfactualListItem>>(`/decisions/counterfactuals?${query}`);
}

export async function getDecisionCounterfactualDetail(decisionId: string): Promise<CounterfactualDetail> {
  return requestJson<CounterfactualDetail>(`/decisions/${decisionId}/counterfactuals`);
}

export async function getDecisionQuality(filters: DecisionReadFilters): Promise<PaginatedResponse<DecisionQualityItem>> {
  const query = buildQuery(filters);
  return requestJson<PaginatedResponse<DecisionQualityItem>>(`/decisions/quality?${query}`);
}

export async function getDecisionRecommendations(filters: DecisionReadFilters): Promise<PaginatedResponse<DecisionRecommendationItem>> {
  const query = buildQuery(filters);
  return requestJson<PaginatedResponse<DecisionRecommendationItem>>(`/decisions/recommendations?${query}`);
}
