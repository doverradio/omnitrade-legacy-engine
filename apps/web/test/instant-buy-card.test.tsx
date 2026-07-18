import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import InstantBuyCard from "@/components/domain/InstantBuyCard";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/exchange-connections") {
      return jsonResponse(200, {
        items: [
          {
            exchange_connection_id: "c1",
            provider: "kraken_spot",
            provider_label: "Kraken Spot",
            connection_name: "Kraken Main",
            environment: "production",
            status: "connected",
            credentials_valid: true,
            credential_mask: { api_key_name: "***", private_key: "***", passphrase: null },
            api_permissions: ["trade"],
            account_status: "active",
            balances: [{ currency: "USD", available: "100.00", reserved: "0", total: "100.00" }],
            total_equity_usd: "100.00",
            last_successful_sync_at: "2026-07-17T00:00:00Z",
            last_heartbeat_at: "2026-07-17T00:00:00Z",
            last_api_error: null,
            readiness: { verdict: "READY_FOR_OPERATOR_REVIEW", checked_at: "2026-07-17T00:00:00Z", checks: [] },
            updated_at: "2026-07-17T00:00:00Z",
          },
        ],
      });
    }

    if (url.pathname === "/instant-trades/buy") {
      return jsonResponse(200, {
        internal_order_id: "11111111-1111-1111-1111-111111111111",
        provider_order_id: "O-1",
        status: "PENDING",
        requested_amount: "5.00",
        executed_quantity: null,
        average_fill_price: null,
        fees: {},
        created_at: "2026-07-17T00:00:00Z",
        submitted_at: "2026-07-17T00:00:01Z",
        acknowledged_at: null,
        filled_at: null,
        updated_at: "2026-07-17T00:00:01Z",
        reconciliation_state: null,
        order: {
          live_crypto_order_id: "11111111-1111-1111-1111-111111111111",
          provider: "kraken_spot",
          environment: "production",
          product: "BTC-USD",
          side: "BUY",
          raw_status: "SUBMISSION_PENDING",
          failure_code: null,
          failure_reason: null,
        },
      });
    }

    if (url.pathname === "/instant-trades/11111111-1111-1111-1111-111111111111") {
      return jsonResponse(200, {
        internal_order_id: "11111111-1111-1111-1111-111111111111",
        provider_order_id: "O-1",
        status: "FILLED",
        requested_amount: "5.00",
        executed_quantity: "0.00005",
        average_fill_price: "100000",
        fees: { USD: "0.01" },
        created_at: "2026-07-17T00:00:00Z",
        submitted_at: "2026-07-17T00:00:01Z",
        acknowledged_at: "2026-07-17T00:00:02Z",
        filled_at: "2026-07-17T00:00:03Z",
        updated_at: "2026-07-17T00:00:03Z",
        reconciliation_state: "filled",
        order: {
          live_crypto_order_id: "11111111-1111-1111-1111-111111111111",
          provider: "kraken_spot",
          environment: "production",
          product: "BTC-USD",
          side: "BUY",
          raw_status: "FILLED",
          failure_code: null,
          failure_reason: null,
        },
      });
    }

    return jsonResponse(404, { error: { message: `Unhandled route ${url.pathname}` } });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("InstantBuyCard", () => {
  it("submits and shows visible state progression", async () => {
    installFetchMock();
    render(<InstantBuyCard />);

    expect(await screen.findByText("Buy Asset")).toBeInTheDocument();

    const uuidInputs = screen.getAllByPlaceholderText("UUID");
    fireEvent.change(uuidInputs[0], { target: { value: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" } });
    const profileInput = uuidInputs[1];
    fireEvent.change(profileInput, { target: { value: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Buy Now" }));

    expect(await screen.findByText("State: PENDING")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Internal Order ID: 11111111-1111-1111-1111-111111111111")).toBeInTheDocument();
    });
  });
});
