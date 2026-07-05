export type BacktestRunRequest = {
  strategy_id: string;
  parameter_set_id: string;
  asset_id: string;
  interval: "1m" | "5m" | "15m" | "1h" | "1d";
  start_time: string;
  end_time: string;
  initial_capital: string;
  fee_bps: string;
  slippage_bps: string;
};

export type BacktestRunAcceptedResponse = {
  backtest_id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
};

export type BacktestMetrics = {
  total_return_usd: string;
  total_return_pct: string;
  win_rate: string;
  max_drawdown: string;
  sharpe_like: string;
  trade_count: number;
  average_trade_usd: string;
  fee_drag_pct: string;
  equity_curve?: Array<{ time: string; equity: string | number }>;
};

export type SmallAccountWarning = {
  type: string;
  detail: string;
};

export type BacktestTrade = {
  side: "buy" | "sell" | string;
  quantity: string;
  price: string;
  executed_at: string;
  reason?: string | null;
};

export type BacktestResult = {
  id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  strategy_id: string;
  parameter_set_id: string;
  asset_id: string;
  initial_capital: string;
  metrics: BacktestMetrics | null;
  small_account_warning: SmallAccountWarning | null;
  trades: BacktestTrade[];
  error_detail?: string | null;
};

export type BacktestListItem = {
  id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  strategy_id: string;
  parameter_set_id: string;
  asset_id: string;
  interval: "1m" | "5m" | "15m" | "1h" | "1d" | string;
  start_time: string;
  end_time: string;
  initial_capital: string;
  fee_bps: string;
  slippage_bps: string;
  metrics: BacktestMetrics | null;
  small_account_warning: SmallAccountWarning | null;
};

type BacktestListResponse = {
  items: BacktestListItem[];
  next_cursor?: string | null;
};

type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export class ApiRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const response = await fetch(url, {
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

export async function runBacktest(payload: BacktestRunRequest): Promise<BacktestRunAcceptedResponse> {
  return requestJson<BacktestRunAcceptedResponse>("/backtests/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getBacktest(backtestId: string): Promise<BacktestResult> {
  return requestJson<BacktestResult>(`/backtests/${backtestId}`);
}

export async function getBacktests(): Promise<BacktestListItem[]> {
  const payload = await requestJson<BacktestListResponse>("/backtests");
  return payload.items;
}
