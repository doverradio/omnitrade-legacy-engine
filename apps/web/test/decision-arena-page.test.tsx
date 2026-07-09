import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DecisionArenaPage from "@/app/decision-arena/page";

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

    if (url.pathname === "/arena/tournament") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, {
        tournament_id: "99999999-9999-9999-9999-999999999999",
        generated_at: "2026-07-09T12:00:00Z",
        compared_strategies: ["MA Crossover", "RSI Mean Reversion"],
        ranking: [
          {
            strategy_name: "MA Crossover",
            quality_score: 100,
            replay_variance: "0.00",
            replay_count: 1,
            paper_trades: 6,
            realized_pnl: "18.5",
            unrealized_pnl: "2.25",
            win_rate: "0.50",
            overall_rank: 1,
          },
          {
            strategy_name: "RSI Mean Reversion",
            quality_score: 50,
            replay_variance: "0.30",
            replay_count: 1,
            paper_trades: 3,
            realized_pnl: "7.5",
            unrealized_pnl: "0",
            win_rate: "0.33",
            overall_rank: 2,
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

    if (url.pathname === "/arena/decision-intelligence") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, {
        recommendation_id: "44444444-4444-4444-4444-444444444444",
        generated_at: "2026-07-09T12:00:00Z",
        compared_strategies: ["MA Crossover", "RSI Mean Reversion"],
        highest_quality_strategy: "MA Crossover",
        evidence_summary: "Compared 2 active strategies using deterministic replay quality and variance tie-breaks.",
        confidence_summary: "Best strategy confidence note: Confidence aligned with the original decision.",
        recommendation_summary: "MA Crossover ranked highest by deterministic quality scoring with configured tie-break rules.",
        human_review_required: true,
        promotion_recommended: false,
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
  it("renders tournament ranking", async () => {
    const fetchMock = installFetchMock();
    render(<DecisionArenaPage />);

    await waitFor(() => {
      expect(screen.getByText("Decision Arena Tournament")).toBeInTheDocument();
    });

    expect(screen.getByRole("table", { name: /Tournament Ranking/i })).toBeInTheDocument();
    expect(screen.getAllByText(/Quality/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Replay Variance/i)).toBeInTheDocument();
    expect(screen.getAllByText("MA Crossover").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("RSI Mean Reversion").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Replay Agents")).toBeInTheDocument();
    expect(screen.getByText(/Tournament Summary/i)).toBeInTheDocument();

    const nonGetCalls = fetchMock.mock.calls.filter((call) => {
      const init = call[1] as RequestInit | undefined;
      return (init?.method ?? "GET") !== "GET";
    });

    expect(nonGetCalls).toHaveLength(0);
  });

  it("renders champion and runner up summary", async () => {
    installFetchMock();
    render(<DecisionArenaPage />);

    expect(await screen.findByText(/Current Champion/i)).toBeInTheDocument();
    expect(await screen.findByText(/Runner Up/i)).toBeInTheDocument();
    expect(screen.getAllByText("MA Crossover").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("RSI Mean Reversion").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/Human Review Required/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/History Placeholder/i)).toBeInTheDocument();
    expect(screen.getByText(/Future: Tournament History/i)).toBeInTheDocument();
    expect(screen.getByText(/Future: Champion History/i)).toBeInTheDocument();
  });

  it("renders empty tournament state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        const method = input instanceof Request ? input.method : init?.method ?? "GET";
        const url = new URL(rawUrl);

        if (url.pathname === "/arena/tournament") {
          if (method !== "GET") {
            return jsonResponse(405, {
              error: {
                message: `Unexpected method ${method}`,
              },
            });
          }

          return jsonResponse(200, {
            tournament_id: "99999999-9999-9999-9999-999999999999",
            generated_at: "2026-07-09T12:00:00Z",
            compared_strategies: [],
            ranking: [],
          });
        }

        if (url.pathname === "/arena/decision-intelligence") {
          return jsonResponse(200, {
            recommendation_id: "44444444-4444-4444-4444-444444444444",
            generated_at: "2026-07-09T12:00:00Z",
            compared_strategies: [],
            highest_quality_strategy: null,
            evidence_summary: "No active strategies had replay-ready evidence.",
            confidence_summary: "No confidence comparison available.",
            recommendation_summary: "No deterministic recommendation can be generated yet.",
            human_review_required: true,
            promotion_recommended: false,
          });
        }

        if (url.pathname === "/arena/replay-agents") {
          return jsonResponse(200, []);
        }

        return jsonResponse(404, {
          error: {
            message: `Unhandled route in test: ${method} ${url.pathname}`,
          },
        });
      }),
    );

    render(<DecisionArenaPage />);

    expect(await screen.findByRole("heading", { name: "Decision Arena" })).toBeInTheDocument();
    expect(await screen.findByText(/No active strategies are available for tournament comparison yet\./i)).toBeInTheDocument();
    expect(await screen.findByText(/Current Champion/i)).toBeInTheDocument();
    expect((await screen.findAllByText(/^None$/i)).length).toBeGreaterThan(0);
  });
});
