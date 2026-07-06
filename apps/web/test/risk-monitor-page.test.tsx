import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import RiskMonitorPage from "@/app/risk-monitor/page";

type Scenario = "ok" | "status-unknown";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function installFetchMock(scenario: Scenario) {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = input instanceof Request ? input.method : init?.method ?? "GET";
    const url = new URL(rawUrl);

    if (url.pathname === "/risk/status" && method === "GET") {
      if (scenario === "status-unknown") {
        return jsonResponse(503, {
          error: {
            code: "risk_status_unavailable",
            message: "Risk status unavailable",
          },
        });
      }

      return jsonResponse(200, {
        global_kill_switch: {
          engaged: false,
          engaged_at: null,
          engaged_by: null,
          reason: null,
        },
        account: {
          account_id: "11111111-1111-1111-1111-111111111111",
          trading_paused: false,
          paused_reason: null,
          daily_loss: {
            used: "15.00",
            limit: "30.00",
            pct_used: "0.5",
          },
          drawdown: {
            used: "20.00",
            limit: "50.00",
            pct_used: "0.4",
          },
          active_cooldowns: [],
          active_no_trade_zones: [],
        },
      });
    }

    if (url.pathname === "/risk/rules" && method === "GET") {
      return jsonResponse(200, {
        account_id: "11111111-1111-1111-1111-111111111111",
        rules: {
          max_position_size_pct: "0.10",
          max_daily_loss_pct: "0.03",
          max_drawdown_pct: "0.10",
          default_stop_loss_pct: "0.03",
          cooldown_after_losses: 3,
          cooldown_duration_hours: 24,
        },
        is_override: true,
        system_defaults: {
          max_position_size_pct: "0.10",
          max_daily_loss_pct: "0.03",
          max_drawdown_pct: "0.10",
          default_stop_loss_pct: "0.03",
          cooldown_after_losses: 3,
          cooldown_duration_hours: 24,
        },
      });
    }

    if (url.pathname === "/risk/kill-switch/enable" && method === "POST") {
      return jsonResponse(200, {
        scope: "account",
        account_id: "11111111-1111-1111-1111-111111111111",
        engaged: true,
        engaged_at: "2026-07-06T00:00:00Z",
        engaged_by: "user:risk-monitor",
      });
    }

    if (url.pathname === "/risk/rules" && method === "PATCH") {
      return jsonResponse(200, {
        account_id: "11111111-1111-1111-1111-111111111111",
        rules: {
          max_position_size_pct: "0.20",
          max_daily_loss_pct: "0.03",
          max_drawdown_pct: "0.10",
          default_stop_loss_pct: "0.03",
          cooldown_after_losses: 3,
          cooldown_duration_hours: 24,
        },
        is_override: true,
        system_defaults: {
          max_position_size_pct: "0.10",
          max_daily_loss_pct: "0.03",
          max_drawdown_pct: "0.10",
          default_stop_loss_pct: "0.03",
          cooldown_after_losses: 3,
          cooldown_duration_hours: 24,
        },
      });
    }

    return jsonResponse(404, {
      error: {
        message: `Unhandled route in test: ${method} ${url.pathname}`,
      },
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RiskMonitorPage Prompt 6.9", () => {
  it("shows STATUS UNKNOWN fail-visible banner when /risk/status returns 503", async () => {
    installFetchMock("status-unknown");
    render(<RiskMonitorPage />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Paper account ID"), "11111111-1111-1111-1111-111111111111");
    await user.click(screen.getByRole("button", { name: "Load Risk Status" }));

    expect(await screen.findByText(/STATUS UNKNOWN/i)).toBeInTheDocument();
    expect(screen.getByText(/unavailable\/unsafe/i)).toBeInTheDocument();
  });

  it("requires reason before confirming kill-switch action", async () => {
    const fetchMock = installFetchMock("ok");
    render(<RiskMonitorPage />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Paper account ID"), "11111111-1111-1111-1111-111111111111");
    await user.click(screen.getByRole("button", { name: "Load Risk Status" }));

    await waitFor(() => {
      expect(screen.getByText("Global kill switch")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Pause This Account" }));
    await user.click(screen.getByRole("button", { name: "Confirm and Submit" }));

    expect(await screen.findByText(/Please provide a short reason/i)).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("Describe why this change is necessary"), "manual safety check");
    await user.click(screen.getByRole("button", { name: "Confirm and Submit" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    const enableCalls = fetchMock.mock.calls.filter((call) => {
      const input = call[0];
      const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      return rawUrl.endsWith("/risk/kill-switch/enable");
    });

    expect(enableCalls).toHaveLength(1);
  });

  it("requires explicit loosening acknowledgement before saving loosened rules", async () => {
    const fetchMock = installFetchMock("ok");
    render(<RiskMonitorPage />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Paper account ID"), "11111111-1111-1111-1111-111111111111");
    await user.click(screen.getByRole("button", { name: "Load Risk Status" }));

    await waitFor(() => {
      expect(screen.getByText("Risk Rules Configuration")).toBeInTheDocument();
    });

    const positionInput = screen.getByLabelText("Max position size (ratio)");
    await user.clear(positionInput);
    await user.type(positionInput, "0.20");

    await user.click(screen.getByRole("button", { name: "Save Rule Changes" }));
    await user.type(screen.getByPlaceholderText("Describe why this change is necessary"), "testing loosen path");
    await user.click(screen.getByRole("button", { name: "Confirm and Submit" }));

    expect(await screen.findByText(/must explicitly confirm loosening/i)).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Confirm and Submit" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    const patchCalls = fetchMock.mock.calls.filter((call) => {
      const input = call[0];
      const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const init = call[1] as RequestInit | undefined;
      return rawUrl.endsWith("/risk/rules") && (init?.method ?? "GET") === "PATCH";
    });

    expect(patchCalls).toHaveLength(1);
  });
});