import { ApiRequestError } from "@/lib/api/arena";

export type CapitalCampaignStatus =
  | "DRAFT"
  | "READY"
  | "RUNNING"
  | "PAUSED"
  | "TARGET_REACHED"
  | "COMPLETED"
  | "ARCHIVED";

export type CapitalCampaign = {
  id: number;
  uuid: string;
  owner: string;
  name: string;
  description: string | null;
  status: CapitalCampaignStatus;
  campaign_type: string;
  exchange: string | null;
  paper_account_id: string | null;
  validation_run_id: string | null;
  strategy_id: string | null;
  starting_capital: string;
  current_equity: string;
  realized_profit: string;
  unrealized_profit: string;
  fees: string;
  roi: string;
  created_at: string;
  updated_at: string;
};

export type CapitalCampaignListResponse = {
  items: CapitalCampaign[];
};

export type ProfitPolicyType =
  | "HOLD_PROFIT"
  | "FULL_COMPOUND"
  | "PARTIAL_COMPOUND"
  | "WITHDRAW_PROFIT"
  | "WITHDRAW_AND_COMPOUND"
  | "PROTECTED_PRINCIPAL"
  | "MANUAL_REVIEW";

export type CapitalCampaignProfitPolicy = {
  policy_id: number;
  policy_uuid: string;
  capital_campaign_id: number;
  policy_type: ProfitPolicyType;
  profit_target_amount: string | null;
  profit_target_percent: string | null;
  compound_percent: string;
  withdraw_percent: string;
  protected_principal_amount: string | null;
  minimum_realized_profit: string;
  maximum_campaign_capital: string | null;
  minimum_cash_reserve: string;
  fee_reserve_percent: string;
  tax_reserve_percent: string;
  cooldown_hours: number;
  require_operator_approval: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type CapitalCampaignProfitPolicyUpsertRequest = {
  policy_type: ProfitPolicyType;
  profit_target_amount?: string | null;
  profit_target_percent?: string | null;
  compound_percent?: string;
  withdraw_percent?: string;
  protected_principal_amount?: string | null;
  minimum_realized_profit?: string;
  maximum_campaign_capital?: string | null;
  minimum_cash_reserve?: string;
  fee_reserve_percent?: string;
  tax_reserve_percent?: string;
  cooldown_hours?: number;
  require_operator_approval?: boolean;
  is_active?: boolean;
};

export type ProfitCycleStatus =
  | "CALCULATING"
  | "BELOW_TARGET"
  | "TARGET_REACHED"
  | "REVIEW_REQUIRED"
  | "APPROVED"
  | "COMPOUNDING_RECOMMENDED"
  | "WITHDRAWAL_RECOMMENDED"
  | "COMPLETED"
  | "CANCELLED"
  | "ERROR";

export type SettlementState = "SETTLED" | "SETTLEMENT_UNKNOWN";

export type CapitalCampaignProfitCycle = {
  cycle_id: number;
  cycle_uuid: string;
  capital_campaign_id: number;
  profit_policy_id: number;
  cycle_number: number;
  opening_capital: string;
  opening_equity: string;
  realized_profit: string;
  unrealized_profit: string;
  fees: string;
  eligible_profit: string;
  compound_amount: string;
  withdrawal_amount: string;
  reserve_amount: string;
  closing_campaign_capital: string;
  target_reached: boolean;
  status: ProfitCycleStatus;
  settlement_state: SettlementState;
  calculation_snapshot: Record<string, unknown>;
  calculated_at: string;
  approved_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CapitalCampaignProfitCycleListResponse = {
  items: CapitalCampaignProfitCycle[];
};

export type CapitalCampaignCreateRequest = {
  owner: string;
  name: string;
  description?: string | null;
  status?: CapitalCampaignStatus;
  campaign_type: string;
  exchange?: string | null;
  paper_account_id?: string | null;
  validation_run_id?: string | null;
  strategy_id?: string | null;
  starting_capital: string;
  current_equity?: string | null;
  realized_profit?: string;
  unrealized_profit?: string;
  fees?: string;
};

// --- Campaign domain definitions + orchestration read paths (worker PROCESS trace) ---

export type CapitalCampaignDomainDefinition = {
  campaign_id: string;
  version: number;
  name: string;
  status: string;
  allowed_instruments: string[];
  created_at: string;
};

export type CapitalCampaignDomainDefinitionListResponse = {
  items: CapitalCampaignDomainDefinition[];
};

// Mirrors app/services/orchestration/process_trace.py::build_process_trace_event's
// return shape exactly -- this is real, persisted worker evidence, never
// recomputed or simulated by the frontend.
export type ProcessTraceEvent = {
  schema_version: string;
  process_stage: string;
  gate: string;
  verdict: string;
  reason: string | null;
  instrument: string | null;
  candidate_id: string | null;
  decision_record_id: string | null;
  attempt_id: string | null;
  cycle_id: string | null;
  observed_value: string | null;
  threshold: string | null;
  next: string | null;
  timestamp: string;
};

export type CampaignOrchestrationCycleSummary = {
  cycle_id: string | null;
  state: string | null;
  cycle_kind: string | null;
  capital_campaign_id: string | null;
  capital_campaign_version: number | null;
  started_at: string | null;
  completed_at: string | null;
  termination_stage: string | null;
  failure_reason: string | null;
  deterministic_explanation: string[];
  process_trace: ProcessTraceEvent[];
};

export type CampaignOrchestrationCampaignSnapshot = {
  campaign_id: string;
  status: string;
  allowed_instruments: string[];
};

export type CampaignOrchestrationHistoryResponse = {
  mode: string;
  campaign_id: string;
  version: number;
  count: number;
  campaign_snapshot: CampaignOrchestrationCampaignSnapshot;
  items: CampaignOrchestrationCycleSummary[];
};

type ErrorEnvelope = {
  error?: {
    message?: string;
  };
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  // A bodyless GET has nothing to describe as JSON -- setting Content-Type
  // on it anyway takes the request outside the CORS-safelisted header set
  // and forces the browser into an unnecessary preflight OPTIONS round trip
  // for every read-only call this client makes (including /workflow's
  // trace reads). Only advertise a JSON body when one is actually sent.
  if (init?.body !== undefined && headers["Content-Type"] === undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    ...init,
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
      // Keep fallback.
    }
    throw new ApiRequestError(message, response.status);
  }

  return (await response.json()) as T;
}

export async function listCapitalCampaigns(params?: { status?: CapitalCampaignStatus; owner?: string }): Promise<CapitalCampaign[]> {
  const query = new URLSearchParams();
  if (params?.status) {
    query.set("status", params.status);
  }
  if (params?.owner) {
    query.set("owner", params.owner);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const payload = await requestJson<CapitalCampaignListResponse>(`/capital-campaigns${suffix}`);
  return payload.items;
}

export async function getCapitalCampaign(campaignUuid: string): Promise<CapitalCampaign> {
  return requestJson<CapitalCampaign>(`/capital-campaigns/${encodeURIComponent(campaignUuid)}`);
}

export async function createCapitalCampaign(payload: CapitalCampaignCreateRequest): Promise<CapitalCampaign> {
  return requestJson<CapitalCampaign>("/capital-campaigns", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getCapitalCampaignProfitPolicy(campaignUuid: string): Promise<CapitalCampaignProfitPolicy> {
  return requestJson<CapitalCampaignProfitPolicy>(`/capital-campaigns/${encodeURIComponent(campaignUuid)}/profit-policy`);
}

export async function upsertCapitalCampaignProfitPolicy(
  campaignUuid: string,
  payload: CapitalCampaignProfitPolicyUpsertRequest,
  method: "POST" | "PATCH" = "PATCH",
): Promise<CapitalCampaignProfitPolicy> {
  return requestJson<CapitalCampaignProfitPolicy>(`/capital-campaigns/${encodeURIComponent(campaignUuid)}/profit-policy`, {
    method,
    body: JSON.stringify(payload),
  });
}

export async function evaluateCapitalCampaignProfitCycle(
  campaignUuid: string,
  payload?: { force_new_cycle?: boolean; actor?: string },
): Promise<CapitalCampaignProfitCycle> {
  return requestJson<CapitalCampaignProfitCycle>(`/capital-campaigns/${encodeURIComponent(campaignUuid)}/profit-cycles/evaluate`, {
    method: "POST",
    body: JSON.stringify({ force_new_cycle: false, actor: "operator", ...(payload ?? {}) }),
  });
}

export async function listCapitalCampaignProfitCycles(campaignUuid: string): Promise<CapitalCampaignProfitCycle[]> {
  const payload = await requestJson<CapitalCampaignProfitCycleListResponse>(`/capital-campaigns/${encodeURIComponent(campaignUuid)}/profit-cycles`);
  return payload.items;
}

export async function approveCapitalCampaignProfitCycle(campaignUuid: string, cycleUuid: string): Promise<CapitalCampaignProfitCycle> {
  return requestJson<CapitalCampaignProfitCycle>(`/capital-campaigns/${encodeURIComponent(campaignUuid)}/profit-cycles/${encodeURIComponent(cycleUuid)}/approve`, {
    method: "POST",
    body: JSON.stringify({ actor: "operator" }),
  });
}

export async function rejectCapitalCampaignProfitCycle(campaignUuid: string, cycleUuid: string, reason?: string): Promise<CapitalCampaignProfitCycle> {
  return requestJson<CapitalCampaignProfitCycle>(`/capital-campaigns/${encodeURIComponent(campaignUuid)}/profit-cycles/${encodeURIComponent(cycleUuid)}/reject`, {
    method: "POST",
    body: JSON.stringify({ actor: "operator", reason: reason ?? null }),
  });
}

// listCapitalCampaignDomainDefinitions with no arguments reuses the exact
// same "governing campaign" resolution the real orchestration worker itself
// uses (list_campaign_definitions, default latest_only=true) -- this is how
// a read-only viewer discovers the current campaign without ever hard-coding
// an id.
export async function listCapitalCampaignDomainDefinitions(): Promise<CapitalCampaignDomainDefinition[]> {
  const payload = await requestJson<CapitalCampaignDomainDefinitionListResponse>("/capital-campaigns/domain");
  return payload.items;
}

export async function getCampaignOrchestrationHistory(campaignId: string, params?: { version?: number; limit?: number }): Promise<CampaignOrchestrationHistoryResponse> {
  const query = new URLSearchParams();
  if (params?.version !== undefined) {
    query.set("version", String(params.version));
  }
  if (params?.limit !== undefined) {
    query.set("limit", String(params.limit));
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<CampaignOrchestrationHistoryResponse>(`/capital-campaigns/domain/${encodeURIComponent(campaignId)}/orchestration/history${suffix}`);
}
