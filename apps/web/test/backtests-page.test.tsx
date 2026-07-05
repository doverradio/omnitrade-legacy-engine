import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import BacktestsPage from "@/app/backtests/page";

type MockResponsePayload = {
  status?: number;
  body: unknown;
};

function jsonResponse(payload: MockResponsePayload): Response {
  return new Response(JSON.stringify(payload.body), {
    status: payload.status ?? 200,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

type BacktestStatus = "running" | "completed" | "failed";

function installFetchMock(backtestDetailStatus: BacktestStatus, warningDetail?: string) {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);
    const method = init?.method ?? "GET";

    if (url.pathname === "/strategies" && method === "GET") {
      return jsonResponse({
        body: {
          items: [
            {
              id: "strategy-1",
              name: "MA Crossover",
              slug: "ma_crossover",
              is_active: false,
              module_version: "1.0.0",
              default_params: { fast_period: 10, slow_period: 50 },
            },
          ],
        },
      });
    }

    if (url.pathname === "/parameter-sets" && method === "GET") {
      return jsonResponse({
        body: {
          items: [
            {
              id: "param-1",
              strategy_id: "strategy-1",
              name: "default-v1",
              parameters: { fast_period: 10, slow_period: 50 },
            },
          ],
        },
      });
    }

    if (url.pathname === "/markets/assets" && method === "GET") {
      return jsonResponse({
        body: {
          items: [
            {
              id: "asset-1",
              symbol: "BTCUSDT",
              asset_class: "crypto",
              exchange: "binance_us",
              is_active: true,
            },
          ],
        },
      });
    }

    if (url.pathname === "/backtests" && method === "GET") {
      return jsonResponse({
        body: {
          items: [],
          next_cursor: null,
        },
      });
    }

    if (url.pathname === "/backtests/run" && method === "POST") {
      return jsonResponse({
        status: 202,
        body: {
          backtest_id: "backtest-1",
          status: "running",
        },
      });
    }

    if (url.pathname === "/backtests/backtest-1" && method === "GET") {
      if (backtestDetailStatus === "running") {
        return jsonResponse({
          body: {
            id: "backtest-1",
            status: "running",
            strategy_id: "strategy-1",
            parameter_set_id: "param-1",
            asset_id: "asset-1",
            initial_capital: "25",
            metrics: null,
            small_account_warning: null,
            trades: [],
          },
        });
      }

      if (backtestDetailStatus === "failed") {
        return jsonResponse({
          body: {
            id: "backtest-1",
            status: "failed",
            strategy_id: "strategy-1",
            parameter_set_id: "param-1",
            asset_id: "asset-1",
            initial_capital: "25",
            metrics: null,
            small_account_warning: null,
            trades: [],
            error_detail: "Engine error for test",
          },
        });
      }

      return jsonResponse({
        body: {
          id: "backtest-1",
          status: "completed",
          strategy_id: "strategy-1",
          parameter_set_id: "param-1",
          asset_id: "asset-1",
          initial_capital: "25",
          metrics: {
            total_return_usd: "4.12",
            total_return_pct: "0.165",
            win_rate: "0.57",
            max_drawdown: "0.092",
            sharpe_like: "1.21",
            trade_count: 42,
            average_trade_usd: "0.098",
            fee_drag_pct: "0.34",
          },
          small_account_warning: warningDetail
            ? {
                type: "high_fee_drag",
                detail: warningDetail,
              }
            : null,
          trades: [
            {
              side: "buy",
              quantity: "0.00038",
              price: "64200.00",
              executed_at: "2025-02-11T14:00:00Z",
              reason: "fast MA crossed above slow MA",
            },
          ],
        },
      });
    }

    return jsonResponse({
      status: 404,
      body: {
        error: {
          message: `Unhandled route: ${method} ${url.pathname}`,
        },
      },
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function waitForFormReady() {
  await screen.findByRole("heading", { name: "Backtest Configuration" });
  await screen.findByRole("button", { name: "Run Backtest" });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BacktestsPage", () => {
  it("shows form validation error when starting balance is below minimum", async () => {
    const fetchMock = installFetchMock("completed");
    render(<BacktestsPage />);
    const user = userEvent.setup();

    await waitForFormReady();

    const startingBalance = screen.getByLabelText("Backtest Starting Capital");
    await user.clear(startingBalance);
    await user.type(startingBalance, "10");
    await user.click(screen.getByRole("button", { name: "Run Backtest" }));

    expect(await screen.findByText("Backtest Starting Capital must be at least $25.")).toBeInTheDocument();

    const postedRunCalls = fetchMock.mock.calls.filter((call) => {
      const input = call[0];
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      return url.endsWith("/backtests/run");
    });
    expect(postedRunCalls).toHaveLength(0);
  });

  it("shows running state after a successful run request", async () => {
    installFetchMock("running");
    render(<BacktestsPage />);
    const user = userEvent.setup();

    await waitForFormReady();
    await user.click(screen.getByRole("button", { name: "Run Backtest" }));

    expect(await screen.findByText("Backtest in progress")).toBeInTheDocument();
  });

  it("shows failed state when the backtest run fails", async () => {
    installFetchMock("failed");
    render(<BacktestsPage />);
    const user = userEvent.setup();

    await waitForFormReady();
    await user.click(screen.getByRole("button", { name: "Run Backtest" }));

    expect(await screen.findByText("Backtest failed")).toBeInTheDocument();
    expect(await screen.findByText("Engine error for test")).toBeInTheDocument();
  });

  it("renders completed result metrics and trades", async () => {
    installFetchMock("completed");
    render(<BacktestsPage />);
    const user = userEvent.setup();

    await waitForFormReady();
    await user.click(screen.getByRole("button", { name: "Run Backtest" }));

    await waitFor(() => {
      expect(screen.getByText("Backtest Results")).toBeInTheDocument();
    });

    expect(screen.getByText(/\+\$4.12/)).toBeInTheDocument();
    expect(screen.getByText("Fee Drag")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("fast MA crossed above slow MA")).toBeInTheDocument();
  });

  it("renders the small account warning when present", async () => {
    installFetchMock("completed", "Fees consumed 34% of gross gains at this starting balance.");
    render(<BacktestsPage />);
    const user = userEvent.setup();

    await waitForFormReady();
    await user.click(screen.getByRole("button", { name: "Run Backtest" }));

    expect(await screen.findByText("Small Account Warning")).toBeInTheDocument();
    expect(await screen.findByText("Fees consumed 34% of gross gains at this starting balance.")).toBeInTheDocument();
  });
});
