import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import CapitalCampaignDetailCenter from "@/components/domain/CapitalCampaignDetailCenter";

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

describe("CapitalCampaignDetailCenter", () => {
  it("renders loading state", () => {
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          setTimeout(() => resolve(jsonResponse(200, { items: [] })), 1);
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<CapitalCampaignDetailCenter campaignUuid="11111111-1111-1111-1111-111111111111" />);

    expect(screen.getByText("Loading campaign...")).toBeInTheDocument();
  });

  it("renders campaign detail", async () => {
    const fetchMock = vi.fn(async () => {
      return jsonResponse(200, {
        id: 1,
        uuid: "11111111-1111-1111-1111-111111111111",
        owner: "owner-1",
        name: "BTC Campaign #1",
        description: "First campaign",
        status: "RUNNING",
        campaign_type: "paper_validation",
        exchange: "coinbase_advanced",
        paper_account_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        validation_run_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        strategy_id: null,
        starting_capital: "25.00",
        current_equity: "27.43",
        realized_profit: "1.80",
        unrealized_profit: "0.63",
        fees: "0.10",
        roi: "9.72",
        created_at: "2026-07-10T16:00:00Z",
        updated_at: "2026-07-10T16:00:00Z",
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CapitalCampaignDetailCenter campaignUuid="11111111-1111-1111-1111-111111111111" />);

    expect(await screen.findByText("BTC Campaign #1")).toBeInTheDocument();
    expect(screen.getByText("paper_validation")).toBeInTheDocument();
    expect(screen.getByText("Back to Campaigns")).toBeInTheDocument();
    expect(screen.getByText("Created At")).toBeInTheDocument();
    expect(screen.getByText("Updated At")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Campaigns" })).toHaveAttribute("href", "/capital-campaigns");
  });

  it("renders error state", async () => {
    const fetchMock = vi.fn(async () => {
      return jsonResponse(404, { error: { message: "Capital campaign not found" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CapitalCampaignDetailCenter campaignUuid="11111111-1111-1111-1111-111111111111" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Capital campaign not found");
  });

  it("renders not found fallback for malformed payload", async () => {
    const fetchMock = vi.fn(async () => {
      return jsonResponse(200, null);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CapitalCampaignDetailCenter campaignUuid="11111111-1111-1111-1111-111111111111" />);

    expect(await screen.findByText("Campaign not found.")).toBeInTheDocument();
  });
});
