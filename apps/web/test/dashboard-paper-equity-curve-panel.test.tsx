import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PaperEquityCurvePanel from "@/components/domain/PaperEquityCurvePanel";

type Scenario = "empty" | "curve";

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

    if (url.pathname !== "/paper/equity-curve") {
      return jsonResponse(404, {
        error: {
          message: `Unhandled route in test: GET ${url.pathname}`,
        },
      });
    }

    if (scenario === "empty") {
      return jsonResponse(200, {
        account_id: "11111111-1111-1111-1111-111111111111",
        window_minutes: 720,
        interval: 15,
        starting_balance: "1000",
        current_equity: "1000",
        total_return_usd: "0",
        total_return_pct: "0",
        latest_point_timestamp: "2026-07-09T10:00:00Z",
        points: [
          {
            timestamp: "2026-07-09T09:00:00Z",
            equity: "1000",
            cash_balance: "1000",
            realized_pnl: "0",
            unrealized_pnl: "0",
            trade_count_at_point: 0,
          },
          {
            timestamp: "2026-07-09T10:00:00Z",
            equity: "1000",
            cash_balance: "1000",
            realized_pnl: "0",
            unrealized_pnl: "0",
            trade_count_at_point: 0,
          },
        ],
      });
    }

    return jsonResponse(200, {
      account_id: "11111111-1111-1111-1111-111111111111",
      window_minutes: 720,
      interval: 15,
      starting_balance: "1000",
      current_equity: "1008",
      total_return_usd: "8",
      total_return_pct: "0.008",
      latest_point_timestamp: "2026-07-09T10:00:00Z",
      points: [
        {
          timestamp: "2026-07-09T09:30:00Z",
          equity: "1000",
          cash_balance: "1000",
          realized_pnl: "0",
          unrealized_pnl: "0",
          trade_count_at_point: 0,
        },
        {
          timestamp: "2026-07-09T09:45:00Z",
          equity: "900",
          cash_balance: "899",
          realized_pnl: "0",
          unrealized_pnl: "1",
          trade_count_at_point: 1,
        },
        {
          timestamp: "2026-07-09T10:00:00Z",
          equity: "1008",
          cash_balance: "1008",
          realized_pnl: "8",
          unrealized_pnl: "0",
          trade_count_at_point: 2,
        },
      ],
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PaperEquityCurvePanel", () => {
  it("renders empty-state flat balance explanation", async () => {
    installFetchMock("empty");
    render(<PaperEquityCurvePanel />);

    expect(await screen.findByRole("heading", { name: "Paper Equity Curve" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("1000").length).toBeGreaterThanOrEqual(2);
    });

    expect(screen.getByText(/No paper equity movement yet/i)).toBeInTheDocument();
    expect(screen.getByText("PAPER / SIMULATED")).toBeInTheDocument();
  });

  it("renders equity curve line and summary", async () => {
    installFetchMock("curve");
    render(<PaperEquityCurvePanel />);

    expect(await screen.findByRole("heading", { name: "Paper Equity Curve" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("1008")).toBeInTheDocument();
    });

    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("0.80%")).toBeInTheDocument();
    expect(screen.getByTestId("equity-curve-polyline")).toBeInTheDocument();
  });
});
