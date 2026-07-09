import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PaperPipelineFlow from "@/components/domain/PaperPipelineFlow";

type Scenario = "flowing" | "empty" | "risk-rejected";

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

    if (url.pathname !== "/paper/pipeline-health") {
      return jsonResponse(404, {
        error: {
          message: `Unhandled route in test: GET ${url.pathname}`,
        },
      });
    }

    if (scenario === "empty") {
      return jsonResponse(200, {
        window_minutes: 120,
        candles: 0,
        signals_created: 0,
        hold_signals: 0,
        buy_sell_signals: 0,
        execution_candidates: 0,
        executions_attempted: 0,
        risk_events: 0,
        risk_rejected: 0,
        trades: 0,
        decision_records: 0,
        latest_rejection_reason: null,
        latest_updated_at: null,
        recent_activity: [],
      });
    }

    if (scenario === "risk-rejected") {
      return jsonResponse(200, {
        window_minutes: 120,
        candles: 100,
        signals_created: 6,
        hold_signals: 1,
        buy_sell_signals: 5,
        execution_candidates: 5,
        executions_attempted: 5,
        risk_events: 5,
        risk_rejected: 3,
        trades: 0,
        decision_records: 6,
        latest_rejection_reason: "position_below_minimum_order_size",
        latest_updated_at: "2026-07-08T12:00:00Z",
        recent_activity: [
          {
            signal_id: "11111111-1111-1111-1111-111111111111",
            action: "buy",
            status: "risk_rejected",
            reason: "position_below_minimum_order_size",
            created_at: "2026-07-08T12:00:00Z",
          },
        ],
      });
    }

    return jsonResponse(200, {
      window_minutes: 120,
      candles: 1234,
      signals_created: 36,
      hold_signals: 30,
      buy_sell_signals: 6,
      execution_candidates: 6,
      executions_attempted: 6,
      risk_events: 6,
      risk_rejected: 0,
      trades: 1,
      decision_records: 36,
      latest_rejection_reason: null,
      latest_updated_at: "2026-07-08T12:00:00Z",
      recent_activity: [
        {
          signal_id: "22222222-2222-2222-2222-222222222222",
          action: "buy",
          status: "executed",
          reason: null,
          created_at: "2026-07-08T12:00:00Z",
        },
      ],
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("PaperPipelineFlow", () => {
  it("renders hero metric and active paper pipeline state", async () => {
    installFetchMock("flowing");
    render(<PaperPipelineFlow />);

    expect(await screen.findByRole("heading", { name: "Paper Pipeline Flow" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("1234")).toBeInTheDocument();
    });

    expect(screen.getByText("PAPER DECISION RECORDS")).toBeInTheDocument();
    expect(screen.getByLabelText("Paper decision records value").textContent).not.toBe("0");
    expect(screen.getByText("PAPER / SIMULATED")).toBeInTheDocument();
    expect(screen.getByText("BUY")).toBeInTheDocument();
    expect(screen.getByText("executed")).toBeInTheDocument();
    expect(screen.getByText("trade: yes")).toBeInTheDocument();
    expect(screen.getByText("Reason: -")).toBeInTheDocument();
  });

  it("renders empty-state pipeline with no recent activity", async () => {
    installFetchMock("empty");
    render(<PaperPipelineFlow />);

    expect(await screen.findByRole("heading", { name: "Paper Pipeline Flow" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("0").length).toBeGreaterThan(1);
    });

    expect(screen.getByText("No recent signals in this window.")).toBeInTheDocument();
    expect(screen.getByText("Waiting for market data")).toBeInTheDocument();
    expect(screen.getByText("None")).toBeInTheDocument();
  });

  it("surfaces rejected state and blocking-safe summary", async () => {
    installFetchMock("risk-rejected");
    render(<PaperPipelineFlow />);

    expect(await screen.findByRole("heading", { name: "Paper Pipeline Flow" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("position_below_minimum_order_size")).toBeInTheDocument();
    });

    expect(screen.getAllByText("Risk blocking safely").length).toBeGreaterThan(0);
    expect(screen.getByText("risk_rejected")).toBeInTheDocument();
    expect(screen.getByText("trade: no")).toBeInTheDocument();
  });

  it("auto-refreshes every 5 seconds", async () => {
    vi.useFakeTimers();
    const fetchMock = installFetchMock("flowing");
    render(<PaperPipelineFlow />);

    await Promise.resolve();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
