import { ApiRequestError } from "@/lib/api/live";
import { getOperatorAuthHeaders } from "@/lib/api/operator-auth";

export type InstantTradeStatus =
  | "VALIDATING"
  | "SUBMITTING"
  | "PENDING"
  | "FILLED"
  | "RECONCILIATION_REQUIRED"
  | "REJECTED"
  | "FAILED";

export type InstantTradeBuyRequest = {
  paper_account_id: string;
  live_trading_profile_id: string;
  provider: string;
  environment: "production" | "sandbox";
  product: string;
  quote_amount: string;
  actor: string;
  confirmation: boolean;
  idempotency_key: string;
};

export type InstantTradeReceipt = {
  internal_order_id: string;
  provider_order_id: string | null;
  status: InstantTradeStatus;
  requested_amount: string;
  executed_quantity: string | null;
  average_fill_price: string | null;
  fees: Record<string, string>;
  created_at: string;
  submitted_at: string | null;
  acknowledged_at: string | null;
  filled_at: string | null;
  updated_at: string;
  reconciliation_state: string | null;
  order: {
    live_crypto_order_id: string;
    provider: string;
    environment: string;
    product: string;
    side: string;
    raw_status: string;
    failure_code: string | null;
    failure_reason: string | null;
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

export async function buyInstantTrade(payload: InstantTradeBuyRequest): Promise<InstantTradeReceipt> {
  return requestJson<InstantTradeReceipt>("/instant-trades/buy", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getInstantTrade(orderId: string): Promise<InstantTradeReceipt> {
  return requestJson<InstantTradeReceipt>(`/instant-trades/${encodeURIComponent(orderId)}`);
}

export async function adoptInstantTrade(orderId: string, actor: string): Promise<InstantTradeReceipt> {
  return requestJson<InstantTradeReceipt>(`/instant-trades/${encodeURIComponent(orderId)}/adopt-into-autonomous-management`, {
    method: "POST",
    body: JSON.stringify({ actor }),
  });
}
