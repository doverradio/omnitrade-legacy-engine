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
};

type ParameterSetsResponse = {
  items: ParameterSetItem[];
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
