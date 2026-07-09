import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MissionControlIntelligenceCenter from "@/components/domain/MissionControlIntelligenceCenter";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function buildPayload(range: string, severity: "green" | "yellow" | "red" = "green") {
  return {
    version: "v1",
    range,
    generated_at: "2026-07-09T10:00:00Z",
    current_score: range === "all" ? 79 : 82,
    delta_label: range === "all" ? "+1 this period" : "+4 this week",
    confidence: "High",
    trend: {
      direction: range === "all" ? "flat" : "up",
      label: range === "all" ? "Stable" : "Improving",
      delta_label: range === "all" ? "+1 this period" : "+4 this week",
      confidence: "High",
    },
    history: [
      { timestamp: "2026-07-09T08:00:00Z", score: 78, paper_equity: "104000.00", paper_pnl: "0.00", signals: 20, trades: 4, decision_count: 40, health: 80 },
      { timestamp: "2026-07-09T09:00:00Z", score: 80, paper_equity: "104200.00", paper_pnl: "200.00", signals: 30, trades: 6, decision_count: 60, health: 82 },
      { timestamp: "2026-07-09T10:00:00Z", score: range === "all" ? 79 : 82, paper_equity: "104523.55", paper_pnl: "523.55", signals: 42, trades: 8, decision_count: 82, health: 84 },
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
        severity,
        category: "system",
        event_type: "VALIDATION_RUN_STARTED",
        metadata: {},
      },
      {
        event_id: "validation-2",
        timestamp: "2026-07-09T10:00:00Z",
        title: "Champion Changed",
        description: "The lead strategy changed.",
        related_validation_run: "11111111-1111-1111-1111-111111111111",
        health_at_that_moment: 84,
        paper_equity: "104523.55",
        paper_pnl: "523.55",
        signals: 42,
        trades: 8,
        decision_count: 82,
        severity: "purple",
        category: "research",
        event_type: "CHAMPION_STRATEGY_CHANGED",
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
      {
        name: "Risk Discipline",
        score: 73,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [68, 69, 70, 71, 72, 73],
        details: "Alerts, operational risk, and validation stability.",
      },
      {
        name: "Research Activity",
        score: 77,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [70, 71, 73, 74, 76, 77],
        details: "Campaigns, laboratory runs, evolution, and memory growth.",
      },
      {
        name: "Execution Health",
        score: 81,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [73, 75, 77, 78, 80, 81],
        details: "Paper trade throughput and decision execution velocity.",
      },
      {
        name: "Infrastructure Health",
        score: 94,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [90, 91, 92, 93, 94, 94],
        details: "API, worker, database, and research adapter health.",
      },
      {
        name: "Paper Trading Health",
        score: 84,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [76, 78, 79, 81, 83, 84],
        details: "Paper equity, fills, and overall proving throughput.",
      },
    ],
    operations: {
      overall_health: severity === "red" ? "red" : severity === "yellow" ? "yellow" : "green",
      run_status: {
        run_id: "run-1",
        started_at: "2026-07-09T00:00:00Z",
        expected_end: "2026-07-12T00:00:00Z",
        uptime: "24:00:00",
        current_phase: "researching",
        health_status: severity === "red" ? "red" : severity === "yellow" ? "yellow" : "green",
      },
      system_health: {
        api: { state: "green", detail: "API responsive" },
        orchestrator: { state: severity === "red" ? "red" : severity === "yellow" ? "yellow" : "green", detail: severity === "red" ? "Heartbeat stale" : "Heartbeat active" },
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
      alerts: severity === "green" ? [] : [{ code: "worker_stopped", severity, message: "Worker stopped" }],
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
  };
}

function installFetchMock(scenario: "healthy" | "empty" | "degraded" = "healthy") {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/mission-control/intelligence") {
      if (scenario === "empty") {
        return jsonResponse(200, buildPayload(url.searchParams.get("range") ?? "24h"));
      }
      if (scenario === "degraded") {
        return jsonResponse(200, buildPayload(url.searchParams.get("range") ?? "24h", "yellow"));
      }
      return jsonResponse(200, buildPayload(url.searchParams.get("range") ?? "24h"));
    }

    return jsonResponse(404, {
      error: {
        message: `Unhandled route in test: GET ${url.pathname}`,
      },
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MissionControlIntelligenceCenter", () => {
  it("renders the hero intelligence timeline, score cards, and recent timeline", async () => {
    installFetchMock("healthy");

    render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();
    expect(screen.getByText("System Intelligence")).toBeInTheDocument();
    expect(screen.getByText("82 / 100")).toBeInTheDocument();
    expect(screen.getByText("Prediction Quality")).toBeInTheDocument();
    expect(screen.getByText("Infrastructure Health")).toBeInTheDocument();
    expect(screen.getByText("Recent Timeline")).toBeInTheDocument();
    expect(screen.getByText("Validation Run Started")).toBeInTheDocument();
  });

  it("switches intelligence range tabs and refetches the selected range", async () => {
    const fetchMock = installFetchMock("healthy");

    render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "7D" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) => {
          const input = call[0];
          const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
          return new URL(rawUrl).pathname === "/mission-control/intelligence" && new URL(rawUrl).searchParams.get("range") === "7d";
        }),
      ).toBe(true);
    });

    fireEvent.click(screen.getByRole("button", { name: "ALL" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) => {
          const input = call[0];
          const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
          return new URL(rawUrl).pathname === "/mission-control/intelligence" && new URL(rawUrl).searchParams.get("range") === "all";
        }),
      ).toBe(true);
    });
  });

  it("keeps accordion sections open together", async () => {
    installFetchMock("healthy");

    render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Research/i }));
    fireEvent.click(screen.getByRole("button", { name: /Alerts/i }));

    expect(screen.getByText("Current Campaign")).toBeInTheDocument();
    expect(screen.getByText("No active alerts.")).toBeInTheDocument();
    expect(screen.getByText("Validation Run Started")).toBeInTheDocument();
  });

  it("opens the event detail modal on mobile when a chart point is clicked", async () => {
    installFetchMock("healthy");
    Object.defineProperty(window, "innerWidth", { value: 375, configurable: true, writable: true });

    render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Open Validation Run Started/i }));

    expect(await screen.findByRole("dialog", { name: "Timeline event detail" })).toBeInTheDocument();
    expect(screen.getByText("Timestamp")).toBeInTheDocument();
    expect(screen.getByText("Related validation run")).toBeInTheDocument();
  });
});
