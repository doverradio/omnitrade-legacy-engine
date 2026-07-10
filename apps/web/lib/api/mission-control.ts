type ErrorEnvelope = {
  error?: {
    message?: string;
  };
};

import { ApiRequestError, type OperationalStatus, type ValidationRun, type ValidationRunEventCategory, type ValidationRunEventSeverity } from "@/lib/api/arena";

export type MissionControlIntelligenceRange = "24h" | "72h" | "7d" | "30d" | "90d" | "all";
export type MissionControlProfitRange = "24h" | "72h" | "7d" | "30d" | "90d" | "all";
export type MissionControlProfitMode = "paper" | "live" | "combined";

export type MissionControlSnapshotHistoryPoint = {
  snapshot_id: string;
  captured_at: string;
  bucket_start: string;
  bucket_end: string;
  overall_score: number | null;
  confidence: string | null;
  data_completeness: number | null;
  market_awareness_score: number | null;
  decision_quality_score: number | null;
  execution_reliability_score: number | null;
  risk_discipline_score: number | null;
  research_progress_score: number | null;
  adaptation_rate_score: number | null;
  operational_health_score: number | null;
  capital_efficiency_score: number | null;
  profit_performance_score: number | null;
  paper_net_profit: string | null;
  live_net_profit: string | null;
  combined_net_profit: string | null;
  paper_equity: string | null;
  live_equity: string | null;
  combined_equity: string | null;
  realized_pnl: string | null;
  unrealized_pnl: string | null;
  fees: string | null;
  drawdown_percent: string | null;
  source_counts: Record<string, number>;
  annotations: Array<Record<string, unknown>>;
  schema_version: string;
};

export type MissionControlSnapshotHistoryResponse = {
  range: MissionControlIntelligenceRange;
  dimension: string | null;
  points: MissionControlSnapshotHistoryPoint[];
  generated_at: string;
};

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
  total_managed_capital: string | null;
  validation_runs: ValidationRun[];
  selected_validation_run_id: string | null;
  notes: string;
};

export type MissionControlProfitSeriesPoint = {
  timestamp: string;
  paper_equity: string | null;
  live_equity: string | null;
  combined_equity: string | null;
  cumulative_realized_pnl: string | null;
  cumulative_unrealized_pnl: string | null;
  cumulative_fees: string | null;
  cumulative_net_profit: string | null;
  drawdown: string | null;
  trade_count: number;
  source_event_ids: string[];
};

export type MissionControlProfitAnnotation = {
  timestamp: string;
  event_type: string;
  title: string;
  description: string;
  severity: string;
  source_record_id: string | null;
  metadata: Record<string, unknown>;
};

export type MissionControlProfitResponse = {
  range: MissionControlProfitRange;
  mode: MissionControlProfitMode;
  start_at: string | null;
  end_at: string;
  starting_equity: string | null;
  ending_equity: string | null;
  gross_profit: string | null;
  gross_loss: string | null;
  realized_pnl: string | null;
  unrealized_pnl: string | null;
  fees: string | null;
  fees_available: boolean;
  net_profit: string | null;
  total_economic_pnl: string | null;
  return_percent: string | null;
  peak_equity: string | null;
  max_drawdown_amount: string | null;
  max_drawdown_percent: string | null;
  winning_trades: number;
  losing_trades: number;
  breakeven_trades: number;
  win_rate: string | null;
  profit_factor: string | null;
  average_win: string | null;
  average_loss: string | null;
  largest_win: string | null;
  largest_loss: string | null;
  trade_count: number;
  open_position_count: number;
  equity_series: MissionControlProfitSeriesPoint[];
  profit_series: MissionControlProfitSeriesPoint[];
  annotations: MissionControlProfitAnnotation[];
  source_counts: Record<string, number>;
  data_completeness: number;
  calculation_explanation: string;
  generated_at: string;
};

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function requestJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    signal,
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
  signal?: AbortSignal,
): Promise<MissionControlIntelligenceResponse> {
  return requestJson<MissionControlIntelligenceResponse>(`/mission-control/intelligence?range=${encodeURIComponent(range)}`, signal);
}

export async function getMissionControlProfit(
  range: MissionControlProfitRange = "24h",
  mode: MissionControlProfitMode = "paper",
  signal?: AbortSignal,
): Promise<MissionControlProfitResponse> {
  return requestJson<MissionControlProfitResponse>(
    `/mission-control/profit?range=${encodeURIComponent(range)}&mode=${encodeURIComponent(mode)}`,
    signal,
  );
}

export async function getMissionControlIntelligenceHistory(
  range: MissionControlIntelligenceRange = "24h",
  dimension: string | null = null,
  signal?: AbortSignal,
): Promise<MissionControlSnapshotHistoryResponse> {
  const query = new URLSearchParams({ range });
  if (dimension) {
    query.set("dimension", dimension);
  }
  return requestJson<MissionControlSnapshotHistoryResponse>(`/mission-control/intelligence/history?${query.toString()}`, signal);
}