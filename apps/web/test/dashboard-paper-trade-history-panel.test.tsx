import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import PaperTradeHistoryPanel from "@/components/domain/PaperTradeHistoryPanel";

type Scenario = "empty" | "populated";

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

    if (url.pathname !== "/paper/trade-history") {
      return jsonResponse(404, {
        error: {
          message: `Unhandled route in test: GET ${url.pathname}`,
        },
      });
    }

    if (scenario === "empty") {
      return jsonResponse(200, {
        items: [],
        limit: 10,
        offset: 0,
        total: 0,
        has_more: false,
      });
    }

    const offset = Number(url.searchParams.get("offset") ?? "0");
    if (offset === 10) {
      return jsonResponse(200, {
        items: [
          {
            trade_id: "33333333-3333-3333-3333-333333333333",
            executed_at: "2026-07-09T10:00:00Z",
            asset: "BTCUSD",
            side: "buy",
            quantity: "1",
            execution_price: "100",
            notional: "100",
            signal_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            strategy_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            decision_record_id: null,
            realized_pnl: null,
            paper_account_id: "11111111-1111-1111-1111-111111111111",
          },
        ],
        limit: 10,
        offset: 10,
        total: 11,
        has_more: false,
      });
    }

    return jsonResponse(200, {
      items: [
        {
          trade_id: "22222222-2222-2222-2222-222222222222",
          executed_at: "2026-07-09T10:05:00Z",
          asset: "BTCUSD",
          side: "sell",
          quantity: "1",
          execution_price: "110",
          notional: "110",
          signal_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          strategy_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
          decision_record_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
          realized_pnl: "9",
          paper_account_id: "11111111-1111-1111-1111-111111111111",
        },
      ],
      limit: 10,
      offset: 0,
      total: 11,
      has_more: true,
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PaperTradeHistoryPanel", () => {
  it("renders empty state", async () => {
    installFetchMock("empty");
    render(<PaperTradeHistoryPanel />);

    expect(await screen.findByRole("heading", { name: "Paper Trade History" })).toBeInTheDocument();
    expect(await screen.findByText("No paper trade evidence yet.")).toBeInTheDocument();
  });

  it("renders populated state", async () => {
    installFetchMock("populated");
    render(<PaperTradeHistoryPanel />);

    expect(await screen.findByRole("heading", { name: "Paper Trade History" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("BTCUSD")).toBeInTheDocument();
    });

    expect(screen.getByText("SELL")).toBeInTheDocument();
    expect(screen.getAllByText("110").length).toBeGreaterThan(0);
    expect(screen.getByText("9")).toBeInTheDocument();
    expect(screen.getAllByText("PAPER").length).toBeGreaterThan(0);
  });

  it("supports pagination controls", async () => {
    const fetchMock = installFetchMock("populated");
    const user = userEvent.setup();
    render(<PaperTradeHistoryPanel />);

    expect(await screen.findByRole("heading", { name: "Paper Trade History" })).toBeInTheDocument();

    const nextButton = await screen.findByRole("button", { name: "Next" });
    await user.click(nextButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    const secondCallUrl = String(fetchMock.mock.calls[1][0]);
    expect(secondCallUrl).toContain("offset=10");

    const previousButton = await screen.findByRole("button", { name: "Previous" });
    await user.click(previousButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    const thirdCallUrl = String(fetchMock.mock.calls[2][0]);
    expect(thirdCallUrl).toContain("offset=0");
  });
});
