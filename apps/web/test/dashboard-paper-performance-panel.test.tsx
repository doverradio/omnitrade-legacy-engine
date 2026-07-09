import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PaperPerformancePanel from "@/components/domain/PaperPerformancePanel";

type Scenario = "with-data" | "empty";

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

    if (url.pathname !== "/paper/performance-summary") {
      return jsonResponse(404, {
        error: {
          message: `Unhandled route in test: GET ${url.pathname}`,
        },
      });
    }

    if (scenario === "empty") {
      return jsonResponse(200, {
        account_id: "11111111-1111-1111-1111-111111111111",
        starting_balance: "1000",
        current_cash_balance: "1000",
        equity: "1000",
        realized_pnl: "0",
        unrealized_pnl: "0",
        total_return_usd: "0",
        total_return_pct: "0",
        trade_count: 0,
        win_count: 0,
        loss_count: 0,
        win_rate: "0",
        latest_trade: null,
        positions: [],
        by_asset: [],
        by_strategy: [],
      });
    }

    return jsonResponse(200, {
      account_id: "11111111-1111-1111-1111-111111111111",
      starting_balance: "1000",
      current_cash_balance: "1008",
      equity: "1013",
      realized_pnl: "9",
      unrealized_pnl: "5",
      total_return_usd: "13",
      total_return_pct: "0.013",
      trade_count: 2,
      win_count: 1,
      loss_count: 0,
      win_rate: "0.5",
      latest_trade: {
        id: "22222222-2222-2222-2222-222222222222",
        asset_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        symbol: "BTCUSD",
        strategy_id: "33333333-3333-3333-3333-333333333333",
        side: "sell",
        quantity: "1",
        price: "110",
        fee: "1",
        executed_at: "2026-07-09T10:05:00Z",
      },
      positions: [
        {
          asset_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          symbol: "BTCUSD",
          quantity: "0.5",
          avg_entry_price: "100",
          unrealized_pnl_usd: "5",
          unrealized_pnl_pct: "0.05",
        },
      ],
      by_asset: [
        {
          asset_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          symbol: "BTCUSD",
          trade_count: 2,
          realized_pnl: "9",
          unrealized_pnl: "5",
          total_pnl: "14",
        },
      ],
      by_strategy: [
        {
          strategy_id: "33333333-3333-3333-3333-333333333333",
          trade_count: 1,
          win_count: 1,
          loss_count: 0,
          win_rate: "1",
          realized_pnl: "9",
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

describe("PaperPerformancePanel", () => {
  it("renders paper performance metrics", async () => {
    installFetchMock("with-data");
    render(<PaperPerformancePanel />);

    expect(await screen.findByRole("heading", { name: "Paper Performance Summary" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("1013")).toBeInTheDocument();
    });

    expect(screen.getByText("PAPER / SIMULATED")).toBeInTheDocument();
    expect(screen.getByText("13")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("50.00%")).toBeInTheDocument();
    expect(screen.getByText(/SELL 1 @ 110/)).toBeInTheDocument();
    expect(screen.getByText(/BTCUSD: 0.5/)).toBeInTheDocument();
  });

  it("renders empty state when no trades are available", async () => {
    installFetchMock("empty");
    render(<PaperPerformancePanel />);

    expect(await screen.findByRole("heading", { name: "Paper Performance Summary" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("No paper trades yet.")).toBeInTheDocument();
    });

    expect(screen.getByText("No open positions.")).toBeInTheDocument();
    expect(screen.getAllByText("0.00%").length).toBeGreaterThan(0);
  });
});
