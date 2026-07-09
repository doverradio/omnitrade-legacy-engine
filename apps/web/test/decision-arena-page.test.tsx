import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import DecisionArenaPage from "@/app/decision-arena/page";
import * as arenaApi from "@/lib/api/arena";

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
    const method = input instanceof Request ? input.method : init?.method ?? "GET";
    const url = new URL(rawUrl);

    if (url.pathname === "/arena/strategy-scoreboard") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, {
        items: [
          {
            strategy_id: "11111111-1111-1111-1111-111111111111",
            strategy_name: "MA Crossover",
            enabled: true,
            status: "active",
            signals_generated: 12,
            buy_signals: 5,
            sell_signals: 4,
            hold_signals: 3,
            paper_trades: 6,
            open_positions: 1,
            realized_pnl: "18.5",
            unrealized_pnl: "2.25",
            total_return_pct: "0.02075",
            decision_records: 9,
            last_signal_timestamp: "2026-07-09T09:50:00Z",
            last_trade_timestamp: "2026-07-09T09:55:00Z",
            latest_decision_package_id: "dpkg:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
          },
        ],
      });
    }

    if (url.pathname === "/arena/replay-agents") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, [
        {
          replay_agent_id: "11111111-1111-1111-1111-111111111111",
          name: "Default Replay Agent",
          status: "Registered",
          capabilities: [
            {
              name: "Decision Package consumer",
              description: "Consumes immutable Decision Packages for read-only research analysis.",
            },
          ],
          decision_package_consumer: true,
          execution_logic: false,
          processing_enabled: false,
          scheduling_enabled: false,
          writes_enabled: false,
        },
      ]);
    }

    if (url.pathname === "/arena/replay" && method === "POST") {
      return jsonResponse(200, {
        replay_id: "22222222-2222-2222-2222-222222222222",
        replay_agent_id: "11111111-1111-1111-1111-111111111111",
        decision_package_id: "dpkg:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        replay_timestamp: "2026-07-09T12:00:00Z",
        reconstructed_action: "BUY",
        reconstructed_confidence: "0.875",
        supporting_evidence: [{ type: "decision_record" }],
        explanation: "Replayed immutable decision package.",
        metadata: { mode: "read_only" },
      });
    }

    if (method !== "GET") {
      return jsonResponse(405, {
        error: {
          message: `Unexpected method ${method}`,
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
  vi.restoreAllMocks();
});

describe("DecisionArenaPage", () => {
  it("renders the strategy scoreboard and uses GET-only API integration", async () => {
    const fetchMock = installFetchMock();
    render(<DecisionArenaPage />);

    await waitFor(() => {
      expect(screen.getByText("Strategy Scoreboard")).toBeInTheDocument();
    });

    expect(screen.getByText("MA Crossover")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Decision Records")).toBeInTheDocument();
    expect(screen.getByText("Replay Agents")).toBeInTheDocument();
    expect(
      screen.getAllByText(/These panels will activate as additional replay agents and research systems are introduced/i),
    ).toHaveLength(3);
    expect(screen.getByText(/Replay agents analyze immutable Decision Packages without affecting production/i)).toBeInTheDocument();

    const nonGetCalls = fetchMock.mock.calls.filter((call) => {
      const init = call[1] as RequestInit | undefined;
      return (init?.method ?? "GET") !== "GET";
    });

    expect(nonGetCalls).toHaveLength(0);
  });

  it("replays the latest package for a strategy", async () => {
    installFetchMock();
    vi.spyOn(arenaApi, "replayDecisionPackage").mockResolvedValue({
      replay_id: "22222222-2222-2222-2222-222222222222",
      replay_agent_id: "11111111-1111-1111-1111-111111111111",
      decision_package_id: "dpkg:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      replay_timestamp: "2026-07-09T12:00:00Z",
      reconstructed_action: "BUY",
      reconstructed_confidence: "0.875",
      supporting_evidence: [{ type: "decision_record" }],
      explanation: "Replayed immutable decision package.",
      metadata: { mode: "read_only" },
    });

    const user = userEvent.setup();
    render(<DecisionArenaPage />);

    const replayButton = await screen.findByRole("button", { name: "Replay" });
    await user.click(replayButton);

    expect(await screen.findByRole("status")).toHaveTextContent(
      /Replay completed\. Decision reproduced successfully\./i,
    );
    expect(screen.getByText(/Reconstructed action: BUY/i)).toBeInTheDocument();
  });

  it("renders empty state when no strategies exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(200, {
          items: [],
        }),
      ),
    );

    render(<DecisionArenaPage />);

    expect(await screen.findByRole("heading", { name: "Decision Arena" })).toBeInTheDocument();
    expect(await screen.findByText(/No strategies are registered yet/i)).toBeInTheDocument();
  });
});
