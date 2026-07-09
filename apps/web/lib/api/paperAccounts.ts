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

export type PaperTrade = {
  id: string;
  asset_id: string;
  side: string;
  quantity: string;
  price: string;
  fee: string;
  executed_at: string;
  signal_id?: string | null;
  strategy_id?: string | null;
  symbol?: string | null;
};

export type PaperTradeListResponse = {
  items: PaperTrade[];
  next_cursor: string | null;
};

export type PipelineActivity = {
  signal_id: string;
  action: string;
  status: string;
  reason?: string | null;
  created_at: string;
};

export type PaperPipelineHealth = {
  window_minutes: number;
  candles: number;
  signals_created: number;
  hold_signals: number;
  buy_sell_signals: number;
  execution_candidates: number;
  executions_attempted: number;
  risk_events: number;
  risk_rejected: number;
  trades: number;
  decision_records: number;
  latest_rejection_reason?: string | null;
  latest_updated_at?: string | null;
  recent_activity: PipelineActivity[];
};

export type GetPaperTradesParams = {
  account_id: string;
  strategy_id?: string;
  asset_id?: string;
  start_time?: string;
  end_time?: string;
  limit?: number;
  cursor?: string;
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

export async function getPaperTrades(params: GetPaperTradesParams): Promise<PaperTradeListResponse> {
  const search = new URLSearchParams();
  search.set("account_id", params.account_id);

  if (params.strategy_id) {
    search.set("strategy_id", params.strategy_id);
  }
  if (params.asset_id) {
    search.set("asset_id", params.asset_id);
  }
  if (params.start_time) {
    search.set("start_time", params.start_time);
  }
  if (params.end_time) {
    search.set("end_time", params.end_time);
  }
  if (typeof params.limit === "number") {
    search.set("limit", String(params.limit));
  }
  if (params.cursor) {
    search.set("cursor", params.cursor);
  }

  return requestJson<PaperTradeListResponse>(`/paper/trades?${search.toString()}`);
}

export async function getPaperPipelineHealth(windowMinutes = 120): Promise<PaperPipelineHealth> {
  const query = new URLSearchParams({ window_minutes: String(windowMinutes) });
  return requestJson<PaperPipelineHealth>(`/paper/pipeline-health?${query.toString()}`);
}
