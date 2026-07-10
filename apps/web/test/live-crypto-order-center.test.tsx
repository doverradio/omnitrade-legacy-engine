import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LiveCryptoOrderCenter from "@/components/domain/LiveCryptoOrderCenter";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function orderItem(overrides: Record<string, unknown> = {}) {
  return {
    live_crypto_order_id: "live-order-1",
    crypto_order_preview_id: "preview-1",
    exchange_connection_id: "connection-1",
    provider: "coinbase_advanced",
    environment: "production",
    product_id: "BTC-USD",
    side: "BUY",
    order_type: "MARKET",
    requested_quote_size: "5.00",
    client_order_id: "client-1",
    status: "PENDING_CONFIRMATION",
    risk_event_id: null,
    decision_record_id: null,
    validation_run_id: null,
    provider_order_id: null,
    provider_status: null,
    submitted_at: null,
    acknowledged_at: null,
    filled_at: null,
    cancelled_at: null,
    failure_code: null,
    failure_reason: null,
    safe_provider_response: {
      prepared_by: "operator:human",
      execution_risk_verdict: "APPROVE",
    },
    audit_correlation_id: "audit-1",
    operator_confirmation_id: null,
    created_at: "2026-07-09T10:00:00Z",
    updated_at: "2026-07-09T10:00:00Z",
    ...overrides,
  };
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/live-crypto-orders/readiness") {
      return jsonResponse(200, {
        overall_verdict: "READY_FOR_DRY_RUN",
        live_mode_enabled: false,
        live_profile_ready: false,
        feature_flag_enabled: false,
        dry_run_enabled: true,
        max_order_usd: "5.00",
        latest_preview_age_seconds: 18,
        latest_balance_age_seconds: 12,
        latest_readiness_age_seconds: 15,
        latest_price_age_seconds: 10,
        reason: "live_submission_disabled",
        checks: [],
      });
    }

    if (url.pathname === "/live-crypto-orders" && (init?.method ?? "GET") === "GET") {
      return jsonResponse(200, { items: [orderItem()] });
    }

    if (url.pathname === "/live-crypto-orders/prepare-confirmation" && (init?.method ?? "GET") === "POST") {
      return jsonResponse(200, {
        live_crypto_order: orderItem({ status: "PENDING_CONFIRMATION" }),
        confirmation_challenge_id: "challenge-1",
        confirmation_phrase_required: "BUY BTC",
        confirmation_expires_at: "2026-07-09T10:05:00Z",
        live_money_warning: "LIVE MONEY: operator confirmation required before submission.",
        execution_risk_verdict: "APPROVE",
        preview_age_seconds: 18,
        estimated_usd_balance_after: null,
        usd_balance_before: null,
      });
    }

    if (url.pathname === "/live-crypto-orders/dry-run" && (init?.method ?? "GET") === "POST") {
      return jsonResponse(200, {
        live_crypto_order: orderItem({ status: "DRY_RUN_READY" }),
        dry_run_status: "DRY_RUN_READY",
        dry_run_message: "Dry run completed. No Coinbase order was submitted.",
        safe_request_summary: { dry_run: true },
        provider_create_order_called: false,
        order_submitted: false,
      });
    }

    if (url.pathname === "/live-crypto-orders/submit" && (init?.method ?? "GET") === "POST") {
      return jsonResponse(200, {
        live_crypto_order: orderItem({ status: "SUBMITTED", provider_order_id: "provider-order-1", submitted_at: "2026-07-09T10:01:00Z" }),
        execution_risk_verdict: "APPROVE",
        provider_create_order_responded: true,
        provider_reconciliation_status: "OPEN",
        safe_provider_response: { create_order_success: true },
        order_submitted: true,
      });
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

describe("LiveCryptoOrderCenter", () => {
  it("renders the live-money gate and blocks submission when the server flag is disabled", async () => {
    installFetchMock();

    render(<LiveCryptoOrderCenter />);

    expect(await screen.findByRole("heading", { name: "Live Crypto Orders" })).toBeInTheDocument();
    expect(screen.getByText("LIVE MONEY")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Enter live_trading_profile_id"), {
      target: { value: "profile-1" },
    });
    fireEvent.change(screen.getByPlaceholderText("Enter approved preview ID"), {
      target: { value: "preview-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load Readiness" }));

    expect(await screen.findByText("Feature flag: Disabled")).toBeInTheDocument();
    expect(screen.getByText("Dry run: Enabled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare Confirmation" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Submit Live Order" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run Dry Run" })).toBeEnabled();
    expect(screen.getByText("Recent Live Orders")).toBeInTheDocument();
  });

  it("prepares and submits a live order through the dedicated live-order endpoints only", async () => {
    const fetchMock = installFetchMock();
    fetchMock.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const url = new URL(rawUrl);

      if (url.pathname === "/live-crypto-orders/readiness") {
        return jsonResponse(200, {
          overall_verdict: "READY_FOR_OPERATOR_ENABLEMENT",
          live_mode_enabled: true,
          live_profile_ready: true,
          feature_flag_enabled: true,
          dry_run_enabled: true,
          max_order_usd: "5.00",
          latest_preview_age_seconds: 12,
          latest_balance_age_seconds: 9,
          latest_readiness_age_seconds: 8,
          latest_price_age_seconds: 5,
          reason: null,
          checks: [],
        });
      }

      if (url.pathname === "/live-crypto-orders" && (init?.method ?? "GET") === "GET") {
        return jsonResponse(200, { items: [] });
      }

      if (url.pathname === "/live-crypto-orders/prepare-confirmation" && (init?.method ?? "GET") === "POST") {
        return jsonResponse(200, {
          live_crypto_order: orderItem(),
          confirmation_challenge_id: "challenge-1",
          confirmation_phrase_required: "BUY BTC",
          confirmation_expires_at: "2026-07-09T10:05:00Z",
          live_money_warning: "LIVE MONEY: operator confirmation required before submission.",
          execution_risk_verdict: "APPROVE",
          preview_age_seconds: 12,
          estimated_usd_balance_after: null,
          usd_balance_before: null,
        });
      }

      if (url.pathname === "/live-crypto-orders/submit" && (init?.method ?? "GET") === "POST") {
        return jsonResponse(200, {
          live_crypto_order: orderItem({ status: "SUBMITTED", provider_order_id: "provider-order-1", submitted_at: "2026-07-09T10:01:00Z" }),
          execution_risk_verdict: "APPROVE",
          provider_create_order_responded: true,
          provider_reconciliation_status: "OPEN",
          safe_provider_response: { create_order_success: true },
          order_submitted: true,
        });
      }

      return jsonResponse(404, {
        error: {
          message: `Unhandled route in test: ${init?.method ?? "GET"} ${url.pathname}`,
        },
      });
    });

    render(<LiveCryptoOrderCenter />);

    fireEvent.change(screen.getByPlaceholderText("Enter live_trading_profile_id"), {
      target: { value: "profile-1" },
    });
    fireEvent.change(screen.getByPlaceholderText("Enter approved preview ID"), {
      target: { value: "preview-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load Readiness" }));

    expect(await screen.findByText("Feature flag: Enabled")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Prepare Confirmation" }));
    expect(await screen.findByText(/Confirmation required: BUY BTC/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Submit Live Order" }));
    expect(await screen.findByText("Live order submitted.")).toBeInTheDocument();
    expect(screen.getAllByText("SUBMITTED").length).toBeGreaterThan(0);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) => {
          const input = call[0];
          const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
          return new URL(rawUrl).pathname === "/live-crypto-orders/submit";
        }),
      ).toBe(true);
    });
  });
});
