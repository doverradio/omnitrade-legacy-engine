import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import CryptoOrderPreviewCenter from "@/components/domain/CryptoOrderPreviewCenter";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function previewItem(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/exchange-connections") {
      return jsonResponse(200, {
        items: [
          {
            exchange_connection_id: "11111111-1111-1111-1111-111111111111",
            provider: "coinbase_advanced",
            provider_label: "Coinbase Advanced",
            connection_name: "Primary Coinbase",
            environment: "production",
            status: "connected",
            credentials_valid: true,
            credential_mask: { api_key_name: "******1234", private_key: "********", passphrase: "********" },
            api_permissions: ["view", "trade"],
            account_status: "active",
            balances: [
              { currency: "USD", available: "100.00", reserved: "0.00", total: "100.00" },
              { currency: "BTC", available: "0.50", reserved: "0.00", total: "0.50" },
            ],
            total_equity_usd: "100.00",
            last_successful_sync_at: "2026-07-09T10:00:00Z",
            last_heartbeat_at: "2026-07-09T10:00:00Z",
            last_api_error: null,
            readiness: {
              verdict: "READY_FOR_PREVIEW",
              checked_at: "2026-07-09T10:00:00Z",
              checks: [],
            },
            updated_at: "2026-07-09T10:00:00Z",
          },
        ],
      });
    }

    if (url.pathname === "/crypto-order-previews/readiness") {
      return jsonResponse(200, {
        ready: true,
        allowed_products: ["BTC-USD"],
        max_quote_size_usd: "25.00",
        default_quote_size_usd: "5.00",
        market_data_max_age_minutes: 15,
        expiration_minutes: 5,
      });
    }

    if (url.pathname === "/crypto-order-previews" && (init?.method ?? "GET") === "GET") {
      return jsonResponse(200, { items: [previewItem()] });
    }

    if (url.pathname === "/crypto-order-previews" && (init?.method ?? "GET") === "POST") {
      return jsonResponse(200, previewItem({ crypto_order_preview_id: "preview-2", preview_id: "preview-456", created_at: "2026-07-09T10:02:00Z", updated_at: "2026-07-09T10:02:00Z" }));
    }

    return jsonResponse(404, {
      error: {
        message: `Unhandled route in test: ${init?.method ?? "GET"} ${url.pathname}`,
      },
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CryptoOrderPreviewCenter", () => {
  it("renders the preview workspace and only calls the preview endpoint when generating", async () => {
    const fetchMock = installFetchMock();

    render(<CryptoOrderPreviewCenter />);

    expect(await screen.findByRole("heading", { name: "Crypto Order Preview" })).toBeInTheDocument();
    expect(screen.getAllByText("No order has been placed. This is an estimated preview only.").length).toBeGreaterThan(0);
    expect(screen.getByText("Preview Result")).toBeInTheDocument();
    expect(screen.getByText("Latest Preview Evidence")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Generate Preview/i }));

    expect(await screen.findByText("Preview generated.")).toBeInTheDocument();
    expect(screen.getByText("Status: PREVIEW_READY")).toBeInTheDocument();

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) => {
          const input = call[0];
          const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
          return new URL(rawUrl).pathname === "/crypto-order-previews" && (call[1] as RequestInit | undefined)?.method === "POST";
        }),
      ).toBe(true);
    });

    expect(
      fetchMock.mock.calls.every((call) => {
        const input = call[0];
        const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        return !new URL(rawUrl).pathname.includes("/orders") || new URL(rawUrl).pathname === "/crypto-order-previews";
      }),
    ).toBe(true);
  });
});
