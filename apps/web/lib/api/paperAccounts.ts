type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export type PaperAccount = {
  id: string;
  name: string;
  asset_class: "crypto" | "stock" | string;
  starting_balance: string;
  current_cash_balance: string;
  is_active?: boolean;
  equity: string;
  equity_return_usd: string;
  equity_return_pct: string;
  positions: PaperAccountPosition[];
};

export type PaperAccountPosition = {
  asset_id: string;
  symbol: string;
  quantity: string;
  avg_entry_price: string;
  unrealized_pnl_usd: string;
  unrealized_pnl_pct: string;
};

export type CreatePaperAccountRequest = {
  name: string;
  asset_class: "crypto" | "stock";
  starting_balance: string;
};

export type CreatePaperAccountResponse = PaperAccount;

export type ResetPaperAccountRequest = {
  account_id: string;
  confirm: true;
};

export type ResetPaperAccountResponse = {
  account_id: string;
  current_cash_balance: string;
  positions: PaperAccountPosition[];
};

type PaperAccountResponse = PaperAccount;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

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
      // Keep generic message when the response body is not valid JSON.
    }

    throw new ApiRequestError(message, response.status);
  }

  return (await response.json()) as T;
}

export async function getPaperAccount(accountId?: string): Promise<PaperAccount> {
  const query = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  return requestJson<PaperAccountResponse>(`/paper/account${query}`);
}

export async function createPaperAccount(payload: CreatePaperAccountRequest): Promise<CreatePaperAccountResponse> {
  return requestJson<CreatePaperAccountResponse>("/paper/account", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function resetPaperAccount(payload: ResetPaperAccountRequest): Promise<ResetPaperAccountResponse> {
  return requestJson<ResetPaperAccountResponse>("/paper/reset", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
