type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export type LiveOperatorWarning = {
  code: string;
  message: string;
};

export type LiveRegistrationStatusResponse = {
  live_trading_profile_id: string | null;
  paper_account_id: string | null;
  readiness_state: string;
  operating_mode: string;
  approval_state: string;
  live_opt_in: boolean | null;
  human_approval_recorded: boolean | null;
  governance_approved: boolean | null;
  risk_authority_model: string | null;
  paper_default_mode: boolean | null;
  status_state: "available" | "unknown" | "unavailable";
  warnings: LiveOperatorWarning[];
};

export type LiveApprovalEvent = {
  approval_event_id: string;
  live_trading_profile_id: string;
  checkpoint_type: string;
  approval_state: string;
  lifecycle_state: string;
  operating_mode: string;
  expires_at: string | null;
  renewal_condition: string | null;
  idempotency_key: string;
};

export type LiveApprovalStatusResponse = {
  live_trading_profile_id: string;
  status_state: "available" | "unknown" | "unavailable";
  total_events: number;
  items: LiveApprovalEvent[];
  warnings: LiveOperatorWarning[];
};

export type LiveReconciliationSummaryResponse = {
  live_trading_profile_id: string;
  status_state: "available" | "unknown" | "unavailable";
  total_events: number;
  open_count: number;
  partially_filled_count: number;
  filled_count: number;
  canceled_count: number;
  rejected_count: number;
  unresolved_count: number;
  latest_event_type: string | null;
  latest_reconciliation_status: string | null;
  latest_provider_name: string | null;
  latest_recorded_at: string | null;
  warnings: LiveOperatorWarning[];
};

export type LiveExecutionQualityItem = {
  quality_metric_id: string;
  provider_name: string;
  symbol: string;
  side: string;
  expected_price: string | null;
  expected_price_state: string;
  actual_fill_price: string | null;
  actual_price_state: string;
  slippage_abs: string | null;
  slippage_bps: string | null;
  slippage_state: string;
  market_context: Record<string, unknown>;
  telemetry_context: Record<string, unknown>;
  recorded_at: string;
};

export type LiveExecutionQualityResponse = {
  live_trading_profile_id: string;
  status_state: "available" | "unknown" | "unavailable";
  total_records: number;
  available_slippage_records: number;
  unknown_or_unavailable_records: number;
  average_slippage_bps: string | null;
  items: LiveExecutionQualityItem[];
  warnings: LiveOperatorWarning[];
};

export type LiveComplianceEvidenceItem = {
  evidence_record_id: string;
  event_type: string;
  attributable_actor_id: string;
  attributable_actor_role: string;
  action_name: string;
  action_source: string;
  action_summary: string;
  provenance_hash: string;
  linked_records: Record<string, string>;
  evidence_payload: Record<string, unknown>;
  provenance: Record<string, unknown>;
  recorded_at: string;
};

export type LiveComplianceReadResponse = {
  live_trading_profile_id: string;
  status_state: "available" | "unknown" | "unavailable";
  total_records: number;
  items: LiveComplianceEvidenceItem[];
  warnings: LiveOperatorWarning[];
};

export type LiveComplianceExportResponse = {
  live_trading_profile_id: string;
  exported_by: string;
  exported_at: string;
  status_state: "available" | "unknown" | "unavailable";
  total_records: number;
  records: LiveComplianceEvidenceItem[];
  warnings: LiveOperatorWarning[];
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

async function requestJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
    },
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
      // Keep generic message when body is not JSON.
    }

    throw new ApiRequestError(message, response.status, code);
  }

  return (await response.json()) as T;
}

export async function getLiveRegistrationStatus(profileId: string): Promise<LiveRegistrationStatusResponse> {
  const query = new URLSearchParams();
  query.set("live_trading_profile_id", profileId);
  return requestJson<LiveRegistrationStatusResponse>(`/live/registration/status?${query.toString()}`);
}

export async function getLiveApprovalsStatus(profileId: string): Promise<LiveApprovalStatusResponse> {
  const query = new URLSearchParams();
  query.set("live_trading_profile_id", profileId);
  return requestJson<LiveApprovalStatusResponse>(`/live/approvals/status?${query.toString()}`);
}

export async function getLiveReconciliationStatus(profileId: string): Promise<LiveReconciliationSummaryResponse> {
  const query = new URLSearchParams();
  query.set("live_trading_profile_id", profileId);
  return requestJson<LiveReconciliationSummaryResponse>(`/live/reconciliation/status?${query.toString()}`);
}

export async function getLiveExecutionQuality(profileId: string): Promise<LiveExecutionQualityResponse> {
  const query = new URLSearchParams();
  query.set("live_trading_profile_id", profileId);
  return requestJson<LiveExecutionQualityResponse>(`/live/execution-quality?${query.toString()}`);
}

export async function getLiveComplianceEvidence(profileId: string): Promise<LiveComplianceReadResponse> {
  const query = new URLSearchParams();
  query.set("live_trading_profile_id", profileId);
  return requestJson<LiveComplianceReadResponse>(`/live/compliance/evidence?${query.toString()}`);
}

export async function exportLiveComplianceBundle(
  profileId: string,
  exportedBy: string,
): Promise<LiveComplianceExportResponse> {
  const query = new URLSearchParams();
  query.set("live_trading_profile_id", profileId);
  query.set("exported_by", exportedBy);
  return requestJson<LiveComplianceExportResponse>(`/live/compliance/export?${query.toString()}`);
}
