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
  function installDetailFetchMock(mode: "ok" | "error" | "malformed" = "ok") {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const url = new URL(rawUrl);

      if (mode === "error") {
        return jsonResponse(404, { error: { message: "Capital campaign not found" } });
      }

      if (mode === "malformed") {
        return jsonResponse(200, null);
      }

      if (url.pathname.endsWith("/profit-policy")) {
        return jsonResponse(200, {
          policy_id: 1,
          policy_uuid: "11111111-1111-1111-1111-111111111111",
          capital_campaign_id: 1,
          policy_type: "FULL_COMPOUND",
          profit_target_amount: "5.00",
          profit_target_percent: null,
          compound_percent: "100",
          withdraw_percent: "0",
          protected_principal_amount: null,
          minimum_realized_profit: "0",
          maximum_campaign_capital: null,
          minimum_cash_reserve: "0",
          fee_reserve_percent: "0",
          tax_reserve_percent: "0",
          cooldown_hours: 0,
          require_operator_approval: true,
          is_active: true,
          created_at: "2026-07-10T16:00:00Z",
          updated_at: "2026-07-10T16:00:00Z",
        });
      }

      if (url.pathname.endsWith("/profit-cycles")) {
        return jsonResponse(200, {
          items: [
            {
              cycle_id: 1,
              cycle_uuid: "22222222-2222-2222-2222-222222222222",
              capital_campaign_id: 1,
              profit_policy_id: 1,
              cycle_number: 1,
              opening_capital: "25.00",
              opening_equity: "27.43",
              realized_profit: "5.00",
              unrealized_profit: "0.63",
              fees: "0.10",
              eligible_profit: "4.90",
              compound_amount: "4.90",
              withdrawal_amount: "0",
              reserve_amount: "0",
              closing_campaign_capital: "29.90",
              target_reached: true,
              status: "REVIEW_REQUIRED",
              settlement_state: "SETTLEMENT_UNKNOWN",
              calculation_snapshot: { target_progress_percent: "100" },
              calculated_at: "2026-07-10T16:00:00Z",
              approved_at: null,
              completed_at: null,
              created_at: "2026-07-10T16:00:00Z",
              updated_at: "2026-07-10T16:00:00Z",
            },
          ],
        });
      }

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
  }

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
    installDetailFetchMock("ok");

    render(<CapitalCampaignDetailCenter campaignUuid="11111111-1111-1111-1111-111111111111" />);

    expect(await screen.findByText("BTC Campaign #1")).toBeInTheDocument();
    expect(screen.getByText("paper_validation")).toBeInTheDocument();
    expect(screen.getByText("Back to Campaigns")).toBeInTheDocument();
    expect(screen.getByText("Created At")).toBeInTheDocument();
    expect(screen.getByText("Updated At")).toBeInTheDocument();
    expect(screen.getByText("Profit Policy")).toBeInTheDocument();
    expect(screen.getByText("Compounding Preview")).toBeInTheDocument();
    expect(screen.getAllByText("This is an accounting recommendation only. No funds will move.").length).toBeGreaterThan(0);
    expect(screen.getByText("Profit Cycles")).toBeInTheDocument();
    expect(screen.queryByText("Execute Withdrawal")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Campaigns" })).toHaveAttribute("href", "/capital-campaigns");
  });

  it("renders error state", async () => {
    installDetailFetchMock("error");

    render(<CapitalCampaignDetailCenter campaignUuid="11111111-1111-1111-1111-111111111111" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Capital campaign not found");
  });

  it("renders not found fallback for malformed payload", async () => {
    installDetailFetchMock("malformed");

    render(<CapitalCampaignDetailCenter campaignUuid="11111111-1111-1111-1111-111111111111" />);

    expect(await screen.findByText("Campaign not found.")).toBeInTheDocument();
  });
});
