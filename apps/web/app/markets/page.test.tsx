import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import MarketsPage from "./page";
import { ApiRequestError } from "@/lib/api/markets";

const { getMarketsAssetsMock, getMarketCandlesMock } = vi.hoisted(() => {
  return {
    getMarketsAssetsMock: vi.fn(),
    getMarketCandlesMock: vi.fn(),
  };
});

vi.mock("@/components/charts/CandleChart", () => ({
  default: () => React.createElement("div", { "data-testid": "candle-chart" }),
}));

vi.mock("@/lib/api/markets", () => {
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
    getMarketsAssets: getMarketsAssetsMock,
    getMarketCandles: getMarketCandlesMock,
  };
});

describe("markets remaining Prompt 1.10 criteria", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getMarketsAssetsMock.mockResolvedValue([]);
    getMarketCandlesMock.mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
  });

  it("shows empty-state UI for a valid asset with zero candles", async () => {
    getMarketsAssetsMock.mockResolvedValue([
      {
        id: "asset-btc",
        symbol: "BTCUSDT",
        asset_class: "crypto",
        exchange: "binance_us",
        is_active: true,
      },
    ]);
    getMarketCandlesMock.mockResolvedValue([]);

    render(React.createElement(MarketsPage));

    await screen.findByRole("heading", { name: "BTCUSDT", level: 2 });
    expect(await screen.findByText("No candle data available for this range yet")).toBeInTheDocument();
    expect(screen.queryByText(/Failed to load candles:/)).not.toBeInTheDocument();
  });

  it("shows page-level asset error when assets API fails", async () => {
    getMarketsAssetsMock.mockRejectedValue(new ApiRequestError("asset endpoint boom", 500));

    render(React.createElement(MarketsPage));

    expect(await screen.findByText("Could not load assets. asset endpoint boom")).toBeInTheDocument();
  });

  it("shows chart-area error for candles failure while asset list remains usable", async () => {
    getMarketsAssetsMock.mockResolvedValue([
      {
        id: "asset-btc",
        symbol: "BTCUSDT",
        asset_class: "crypto",
        exchange: "binance_us",
        is_active: true,
      },
      {
        id: "asset-eth",
        symbol: "ETHUSDT",
        asset_class: "crypto",
        exchange: "binance_us",
        is_active: true,
      },
    ]);

    getMarketCandlesMock.mockRejectedValue(new ApiRequestError("candles endpoint boom", 500));

    render(React.createElement(MarketsPage));

    expect(await screen.findByText("Failed to load candles: candles endpoint boom")).toBeInTheDocument();
    expect(screen.queryByText(/Could not load assets\./)).not.toBeInTheDocument();

    const ethButton = await screen.findByRole("button", { name: /ETHUSDT/ });
    await userEvent.click(ethButton);

    await waitFor(() => {
      expect(getMarketCandlesMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    expect(
      getMarketCandlesMock.mock.calls.some((call) => call[0]?.assetId === "asset-eth")
    ).toBe(true);
    expect(screen.getByText("Failed to load candles: candles endpoint boom")).toBeInTheDocument();
    expect(screen.queryByText(/Could not load assets\./)).not.toBeInTheDocument();
  });
});
