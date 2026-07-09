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
          {
            strategy_id: "22222222-2222-2222-2222-222222222222",
            strategy_name: "RSI Mean Reversion",
            enabled: false,
            status: "disabled",
            signals_generated: 8,
            buy_signals: 2,
            sell_signals: 1,
            hold_signals: 5,
            paper_trades: 3,
            open_positions: 0,
            realized_pnl: "7.5",
            unrealized_pnl: "0",
            total_return_pct: "0.015",
            decision_records: 4,
            last_signal_timestamp: "2026-07-09T08:50:00Z",
            last_trade_timestamp: "2026-07-09T08:55:00Z",
            latest_decision_package_id: "dpkg:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
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

    if (url.pathname === "/arena/coach-review" && method === "POST") {
      return jsonResponse(200, {
        observation_id: "33333333-3333-3333-3333-333333333333",
        evaluation_timestamp: "2026-07-09T12:00:01Z",
        summary: "Replay successfully reproduced the production decision.",
        strengths: ["Replay successfully reproduced the production decision."],
        weaknesses: [],
        confidence_note: "Confidence aligned with the original decision.",
        reproducibility_note: "Replay reproduced the production decision exactly.",
        suggested_follow_up: "Use this replay as a deterministic baseline for future comparisons.",
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

    expect(screen.getAllByText("MA Crossover").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("RSI Mean Reversion")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Decision Records")).toBeInTheDocument();
    expect(screen.getByText("Replay Agents")).toBeInTheDocument();
    expect(screen.getByText(/Rule-Based Decision Intelligence/i)).toBeInTheDocument();
    expect(screen.getByText(/Best Current Strategy/i)).toBeInTheDocument();
    expect(screen.getAllByText(/MA Crossover/i).length).toBeGreaterThanOrEqual(1);
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
    vi.spyOn(arenaApi, "evaluateReplayResult").mockResolvedValue({
      quality_score: 100,
      decision_reproduced: true,
      action_matches_original: true,
      confidence_matches_original: true,
      replay_duration_ms: 12,
      evaluation_timestamp: "2026-07-09T12:00:01Z",
      calibration: null,
      opportunity_cost: null,
      drawdown: null,
      risk_adjusted_return: null,
      explanation_quality: null,
    });
    vi.spyOn(arenaApi, "coachReviewDecisionQuality").mockResolvedValue({
      observation_id: "33333333-3333-3333-3333-333333333333",
      evaluation_timestamp: "2026-07-09T12:00:01Z",
      summary: "Replay successfully reproduced the production decision.",
      strengths: ["Replay successfully reproduced the production decision."],
      weaknesses: [],
      confidence_note: "Confidence aligned with the original decision.",
      reproducibility_note: "Replay reproduced the production decision exactly.",
      suggested_follow_up: "Use this replay as a deterministic baseline for future comparisons.",
    });

    const user = userEvent.setup();
    render(<DecisionArenaPage />);

    const replayButtons = await screen.findAllByRole("button", { name: "Replay" });
    expect(replayButtons).toHaveLength(2);
    const replayButton = replayButtons[0];
    await user.click(replayButton);

    expect(await screen.findByRole("status")).toHaveTextContent(
      /Replay completed\. Decision reproduced successfully\./i,
    );
    expect(screen.getByText(/Reconstructed action: BUY/i)).toBeInTheDocument();
    expect(screen.getByText(/Quality Score 100/i)).toBeInTheDocument();
    expect(screen.getAllByText("Decision reproduced")).toHaveLength(1);
    expect(screen.getByText(/Action Match/i)).toBeInTheDocument();
    expect(screen.getByText(/Confidence Match/i)).toBeInTheDocument();
    expect(screen.getByText(/Replay Duration/i)).toBeInTheDocument();
    expect(screen.getAllByText("Planned")).toHaveLength(5);
    expect(screen.getByText(/Deterministic AI Coach \(Rule-Based\)/i)).toBeInTheDocument();
    expect(screen.getAllByText("Replay successfully reproduced the production decision.")).toHaveLength(2);
    expect((await screen.findAllByText(/Confidence aligned with the original decision\./i)).length).toBeGreaterThanOrEqual(1);
  });

  it("renders the empty coach state before replay", async () => {
    installFetchMock();
    render(<DecisionArenaPage />);

    expect(await screen.findByText(/No coach observation yet\. Run Replay to generate a deterministic AI Coach review\./i)).toBeInTheDocument();
  });

  it("renders empty state when no strategies exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        const url = new URL(rawUrl);

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

        return jsonResponse(200, {
          items: [],
        });
      }),
    );

    render(<DecisionArenaPage />);

    expect(await screen.findByRole("heading", { name: "Decision Arena" })).toBeInTheDocument();
    expect(await screen.findByText(/No strategies are registered yet/i)).toBeInTheDocument();
    expect(await screen.findByText(/No deterministic recommendation can be generated yet\./i)).toBeInTheDocument();
  });
});
