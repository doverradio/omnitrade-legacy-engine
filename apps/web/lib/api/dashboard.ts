type ErrorEnvelope = {
  error?: {
    message?: string;
  };
};

export type DashboardIntelligenceRange = "24h" | "7d" | "30d" | "90d";

export type DashboardIntelligenceComponent = {
  name: string;
  score: number;
  weight: number;
  explanation: string;
};

export type DashboardIntelligenceTimelinePoint = {
  timestamp: string;
  score: number;
  equity: string;
  decision_quality: number;
  research_quality: number;
  operational_health: number;
};

export type DashboardIntelligenceScore = {
  score: number;
  data_completeness: number;
  range: DashboardIntelligenceRange;
  generated_at: string;
  components: DashboardIntelligenceComponent[];
  timeline: DashboardIntelligenceTimelinePoint[];
};

function resolveApiBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_API_BASE_URL) {
    return process.env.NEXT_PUBLIC_API_BASE_URL;
  }

  if (typeof window !== "undefined" && window.location.hostname === "app.bigdeal.sale") {
    return "https://api.bigdeal.sale";
  }

  return "http://localhost:8000";
}

const API_BASE_URL = resolveApiBaseUrl();

export class ApiRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

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

export async function getDashboardIntelligenceScore(range: DashboardIntelligenceRange = "24h"): Promise<DashboardIntelligenceScore> {
  return requestJson<DashboardIntelligenceScore>(`/dashboard/intelligence-score?range=${encodeURIComponent(range)}`);
}