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

type ErrorEnvelope = {
  error?: {
    message?: string;
  };
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
