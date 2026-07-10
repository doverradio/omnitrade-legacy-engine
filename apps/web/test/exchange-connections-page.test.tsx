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
          api_key_name: "******1234",
          private_key: "********",
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
        readiness: {
          verdict: "READ_ONLY_READY",
          checked_at: "2026-07-09T10:05:00Z",
          checks: [
            {
              code: "credentials_stored",
              label: "Credentials Stored",
              status: "pass",
              explanation: "Encrypted credentials are present.",
              checked_at: "2026-07-09T10:05:00Z",
              remediation: "Save Coinbase API key name and private key in Exchange Connections.",
            },
            {
              code: "balances_retrieved",
              label: "Balances Retrieved",
              status: "pass",
              explanation: "Balances retrieved successfully.",
              checked_at: "2026-07-09T10:05:00Z",
              remediation: "Run Verify Connection or Refresh Balances.",
            },
          ],
        },
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
    if (url.pathname.endsWith("/verify") && init?.method === "POST") {
      return jsonResponse(200, connectedPayload().items[0]);
    }
    if (url.pathname.endsWith("/disconnect") && init?.method === "POST") {
      return jsonResponse(200, {
        exchange_connection_id: "11111111-1111-1111-1111-111111111111",
        disconnected: true,
        message: "Credentials removed locally. Revoke the API key in Coinbase separately if needed.",
      });
    }
    if (url.pathname.endsWith("/rotate-credentials") && init?.method === "POST") {
      return jsonResponse(200, connectedPayload().items[0]);
    }
    if (url.pathname.endsWith("/readiness") && (!init || !init.method || init.method === "GET")) {
      return jsonResponse(200, connectedPayload().items[0].readiness);
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
    expect(screen.getByText("Credentials Stored")).toBeInTheDocument();
    expect(screen.getByText("Balances Retrieved")).toBeInTheDocument();
  });

  it("tests and saves a connection", async () => {
    const fetchMock = installFetchMock();

    render(<ExchangeConnectionsPage />);
    expect(await screen.findByRole("heading", { name: "Exchange Connections" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("API Key Name"), { target: { value: "key" } });
    fireEvent.change(screen.getByLabelText("Private Key / API Secret"), { target: { value: "secret" } });

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
