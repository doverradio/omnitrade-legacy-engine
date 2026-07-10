import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import CapitalCampaignsPage from "@/app/capital-campaigns/page";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CapitalCampaignsPage", () => {
  it("renders loading state before data resolves", async () => {
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          setTimeout(() => resolve(jsonResponse(200, { items: [] })), 1);
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<CapitalCampaignsPage />);
    expect(screen.getByText("Loading campaigns...")).toBeInTheDocument();
    expect(await screen.findByText("No capital campaigns yet")).toBeInTheDocument();
  });

  it("renders error state when API fails", async () => {
    const fetchMock = vi.fn(async () => {
      return jsonResponse(500, { error: { message: "Service unavailable" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CapitalCampaignsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Service unavailable");
  });

  it("renders empty state when no campaigns exist", async () => {
    const fetchMock = vi.fn(async () => {
      return jsonResponse(200, { items: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CapitalCampaignsPage />);

    expect(await screen.findByText("No capital campaigns yet")).toBeInTheDocument();
    expect(screen.getByText("Total Campaigns")).toBeInTheDocument();
  });

  it("renders campaign cards", async () => {
    const fetchMock = vi.fn(async () => {
      return jsonResponse(200, {
        items: [
          {
            id: 1,
            uuid: "11111111-1111-1111-1111-111111111111",
            owner: "owner-1",
            name: "BTC Campaign #1",
            description: "First campaign",
            status: "RUNNING",
            campaign_type: "paper_validation",
            exchange: "coinbase_advanced",
            paper_account_id: null,
            validation_run_id: null,
            strategy_id: null,
            starting_capital: "25.00",
            current_equity: "27.43",
            realized_profit: "1.80",
            unrealized_profit: "0.63",
            fees: "0.10",
            roi: "9.72",
            created_at: "2026-07-10T16:00:00Z",
            updated_at: "2026-07-10T16:00:00Z",
          },
        ],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CapitalCampaignsPage />);

    expect(await screen.findByText("BTC Campaign #1")).toBeInTheDocument();
    expect(screen.getByText("View Campaign")).toBeInTheDocument();
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByText("Realized Profit")).toBeInTheDocument();
    expect(screen.getByText("Unrealized Profit")).toBeInTheDocument();
    expect(screen.getByText("Created")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /BTC Campaign #1/i })).toHaveAttribute(
      "href",
      "/capital-campaigns/11111111-1111-1111-1111-111111111111",
    );
  });
});
