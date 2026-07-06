import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PaperTradingPage from "./page";

const { createPaperAccountMock, getPaperAccountMock, resetPaperAccountMock } = vi.hoisted(() => {
  return {
    createPaperAccountMock: vi.fn(),
    getPaperAccountMock: vi.fn(),
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
  });

  afterEach(() => {
    cleanup();
  });

  it("loads, switches, creates, and resets paper accounts using the documented contracts", async () => {
    const user = userEvent.setup();

    render(React.createElement(PaperTradingPage));

    expect(await screen.findByRole("heading", { name: "Portfolio Intelligence + Paper Execution Foundation" })).toBeInTheDocument();
    await screen.findByText("Selected account ID: paper-account-1");
    expect(screen.getAllByText("Paper Balance: $25.00").length).toBeGreaterThan(0);
    expect(screen.getByText("+$0.00 (+0.00%)")).toBeInTheDocument();
    expect(screen.getAllByText("PAPER").length).toBeGreaterThan(0);

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
});
