import { ApiRequestError } from "@/lib/api/arena";

export type ExchangeProvider = "coinbase_advanced";
export type ExchangeEnvironment = "sandbox" | "production";
export type ExchangeConnectionStatus = "connected" | "disconnected" | "error";

export type ExchangeCredentialMask = {
  api_key: string;
  api_secret: string;
  passphrase: string | null;
};

export type ExchangeBalance = {
  currency: "USD" | "BTC" | "ETH";
  available: string;
  reserved: string;
  total: string;
};

export type ExchangeReadinessCheck = {
  code: string;
  label: string;
  ok: boolean;
  detail: string;
};

export type ExchangeConnection = {
  exchange_connection_id: string;
  provider: ExchangeProvider;
  provider_label: string;
  connection_name: string;
  environment: ExchangeEnvironment;
  status: ExchangeConnectionStatus;
  credentials_valid: boolean;
  credential_mask: ExchangeCredentialMask;
  api_permissions: string[];
  account_status: string | null;
  balances: ExchangeBalance[];
  total_equity_usd: string | null;
  last_successful_sync_at: string | null;
  last_heartbeat_at: string | null;
  last_api_error: string | null;
  readiness_checks: ExchangeReadinessCheck[];
  updated_at: string;
};

export type ExchangeConnectionListResponse = {
  items: ExchangeConnection[];
};

export type TestExchangeConnectionRequest = {
  provider: ExchangeProvider;
  environment: ExchangeEnvironment;
  api_key: string;
  api_secret: string;
  passphrase: string;
};

export type TestExchangeConnectionResponse = {
  reachable: boolean;
  authenticated: boolean;
  account_status: string | null;
  permissions: string[];
  heartbeat_at: string;
  error: string | null;
};

export type SaveExchangeConnectionRequest = {
  provider: ExchangeProvider;
  connection_name: string;
  environment: ExchangeEnvironment;
  api_key: string;
  api_secret: string;
  passphrase: string;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type ErrorEnvelope = {
  error?: {
    message?: string;
  };
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

export async function getExchangeConnections(): Promise<ExchangeConnectionListResponse> {
  return requestJson<ExchangeConnectionListResponse>("/exchange-connections");
}

export async function testExchangeConnection(payload: TestExchangeConnectionRequest): Promise<TestExchangeConnectionResponse> {
  return requestJson<TestExchangeConnectionResponse>("/exchange-connections/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function saveExchangeConnection(payload: SaveExchangeConnectionRequest): Promise<ExchangeConnection> {
  return requestJson<ExchangeConnection>("/exchange-connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function refreshExchangeBalances(exchangeConnectionId: string): Promise<ExchangeConnection> {
  return requestJson<ExchangeConnection>(`/exchange-connections/${encodeURIComponent(exchangeConnectionId)}/refresh/balances`, {
    method: "POST",
  });
}

export async function refreshExchangeAccount(exchangeConnectionId: string): Promise<ExchangeConnection> {
  return requestJson<ExchangeConnection>(`/exchange-connections/${encodeURIComponent(exchangeConnectionId)}/refresh/account`, {
    method: "POST",
  });
}

export async function refreshExchangePermissions(exchangeConnectionId: string): Promise<ExchangeConnection> {
  return requestJson<ExchangeConnection>(`/exchange-connections/${encodeURIComponent(exchangeConnectionId)}/refresh/permissions`, {
    method: "POST",
  });
}
