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

export type DecisionRecordItem = {
  decision_id: string;
  timestamp: string;
  asset_id: string | null;
  trade_accepted: boolean;
  review_status: string | null;
  outcome: string | null;
  action: string | null;
  provider: string | null;
  environment: string | null;
  product_id: string | null;
  confidence: string | null;
  risk_verdict: string;
  first_failing_risk_rule: string | null;
  requested_notional: string | null;
  approved_notional: string | null;
  preview_status: string;
  approval_status: string;
  rehearsal_status: string;
  execution_status: string;
  has_decision_snapshot: boolean;
  has_price_evidence: boolean;
  has_risk_event: boolean;
  evidence_completeness: string;
  decision_explanation: {
    trade_rejected_reason: string | null;
    ai_reflection: Record<string, unknown> | null;
    post_trade_notes: Record<string, unknown> | null;
    human_notes: string | null;
    lessons_learned: Array<Record<string, unknown>> | null;
  };
  linked_signal: {
    signal_id: string | null;
    strategy_id: string | null;
    asset_id: string | null;
    action: string | null;
    status: string | null;
    signal_time: string | null;
  };
  quality_score: {
    availability_state: KnownState;
    state_reason: string | null;
    scoring_model_version: string | null;
    composite_score: string | null;
    created_at: string | null;
  };
  future_outcome_tracking: {
    availability_state: KnownState;
    state_reason: string | null;
    horizons_evaluated: string[];
    resolved_horizons: number;
    total_horizons: number;
    latest_evaluated_at: string | null;
    latest_horizon_label: string | null;
    latest_evaluation_state: string | null;
    latest_best_action: string | null;
    latest_actual_action_correct: boolean | null;
  };
  recommendation_history: {
    count: number;
    latest_recommendation_at: string | null;
    latest_recommendation_type: string | null;
    latest_recommendation_state: KnownState;
    recommendation_ids: string[];
  };
};

export type DecisionRecordFilters = {
  decision_id?: string;
  asset_id?: string;
  strategy_id?: string;
  action?: string;
  trade_accepted?: boolean;
  review_status?: string;
  environment?: string;
  provider?: string;
  product_id?: string;
  q?: string;
  sort?:
    | "newest"
    | "oldest"
    | "highest_confidence"
    | "lowest_confidence"
    | "highest_quality"
    | "lowest_quality"
    | "largest_requested_notional"
    | "largest_approved_notional"
    | "most_recently_reviewed";
  has_decision_snapshot?: boolean;
  has_price_evidence?: boolean;
  has_risk_event?: boolean;
  start_time?: string;
  end_time?: string;
  page?: number;
  page_size?: number;
};

export type DecisionExplorerSummary = {
  total_decisions: number;
  accepted: number;
  risk_rejected: number;
  hold_wait: number;
  preview_ready: number;
  submitted: number;
  executed: number;
  needs_review: number;
  missing_linkage: number;
};

export type DecisionInspectorStage = {
  stage: string;
  status: "completed" | "rejected" | "pending" | "not_applicable" | "missing" | "unavailable";
  label: string;
  detail: string;
};

export type DecisionInspectorResponse = {
  decision_id: string;
  header: {
    title: string;
    decision_id: string;
    current_status: string;
    timestamp: string;
    strategy: string | null;
    campaign: string | null;
    provider: string | null;
    environment: string;
    market: string;
    confidence: string | null;
    decision_quality: string | null;
    review_status: string;
    environment_badge: string;
    paper_live_badge: string;
  };
  timeline: DecisionInspectorStage[];
  narrative: {
    title: string;
    explanation: string;
    evidence_gaps: Array<string | null>;
  };
  execution_price_evidence: {
    availability: string;
    provider: string | null;
    venue: string | null;
    product: string | null;
    base_currency: string | null;
    quote_currency: string | null;
    observed_price: string | null;
    bid: string | null;
    ask: string | null;
    reference_price: string | null;
    observed_timestamp: string | null;
    retrieved_timestamp: string | null;
    evidence_age_seconds: number | null;
    freshness_seconds: number | null;
    validation_status: string;
    evidence_id: string | null;
  };
  risk_evaluation: {
    verdict: string;
    first_failing_rule: Record<string, unknown> | null;
    stopped_after_first_fail: boolean;
    risk_adjusted_sizing: string | null;
    checks: Array<Record<string, unknown>>;
  };
  decision_intelligence: Record<string, string>;
  preview: {
    availability: string;
    state_reason: string | null;
    preview_id: string | null;
    requested_amount: string | null;
    approved_amount: string | null;
    estimated_quantity: string | null;
    estimated_fees: string | null;
    expiration: string | null;
    submission_state: string;
    execution_state: string;
    human_approval_state: string;
  };
  audit_timeline: Array<{
    actor: string;
    timestamp: string;
    action: string;
    entity_type: string;
    correlation_id: string | null;
  }>;
  integrity_warnings: string[];
  counterfactual: {
    availability: string;
    state_reason: string | null;
    items: Array<Record<string, unknown>>;
    summary: string;
  };
  linkage_health: Array<{
    component: string;
    status: string;
    reason: string;
  }>;
};

export type CoachReviewGenerationResponse = {
  status: string;
  advisory_only: boolean;
  paper_mode_only: boolean;
  no_automatic_strategy_changes: boolean;
  scanned_records: number;
  inserted_recommendations: number;
  skipped_existing: number;
  recommendation_ids: string[];
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

export async function getDecisionRecords(filters: DecisionRecordFilters): Promise<PaginatedResponse<DecisionRecordItem>> {
  const query = new URLSearchParams();
  if (filters.decision_id) {
    query.set("decision_id", filters.decision_id);
  }
  if (filters.asset_id) {
    query.set("asset_id", filters.asset_id);
  }
  if (filters.strategy_id) {
    query.set("strategy_id", filters.strategy_id);
  }
  if (filters.action) {
    query.set("action", filters.action);
  }
  if (typeof filters.trade_accepted === "boolean") {
    query.set("trade_accepted", String(filters.trade_accepted));
  }
  if (filters.review_status) {
    query.set("review_status", filters.review_status);
  }
  if (filters.environment) {
    query.set("environment", filters.environment);
  }
  if (filters.provider) {
    query.set("provider", filters.provider);
  }
  if (filters.product_id) {
    query.set("product_id", filters.product_id);
  }
  if (filters.q) {
    query.set("q", filters.q);
  }
  if (filters.sort) {
    query.set("sort", filters.sort);
  }
  if (typeof filters.has_decision_snapshot === "boolean") {
    query.set("has_decision_snapshot", String(filters.has_decision_snapshot));
  }
  if (typeof filters.has_price_evidence === "boolean") {
    query.set("has_price_evidence", String(filters.has_price_evidence));
  }
  if (typeof filters.has_risk_event === "boolean") {
    query.set("has_risk_event", String(filters.has_risk_event));
  }
  if (filters.start_time) {
    query.set("start_time", filters.start_time);
  }
  if (filters.end_time) {
    query.set("end_time", filters.end_time);
  }
  query.set("page", String(filters.page ?? 1));
  query.set("page_size", String(filters.page_size ?? 50));

  return requestJson<PaginatedResponse<DecisionRecordItem>>(`/decisions/records?${query.toString()}`);
}

export async function getDecisionExplorerSummary(filters: DecisionRecordFilters): Promise<DecisionExplorerSummary> {
  const query = new URLSearchParams();
  if (filters.decision_id) {
    query.set("decision_id", filters.decision_id);
  }
  if (filters.asset_id) {
    query.set("asset_id", filters.asset_id);
  }
  if (filters.strategy_id) {
    query.set("strategy_id", filters.strategy_id);
  }
  if (filters.action) {
    query.set("action", filters.action);
  }
  if (typeof filters.trade_accepted === "boolean") {
    query.set("trade_accepted", String(filters.trade_accepted));
  }
  if (filters.review_status) {
    query.set("review_status", filters.review_status);
  }
  if (filters.environment) {
    query.set("environment", filters.environment);
  }
  if (filters.provider) {
    query.set("provider", filters.provider);
  }
  if (filters.product_id) {
    query.set("product_id", filters.product_id);
  }
  if (filters.q) {
    query.set("q", filters.q);
  }
  if (typeof filters.has_decision_snapshot === "boolean") {
    query.set("has_decision_snapshot", String(filters.has_decision_snapshot));
  }
  if (typeof filters.has_price_evidence === "boolean") {
    query.set("has_price_evidence", String(filters.has_price_evidence));
  }
  if (typeof filters.has_risk_event === "boolean") {
    query.set("has_risk_event", String(filters.has_risk_event));
  }
  if (filters.start_time) {
    query.set("start_time", filters.start_time);
  }
  if (filters.end_time) {
    query.set("end_time", filters.end_time);
  }
  return requestJson<DecisionExplorerSummary>(`/decisions/explorer/summary?${query.toString()}`);
}

export async function getDecisionInspector(decisionId: string): Promise<DecisionInspectorResponse> {
  return requestJson<DecisionInspectorResponse>(`/decisions/${decisionId}/inspector`);
}

export async function generateCoachReviews(params?: {
  lookback_hours?: number;
  limit?: number;
}): Promise<CoachReviewGenerationResponse> {
  const query = new URLSearchParams();
  query.set("lookback_hours", String(params?.lookback_hours ?? 24));
  query.set("limit", String(params?.limit ?? 250));
  return requestJson<CoachReviewGenerationResponse>(`/decisions/coach/reviews/generate?${query.toString()}`, {
    method: "POST",
  });
}
