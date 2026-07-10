import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ExchangeConnectionsPage from "@/app/exchange-connections/page";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function connectedPayload() {
  return {
    items: [
      {
        exchange_connection_id: "11111111-1111-1111-1111-111111111111",
        provider: "coinbase_advanced",
        provider_label: "Coinbase Advanced",
        connection_name: "Primary Coinbase",
        environment: "sandbox",
        status: "connected",
        credentials_valid: true,
        credential_mask: {
          api_key: "******1234",
          api_secret: "********",
          passphrase: "********",
        },
        api_permissions: ["view", "trade"],
        account_status: "active",
        balances: [
          { currency: "USD", available: "100", reserved: "10", total: "110" },
          { currency: "BTC", available: "0.1", reserved: "0.02", total: "0.12" },
          { currency: "ETH", available: "1.5", reserved: "0.25", total: "1.75" },
        ],
        total_equity_usd: "110",
        last_successful_sync_at: "2026-07-09T10:00:00Z",
        last_heartbeat_at: "2026-07-09T10:05:00Z",
        last_api_error: null,
        readiness_checks: [
          { code: "exchange_connected", label: "Exchange Connected", ok: true, detail: "Connected" },
          { code: "credentials_valid", label: "Credentials Valid", ok: true, detail: "Validated" },
          { code: "balances_retrieved", label: "Balances Retrieved", ok: true, detail: "Balances available" },
          { code: "permissions_verified", label: "Permissions Verified", ok: true, detail: "Permissions available" },
          { code: "time_synced", label: "Time Synced", ok: true, detail: "Heartbeat fresh" },
          { code: "api_reachable", label: "API Reachable", ok: true, detail: "Reachable" },
        ],
        updated_at: "2026-07-09T10:05:00Z",
      },
    ],
  };
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/exchange-connections" && (!init || !init.method || init.method === "GET")) {
      return jsonResponse(200, connectedPayload());
    }
    if (url.pathname === "/exchange-connections/test" && init?.method === "POST") {
      return jsonResponse(200, {
        reachable: true,
        authenticated: true,
        account_status: "active",
        permissions: ["view"],
        heartbeat_at: "2026-07-09T10:05:00Z",
        error: null,
      });
    }
    if (url.pathname === "/exchange-connections" && init?.method === "POST") {
      return jsonResponse(201, connectedPayload().items[0]);
    }
    if (url.pathname.endsWith("/refresh/balances") || url.pathname.endsWith("/refresh/account") || url.pathname.endsWith("/refresh/permissions")) {
      return jsonResponse(200, connectedPayload().items[0]);
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
});

describe("ExchangeConnectionsPage", () => {
  it("renders exchange connection page and status", async () => {
    installFetchMock();

    render(<ExchangeConnectionsPage />);

    expect(await screen.findByRole("heading", { name: "Exchange Connections" })).toBeInTheDocument();
    expect(screen.getByText("Coinbase Advanced")).toBeInTheDocument();
    expect(screen.getAllByText("connected").length).toBeGreaterThan(0);
  });

  it("renders masked credentials", async () => {
    installFetchMock();

    render(<ExchangeConnectionsPage />);

    expect(await screen.findByText("******1234")).toBeInTheDocument();
    expect(screen.getAllByText("********").length).toBeGreaterThan(0);
  });

  it("renders balances including USD BTC ETH and total equity", async () => {
    installFetchMock();

    render(<ExchangeConnectionsPage />);

    expect(await screen.findByText("Balances")).toBeInTheDocument();
    expect(screen.getByText("USD")).toBeInTheDocument();
    expect(screen.getByText("BTC")).toBeInTheDocument();
    expect(screen.getByText("ETH")).toBeInTheDocument();
    expect(screen.getByText(/Total Equity \(USD\):/i)).toBeInTheDocument();
  });

  it("renders readiness cards", async () => {
    installFetchMock();

    render(<ExchangeConnectionsPage />);

    expect(await screen.findByText("Live Readiness")).toBeInTheDocument();
    expect(screen.getByText("Exchange Connected")).toBeInTheDocument();
    expect(screen.getByText("Credentials Valid")).toBeInTheDocument();
    expect(screen.getByText("Balances Retrieved")).toBeInTheDocument();
    expect(screen.getByText("Permissions Verified")).toBeInTheDocument();
    expect(screen.getByText("Time Synced")).toBeInTheDocument();
    expect(screen.getByText("API Reachable")).toBeInTheDocument();
  });

  it("tests and saves a connection", async () => {
    const fetchMock = installFetchMock();

    render(<ExchangeConnectionsPage />);
    expect(await screen.findByRole("heading", { name: "Exchange Connections" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "key" } });
    fireEvent.change(screen.getByLabelText("API Secret"), { target: { value: "secret" } });

    fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    expect(await screen.findByText("Connection test succeeded.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Connection saved.")).toBeInTheDocument();

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(calls.some((item) => item.includes("/exchange-connections/test"))).toBe(true);
      expect(calls.filter((item) => item.endsWith("/exchange-connections")).length).toBeGreaterThan(1);
    });
  });
});
