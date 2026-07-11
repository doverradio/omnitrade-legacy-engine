import { ApiRequestError } from "@/lib/api/arena";

export type CapitalLedgerStatus = "all" | "active" | "inactive" | "completed" | "cancelled";
export type CapitalLedgerType =
  | "all"
  | "paper_account"
  | "validation_run"
  | "research_campaign"
  | "strategy_allocation"
  | "position"
  | "compounding_recommendation"
  | "withdrawal_recommendation"
  | "profit_reserve"
  | "policy_review";

export type CapitalLedgerSummary = {
  total_managed_capital: string;
  total_starting_capital: string;
  total_current_equity: string;
  total_allocated_capital: string;
  total_available_capital: string;
  total_reserved_capital: string;
  total_realized_pnl: string;
  total_unrealized_pnl: string;
  active_capital_pools: number;
  inactive_capital_pools: number;
  active_positions: number;
  total_trades: number;
  utilization_percent: number;
  data_completeness_percent: number;
  unavailable_sources: string[];
  generated_at: string;
};

export type CapitalLedgerPool = {
  capital_pool_id: string;
  capital_pool_type: CapitalLedgerType;
  name: string;
  status: Exclude<CapitalLedgerStatus, "all">;
  starting_capital: string | null;
  current_equity: string | null;
  allocated_capital: string | null;
  available_capital: string | null;
  reserved_capital: string | null;
  realized_pnl: string | null;
  unrealized_pnl: string | null;
  pnl_percent: number | null;
  started_at: string | null;
  completed_at: string | null;
  related_entity_type: string;
  related_entity_id: string;
  related_page_url: string;
  capital_campaign_uuid?: string | null;
  capital_campaign_name?: string | null;
  capital_campaign_status?: string | null;
  parent_capital_pool_id?: string | null;
  child_allocations_count: number;
  notes?: string | null;
};

export type CapitalLedgerResponse = {
  summary: CapitalLedgerSummary;
  capital_pools: CapitalLedgerPool[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
};

type ErrorEnvelope = {
  error?: {
    message?: string;
  };
};

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
    try {
      const payload = (await response.json()) as ErrorEnvelope;
      if (payload.error?.message) {
        message = payload.error.message;
      }
    } catch {
      // Keep fallback message.
    }
    throw new ApiRequestError(message, response.status);
  }

  return (await response.json()) as T;
}

export async function getCapitalLedger(params?: {
  status?: CapitalLedgerStatus;
  type?: CapitalLedgerType;
  page?: number;
  pageSize?: number;
}): Promise<CapitalLedgerResponse> {
  const query = new URLSearchParams();
  query.set("status", params?.status ?? "all");
  query.set("type", params?.type ?? "all");
  query.set("page", String(params?.page ?? 1));
  query.set("page_size", String(params?.pageSize ?? 50));
  return requestJson<CapitalLedgerResponse>(`/capital/ledger?${query.toString()}`);
}
