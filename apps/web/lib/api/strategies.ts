import { ApiRequestError } from "@/lib/api/backtests";

type ErrorEnvelope = {
  error?: {
    message?: string;
  };
};

export type StrategyItem = {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  module_version: string;
  default_params?: Record<string, unknown>;
};

type StrategiesResponse = {
  items: StrategyItem[];
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

export async function getStrategies(): Promise<StrategyItem[]> {
  const payload = await requestJson<StrategiesResponse>("/strategies");
  return payload.items;
}
