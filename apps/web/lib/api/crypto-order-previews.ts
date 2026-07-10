import { ApiRequestError } from "@/lib/api/arena";
import { getOperatorAuthHeaders } from "@/lib/api/operator-auth";

export type CryptoOrderPreviewStatus =
  | "DRAFT"
  | "VALIDATING"
  | "RISK_REJECTED"
  | "CONNECTION_NOT_READY"
  | "BALANCE_INSUFFICIENT"
  | "PREVIEW_REQUESTED"
  | "PREVIEW_READY"
  | "PREVIEW_FAILED"
  | "EXPIRED"
  | "CANCELLED";

export type CryptoOrderPreviewSide = "BUY" | "SELL";
export type CryptoOrderPreviewOrderType = "MARKET";
export type CryptoOrderPreviewGeneratedBy = "operator" | "system_recommendation";
export type CryptoOrderPreviewReadinessVerdict =
  | "READY_FOR_PREVIEW"
  | "READ_ONLY_READY"
  | "MISCONFIGURED"
  | "UNREACHABLE"
  | "PERMISSION_INSUFFICIENT"
  | "CLOCK_SKEW"
  | "AUTHENTICATION_FAILED"
  | "UNKNOWN";
export type CryptoOrderPreviewRiskVerdict = "approved_for_preview" | "rejected" | "blocked" | "needs_refresh";

export type CryptoOrderPreviewReadiness = {
  ready: boolean;
  allowed_products: string[];
  max_quote_size_usd: string;
  default_quote_size_usd: string;
  market_data_max_age_minutes: number;
  expiration_minutes: number;
};

export type CryptoOrderPreview = {
  crypto_order_preview_id: string;
  preview_version: number;
  status: CryptoOrderPreviewStatus;
  provider: string;
  environment: "sandbox" | "production";
  product_id: string;
  side: CryptoOrderPreviewSide;
  order_type: CryptoOrderPreviewOrderType;
  quote_size: string | null;
  base_size: string | null;
  requested_amount: string;
  requested_amount_currency: "USD" | "BTC";
  readiness_verdict: CryptoOrderPreviewReadinessVerdict | null;
  risk_verdict: CryptoOrderPreviewRiskVerdict | null;
  risk_explanation: string | null;
  strategy_id: string | null;
  strategy_name: string | null;
  decision_record_id: string | null;
  validation_run_id: string | null;
  preview_id: string | null;
  estimated_average_price: string | null;
  estimated_total_value: string | null;
  estimated_base_size: string | null;
  estimated_quote_size: string | null;
  estimated_fee: string | null;
  estimated_fee_currency: string | null;
  estimated_slippage: string | null;
  estimated_commission_total: string | null;
  best_bid: string | null;
  best_ask: string | null;
  available_balance_before: string | null;
  estimated_balance_after: string | null;
  failure_reason: string | null;
  warning_messages: string[];
  exchange_response_summary: Record<string, unknown>;
  expires_at: string;
  generated_by: CryptoOrderPreviewGeneratedBy;
  audit_correlation_id: string | null;
  order_submitted: boolean;
  execution_available: boolean;
  created_at: string;
  updated_at: string;
  refreshed_from_preview_id: string | null;
};

export type CryptoOrderPreviewCreateRequest = {
  exchange_connection_id: string;
  environment: "sandbox" | "production";
  product_id: string;
  side: CryptoOrderPreviewSide;
  order_type: CryptoOrderPreviewOrderType;
  quote_size?: string | null;
  base_size?: string | null;
  requested_amount_currency: "USD" | "BTC";
  decision_record_id?: string | null;
  validation_run_id?: string | null;
  strategy_id?: string | null;
  strategy_name?: string | null;
  generated_by?: CryptoOrderPreviewGeneratedBy;
  client_request_id?: string | null;
};

export type CryptoOrderPreviewRefreshRequest = {
  client_request_id?: string | null;
};

export type CryptoOrderPreviewCancelRequest = {
  reason: string;
};

type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...getOperatorAuthHeaders(),
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
      // Keep generic message.
    }
    throw new ApiRequestError(message, response.status);
  }

  return (await response.json()) as T;
}

export async function getCryptoOrderPreviewReadiness(): Promise<CryptoOrderPreviewReadiness> {
  return requestJson<CryptoOrderPreviewReadiness>("/crypto-order-previews/readiness");
}

export async function listCryptoOrderPreviews(limit = 25): Promise<CryptoOrderPreview[]> {
  return requestJson<{ items: CryptoOrderPreview[] }>(`/crypto-order-previews?limit=${encodeURIComponent(String(limit))}`)
    .then((payload) => payload.items);
}

export async function getCryptoOrderPreview(previewId: string): Promise<CryptoOrderPreview> {
  return requestJson<CryptoOrderPreview>(`/crypto-order-previews/${encodeURIComponent(previewId)}`);
}

export async function createCryptoOrderPreview(payload: CryptoOrderPreviewCreateRequest): Promise<CryptoOrderPreview> {
  return requestJson<CryptoOrderPreview>("/crypto-order-previews", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function refreshCryptoOrderPreview(previewId: string, payload?: CryptoOrderPreviewRefreshRequest): Promise<CryptoOrderPreview> {
  return requestJson<CryptoOrderPreview>(`/crypto-order-previews/${encodeURIComponent(previewId)}/refresh`, {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}

export async function cancelCryptoOrderPreview(previewId: string, payload: CryptoOrderPreviewCancelRequest): Promise<CryptoOrderPreview> {
  return requestJson<CryptoOrderPreview>(`/crypto-order-previews/${encodeURIComponent(previewId)}/cancel`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
