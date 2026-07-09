import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ValidationRunsPage from "@/app/validation-runs/page";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

type Scenario = "empty" | "active";

function installFetchMock(scenario: Scenario) {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = input instanceof Request ? input.method : init?.method ?? "GET";
    const url = new URL(rawUrl);

    if (url.pathname === "/validation-runs" && method === "GET") {
      if (scenario === "empty") {
        return jsonResponse(200, { items: [] });
      }

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
            enabled_strategies: ["MA Crossover", "RSI"],
            enabled_research_agents: ["Baseline", "OpenAI Sandbox"],
            enabled_research_features: ["Laboratory", "Evolution"],
            health_score: 88,
            result_status: "INCOMPLETE",
          },
        ],
      });
    }

    if (url.pathname === "/validation-runs" && method === "POST") {
      return jsonResponse(200, {
        validation_run_id: "22222222-2222-2222-2222-222222222222",
        name: "Started From Form",
        objective: "Validate flow",
        duration_hours: 72,
        status: "DRAFT",
        started_at: null,
        expected_end_at: null,
        completed_at: null,
        paper_capital: "100000",
        enabled_strategies: ["MA Crossover", "RSI"],
        enabled_research_agents: ["Baseline", "OpenAI Sandbox"],
        enabled_research_features: ["Laboratory", "Evolution", "Tournament", "Capital Allocation"],
        health_score: null,
        result_status: "INCOMPLETE",
      });
    }

    if (url.pathname === "/validation-runs/22222222-2222-2222-2222-222222222222/start" && method === "POST") {
      return jsonResponse(200, {
        run: {
          validation_run_id: "22222222-2222-2222-2222-222222222222",
          name: "Started From Form",
          objective: "Validate flow",
          duration_hours: 72,
          status: "RUNNING",
          started_at: "2026-07-09T02:00:00Z",
          expected_end_at: "2026-07-12T02:00:00Z",
          completed_at: null,
          paper_capital: "100000",
          enabled_strategies: ["MA Crossover", "RSI"],
          enabled_research_agents: ["Baseline", "OpenAI Sandbox"],
          enabled_research_features: ["Laboratory", "Evolution", "Tournament", "Capital Allocation"],
          health_score: 85,
          result_status: "INCOMPLETE",
        },
        initial_metrics: {
          elapsed_percentage: 0,
          time_remaining: "3d 00h 00m",
          candles_processed_during_run: 0,
          signals_generated_during_run: 0,
          trades_executed_during_run: 0,
          decision_records_created_during_run: 0,
          paper_pnl_during_run: "0",
          current_equity: "100000",
          current_champion: null,
          candidates_generated: 0,
          candidates_evaluated: 0,
          evolution_descendants: 0,
          research_memory_growth: 0,
          alerts_count: 0,
        },
      });
    }

    if (url.pathname === "/validation-runs/11111111-1111-1111-1111-111111111111/metrics" && method === "GET") {
      return jsonResponse(200, {
        elapsed_percentage: 45.5,
        time_remaining: "1d 15h 10m",
        candles_processed_during_run: 1024,
        signals_generated_during_run: 48,
        trades_executed_during_run: 6,
        decision_records_created_during_run: 48,
        paper_pnl_during_run: "120.50",
        current_equity: "100120.50",
        current_champion: "MA Crossover",
        candidates_generated: 14,
        candidates_evaluated: 14,
        evolution_descendants: 8,
        research_memory_growth: 32,
        alerts_count: 1,
      });
    }

    if (url.pathname === "/validation-runs/11111111-1111-1111-1111-111111111111/events" && method === "GET") {
      return jsonResponse(200, [
        {
          event_type: "VALIDATION_RUN_STARTED",
          message: "Validation run started",
          payload: {},
          created_at: "2026-07-09T00:00:00Z",
        },
      ]);
    }

    if (url.pathname === "/validation-runs/11111111-1111-1111-1111-111111111111" && method === "GET") {
      return jsonResponse(200, {
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
        overall_score: 88,
        scorecards: [
          { category: "API Health", status: "GREEN", score: 100, notes: "OK" },
          { category: "Worker Health", status: "YELLOW", score: 70, notes: "Minor delay" },
        ],
      });
    }

    if (url.pathname === "/validation-runs/11111111-1111-1111-1111-111111111111/cancel" && method === "POST") {
      return jsonResponse(200, {
        validation_run_id: "11111111-1111-1111-1111-111111111111",
        name: "72h Proving",
        objective: "Validate stability",
        duration_hours: 72,
        status: "CANCELLED",
        started_at: "2026-07-09T00:00:00Z",
        expected_end_at: "2026-07-12T00:00:00Z",
        completed_at: "2026-07-09T06:00:00Z",
        paper_capital: "100000",
        enabled_strategies: ["MA Crossover", "RSI"],
        enabled_research_agents: ["Baseline", "OpenAI Sandbox"],
        enabled_research_features: ["Laboratory", "Evolution"],
        health_score: 72,
        result_status: "INCOMPLETE",
      });
    }

    if (url.pathname === "/validation-runs/22222222-2222-2222-2222-222222222222/metrics" && method === "GET") {
      return jsonResponse(200, {
        elapsed_percentage: 0,
        time_remaining: "3d 00h 00m",
        candles_processed_during_run: 0,
        signals_generated_during_run: 0,
        trades_executed_during_run: 0,
        decision_records_created_during_run: 0,
        paper_pnl_during_run: "0",
        current_equity: "100000",
        current_champion: null,
        candidates_generated: 0,
        candidates_evaluated: 0,
        evolution_descendants: 0,
        research_memory_growth: 0,
        alerts_count: 0,
      });
    }

    if (url.pathname === "/validation-runs/22222222-2222-2222-2222-222222222222/events" && method === "GET") {
      return jsonResponse(200, []);
    }

    if (url.pathname === "/validation-runs/22222222-2222-2222-2222-222222222222" && method === "GET") {
      return jsonResponse(200, {
        validation_run_id: "22222222-2222-2222-2222-222222222222",
        name: "Started From Form",
        objective: "Validate flow",
        duration_hours: 72,
        status: "RUNNING",
        started_at: "2026-07-09T02:00:00Z",
        expected_end_at: "2026-07-12T02:00:00Z",
        completed_at: null,
        paper_capital: "100000",
        enabled_strategies: ["MA Crossover", "RSI"],
        enabled_research_agents: ["Baseline", "OpenAI Sandbox"],
        enabled_research_features: ["Laboratory", "Evolution", "Tournament", "Capital Allocation"],
        health_score: 85,
        result_status: "INCOMPLETE",
        overall_score: 85,
        scorecards: [
          { category: "API Health", status: "GREEN", score: 100, notes: "OK" },
        ],
      });
    }

    return jsonResponse(404, {
      error: {
        message: `Unhandled route in test: ${method} ${url.pathname}`,
      },
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ValidationRunsPage", () => {
  it("renders form and empty state", async () => {
    installFetchMock("empty");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start Validation Run" })).toBeInTheDocument();
    expect(screen.getByText("No validation runs yet.")).toBeInTheDocument();
  });

  it("renders active run progress, scorecard, history and detail", async () => {
    installFetchMock("active");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("72h Proving").length).toBeGreaterThan(0);
    });

    expect(screen.getByText("Active Validation Run")).toBeInTheDocument();
    expect(screen.getByText("45.50%")).toBeInTheDocument();
    expect(screen.getByText("Scorecard")).toBeInTheDocument();
    expect(screen.getByText("API Health")).toBeInTheDocument();
    expect(screen.getByText("Validation Run History")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "View" }));

    await waitFor(() => {
      expect(screen.getByText("Validation Run Detail")).toBeInTheDocument();
    });
    expect(screen.getByText("Timeline / Events")).toBeInTheDocument();
    expect(screen.getByText("Final Result Summary")).toBeInTheDocument();
  });

  it("supports start run flow from form", async () => {
    const fetchMock = installFetchMock("empty");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Start Validation Run" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/validation-runs"),
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
