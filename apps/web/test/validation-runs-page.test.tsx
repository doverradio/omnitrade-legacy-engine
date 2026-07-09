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

type Scenario = "empty" | "active" | "multi-active";

function installFetchMock(scenario: Scenario) {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = input instanceof Request ? input.method : init?.method ?? "GET";
    const url = new URL(rawUrl);

    if (url.pathname === "/validation-runs" && method === "GET") {
      if (scenario === "empty") {
        return jsonResponse(200, { items: [] });
      }

      if (scenario === "multi-active") {
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
            {
              validation_run_id: "33333333-3333-3333-3333-333333333333",
              name: "24h Experiment",
              objective: "Second active run",
              duration_hours: 24,
              status: "RUNNING",
              started_at: "2026-07-09T02:00:00Z",
              expected_end_at: "2026-07-10T02:00:00Z",
              completed_at: null,
              paper_capital: "25",
              enabled_strategies: ["RSI"],
              enabled_research_agents: ["Baseline"],
              enabled_research_features: ["Laboratory"],
              health_score: 79,
              result_status: "INCOMPLETE",
            },
          ],
        });
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

    if (url.pathname === "/validation-runs/33333333-3333-3333-3333-333333333333/metrics" && method === "GET") {
      return jsonResponse(200, {
        elapsed_percentage: 12.25,
        time_remaining: "0d 20h 00m",
        candles_processed_during_run: 210,
        signals_generated_during_run: 9,
        trades_executed_during_run: 2,
        decision_records_created_during_run: 9,
        paper_pnl_during_run: "3.12",
        current_equity: "28.12",
        current_champion: "RSI",
        candidates_generated: 3,
        candidates_evaluated: 3,
        evolution_descendants: 0,
        research_memory_growth: 4,
        alerts_count: 0,
      });
    }

    if (url.pathname === "/validation-runs/11111111-1111-1111-1111-111111111111/events" && method === "GET") {
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
        page_size: 30,
        total: 1,
        has_more: false,
        order: "newest",
        window: "entire_run",
        category: "all",
        search: null,
      });
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

    if (url.pathname === "/validation-runs/33333333-3333-3333-3333-333333333333/events" && method === "GET") {
      return jsonResponse(200, {
        items: [
          {
            id: 2,
            validation_run_id: "33333333-3333-3333-3333-333333333333",
            timestamp: "2026-07-09T02:00:00Z",
            event_type: "VALIDATION_STARTED",
            category: "all",
            severity: "green",
            title: "Validation Started",
            description: "Second run started",
            metadata: {},
          },
        ],
        page: 1,
        page_size: 30,
        total: 1,
        has_more: false,
        order: "newest",
        window: "entire_run",
        category: "all",
        search: null,
      });
    }

    if (url.pathname === "/validation-runs/33333333-3333-3333-3333-333333333333" && method === "GET") {
      return jsonResponse(200, {
        validation_run_id: "33333333-3333-3333-3333-333333333333",
        name: "24h Experiment",
        objective: "Second active run",
        duration_hours: 24,
        status: "RUNNING",
        started_at: "2026-07-09T02:00:00Z",
        expected_end_at: "2026-07-10T02:00:00Z",
        completed_at: null,
        paper_capital: "25",
        enabled_strategies: ["RSI"],
        enabled_research_agents: ["Baseline"],
        enabled_research_features: ["Laboratory"],
        health_score: 79,
        result_status: "INCOMPLETE",
        overall_score: 79,
        scorecards: [
          { category: "Campaign Engine", status: "GREEN", score: 100, notes: "Active" },
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
      return jsonResponse(200, {
        items: [],
        page: 1,
        page_size: 30,
        total: 0,
        has_more: false,
        order: "newest",
        window: "entire_run",
        category: "all",
        search: null,
      });
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
  it("uses accordion default state with active section open", async () => {
    installFetchMock("empty");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();
    expect(screen.getByText("Active Validation Runs")).toBeInTheDocument();
    expect(screen.getByText("No active validation runs.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start Validation Run" })).not.toBeInTheDocument();
  });

  it("allows multiple accordions to stay open", async () => {
    installFetchMock("active");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /New Validation Run/i }));
    fireEvent.click(screen.getByRole("button", { name: /Scorecard/i }));

    expect(screen.getByRole("button", { name: "Start Validation Run" })).toBeInTheDocument();
    expect(screen.getByText("Default proving capital is $25 in Small Account Mode.")).toBeInTheDocument();
    expect(screen.getByText("Active Validation Runs")).toBeInTheDocument();
    expect(screen.getByText("Scorecard")).toBeInTheDocument();
  });

  it("renders active run cards and selecting a run updates scorecard/timeline target", async () => {
    installFetchMock("multi-active");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("72h Proving").length).toBeGreaterThan(0);
    });
    expect(screen.getByText("24h Experiment")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Select run" }));
    fireEvent.click(screen.getByRole("button", { name: /Scorecard/i }));
    fireEvent.click(screen.getByRole("button", { name: /Validation Timeline/i }));

    await waitFor(() => {
      expect(screen.getByText("Campaign Engine")).toBeInTheDocument();
    });
    expect(screen.getByText("Objective: Second active run")).toBeInTheDocument();
    expect(screen.getAllByText("Validation Timeline").length).toBeGreaterThan(0);
  });

  it("shows mobile-friendly empty states for scorecard and timeline when no run selected", async () => {
    installFetchMock("empty");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Scorecard/i }));
    fireEvent.click(screen.getByRole("button", { name: /Validation Timeline/i }));

    await waitFor(() => {
      expect(screen.getByText("Select a validation run to view scorecard.")).toBeInTheDocument();
    });
    expect(screen.getByText("Select a validation run to view timeline.")).toBeInTheDocument();
  });

  it("supports start run flow from form", async () => {
    const fetchMock = installFetchMock("empty");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /New Validation Run/i }));

    fireEvent.click(screen.getByRole("button", { name: "Start Validation Run" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/validation-runs"),
        expect.objectContaining({ method: "POST" }),
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Validation run started successfully.")).toBeInTheDocument();
    });
  });

  it("auto refreshes while active runs exist", async () => {
    installFetchMock("active");
    const intervalSpy = vi.spyOn(window, "setInterval");

    render(<ValidationRunsPage />);

    expect(await screen.findByRole("heading", { name: "Validation Runs" })).toBeInTheDocument();
    expect(intervalSpy).toHaveBeenCalledWith(expect.any(Function), 5000);
    intervalSpy.mockRestore();
  });
});
