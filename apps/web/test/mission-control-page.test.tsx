import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MissionControlPage from "@/app/mission-control/page";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname !== "/mission-control/intelligence") {
      return jsonResponse(404, {
        error: {
          message: `Unhandled route in test: GET ${url.pathname}`,
        },
      });
    }

    return jsonResponse(200, {
      version: "v1",
      range: url.searchParams.get("range") ?? "24h",
      generated_at: "2026-07-09T10:00:00Z",
      current_score: 82,
      delta_label: "+4 this week",
      confidence: "High",
      trend: {
        direction: "up",
        label: "Improving",
        delta_label: "+4 this week",
        confidence: "High",
      },
      history: [
        { timestamp: "2026-07-09T08:00:00Z", score: 78, paper_equity: "104000.00", paper_pnl: "0.00", signals: 20, trades: 4, decision_count: 40, health: 80 },
        { timestamp: "2026-07-09T10:00:00Z", score: 82, paper_equity: "104523.55", paper_pnl: "523.55", signals: 42, trades: 8, decision_count: 82, health: 84 },
      ],
      timeline_events: [
        {
          event_id: "validation-1",
          timestamp: "2026-07-09T09:00:00Z",
          title: "Validation Run Started",
          description: "Validation run is now active.",
          related_validation_run: "11111111-1111-1111-1111-111111111111",
          health_at_that_moment: 80,
          paper_equity: "104200.00",
          paper_pnl: "200.00",
          signals: 30,
          trades: 6,
          decision_count: 60,
          severity: "green",
          category: "system",
          event_type: "VALIDATION_RUN_STARTED",
          metadata: {},
        },
      ],
      metric_breakdown: [
        {
          name: "Prediction Quality",
          score: 82,
          trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
          sparkline: [74, 76, 78, 79, 81, 82],
          details: "Validation health, signal generation, and decision activity.",
        },
      ],
      operations: {
        overall_health: "green",
        run_status: {
          run_id: "run-1",
          started_at: "2026-07-09T00:00:00Z",
          expected_end: "2026-07-12T00:00:00Z",
          uptime: "24:00:00",
          current_phase: "researching",
          health_status: "green",
        },
        system_health: {
          api: { state: "green", detail: "API responsive" },
          orchestrator: { state: "green", detail: "Heartbeat active" },
          database: { state: "green", detail: "Database connected" },
          research_agent: { state: "green", detail: "OpenAI research adapter available" },
        },
        research_status: { current_campaign: "Campaign Alpha", current_champion: "RSI Mean Reversion", campaign_status: "RUNNING" },
        monitoring: {
          candles_processed: 120000,
          signals_generated: 900,
          paper_trades_executed: 120,
          decision_records_created: 900,
          replay_count: 140,
          candidate_count: 80,
          campaign_count: 3,
          laboratory_runs: 25,
          evolution_count: 44,
          current_champion: "RSI Mean Reversion",
          paper_equity: "104523.55",
          signals_today: 42,
          trades_today: 8,
          research_memory_growth: 350,
        },
        alerts: [],
      },
      validation_runs: [
        {
          validation_run_id: "11111111-1111-1111-1111-111111111111",
          name: "72h Proving",
          objective: "Validate stability",
          duration_hours: 72,
          status: "RUNNING",
          started_at: "2026-07-09T00:00:00Z",
          expected_end_at: "2026-07-12T00:00:00Z",
          completed_at: null,
          paper_capital: "100000",
          enabled_strategies: ["MA Crossover", "RSI"],
          enabled_research_agents: ["Baseline", "OpenAI Sandbox"],
          enabled_research_features: ["Laboratory", "Evolution"],
          health_score: 88,
          result_status: "INCOMPLETE",
        },
      ],
      selected_validation_run_id: "11111111-1111-1111-1111-111111111111",
      notes: "Mission Control Intelligence Center V1 is a deterministic placeholder built from available operational metrics. It is informational only and does not change trading, research, or allocation behavior.",
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MissionControlPage", () => {
  it("renders the mission control intelligence center", async () => {
    installFetchMock();

    render(<MissionControlPage />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("System Intelligence")).toBeInTheDocument();
    });

    expect(screen.getByText("Validation Runs")).toBeInTheDocument();
    expect(screen.getByText("Research")).toBeInTheDocument();
    expect(screen.getByText("Monitoring")).toBeInTheDocument();
    expect(screen.getByText("Infrastructure")).toBeInTheDocument();
    expect(screen.getByText("Paper Trading")).toBeInTheDocument();
    expect(screen.getByText("Alerts")).toBeInTheDocument();
  });
});
