import { ApiRequestError } from "@/lib/api/live";

export type LiveCryptoOrderStatus =
  | "PENDING_CONFIRMATION"
  | "CONFIRMATION_EXPIRED"
  | "VALIDATING"
  | "RISK_REJECTED"
  | "SUBMISSION_PENDING"
  | "SUBMITTED"
  | "ACKNOWLEDGED"
  | "PARTIALLY_FILLED"
  | "FILLED"
  | "REJECTED"
  | "CANCELLED"
  | "RECONCILIATION_REQUIRED"
  | "UNKNOWN";

export type LiveCryptoOrderProviderStatus =
  | "PENDING"
  | "OPEN"
  | "FILLED"
  | "CANCELLED"
  | "EXPIRED"
  | "FAILED"
  | "QUEUED"
  | "CANCEL_QUEUED"
  | "EDIT_QUEUED"
  | "UNKNOWN";

export type LiveCryptoOrder = {
  live_crypto_order_id: string;
  crypto_order_preview_id: string;
  exchange_connection_id: string;
  provider: string;
  environment: string;
  product_id: string;
  side: string;
  order_type: string;
  requested_quote_size: string;
  client_order_id: string;
  status: LiveCryptoOrderStatus;
  risk_event_id: string | null;
  decision_record_id: string | null;
  validation_run_id: string | null;
  provider_order_id: string | null;
  provider_status: LiveCryptoOrderProviderStatus | null;
  submitted_at: string | null;
  acknowledged_at: string | null;
  filled_at: string | null;
  cancelled_at: string | null;
  failure_code: string | null;
  failure_reason: string | null;
  safe_provider_response: Record<string, unknown>;
  audit_correlation_id: string;
  operator_confirmation_id: string | null;
  created_at: string;
  updated_at: string;
};

export type LiveCryptoOrderReadiness = {
  live_mode_enabled: boolean;
  live_profile_ready: boolean;
  feature_flag_enabled: boolean;
  max_order_usd: string;
  latest_preview_age_seconds: number | null;
  latest_balance_age_seconds: number | null;
  latest_readiness_age_seconds: number | null;
  latest_price_age_seconds: number | null;
  reason: string | null;
};

export type LiveCryptoOrderPrepareRequest = {
  live_trading_profile_id: string;
  crypto_order_preview_id: string;
  operator_identity: string;
  idempotency_token?: string | null;
};

export type LiveCryptoOrderSubmitRequest = {
  live_crypto_order_id: string;
  confirmation_challenge_id: string;
  confirmation_phrase: string;
  operator_identity: string;
  idempotency_token: string;
};

export type LiveCryptoOrderCancelRequest = {
  reason: string;
  operator_identity: string;
};

export type LiveCryptoOrderReconcileRequest = {
  operator_identity: string;
};

export type LiveCryptoOrderPrepareResponse = {
  live_crypto_order: LiveCryptoOrder;
  confirmation_challenge_id: string;
  confirmation_phrase_required: string;
  confirmation_expires_at: string;
  live_money_warning: string;
  execution_risk_verdict: string;
  preview_age_seconds: number;
  estimated_usd_balance_after: string | null;
  usd_balance_before: string | null;
};

export type LiveCryptoOrderSubmitResponse = {
  live_crypto_order: LiveCryptoOrder;
  execution_risk_verdict: string;
  provider_create_order_responded: boolean;
  provider_reconciliation_status: string | null;
  safe_provider_response: Record<string, unknown>;
  order_submitted: boolean;
};

export type LiveCryptoOrderReconcileResponse = {
  live_crypto_order: LiveCryptoOrder;
  reconciliation_status: string;
  provider_status: string | null;
  provider_order_id: string | null;
  provider_fill_observed: boolean;
  safe_provider_response: Record<string, unknown>;
};

export type LiveCryptoOrderListResponse = {
  items: LiveCryptoOrder[];
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
    try {
      const payload = (await response.json()) as { error?: { message?: string } };
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

export async function getLiveCryptoOrderReadiness(profileId: string): Promise<LiveCryptoOrderReadiness> {
  const query = new URLSearchParams();
  query.set("live_trading_profile_id", profileId);
  return requestJson<LiveCryptoOrderReadiness>(`/live-crypto-orders/readiness?${query.toString()}`);
}

export async function listLiveCryptoOrders(profileId?: string): Promise<LiveCryptoOrder[]> {
  const query = new URLSearchParams();
  if (profileId) {
    query.set("live_trading_profile_id", profileId);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<LiveCryptoOrderListResponse>(`/live-crypto-orders${suffix}`).then((payload) => payload.items);
}

export async function getLiveCryptoOrder(liveCryptoOrderId: string): Promise<LiveCryptoOrder> {
  return requestJson<LiveCryptoOrder>(`/live-crypto-orders/${encodeURIComponent(liveCryptoOrderId)}`);
}

export async function prepareLiveCryptoOrderConfirmation(
  payload: LiveCryptoOrderPrepareRequest,
): Promise<LiveCryptoOrderPrepareResponse> {
  return requestJson<LiveCryptoOrderPrepareResponse>("/live-crypto-orders/prepare-confirmation", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function submitLiveCryptoOrder(payload: LiveCryptoOrderSubmitRequest): Promise<LiveCryptoOrderSubmitResponse> {
  return requestJson<LiveCryptoOrderSubmitResponse>("/live-crypto-orders/submit", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function reconcileLiveCryptoOrder(
  liveCryptoOrderId: string,
  payload: LiveCryptoOrderReconcileRequest,
): Promise<LiveCryptoOrderReconcileResponse> {
  return requestJson<LiveCryptoOrderReconcileResponse>(`/live-crypto-orders/${encodeURIComponent(liveCryptoOrderId)}/reconcile`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function cancelLiveCryptoOrder(
  liveCryptoOrderId: string,
  payload: LiveCryptoOrderCancelRequest,
): Promise<LiveCryptoOrder> {
  return requestJson<LiveCryptoOrder>(`/live-crypto-orders/${encodeURIComponent(liveCryptoOrderId)}/cancel`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
