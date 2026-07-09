import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MissionControlPage from "@/app/mission-control/page";

type Scenario = "healthy" | "empty" | "degraded";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function installFetchMock(scenario: Scenario) {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/validation-runs") {
      return jsonResponse(200, {
        items: [
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
            enabled_strategies: ["MA Crossover"],
            enabled_research_agents: ["Baseline"],
            enabled_research_features: ["Laboratory"],
            health_score: 88,
            result_status: "INCOMPLETE",
          },
        ],
      });
    }

    if (url.pathname === "/validation-runs/11111111-1111-1111-1111-111111111111/events") {
      return jsonResponse(200, {
        items: [
          {
            id: 1,
            validation_run_id: "11111111-1111-1111-1111-111111111111",
            timestamp: "2026-07-09T00:00:00Z",
            event_type: "VALIDATION_STARTED",
            category: "all",
            severity: "green",
            title: "Validation Started",
            description: "Validation run started",
            metadata: {},
          },
        ],
        page: 1,
        page_size: 5,
        total: 1,
        has_more: false,
        order: "newest",
        window: "entire_run",
        category: "all",
        search: null,
      });
    }

    if (url.pathname !== "/operations/status") {
      return jsonResponse(404, {
        error: {
          message: `Unhandled route in test: GET ${url.pathname}`,
        },
      });
    }

    if (scenario === "degraded") {
      return jsonResponse(200, {
        overall_health: "red",
        run_status: {
          run_id: "run-1",
          started_at: "2026-07-09T00:00:00Z",
          expected_end: "2026-07-12T00:00:00Z",
          uptime: "10:00:00",
          current_phase: "degraded",
          health_status: "red",
        },
        system_health: {
          api: { state: "green", detail: "API responsive" },
          orchestrator: { state: "red", detail: "Heartbeat stale" },
          database: { state: "red", detail: "Database unavailable" },
          research_agent: { state: "yellow", detail: "OpenAI research adapter unavailable" },
        },
        research_status: {
          current_campaign: "Campaign 4",
          current_champion: "MA Crossover",
          campaign_status: "RUNNING",
        },
        monitoring: {
          candles_processed: 10,
          signals_generated: 0,
          paper_trades_executed: 0,
          decision_records_created: 2,
          replay_count: 1,
          candidate_count: 3,
          campaign_count: 1,
          laboratory_runs: 1,
          evolution_count: 0,
          current_champion: "MA Crossover",
          paper_equity: "24.80",
          signals_today: 0,
          trades_today: 0,
          research_memory_growth: 2,
        },
        alerts: [
          { code: "database_unavailable", severity: "red", message: "Database unavailable" },
          { code: "worker_stopped", severity: "red", message: "Worker stopped" },
        ],
      });
    }

    if (scenario === "empty") {
      return jsonResponse(200, {
        overall_health: "yellow",
        run_status: {
          run_id: "run-2",
          started_at: "2026-07-09T00:00:00Z",
          expected_end: "2026-07-12T00:00:00Z",
          uptime: "00:10:00",
          current_phase: "bootstrapping",
          health_status: "yellow",
        },
        system_health: {
          api: { state: "green", detail: "API responsive" },
          orchestrator: { state: "yellow", detail: "Heartbeat pending" },
          database: { state: "green", detail: "Database connected" },
          research_agent: { state: "green", detail: "OpenAI research adapter available" },
        },
        research_status: {
          current_campaign: null,
          current_champion: null,
          campaign_status: "IDLE",
        },
        monitoring: {
          candles_processed: 0,
          signals_generated: 0,
          paper_trades_executed: 0,
          decision_records_created: 0,
          replay_count: 0,
          candidate_count: 0,
          campaign_count: 0,
          laboratory_runs: 0,
          evolution_count: 0,
          current_champion: null,
          paper_equity: "0",
          signals_today: 0,
          trades_today: 0,
          research_memory_growth: 0,
        },
        alerts: [],
      });
    }

    return jsonResponse(200, {
      overall_health: "green",
      run_status: {
        run_id: "run-3",
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
      research_status: {
        current_campaign: "Campaign Alpha",
        current_champion: "RSI Mean Reversion",
        campaign_status: "RUNNING",
      },
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
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MissionControlPage", () => {
  it("renders health indicators and core mission metrics", async () => {
    installFetchMock("healthy");

    render(<MissionControlPage />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Campaign Alpha")).toBeInTheDocument();
    });

    expect(screen.getByText("System Health")).toBeInTheDocument();
    expect(screen.getByText("72-Hour Countdown")).toBeInTheDocument();
    expect(screen.getByText("Research Status")).toBeInTheDocument();
    expect(screen.getByText("Monitoring")).toBeInTheDocument();
    expect(screen.getByText("No active alerts.")).toBeInTheDocument();
    expect(screen.getByText("Latest 5 Validation Timeline Events")).toBeInTheDocument();
  });

  it("renders empty-state values with no active alerts", async () => {
    installFetchMock("empty");

    render(<MissionControlPage />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("None").length).toBeGreaterThan(0);
    });

    expect(screen.getByText("Signals Today")).toBeInTheDocument();
    expect(screen.getAllByText("0").length).toBeGreaterThan(4);
    expect(screen.getByText("No active alerts.")).toBeInTheDocument();
  });

  it("renders degraded alerts and red/yellow health states", async () => {
    installFetchMock("degraded");

    render(<MissionControlPage />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("Database unavailable").length).toBeGreaterThan(0);
    });

    expect(screen.getByText("Worker stopped")).toBeInTheDocument();
    expect(screen.getByText("Campaign 4")).toBeInTheDocument();
    expect(screen.getByText("Status: RUNNING")).toBeInTheDocument();
  });
});
