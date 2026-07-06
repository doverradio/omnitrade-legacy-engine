import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PaperTradingPage from "./page";

const { createPaperAccountMock, getPaperAccountMock, getPaperTradesMock, resetPaperAccountMock } = vi.hoisted(() => {
  return {
    createPaperAccountMock: vi.fn(),
    getPaperAccountMock: vi.fn(),
    getPaperTradesMock: vi.fn(),
    resetPaperAccountMock: vi.fn(),
  };
});

vi.mock("@/lib/api/paperAccounts", () => {
  class ApiRequestError extends Error {
    status: number;

    constructor(message: string, status: number) {
      super(message);
      this.name = "ApiRequestError";
      this.status = status;
    }
  }

  return {
    ApiRequestError,
    createPaperAccount: createPaperAccountMock,
    getPaperAccount: getPaperAccountMock,
    getPaperTrades: getPaperTradesMock,
    resetPaperAccount: resetPaperAccountMock,
  };
});

describe("paper trading page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPaperAccountMock.mockResolvedValue({
      id: "paper-account-1",
      name: "Family Paper Account",
      asset_class: "crypto",
      starting_balance: "25.00",
      current_cash_balance: "25.00",
      equity: "25.00",
      equity_return_usd: "0.00",
      equity_return_pct: "0.00",
      positions: [],
      is_active: true,
    });
    createPaperAccountMock.mockResolvedValue({
      id: "paper-account-2",
      name: "New Paper Account",
      asset_class: "stock",
      starting_balance: "50.00",
      current_cash_balance: "50.00",
      equity: "50.00",
      equity_return_usd: "0.00",
      equity_return_pct: "0.00",
      positions: [],
      is_active: true,
    });
    resetPaperAccountMock.mockResolvedValue({
      account_id: "paper-account-2",
      current_cash_balance: "25.00",
      positions: [],
    });
    getPaperTradesMock.mockResolvedValue({
      items: [
        {
          id: "trade-1",
          asset_id: "asset-1",
          symbol: "BTCUSDT",
          side: "buy",
          quantity: "0.1",
          price: "250",
          fee: "0.25",
          executed_at: "2026-07-06T12:00:00Z",
          signal_id: "signal-1",
        },
      ],
      next_cursor: null,
    });
  });

  afterEach(() => {
    cleanup();
  });

  function setPaperAccountFixture(overrides?: Partial<{
    id: string;
    name: string;
    asset_class: string;
    starting_balance: string;
    current_cash_balance: string;
    equity: string;
    equity_return_usd: string;
    equity_return_pct: string;
    positions: Array<{
      asset_id: string;
      symbol: string;
      quantity: string;
      avg_entry_price: string;
      unrealized_pnl_usd: string;
      unrealized_pnl_pct: string;
    }>;
    is_active: boolean;
  }>) {
    getPaperAccountMock.mockResolvedValue({
      id: "paper-account-1",
      name: "Family Paper Account",
      asset_class: "crypto",
      starting_balance: "25.00",
      current_cash_balance: "25.00",
      equity: "25.00",
      equity_return_usd: "0.00",
      equity_return_pct: "0.00",
      positions: [],
      is_active: true,
      ...overrides,
    });
  }

  function setTradeFixture(items: Array<{
    id: string;
    asset_id: string;
    symbol?: string;
    side: string;
    quantity: string;
    price: string;
    fee: string;
    executed_at: string;
    signal_id?: string;
  }>) {
    getPaperTradesMock.mockResolvedValue({
      items,
      next_cursor: null,
    });
  }

  it("loads, switches, creates, and resets paper accounts using the documented contracts", async () => {
    const user = userEvent.setup();

    render(React.createElement(PaperTradingPage));

    expect(await screen.findByRole("heading", { name: "Portfolio Intelligence + Paper Execution Foundation" })).toBeInTheDocument();
    await screen.findByText("Selected account ID: paper-account-1");
    await waitFor(() => {
      expect(getPaperTradesMock).toHaveBeenCalledWith({
        account_id: "paper-account-1",
        strategy_id: undefined,
        asset_id: undefined,
        start_time: undefined,
        end_time: undefined,
        limit: 100,
      });
    });
    expect(screen.getAllByText("Paper Balance: $25.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+$0.00 (+0.00%)").length).toBeGreaterThan(0);
    expect(screen.getAllByText("PAPER").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Trade history (PAPER)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Portfolio timeline (PAPER)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Performance Analytics (PAPER)" })).toBeInTheDocument();
    expect(screen.getByText("Consistency score")).toBeInTheDocument();
    expect(screen.getByText("Show advanced analytics details")).toBeInTheDocument();
    expect(screen.getByText(/Small-account warning: Fees consumed 100.00% of gross paper gains/i)).toBeInTheDocument();
    expect(screen.getByText("$0.25 (1.00%)")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Paper account ID"));
    await user.type(screen.getByLabelText("Paper account ID"), "paper-account-2");
    await user.click(screen.getByRole("button", { name: "Load paper account" }));

    await waitFor(() => {
      expect(getPaperAccountMock).toHaveBeenCalledWith("paper-account-2");
    });

    await user.type(screen.getByLabelText("Account name"), " Team ");
    await user.selectOptions(screen.getByLabelText("Asset class"), "stock");
    await user.clear(screen.getByLabelText("Paper Account Starting Balance"));
    await user.type(screen.getByLabelText("Paper Account Starting Balance"), "50");
    await user.click(screen.getByRole("button", { name: "Create paper account" }));

    await waitFor(() => {
      expect(createPaperAccountMock).toHaveBeenCalledWith({
        name: "Family Paper Account Team",
        asset_class: "stock",
        starting_balance: "50",
      });
    });
    expect(screen.getByText("Selected account ID: paper-account-2")).toBeInTheDocument();
    expect(screen.getAllByText("Paper Balance: $50.00").length).toBeGreaterThan(0);

    await user.type(screen.getByLabelText("Strategy ID filter (optional)"), "strat-1");
    await user.click(screen.getByRole("button", { name: "Apply trade filters" }));

    await waitFor(() => {
      expect(getPaperTradesMock).toHaveBeenLastCalledWith({
        account_id: "paper-account-2",
        strategy_id: "strat-1",
        asset_id: undefined,
        start_time: undefined,
        end_time: undefined,
        limit: 100,
      });
    });

    await user.click(screen.getByRole("button", { name: "Reset paper account" }));
    expect(screen.getByRole("heading", { name: "Reset the active paper account?" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Confirm reset" }));

    await waitFor(() => {
      expect(resetPaperAccountMock).toHaveBeenCalledWith({
        account_id: "paper-account-2",
        confirm: true,
      });
    });

    expect(screen.getByText("Open Position Value")).toBeInTheDocument();
  });

  it("renders zero-trade empty states while preserving small-account and paper labeling", async () => {
    setPaperAccountFixture();
    setTradeFixture([]);

    render(React.createElement(PaperTradingPage));

    await screen.findByText("Selected account ID: paper-account-1");
    expect(screen.getByText("$25 minimum")).toBeInTheDocument();
    expect(screen.getByDisplayValue("25")).toBeInTheDocument();
    expect(screen.getByText(/No trades yet for this PAPER account and filter range/i)).toBeInTheDocument();
    expect(screen.getByText(/No PAPER trades yet\. Return uses current account equity/i)).toBeInTheDocument();
    expect(screen.getAllByText("+$0.00 (+0.00%)").length).toBeGreaterThan(0);
    expect(screen.getAllByText("PAPER").length).toBeGreaterThan(0);
  });

  it("handles mixed buy and sell sequences with fractional quantities and dollar+percentage reporting", async () => {
    setPaperAccountFixture({
      asset_class: "stock",
      equity: "27.00",
      equity_return_usd: "2.00",
      equity_return_pct: "0.08",
      positions: [
        {
          asset_id: "asset-aapl",
          symbol: "AAPL",
          quantity: "0.5",
          avg_entry_price: "200.00",
          unrealized_pnl_usd: "1.25",
          unrealized_pnl_pct: "0.0125",
        },
      ],
    });
    setTradeFixture([
      {
        id: "trade-buy",
        asset_id: "asset-aapl",
        symbol: "AAPL",
        side: "buy",
        quantity: "0.0500",
        price: "200.00",
        fee: "0.10",
        executed_at: "2026-07-06T10:00:00Z",
      },
      {
        id: "trade-sell",
        asset_id: "asset-aapl",
        symbol: "AAPL",
        side: "sell",
        quantity: "0.0500",
        price: "300.00",
        fee: "0.15",
        executed_at: "2026-07-06T11:00:00Z",
      },
      {
        id: "trade-crypto",
        asset_id: "asset-btc",
        symbol: "BTCUSDT",
        side: "buy",
        quantity: "0.00038",
        price: "65000.00",
        fee: "0.05",
        executed_at: "2026-07-06T12:00:00Z",
      },
    ]);

    render(React.createElement(PaperTradingPage));

    await screen.findByText("Selected account ID: paper-account-1");
    await screen.findByText("0.00038");
    expect(screen.getAllByText("0.0500").length).toBeGreaterThan(0);
    expect(screen.getByText("+$14.85 (+59.40%)")).toBeInTheDocument();
    expect(screen.getAllByText("$-10.10 (-40.40%)").length).toBeGreaterThan(0);
    expect(screen.getByText("$0.30 (13.04%)")).toBeInTheDocument();
    expect(screen.getByText("Paper return")).toBeInTheDocument();
    expect(screen.getAllByText("+$2.00 (+8.00%)").length).toBeGreaterThan(0);
  });

  it("shows explicit trade-history error states and partial analytics fallback", async () => {
    setPaperAccountFixture({
      equity: "26.00",
      equity_return_usd: "1.00",
      equity_return_pct: "0.04",
    });
    getPaperTradesMock.mockRejectedValue(new Error("trade feed unavailable"));

    render(React.createElement(PaperTradingPage));

    await screen.findByText("Selected account ID: paper-account-1");
    await screen.findByText(/Could not load paper trade history\. Failed to load paper trades\./i);
    expect(screen.getByText("Unable to render portfolio timeline because trade history could not be loaded.")).toBeInTheDocument();
    expect(screen.getByText("Trade-derived analytics are partially unavailable because trade history failed to load.")).toBeInTheDocument();
    expect(screen.getByText("Retry trade history load")).toBeInTheDocument();
    expect(screen.getAllByText("+$1.00 (+4.00%)").length).toBeGreaterThan(0);
  });
});
