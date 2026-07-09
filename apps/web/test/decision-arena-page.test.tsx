import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  let laboratoryRunComplete = false;
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

    if (url.pathname === "/arena/capital-allocation") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, {
        recommendation_id: "55555555-5555-5555-5555-555555555555",
        generated_at: "2026-07-09T12:00:00Z",
        total_paper_capital: "100000",
        allocations: [
          {
            strategy_name: "MA Crossover",
            allocation_percent: "70",
            allocation_amount: "70000",
            rationale: "Ranked first in tournament with quality score 100. Receives primary deterministic allocation tier.",
          },
          {
            strategy_name: "RSI Mean Reversion",
            allocation_percent: "30",
            allocation_amount: "30000",
            rationale: "Ranked second in tournament with quality score 50. Receives secondary deterministic allocation tier.",
          },
        ],
      });
    }

    if (url.pathname === "/research/agents") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, [
        {
          agent_id: "66666666-6666-6666-6666-666666666666",
          agent_name: "Baseline Research Agent",
          capabilities: ["Generate deterministic candidate strategies"],
        },
      ]);
    }

    if (url.pathname === "/research/candidates") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, [
        {
          candidate_id: "77777777-7777-7777-7777-777777777777",
          generated_at: "2026-07-09T12:00:00Z",
          originating_agent: "Baseline Research Agent",
          strategy_name: "Volatility Filter MA-RSI Blend",
          description: "Combines moving-average trend filter with RSI threshold confirmation under deterministic volatility guardrails.",
          parameter_set: {
            fast_period: 12,
          },
          rationale: "Baseline deterministic candidate intended to improve signal quality under mixed trend and mean-reversion market states.",
          status: "PROPOSED",
        },
        {
          candidate_id: "77777777-7777-7777-7777-777777777778",
          generated_at: "2026-07-09T12:00:00Z",
          originating_agent: "Baseline Research Agent",
          strategy_name: "MA-RSI Blend rsi10",
          description: "Shorter RSI lookback variant to increase sensitivity in momentum transitions.",
          parameter_set: {
            rsi_period: 10,
          },
          rationale: "Tests whether shorter RSI lookback improves deterministic decision fidelity.",
          status: "PROPOSED",
        },
        {
          candidate_id: "77777777-7777-7777-7777-777777777779",
          generated_at: "2026-07-09T12:00:00Z",
          originating_agent: "Baseline Research Agent",
          strategy_name: "MA-RSI Blend threshold 35/65",
          description: "RSI threshold variant with narrower entry/exit bands.",
          parameter_set: {
            buy_threshold: 35,
            sell_threshold: 65,
          },
          rationale: "Assesses deterministic quality under less extreme RSI trigger levels.",
          status: "PROPOSED",
        },
        {
          candidate_id: "77777777-7777-7777-7777-777777777780",
          generated_at: "2026-07-09T12:00:00Z",
          originating_agent: "Baseline Research Agent",
          strategy_name: "MA Crossover 9/30",
          description: "Faster MA crossover profile for earlier trend entries.",
          parameter_set: {
            fast_period: 9,
            slow_period: 30,
          },
          rationale: "Evaluates deterministic replay quality impact of a more responsive MA pair.",
          status: "PROPOSED",
        },
        {
          candidate_id: "77777777-7777-7777-7777-777777777781",
          generated_at: "2026-07-09T12:00:00Z",
          originating_agent: "Baseline Research Agent",
          strategy_name: "MA Crossover 20/100",
          description: "Slower MA crossover profile to reduce signal churn.",
          parameter_set: {
            fast_period: 20,
            slow_period: 100,
          },
          rationale: "Evaluates deterministic replay quality impact of a slower trend-following profile.",
          status: "PROPOSED",
        },
      ]);
    }

    if (url.pathname === "/research/evaluate-candidates") {
      if (method !== "POST") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, {
        evaluated_count: 5,
        evaluations: [
          {
            evaluation_id: "88888888-8888-8888-8888-888888888888",
            candidate_id: "77777777-7777-7777-7777-777777777777",
            replay_status: "COMPLETED",
            decision_quality_score: 100,
            ai_coach_summary: "Replay successfully reproduced the production decision.",
            decision_intelligence_summary: "Volatility Filter MA-RSI Blend ranked highest by deterministic quality scoring with configured tie-break rules.",
            tournament_rank: 1,
            promotion_eligible: false,
          },
          {
            evaluation_id: "88888888-8888-8888-8888-888888888889",
            candidate_id: "77777777-7777-7777-7777-777777777778",
            replay_status: "COMPLETED",
            decision_quality_score: 50,
            ai_coach_summary: "Replay action differed from production.",
            decision_intelligence_summary: "Deterministic recommendation summary.",
            tournament_rank: 2,
            promotion_eligible: false,
          },
        ],
      });
    }

    if (url.pathname === "/research/laboratory") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, {
        status: laboratoryRunComplete ? "COMPLETED" : "IDLE",
        registered_agents: ["Baseline Research Agent"],
        last_run: laboratoryRunComplete
          ? {
              laboratory_run_id: "99999999-aaaa-bbbb-cccc-999999999999",
              started_at: "2026-07-09T12:00:00Z",
              completed_at: "2026-07-09T12:00:02Z",
              participating_agents: ["Baseline Research Agent"],
              generated_candidates: 5,
              evaluated_candidates: 5,
              status: "COMPLETED",
            }
          : null,
        candidates_generated: laboratoryRunComplete ? 5 : 0,
        candidates_evaluated: laboratoryRunComplete ? 5 : 0,
        success_rate: laboratoryRunComplete ? "100.00%" : "0.00%",
      });
    }

    if (url.pathname === "/research/laboratory/run") {
      if (method !== "POST") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }
      laboratoryRunComplete = true;

      return jsonResponse(200, {
        laboratory_run_id: "99999999-aaaa-bbbb-cccc-999999999999",
        started_at: "2026-07-09T12:00:00Z",
        completed_at: "2026-07-09T12:00:02Z",
        participating_agents: ["Baseline Research Agent"],
        generated_candidates: 5,
        evaluated_candidates: 5,
        status: "COMPLETED",
      });
    }

    if (url.pathname === "/research/memory") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, {
        total_laboratory_runs: laboratoryRunComplete ? 1 : 0,
        total_candidates: laboratoryRunComplete ? 5 : 0,
        highest_quality_candidate: laboratoryRunComplete
          ? {
              laboratory_run_id: "99999999-aaaa-bbbb-cccc-999999999999",
              candidate_id: "77777777-7777-7777-7777-777777777777",
              originating_agent: "Baseline Research Agent",
              parameter_set: {
                fast_period: 12,
              },
              evaluation_summary: "Replay successfully reproduced the production decision.",
              quality_score: 100,
              tournament_rank: 1,
              status: "EVALUATED",
            }
          : null,
        average_quality_score: laboratoryRunComplete ? 75.0 : null,
        latest_laboratory_run: laboratoryRunComplete
          ? {
              laboratory_run_id: "99999999-aaaa-bbbb-cccc-999999999999",
              started_at: "2026-07-09T12:00:00Z",
              completed_at: "2026-07-09T12:00:02Z",
              participating_agents: ["Baseline Research Agent"],
              candidates_generated: 5,
              candidates_evaluated: 5,
            }
          : null,
      });
    }

    if (url.pathname === "/research/memory/runs") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(
        200,
        laboratoryRunComplete
          ? [
              {
                laboratory_run_id: "99999999-aaaa-bbbb-cccc-999999999999",
                started_at: "2026-07-09T12:00:00Z",
                completed_at: "2026-07-09T12:00:02Z",
                participating_agents: ["Baseline Research Agent"],
                candidates_generated: 5,
                candidates_evaluated: 5,
              },
            ]
          : [],
      );
    }

    if (url.pathname === "/research/memory/candidates") {
      if (method !== "GET") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(
        200,
        laboratoryRunComplete
          ? [
              {
                laboratory_run_id: "99999999-aaaa-bbbb-cccc-999999999999",
                candidate_id: "77777777-7777-7777-7777-777777777777",
                originating_agent: "Baseline Research Agent",
                parameter_set: {
                  fast_period: 12,
                },
                evaluation_summary: "Replay successfully reproduced the production decision.",
                quality_score: 100,
                tournament_rank: 1,
                status: "EVALUATED",
              },
            ]
          : [],
      );
    }

    if (url.pathname === "/research/evolve") {
      if (method !== "POST") {
        return jsonResponse(405, {
          error: {
            message: `Unexpected method ${method}`,
          },
        });
      }

      return jsonResponse(200, {
        generated_count: 2,
        descendants: [
          {
            candidate_id: "aaaaaaaa-7777-7777-7777-777777777701",
            parent_candidate_id: "77777777-7777-7777-7777-777777777777",
            generation: 2,
            mutation_reason: "rsi_period 14->12",
            parameter_diff: [
              {
                parameter_name: "rsi_period",
                previous_value: 14,
                new_value: 12,
              },
            ],
            parameter_set: {
              rsi_period: 12,
            },
            generated_at: "2026-07-09T12:00:03Z",
            quality_score: 100,
            tournament_rank: 1,
            status: "EVALUATED",
          },
          {
            candidate_id: "aaaaaaaa-7777-7777-7777-777777777702",
            parent_candidate_id: "77777777-7777-7777-7777-777777777777",
            generation: 2,
            mutation_reason: "rsi_period 14->16",
            parameter_diff: [
              {
                parameter_name: "rsi_period",
                previous_value: 14,
                new_value: 16,
              },
            ],
            parameter_set: {
              rsi_period: 16,
            },
            generated_at: "2026-07-09T12:00:03Z",
            quality_score: 50,
            tournament_rank: 2,
            status: "EVALUATED",
          },
        ],
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
    expect(screen.getByRole("table", { name: /Recommended Allocation/i })).toBeInTheDocument();
    expect(screen.getByText(/Total Paper Capital/i)).toBeInTheDocument();
    expect(screen.getByText(/Rule-Based Capital Allocation/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Research Agents/i })).toBeInTheDocument();
    expect(screen.getByText(/RESEARCH ONLY/i)).toBeInTheDocument();
    expect(screen.getByText(/NO PRODUCTION CHANGES/i)).toBeInTheDocument();
    expect(screen.getAllByText(/HUMAN REVIEW REQUIRED/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Baseline Research Agent/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("table", { name: /Candidate Strategies/i })).toBeInTheDocument();
    expect(screen.getAllByText(/PROPOSED/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/MA Crossover 9\/30/i)).toBeInTheDocument();
    expect(screen.getByText(/MA Crossover 20\/100/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Research Laboratory/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run Laboratory/i })).toBeInTheDocument();
    expect(screen.getByText(/Laboratory Status/i)).toBeInTheDocument();
    expect(screen.getByText(/No laboratory run has completed yet\./i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Research Memory/i })).toBeInTheDocument();
    expect(screen.getByText(/Total Laboratory Runs/i)).toBeInTheDocument();
    expect(screen.getByText(/Recent Candidate History/i)).toBeInTheDocument();
    expect(screen.getByText(/No research memory has been recorded yet\./i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Evolution/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run Evolution/i })).toBeInTheDocument();
    expect(screen.getByText(/No evolved descendants generated yet\./i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Run Laboratory/i }));
    expect(await screen.findByText(/Last run included 1 agent\(s\) and completed with status COMPLETED\./i)).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: /Recent Candidate History/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Run Evolution/i }));
    expect(await screen.findByRole("table", { name: /Evolution Results/i })).toBeInTheDocument();
    expect(screen.getByText(/Lineage Tree/i)).toBeInTheDocument();
    expect(screen.getByText(/rsi_period 14->12/i)).toBeInTheDocument();

    expect(screen.getByRole("button", { name: /Evaluate Candidates/i })).toBeInTheDocument();
    expect(screen.getByText(/No candidate evaluations available yet\./i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Evaluate Candidates/i }));
    expect(await screen.findByRole("table", { name: /Candidate Evaluations/i })).toBeInTheDocument();
    expect(screen.getByText(/Evaluated 5 candidates\./i)).toBeInTheDocument();
    expect(screen.getByText(/Replay successfully reproduced the production decision\./i)).toBeInTheDocument();
    expect(screen.getAllByText(/^100$/i).length).toBeGreaterThan(0);

    const nonGetCalls = fetchMock.mock.calls.filter((call) => {
      const init = call[1] as RequestInit | undefined;
      return (init?.method ?? "GET") !== "GET";
    });

    expect(nonGetCalls).toHaveLength(3);
    const postCallUrls = nonGetCalls.map((call) => new URL(call[0] as string).pathname);
    expect(postCallUrls).toContain("/research/laboratory/run");
    expect(postCallUrls).toContain("/research/evaluate-candidates");
    expect(postCallUrls).toContain("/research/evolve");
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

  it("renders capital allocation panel", async () => {
    installFetchMock();
    render(<DecisionArenaPage />);

    expect(await screen.findByRole("heading", { name: "Capital Allocation" })).toBeInTheDocument();
    expect(screen.getByText(/Human Approval Required/i)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Recommended Allocation/i })).toBeInTheDocument();
    expect(screen.getAllByText("MA Crossover").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/70.00%/i)).toBeInTheDocument();
    expect(screen.getByText(/\$70000\.00/i)).toBeInTheDocument();
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

        if (url.pathname === "/arena/capital-allocation") {
          return jsonResponse(200, {
            recommendation_id: "55555555-5555-5555-5555-555555555555",
            generated_at: "2026-07-09T12:00:00Z",
            total_paper_capital: "100000",
            allocations: [],
          });
        }

        if (url.pathname === "/arena/replay-agents") {
          return jsonResponse(200, []);
        }

        if (url.pathname === "/research/agents") {
          return jsonResponse(200, []);
        }

        if (url.pathname === "/research/candidates") {
          return jsonResponse(200, []);
        }

        if (url.pathname === "/research/evaluate-candidates") {
          return jsonResponse(200, {
            evaluated_count: 0,
            evaluations: [],
          });
        }

        if (url.pathname === "/research/laboratory") {
          return jsonResponse(200, {
            status: "EMPTY",
            registered_agents: [],
            last_run: null,
            candidates_generated: 0,
            candidates_evaluated: 0,
            success_rate: "0.00%",
          });
        }

        if (url.pathname === "/research/laboratory/run") {
          return jsonResponse(200, {
            laboratory_run_id: "99999999-aaaa-bbbb-cccc-999999999999",
            started_at: "2026-07-09T12:00:00Z",
            completed_at: "2026-07-09T12:00:02Z",
            participating_agents: [],
            generated_candidates: 0,
            evaluated_candidates: 0,
            status: "EMPTY",
          });
        }

        if (url.pathname === "/research/memory") {
          return jsonResponse(200, {
            total_laboratory_runs: 0,
            total_candidates: 0,
            highest_quality_candidate: null,
            average_quality_score: null,
            latest_laboratory_run: null,
          });
        }

        if (url.pathname === "/research/memory/runs") {
          return jsonResponse(200, []);
        }

        if (url.pathname === "/research/memory/candidates") {
          return jsonResponse(200, []);
        }

        if (url.pathname === "/research/evolve") {
          return jsonResponse(200, {
            generated_count: 0,
            descendants: [],
          });
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
    expect(await screen.findByText(/No capital allocation recommendation available yet\./i)).toBeInTheDocument();
    expect(await screen.findByText(/No research agents or candidate strategies are available yet\./i)).toBeInTheDocument();
    expect(screen.queryByText(/No candidate evaluations available yet\./i)).not.toBeInTheDocument();
    expect(await screen.findByText(/No laboratory run has completed yet\./i)).toBeInTheDocument();
    expect(await screen.findByText(/No research memory has been recorded yet\./i)).toBeInTheDocument();
    expect(await screen.findByText(/No evolved descendants generated yet\./i)).toBeInTheDocument();
  });
});
