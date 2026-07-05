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
    expect(await screen.findByText("No candle data available.")).toBeInTheDocument();
    expect(screen.queryByText("We could not load candle data right now.")).not.toBeInTheDocument();
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

    expect(await screen.findByText("We could not load candle data right now.")).toBeInTheDocument();
    expect(screen.getByText("Please try again. candles endpoint boom")).toBeInTheDocument();
    expect(screen.queryByText(/Could not load assets\./)).not.toBeInTheDocument();

    const ethButton = await screen.findByRole("button", { name: /ETHUSDT/ });
    await userEvent.click(ethButton);

    await waitFor(() => {
      expect(getMarketCandlesMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    expect(
      getMarketCandlesMock.mock.calls.some((call) => call[0]?.assetId === "asset-eth")
    ).toBe(true);
    expect(screen.getByText("We could not load candle data right now.")).toBeInTheDocument();
    expect(screen.queryByText(/Could not load assets\./)).not.toBeInTheDocument();
  });

  it("reuses in-memory candles when returning to a previous asset/interval", async () => {
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

    getMarketCandlesMock.mockImplementation(async (params: { assetId: string; interval: string }) => {
      if (params.assetId === "asset-btc" && params.interval === "1m") {
        return [
          {
            open_time: "2026-07-05T00:00:00Z",
            open: "1",
            high: "2",
            low: "0.5",
            close: "1.5",
            volume: "10",
          },
        ];
      }

      if (params.assetId === "asset-eth" && params.interval === "1m") {
        return [
          {
            open_time: "2026-07-05T00:00:00Z",
            open: "2",
            high: "3",
            low: "1.5",
            close: "2.5",
            volume: "20",
          },
        ];
      }

      return [];
    });

    render(React.createElement(MarketsPage));

    await screen.findByRole("heading", { name: "BTCUSDT", level: 2 });
    await waitFor(() => {
      expect(getMarketCandlesMock).toHaveBeenCalledWith(expect.objectContaining({ assetId: "asset-btc", interval: "1m" }));
    });

    await userEvent.click(await screen.findByRole("button", { name: /ETHUSDT/ }));
    await waitFor(() => {
      expect(getMarketCandlesMock).toHaveBeenCalledWith(expect.objectContaining({ assetId: "asset-eth", interval: "1m" }));
    });

    const callsBeforeSwitchBack = getMarketCandlesMock.mock.calls.length;
    await userEvent.click(await screen.findByRole("button", { name: /BTCUSDT/ }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "BTCUSDT", level: 2 })).toBeInTheDocument();
    });
    expect(getMarketCandlesMock.mock.calls.length).toBe(callsBeforeSwitchBack);
  });
});
