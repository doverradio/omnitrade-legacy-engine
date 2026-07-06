import { ApiRequestError } from "@/lib/api/backtests";

type ErrorEnvelope = {
  error?: {
    message?: string;
  };
};

export type ParameterSetItem = {
  id: string;
  strategy_id: string;
  name: string;
  parameters: Record<string, unknown>;
  created_at?: string;
};

type ParameterSetsResponse = {
  items: ParameterSetItem[];
};

type SaveParameterSetRequest = {
  name: string;
  parameters: Record<string, unknown>;
};

type SaveParameterSetResponse = ParameterSetItem;

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
      // Keep the generic message when response is not valid JSON.
    }

    throw new ApiRequestError(message, response.status);
  }

  return (await response.json()) as T;
}

export async function getParameterSets(): Promise<ParameterSetItem[]> {
  const payload = await requestJson<ParameterSetsResponse>("/parameter-sets");
  return payload.items;
}

export async function saveParameterSet(strategyId: string, input: SaveParameterSetRequest): Promise<ParameterSetItem> {
  return requestJson<SaveParameterSetResponse>(`/strategies/${strategyId}/parameter-sets`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}
