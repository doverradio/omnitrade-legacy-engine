type ErrorEnvelope = {
  error?: {
    message?: string;
  };
};

import { ApiRequestError, type OperationalStatus, type ValidationRun, type ValidationRunEventCategory, type ValidationRunEventSeverity } from "@/lib/api/arena";

export type MissionControlIntelligenceRange = "24h" | "7d" | "30d" | "90d" | "all";

export type MissionControlIntelligenceTrend = {
  direction: "up" | "down" | "flat";
  label: string;
  delta_label: string;
  confidence: string;
};

export type MissionControlIntelligenceHistoryPoint = {
  timestamp: string;
  score: number;
  paper_equity: string;
  paper_pnl: string;
  signals: number;
  trades: number;
  decision_count: number;
  health: number;
};

export type MissionControlIntelligenceTimelineEvent = {
  event_id: string;
  timestamp: string;
  title: string;
  description: string;
  related_validation_run: string | null;
  health_at_that_moment: number | null;
  paper_equity: string | null;
  paper_pnl: string | null;
  signals: number | null;
  trades: number | null;
  decision_count: number | null;
  severity: ValidationRunEventSeverity;
  category: ValidationRunEventCategory;
  event_type: string;
  metadata: Record<string, unknown>;
};

export type MissionControlIntelligenceMetric = {
  name: string;
  score: number;
  trend: MissionControlIntelligenceTrend;
  sparkline: number[];
  details: string;
};

export type MissionControlIntelligenceResponse = {
  version: string;
  range: MissionControlIntelligenceRange;
  generated_at: string;
  current_score: number;
  delta_label: string;
  confidence: string;
  trend: MissionControlIntelligenceTrend;
  history: MissionControlIntelligenceHistoryPoint[];
  timeline_events: MissionControlIntelligenceTimelineEvent[];
  metric_breakdown: MissionControlIntelligenceMetric[];
  operations: OperationalStatus;
  validation_runs: ValidationRun[];
  selected_validation_run_id: string | null;
  notes: string;
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

export async function getMissionControlIntelligence(
  range: MissionControlIntelligenceRange = "24h",
): Promise<MissionControlIntelligenceResponse> {
  return requestJson<MissionControlIntelligenceResponse>(`/mission-control/intelligence?range=${encodeURIComponent(range)}`);
}