import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import CapitalPage from "@/app/capital/page";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function buildPayload(totalManaged: string, empty = false) {
  return {
    summary: {
      total_managed_capital: totalManaged,
      total_starting_capital: totalManaged,
      total_current_equity: totalManaged,
      total_allocated_capital: totalManaged,
      total_available_capital: "0",
      total_reserved_capital: totalManaged,
      total_realized_pnl: "0",
      total_unrealized_pnl: "0",
      active_capital_pools: empty ? 0 : 2,
      inactive_capital_pools: empty ? 0 : 1,
      active_positions: empty ? 0 : 1,
      total_trades: empty ? 0 : 5,
      utilization_percent: empty ? 0 : 100,
      data_completeness_percent: empty ? 100 : 83.33,
      unavailable_sources: empty ? [] : ["research_campaign_allocations"],
      generated_at: "2026-07-09T10:00:00Z",
    },
    capital_pools: empty
      ? []
      : [
          {
            capital_pool_id: "validation-run:run-a",
            capital_pool_type: "validation_run",
            name: "Run A",
            status: "active",
            starting_capital: "25",
            current_equity: "26",
            allocated_capital: "25",
            available_capital: "1",
            reserved_capital: "25",
            realized_pnl: "0",
            unrealized_pnl: "1",
            pnl_percent: 4,
            started_at: "2026-07-09T00:00:00Z",
            completed_at: null,
            related_entity_type: "validation_run",
            related_entity_id: "run-a",
            related_page_url: "/validation-runs",
            capital_campaign_uuid: "99999999-9999-9999-9999-999999999999",
            capital_campaign_name: "Campaign Link",
            capital_campaign_status: "RUNNING",
            parent_capital_pool_id: null,
            child_allocations_count: 1,
            notes: "Top-level funded validation pool.",
          },
          {
            capital_pool_id: "validation-run:run-b",
            capital_pool_type: "validation_run",
            name: "Run B",
            status: "active",
            starting_capital: "25",
            current_equity: "24",
            allocated_capital: "25",
            available_capital: "-1",
            reserved_capital: "25",
            realized_pnl: "0",
            unrealized_pnl: "-1",
            pnl_percent: -4,
            started_at: "2026-07-09T00:00:00Z",
            completed_at: null,
            related_entity_type: "validation_run",
            related_entity_id: "run-b",
            related_page_url: "/validation-runs",
            capital_campaign_uuid: null,
            capital_campaign_name: null,
            capital_campaign_status: null,
            parent_capital_pool_id: null,
            child_allocations_count: 1,
            notes: "Top-level funded validation pool.",
          },
          {
            capital_pool_id: "validation-run:run-c",
            capital_pool_type: "validation_run",
            name: "Run C",
            status: "completed",
            starting_capital: "25",
            current_equity: "23",
            allocated_capital: "0",
            available_capital: "23",
            reserved_capital: "0",
            realized_pnl: "-2",
            unrealized_pnl: "0",
            pnl_percent: -8,
            started_at: "2026-07-08T00:00:00Z",
            completed_at: "2026-07-09T00:00:00Z",
            related_entity_type: "validation_run",
            related_entity_id: "run-c",
            related_page_url: "/validation-runs",
            capital_campaign_uuid: null,
            capital_campaign_name: null,
            capital_campaign_status: null,
            parent_capital_pool_id: null,
            child_allocations_count: 0,
            notes: null,
          },
          {
            capital_pool_id: "strategy-allocation:run-a:rsi",
            capital_pool_type: "strategy_allocation",
            name: "RSI Allocation",
            status: "active",
            starting_capital: null,
            current_equity: null,
            allocated_capital: null,
            available_capital: null,
            reserved_capital: null,
            realized_pnl: null,
            unrealized_pnl: null,
            pnl_percent: null,
            started_at: "2026-07-09T00:00:00Z",
            completed_at: null,
            related_entity_type: "validation_run",
            related_entity_id: "run-a",
            related_page_url: "/validation-runs",
            capital_campaign_uuid: "99999999-9999-9999-9999-999999999999",
            capital_campaign_name: "Campaign Link",
            capital_campaign_status: "RUNNING",
            parent_capital_pool_id: "validation-run:run-a",
            child_allocations_count: 0,
            notes: "Strategy child allocation.",
          },
          {
            capital_pool_id: "campaign-profit-cycle:cycle-a:compound",
            capital_pool_type: "compounding_recommendation",
            name: "Campaign A Compounding Recommendation",
            status: "inactive",
            starting_capital: null,
            current_equity: null,
            allocated_capital: null,
            available_capital: null,
            reserved_capital: null,
            realized_pnl: null,
            unrealized_pnl: null,
            pnl_percent: null,
            started_at: "2026-07-09T00:00:00Z",
            completed_at: null,
            related_entity_type: "capital_campaign",
            related_entity_id: "campaign-a",
            related_page_url: "/capital-campaigns/11111111-1111-1111-1111-111111111111",
            capital_campaign_uuid: "11111111-1111-1111-1111-111111111111",
            capital_campaign_name: "Campaign Link",
            capital_campaign_status: "RUNNING",
            parent_capital_pool_id: null,
            child_allocations_count: 0,
            notes: "Recommendation evidence only. No funds moved.",
          },
        ],
    page: 1,
    page_size: 200,
    total: empty ? 0 : 5,
    has_more: false,
  };
}

function installFetchMock(scenario: "empty" | "with-data" = "with-data") {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/capital/ledger") {
      if (scenario === "empty") {
        return jsonResponse(200, buildPayload("0", true));
      }
      return jsonResponse(200, buildPayload("50", false));
    }

    if (url.pathname === "/crypto-order-previews") {
      return jsonResponse(200, {
        items: [
          {
            crypto_order_preview_id: "preview-1",
            preview_version: 1,
            status: "PREVIEW_READY",
            provider: "coinbase_advanced",
            environment: "production",
            product_id: "BTC-USD",
            side: "BUY",
            order_type: "MARKET",
            quote_size: "5.00",
            base_size: null,
            requested_amount: "5.00",
            requested_amount_currency: "USD",
            readiness_verdict: "READY_FOR_PREVIEW",
            risk_verdict: "approved_for_preview",
            risk_explanation: "Risk engine approved the proposed preview.",
            strategy_id: null,
            strategy_name: null,
            decision_record_id: null,
            validation_run_id: null,
            preview_id: "preview-123",
            estimated_average_price: "10000.00",
            estimated_total_value: "5.10",
            estimated_base_size: "0.0005",
            estimated_quote_size: "5.00",
            estimated_fee: "0.10",
            estimated_fee_currency: "USD",
            estimated_slippage: "0.01",
            estimated_commission_total: "0.10",
            best_bid: "9995.00",
            best_ask: "10005.00",
            available_balance_before: "100.00",
            estimated_balance_after: "94.90",
            failure_reason: null,
            warning_messages: [],
            exchange_response_summary: {},
            expires_at: "2026-07-09T10:05:00Z",
            generated_by: "operator",
            audit_correlation_id: null,
            order_submitted: false,
            execution_available: false,
            created_at: "2026-07-09T10:00:00Z",
            updated_at: "2026-07-09T10:00:00Z",
            refreshed_from_preview_id: null,
          },
        ],
      });
    }

    return jsonResponse(404, {
      error: {
        message: `Unhandled route in test: GET ${url.pathname}`,
      },
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CapitalPage", () => {
  it("renders Capital Ledger and summary metrics", async () => {
    installFetchMock("with-data");

    render(<CapitalPage />);

    expect(await screen.findByRole("heading", { name: "Capital Ledger" })).toBeInTheDocument();
    expect(screen.getByText("Total Managed Capital")).toBeInTheDocument();
    expect(screen.getAllByText("$50.00").length).toBeGreaterThan(0);
    expect(screen.getByText("Data Completeness: 83.33%")).toBeInTheDocument();
    expect(screen.getByText("Preview Activity")).toBeInTheDocument();
  });

  it("renders active pools and validation run capital rows", async () => {
    installFetchMock("with-data");

    render(<CapitalPage />);

    expect(await screen.findByRole("heading", { name: "Capital Ledger" })).toBeInTheDocument();
    expect(await screen.findByText("Run A")).toBeInTheDocument();
    expect(await screen.findByText("Run B")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Capital Pools/i }));
    expect((await screen.findAllByText("Campaign Link")).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /Validation Run Capital/i }));
    expect((await screen.findAllByText("View Validation Run")).length).toBeGreaterThan(0);
  });

  it("supports metric explanation and filter action", async () => {
    const fetchMock = installFetchMock("with-data");

    render(<CapitalPage />);

    expect(await screen.findByRole("heading", { name: "Capital Ledger" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Active Pools/i }));

    expect(await screen.findByText("Active Pools filters the ledger to pools with active status.")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("status=active"))).toBe(true);
    });
  });

  it("renders archive accordion content", async () => {
    installFetchMock("with-data");

    render(<CapitalPage />);

    expect(await screen.findByRole("heading", { name: "Capital Ledger" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Inactive \/ Archive/i }));

    expect(await screen.findByText("Run C")).toBeInTheDocument();
  });

  it("renders recommendation-only labels without changing managed totals", async () => {
    installFetchMock("with-data");

    render(<CapitalPage />);

    expect(await screen.findByRole("heading", { name: "Capital Ledger" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Capital Pools/i }));
    expect(await screen.findByText("compounding recommendation")).toBeInTheDocument();
    expect(screen.getAllByText("$50.00").length).toBeGreaterThan(0);
  });

  it("renders empty state when no pools exist", async () => {
    installFetchMock("empty");

    render(<CapitalPage />);

    expect(await screen.findByRole("heading", { name: "Capital Ledger" })).toBeInTheDocument();
    expect(screen.getByText("No managed capital found.")).toBeInTheDocument();
  });

  it("keeps multiple mobile accordions open and avoids horizontal overflow on container", async () => {
    installFetchMock("with-data");
    Object.defineProperty(window, "innerWidth", { value: 375, configurable: true, writable: true });

    render(<CapitalPage />);

    const heading = await screen.findByRole("heading", { name: "Capital Ledger" });
    expect(heading).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Capital Pools/i }));
    fireEvent.click(screen.getByRole("button", { name: /Accounting Details/i }));

    expect(screen.getByText("Managed Capital counts only top-level funded pools.")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search by name, ID, or related entity")).toBeInTheDocument();

    const container = heading.closest("div");
    expect(container).toHaveClass("overflow-x-hidden");
  });
});
