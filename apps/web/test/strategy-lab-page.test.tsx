import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import StrategyLabPage from "@/app/strategy-lab/page";

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
  }),
}));

type FetchScenario = "success" | "empty" | "error" | "loading";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function installFetchMock(scenario: FetchScenario) {
  const pending = new Promise<Response>(() => {
    // Intentionally unresolved for loading-state assertions.
  });

  const strategyItems = [
    {
      id: "strategy-1",
      name: "MA Crossover",
      slug: "ma_crossover",
      is_active: false,
      module_version: "1.0.0",
      default_params: {
        fast_period: 10,
        slow_period: 50,
        ma_type: "sma",
      },
    },
    {
      id: "strategy-2",
      name: "RSI Mean Reversion",
      slug: "rsi_mean_reversion",
      is_active: true,
      module_version: "1.0.0",
      default_params: {
        rsi_period: 14,
        oversold: 30,
        overbought: 70,
      },
    },
    {
      id: "strategy-3",
      name: "Breakout",
      slug: "breakout",
      is_active: false,
      module_version: "1.0.0",
      default_params: {
        lookback: 20,
        volume_confirmation: true,
        min_volume_multiple: 1.5,
      },
    },
  ];

  const parameterSetsStore: Array<{
    id: string;
    strategy_id: string;
    name: string;
    parameters: Record<string, unknown>;
    created_at?: string;
  }> = [
    {
      id: "ps-ma-1",
      strategy_id: "strategy-1",
      name: "conservative-v1",
      parameters: {
        fast_period: 12,
        slow_period: 60,
        ma_type: "sma",
      },
      created_at: "2026-07-01T12:00:00Z",
    },
    {
      id: "ps-ma-2",
      strategy_id: "strategy-1",
      name: "balanced-v3",
      parameters: {
        fast_period: 15,
        slow_period: 55,
        ma_type: "ema",
      },
      created_at: "2026-07-02T12:00:00Z",
    },
  ];

  const completedBacktests = [
    {
      id: "bt-1",
      status: "completed",
      strategy_id: "strategy-1",
      parameter_set_id: "ps-ma-1",
      asset_id: "asset-btc",
      interval: "1h",
      start_time: "2026-01-01T00:00:00Z",
      end_time: "2026-01-31T00:00:00Z",
      initial_capital: "25",
      fee_bps: "10",
      slippage_bps: "5",
      metrics: {
        total_return_usd: "4.12",
        total_return_pct: "0.165",
        win_rate: "0.57",
        max_drawdown: "0.09",
        sharpe_like: "1.2",
        trade_count: 42,
        average_trade_usd: "0.11",
        fee_drag_pct: "0.30",
        equity_curve: [
          { time: "2026-01-01T00:00:00Z", equity: "25" },
          { time: "2026-01-15T00:00:00Z", equity: "27.4" },
          { time: "2026-01-31T00:00:00Z", equity: "29.12" },
        ],
      },
    },
    {
      id: "bt-2",
      status: "completed",
      strategy_id: "strategy-1",
      parameter_set_id: "ps-ma-2",
      asset_id: "asset-btc",
      interval: "15m",
      start_time: "2026-02-01T00:00:00Z",
      end_time: "2026-02-28T00:00:00Z",
      initial_capital: "25",
      fee_bps: "8",
      slippage_bps: "4",
      metrics: {
        total_return_usd: "2.40",
        total_return_pct: "0.096",
        win_rate: "0.64",
        max_drawdown: "0.05",
        sharpe_like: "1.1",
        trade_count: 30,
        average_trade_usd: "0.09",
        fee_drag_pct: "0.18",
        equity_curve: [
          { time: "2026-02-01T00:00:00Z", equity: "25" },
          { time: "2026-02-15T00:00:00Z", equity: "26.5" },
          { time: "2026-02-28T00:00:00Z", equity: "27.4" },
        ],
      },
    },
    {
      id: "bt-3",
      status: "completed",
      strategy_id: "strategy-1",
      parameter_set_id: "ps-ma-1",
      asset_id: "asset-eth",
      interval: "1d",
      start_time: "2026-03-01T00:00:00Z",
      end_time: "2026-03-31T00:00:00Z",
      initial_capital: "25",
      fee_bps: "12",
      slippage_bps: "6",
      metrics: {
        total_return_usd: "-1.25",
        total_return_pct: "-0.05",
        win_rate: "0.40",
        max_drawdown: "0.22",
        sharpe_like: "0.4",
        trade_count: 18,
        average_trade_usd: "0.07",
        fee_drag_pct: "0.42",
      },
    },
    {
      id: "bt-4",
      status: "completed",
      strategy_id: "strategy-1",
      parameter_set_id: "ps-ma-2",
      asset_id: "asset-sol",
      interval: "5m",
      start_time: "2026-04-01T00:00:00Z",
      end_time: "2026-04-30T00:00:00Z",
      initial_capital: "25",
      fee_bps: "15",
      slippage_bps: "8",
      metrics: {
        total_return_usd: "0.50",
        total_return_pct: "0.02",
        win_rate: "0.51",
        max_drawdown: "0.11",
        sharpe_like: "0.7",
        trade_count: 25,
        average_trade_usd: "0.08",
        fee_drag_pct: "0.28",
      },
    },
  ];

  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = input instanceof Request ? input.method : init?.method ?? "GET";
    const url = new URL(rawUrl);

    if (scenario === "loading") {
      return pending;
    }

    if (url.pathname === "/parameter-sets" && method === "GET") {
      return jsonResponse(200, {
        items: parameterSetsStore,
      });
    }

    if (url.pathname === "/backtests" && method === "GET") {
      return jsonResponse(200, {
        items: completedBacktests,
      });
    }

    if (url.pathname.startsWith("/strategies/") && url.pathname.endsWith("/parameter-sets") && method === "POST") {
      const strategyId = url.pathname.split("/")[2];
      const request = input instanceof Request ? input : new Request(rawUrl, { method: "POST", body: init?.body });
      const body = (await request.json()) as {
        name?: string;
        parameters?: Record<string, unknown>;
      };

      const name = (body.name ?? "").trim();
      if (!name) {
        return jsonResponse(422, {
          error: {
            message: "Snapshot name is required",
          },
        });
      }

      const duplicate = parameterSetsStore.some(
        (item) => item.strategy_id === strategyId && item.name.toLowerCase() === name.toLowerCase(),
      );
      if (duplicate) {
        return jsonResponse(409, {
          error: {
            message: "Duplicate parameter set name",
          },
        });
      }

      const saved = {
        id: `ps-${parameterSetsStore.length + 1}`,
        strategy_id: strategyId,
        name,
        parameters: body.parameters ?? {},
        created_at: "2026-07-05T10:00:00Z",
      };
      parameterSetsStore.push(saved);
      return jsonResponse(201, saved);
    }

    if (url.pathname === "/backtests/run" && method === "POST") {
      return jsonResponse(202, {
        backtest_id: "bt-1",
        status: "running",
      });
    }

    if (url.pathname !== "/strategies") {
      return jsonResponse(404, {
        error: {
          message: `Unhandled route: ${url.pathname}`,
        },
      });
    }

    if (scenario === "error") {
      return jsonResponse(500, {
        error: {
          message: "Failed to load strategies for test",
        },
      });
    }

    if (scenario === "empty") {
      return jsonResponse(200, {
        items: [],
      });
    }

    return jsonResponse(200, {
      items: strategyItems,
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  pushMock.mockReset();
});

describe("StrategyLabPage Prompt 4.2", () => {
  it("renders Prompt 4.7 review section fields", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-lab-section-review-configuration")).toBeInTheDocument();
    });

    const reviewSection = screen.getByTestId("review-configuration-summary");
    expect(within(reviewSection).getByText("Strategy")).toBeInTheDocument();
    expect(within(reviewSection).getByText("Parameter Summary")).toBeInTheDocument();
    expect(within(reviewSection).getByText("Selected Snapshot")).toBeInTheDocument();
    expect(within(reviewSection).getByText("Starting Capital")).toBeInTheDocument();
    expect(within(reviewSection).getByText("Fee Settings")).toBeInTheDocument();
    expect(within(reviewSection).getByText("Slippage Settings")).toBeInTheDocument();
    expect(within(reviewSection).getByText("Configuration Readiness")).toBeInTheDocument();
    expect(within(reviewSection).getByText("Estimated Behavior")).toBeInTheDocument();
    expect(screen.getByTestId("review-beginner-summary")).toBeInTheDocument();
  });

  it("renders contextual help toggles across major sections", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-lab-section-choose-strategy")).toBeInTheDocument();
    });

    expect(screen.getByTestId("contextual-help-choose-strategy")).toBeInTheDocument();
    expect(screen.getByTestId("contextual-help-configure-parameters")).toBeInTheDocument();
    expect(screen.getByTestId("contextual-help-configuration-snapshots")).toBeInTheDocument();
    expect(screen.getByTestId("contextual-help-review-configuration")).toBeInTheDocument();
    expect(screen.getByTestId("contextual-help-launch-backtest")).toBeInTheDocument();
    expect(screen.getByTestId("contextual-help-research-results-workspace")).toBeInTheDocument();
    expect(screen.getByTestId("contextual-help-insights-workspace")).toBeInTheDocument();
  });

  it("shows beginner launch safety message in review section", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("beginner-launch-message")).toBeInTheDocument();
    });

    expect(screen.getByTestId("beginner-launch-message")).toHaveTextContent(
      "You're about to test this strategy using historical market data. No real money will be used.",
    );
  });

  it("disables launch when required checklist items are incomplete", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Slow Period value")).toBeInTheDocument();
    });

    const slowPeriodInput = screen.getByLabelText("Slow Period value");
    await user.clear(slowPeriodInput);
    await user.type(slowPeriodInput, "2");

    await waitFor(() => {
      expect(screen.getByTestId("launch-gating-message")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Launch backtest" })).toBeDisabled();
  });

  it("launches backtest and navigates to backtests page when checklist is complete", async () => {
    const fetchMock = installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Launch backtest" })).toBeEnabled();
    });

    await user.click(screen.getByRole("button", { name: "Launch backtest" }));

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/backtests");
    });

    const runCall = fetchMock.mock.calls.find((call) => {
      const rawUrl = typeof call[0] === "string" ? call[0] : call[0] instanceof URL ? call[0].toString() : call[0].url;
      const method = call[0] instanceof Request ? call[0].method : (call[1]?.method ?? "GET");
      return new URL(rawUrl).pathname === "/backtests/run" && method === "POST";
    });

    expect(runCall).toBeTruthy();
  });

  it("supports selecting up to three completed runs in comparison selection", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("comparison-selection-list")).toBeInTheDocument();
    });

    const checkbox1 = screen.getByLabelText("Select comparison run bt-1");
    const checkbox2 = screen.getByLabelText("Select comparison run bt-2");
    const checkbox3 = screen.getByLabelText("Select comparison run bt-3");
    const checkbox4 = screen.getByLabelText("Select comparison run bt-4");

    await user.click(checkbox1);
    await user.click(checkbox2);
    await user.click(checkbox3);

    expect(checkbox1).toBeChecked();
    expect(checkbox2).toBeChecked();
    expect(checkbox3).toBeChecked();
    expect(checkbox4).toBeDisabled();

    expect(screen.getByTestId("comparison-workspace-cards")).toBeInTheDocument();
    expect(screen.getByTestId("comparison-card-bt-1")).toBeInTheDocument();
    expect(screen.getByTestId("comparison-card-bt-2")).toBeInTheDocument();
    expect(screen.getByTestId("comparison-card-bt-3")).toBeInTheDocument();
  });

  it("renders required comparison metrics and semantic highlights", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.click(screen.getByLabelText("Select comparison run bt-2"));

    const runACard = screen.getByTestId("comparison-card-bt-1");
    const runBCard = screen.getByTestId("comparison-card-bt-2");

    expect(runACard).toHaveTextContent("Strategy");
    expect(runACard).toHaveTextContent("Snapshot Name");
    expect(runACard).toHaveTextContent("Asset");
    expect(runACard).toHaveTextContent("Timeframe");
    expect(runACard).toHaveTextContent("Date Range");
    expect(runACard).toHaveTextContent("Starting Capital");
    expect(runACard).toHaveTextContent("Ending Equity");
    expect(runACard).toHaveTextContent("Net Profit / Loss");
    expect(runACard).toHaveTextContent("Total Return");
    expect(runACard).toHaveTextContent("Win Rate");
    expect(runACard).toHaveTextContent("Max Drawdown");
    expect(runACard).toHaveTextContent("Fee Drag");
    expect(runACard).toHaveTextContent("Sharpe-like");
    expect(runACard).toHaveTextContent("Configuration Readiness");
    expect(runACard).toHaveTextContent("Not available");

    expect(runACard).toHaveTextContent("Best Total Return");
    expect(runBCard).toHaveTextContent("Highest Win Rate");
    expect(runBCard).toHaveTextContent("Lowest Drawdown");
    expect(runBCard).toHaveTextContent("Lowest Fee Drag");

    expect(within(runACard).getByRole("button", { name: "Win Rate definition" })).toBeInTheDocument();
    expect(within(runACard).getByRole("button", { name: "Max Drawdown definition" })).toBeInTheDocument();
    expect(within(runACard).getByRole("button", { name: "Fee Drag definition" })).toBeInTheDocument();
    expect(within(runACard).getByRole("button", { name: "Sharpe-like definition" })).toBeInTheDocument();
  });

  it("renders deterministic key differences from selected run metrics", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.click(screen.getByLabelText("Select comparison run bt-2"));
    await user.click(screen.getByLabelText("Select comparison run bt-3"));

    const keyDifferences = screen.getByTestId("key-differences-panel");
    expect(keyDifferences).toHaveTextContent("Run A produced more trades.");
    expect(keyDifferences).toHaveTextContent("Run B experienced lower drawdown.");
    expect(keyDifferences).toHaveTextContent("Run C paid higher fees.");
  });

  it("shows beginner metric explanations and hides them in advanced mode", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));

    expect(
      screen.getByText("Total return shows overall performance as dollars and percentage from the starting capital.", {
        selector: "p",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Win rate is the share of completed trades that were profitable.", { selector: "p" })).toBeInTheDocument();

    await user.click(screen.getByRole("switch", { name: "Beginner Mode" }));

    expect(
      screen.queryByText("Total return shows overall performance as dollars and percentage from the starting capital.", {
        selector: "p",
      }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Win rate is the share of completed trades that were profitable.", { selector: "p" })).not.toBeInTheDocument();
  });

  it("keeps accessibility basics for comparison workspace controls", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-lab-section-research-results-workspace")).toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { name: "7) Research Results Workspace" })).toBeInTheDocument();
    expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    expect(screen.getByLabelText("Select comparison run bt-2")).toBeInTheDocument();
  });

  it("renders equity curve comparison and missing curve state", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.click(screen.getByLabelText("Select comparison run bt-3"));

    expect(screen.getByTestId("equity-curve-bt-1")).toBeInTheDocument();
    expect(screen.getByTestId("equity-curve-missing-bt-3")).toHaveTextContent("Not available");
  });

  it("renders deterministic observations from existing metrics", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.click(screen.getByLabelText("Select comparison run bt-2"));
    await user.click(screen.getByLabelText("Select comparison run bt-3"));

    const observationsPanel = screen.getByTestId("observations-panel");
    expect(observationsPanel).toHaveTextContent("Run A produced the highest return");
    expect(observationsPanel).toHaveTextContent("Run B experienced the smallest drawdown");
    expect(observationsPanel).toHaveTextContent("Run A traded most frequently");
  });

  it("renders What Improved facts for two selected runs", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.click(screen.getByLabelText("Select comparison run bt-2"));

    const whatImproved = screen.getByTestId("what-improved-panel");
    expect(whatImproved).toHaveTextContent("Parameter changes:");
    expect(whatImproved).toHaveTextContent("Trade count change:");
    expect(whatImproved).toHaveTextContent("Drawdown change:");
    expect(whatImproved).toHaveTextContent("Fee drag change:");
    expect(whatImproved).toHaveTextContent("Return change:");
  });

  it("shows beginner observation explanations and hides them when beginner mode is off", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.click(screen.getByLabelText("Select comparison run bt-2"));

    expect(screen.getByText("Higher total return means that run grew the starting capital more over the test period.")).toBeInTheDocument();

    await user.click(screen.getByRole("switch", { name: "Beginner Mode" }));

    expect(screen.queryByText("Higher total return means that run grew the starting capital more over the test period.")).not.toBeInTheDocument();
  });

  it("keeps accessibility basics for insights workspace", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("strategy-lab-section-insights-workspace")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));

    expect(screen.getByRole("heading", { name: "8) Insights Workspace" })).toBeInTheDocument();
    expect(screen.getByTestId("metric-trend-visuals")).toBeInTheDocument();
    expect(screen.getByTestId("observations-panel")).toBeInTheDocument();
    expect(screen.getByTestId("what-improved-panel")).toBeInTheDocument();
  });

  it("renders Experiment Log section and local-session notice", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));

    await waitFor(() => {
      expect(screen.getByTestId("experiment-log-section")).toBeInTheDocument();
    });

    expect(screen.getByTestId("experiment-log-local-session-notice")).toHaveTextContent(
      "Local session log — persistence will be added in a later phase.",
    );
  });

  it("creates an experiment log entry from current comparison", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.click(screen.getByLabelText("Select comparison run bt-2"));
    await user.click(screen.getByRole("button", { name: "Create experiment log entry" }));

    expect(screen.getByTestId("experiment-log-list")).toBeInTheDocument();
    expect(screen.getByText(/Compared Runs:/)).toBeInTheDocument();
    expect(screen.getByText(/Strategies:/)).toBeInTheDocument();
    expect(screen.getByText(/Snapshots:/)).toBeInTheDocument();
    expect(screen.getByText(/Key Differences Summary/)).toBeInTheDocument();
    expect(screen.getByText(/Observations Summary/)).toBeInTheDocument();
  });

  it("includes optional notes in experiment log entries", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.type(screen.getByLabelText("Experiment log notes"), "Focused on drawdown stability in this test.");
    await user.click(screen.getByRole("button", { name: "Create experiment log entry" }));

    const logList = screen.getByTestId("experiment-log-list");
    expect(within(logList).getByText("Focused on drawdown stability in this test.")).toBeInTheDocument();
  });

  it("stores beginner mode summary in experiment log entry", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));
    await user.click(screen.getByRole("button", { name: "Create experiment log entry" }));

    expect(screen.getByText(/Beginner Summary:/)).toBeInTheDocument();
  });

  it("keeps accessibility basics for Experiment Log controls", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select comparison run bt-1")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select comparison run bt-1"));

    await waitFor(() => {
      expect(screen.getByTestId("experiment-log-section")).toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { name: "Experiment Log" })).toBeInTheDocument();
    expect(screen.getByLabelText("Experiment log notes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create experiment log entry" })).toBeInTheDocument();
  });

  it("renders saved preset snapshot cards with required fields", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("snapshot-cards")).toBeInTheDocument();
    });

    const card = screen.getByTestId("snapshot-card-ps-ma-1");
    expect(card).toHaveTextContent("conservative-v1");
    expect(card).toHaveTextContent("Strategy: MA Crossover");
    expect(card).toHaveTextContent("Parameter Summary:");
    expect(card).toHaveTextContent("Estimated Behavior:");
    expect(card).toHaveTextContent("Created Date:");
    expect(card).toHaveTextContent("Notes:");
  });

  it("supports save flow for a new snapshot", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Snapshot Name")).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText("Snapshot Name"), "balanced-v2");
    await user.type(screen.getByLabelText("Snapshot Notes"), "Designed for smoother trend capture.");
    await user.click(screen.getByRole("button", { name: "Save snapshot" }));

    const savedCard = screen.getByTestId("snapshot-card-ps-3");
    expect(savedCard).toHaveTextContent("balanced-v2");
    expect(savedCard).toHaveTextContent("Designed for smoother trend capture.");
  });

  it("handles duplicate snapshot names gracefully", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Snapshot Name")).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText("Snapshot Name"), "conservative-v1");
    expect(screen.getByTestId("snapshot-duplicate-warning")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save snapshot" })).toBeDisabled();
  });

  it("applies a snapshot and refreshes parameter editor and validation", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Slow Period value")).toBeInTheDocument();
    });

    const slowInput = screen.getByLabelText("Slow Period value");
    await user.clear(slowInput);
    await user.type(slowInput, "2");
    expect((await screen.findAllByText("Slow Period must be at least 5.")).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Apply snapshot conservative-v1" }));

    expect(screen.queryAllByText("Slow Period must be at least 5.")).toHaveLength(0);
    expect(screen.getByTestId("parameter-form-validation")).toHaveTextContent("All current parameter values are valid.");
    expect(screen.getByLabelText("Slow Period value")).toHaveValue(60);
  });

  it("renders beginner snapshot summary text", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("snapshot-beginner-summary-ps-ma-1")).toBeInTheDocument();
    });

    expect(screen.getAllByText("What was this configuration designed for?").length).toBeGreaterThan(0);
  });

  it("supports snapshot view and select actions with accessibility labels", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "View snapshot conservative-v1" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "View snapshot conservative-v1" }));
    await user.click(screen.getByRole("button", { name: "Select snapshot conservative-v1" }));

    expect(screen.getByTestId("snapshot-card-ps-ma-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply snapshot conservative-v1" })).toBeInTheDocument();
  });

  it("renders Configuration Coach ready state by default", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-lab-section-configuration-coach")).toBeInTheDocument();
    });

    expect(screen.getByTestId("configuration-health-badge")).toHaveTextContent("Ready");
    expect(screen.getByTestId("readiness-score")).toHaveTextContent("100 / 100");
    expect(screen.getByText("Ready for Backtesting")).toBeInTheDocument();
  });

  it("renders Configuration Coach warning state for out-of-range recommendations", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Slow Period value")).toBeInTheDocument();
    });

    const slowPeriodInput = screen.getByLabelText("Slow Period value");
    await user.clear(slowPeriodInput);
    await user.type(slowPeriodInput, "300");

    expect(screen.getByTestId("configuration-health-badge")).toHaveTextContent("Needs Attention");
    expect(screen.getByTestId("readiness-score")).toHaveTextContent("90 / 100");
    expect(within(screen.getByTestId("coach-card-readiness")).getByText("Needs Attention")).toBeInTheDocument();
  });

  it("renders Configuration Coach invalid state for validation errors", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Slow Period value")).toBeInTheDocument();
    });

    const slowPeriodInput = screen.getByLabelText("Slow Period value");
    await user.clear(slowPeriodInput);
    await user.type(slowPeriodInput, "2");

    expect(screen.getByTestId("configuration-health-badge")).toHaveTextContent("Invalid");
    expect(screen.getByTestId("readiness-score")).toHaveTextContent("55 / 100");
    expect(within(screen.getByTestId("coach-card-readiness")).getByText("Invalid")).toBeInTheDocument();
  });

  it("updates What Changed section after parameter edits", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Fast Period value")).toBeInTheDocument();
    });

    const fastPeriodInput = screen.getByLabelText("Fast Period value");
    await user.clear(fastPeriodInput);
    await user.type(fastPeriodInput, "20");

    const changedCard = screen.getByTestId("what-changed-fast_period");
    expect(changedCard).toBeInTheDocument();
    expect(changedCard).toHaveTextContent("10");
    expect(changedCard).toHaveTextContent("20");
    expect(changedCard).toHaveTextContent("Fewer trading signals");
    expect(changedCard).toHaveTextContent("No issues detected.");
  });

  it("renders Estimated Behavior summary", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("coach-card-estimated-behavior")).toBeInTheDocument();
    });

    expect(screen.getByTestId("behavior-trade-frequency")).toBeInTheDocument();
    expect(screen.getByTestId("behavior-responsiveness")).toBeInTheDocument();
    expect(screen.getByTestId("behavior-noise-filtering")).toBeInTheDocument();
    expect(screen.getByTestId("behavior-trend-sensitivity")).toBeInTheDocument();
  });

  it("renders Beginner Mode Things to Know observations", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("coach-card-things-to-know")).toBeInTheDocument();
    });

    expect(screen.getAllByText(/Your configuration is valid./).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Configuration readiness is \d+ out of 100\./).length).toBeGreaterThan(0);
  });

  it("supports Advanced Details collapse and expand", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("advanced-details-collapsed")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("advanced-raw-metadata")).not.toBeVisible();
    await user.click(screen.getByText("Show validation warnings, ranges, and metadata"));
    expect(screen.getByTestId("advanced-raw-metadata")).toBeVisible();
  });

  it("keeps Configuration Coach mobile layout and accessibility basics", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("configuration-coach-cards")).toBeInTheDocument();
    });

    expect(screen.getByTestId("strategy-lab-mobile-wrapper")).toBeInTheDocument();
    expect(screen.getByTestId("coach-card-health")).toBeInTheDocument();
    expect(screen.getByTestId("coach-card-readiness")).toBeInTheDocument();
    expect(screen.getByTestId("coach-card-what-changed")).toBeInTheDocument();
    expect(screen.getByTestId("coach-card-estimated-behavior")).toBeInTheDocument();
    expect(screen.getByTestId("coach-card-why-this-matters")).toBeInTheDocument();
    expect(screen.getByTestId("coach-card-things-to-know")).toBeInTheDocument();
    expect(screen.getByTestId("coach-card-advanced-details")).toBeInTheDocument();
    expect(screen.getByText("Show validation warnings, ranges, and metadata")).toBeInTheDocument();
  });

  it("renders generated parameter editor for selected strategy", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("generated-parameter-editor")).toBeInTheDocument();
    });

    expect(screen.getByTestId("strategy-lab-section-configure-parameters")).toBeInTheDocument();
    expect(screen.getByTestId("parameter-card-fast_period")).toBeInTheDocument();
    expect(screen.getByTestId("parameter-card-slow_period")).toBeInTheDocument();
    expect(screen.getByTestId("parameter-card-ma_type")).toBeInTheDocument();
    expect(screen.getAllByText("What it Controls").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Expected Effect").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Recommended Range").length).toBeGreaterThan(0);
  });

  it("renders integer controls (slider + numeric input)", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByLabelText("Fast Period slider")).toBeInTheDocument();
    });

    expect(screen.getByLabelText("Fast Period value")).toBeInTheDocument();
  });

  it("renders enum dropdown control", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByLabelText("Moving Average Type select")).toBeInTheDocument();
    });
  });

  it("renders percentage slider control when selecting RSI strategy", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select strategy RSI Mean Reversion")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select strategy RSI Mean Reversion"));

    expect(screen.getByLabelText("Oversold Threshold slider")).toBeInTheDocument();
    expect(screen.getByLabelText("Overbought Threshold slider")).toBeInTheDocument();
  });

  it("renders decimal and boolean controls when selecting Breakout strategy", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select strategy Breakout")).toBeInTheDocument();
    });

    await user.click(screen.getByLabelText("Select strategy Breakout"));

    expect(screen.getByLabelText("Minimum Volume Multiple value")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Volume Confirmation switch" })).toBeInTheDocument();
  });

  it("renders beginner explanation blocks and collapses them in advanced mode", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("beginner-why-change-fast_period")).toBeInTheDocument();
    });

    expect(screen.getAllByText("Why would I change this?").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("switch", { name: "Beginner Mode" }));

    expect(screen.queryByTestId("beginner-why-change-fast_period")).not.toBeInTheDocument();
    expect(screen.getByTestId("advanced-beginner-collapsed-fast_period")).toBeInTheDocument();
    expect(screen.getByTestId("advanced-effect-collapsed-fast_period")).toBeInTheDocument();
  });

  it("shows live validation errors when numeric values go out of range", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Slow Period value")).toBeInTheDocument();
    });

    const slowPeriodInput = screen.getByLabelText("Slow Period value");
    await user.clear(slowPeriodInput);
    await user.type(slowPeriodInput, "2");

    expect((await screen.findAllByText("Slow Period must be at least 5.")).length).toBeGreaterThan(0);
    expect(screen.getByTestId("parameter-form-validation")).toHaveTextContent("validation issue");
  });

  it("keeps mobile-first wrappers and accessibility basics", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    expect(screen.getByTestId("strategy-lab-mobile-wrapper")).toBeInTheDocument();
    expect(screen.getByTestId("strategy-lab-sections-grid")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("generated-parameter-editor")).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByLabelText("Fast Period slider")).toBeInTheDocument();
    });

    expect(screen.getByRole("switch", { name: "Beginner Mode" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Research Journey" })).toBeInTheDocument();
    expect(screen.getByLabelText("Fast Period value")).toBeInTheDocument();
    expect(screen.getByLabelText("Moving Average Type select")).toBeInTheDocument();
  });

  it("renders the Research Journey component", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    expect(screen.getByTestId("research-journey")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Strategy Lab Research Workspace" })).toBeInTheDocument();
    expect(screen.getByText("Choose Strategy")).toBeInTheDocument();
    expect(screen.getByText("Configure Parameters")).toBeInTheDocument();
    expect(screen.getByText("Configuration Intelligence")).toBeInTheDocument();
    expect(screen.getByText("Run Backtest")).toBeInTheDocument();
    expect(screen.getByText("Compare Results")).toBeInTheDocument();
    expect(screen.getByText("Learn Why")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("strategy-cards-wrapper")).toBeInTheDocument();
    });
  });

  it("renders strategy cards from GET /strategies", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-cards-wrapper")).toBeInTheDocument();
    });

    expect(screen.getByTestId("strategy-card-title-ma_crossover")).toBeInTheDocument();
    expect(screen.getByTestId("strategy-card-title-rsi_mean_reversion")).toBeInTheDocument();
    expect(screen.getAllByText("Difficulty").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Primary Style").length).toBeGreaterThan(0);
  });

  it("supports selecting a strategy card", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByLabelText("Select strategy MA Crossover")).toBeInTheDocument();
    });

    const maCard = screen.getByLabelText("Select strategy MA Crossover");
    const rsiCard = screen.getByLabelText("Select strategy RSI Mean Reversion");

    expect(maCard).toHaveAttribute("aria-pressed", "true");
    expect(rsiCard).toHaveAttribute("aria-pressed", "false");

    await user.click(rsiCard);

    expect(maCard).toHaveAttribute("aria-pressed", "false");
    expect(rsiCard).toHaveAttribute("aria-pressed", "true");
  });

  it("renders Beginner Mode strategy explanations and supports toggle", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getAllByText("What does this strategy do?").length).toBeGreaterThan(0);
    });

    expect(
      screen.getAllByText(
        "This strategy compares short-term and long-term price averages. When the short-term average rises above the long-term average, it suggests a possible upward trend. When it falls below, it suggests the trend may be weakening.",
      ),
    ).toHaveLength(2);

    const toggle = screen.getByRole("switch", { name: "Beginner Mode" });
    await user.click(toggle);

    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.queryAllByText("What does this strategy do?")).toHaveLength(0);
  });

  it("renders Strategy Detail panel for selected strategy", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    await waitFor(() => {
      expect(screen.getByTestId("strategy-detail-panel")).toBeInTheDocument();
    });

    expect(screen.getByText("Strategy Detail")).toBeInTheDocument();
    expect(screen.getByText(/Beginner explanation:/)).toBeInTheDocument();
    expect(screen.getByText(/Default parameters:/)).toBeInTheDocument();
  });

  it("renders empty state when no strategies are available", async () => {
    installFetchMock("empty");
    render(<StrategyLabPage />);

    expect(await screen.findByTestId("strategy-empty-state")).toBeInTheDocument();
  });

  it("renders loading state while strategies are in flight", () => {
    installFetchMock("loading");
    render(<StrategyLabPage />);

    expect(screen.getByLabelText("Strategies loading")).toBeInTheDocument();
  });

  it("renders error state when strategies request fails", async () => {
    installFetchMock("error");
    render(<StrategyLabPage />);

    expect(await screen.findByTestId("strategy-error-state")).toBeInTheDocument();
    expect(screen.getByText("Failed to load strategies for test")).toBeInTheDocument();
  });

  it("includes loading state", async () => {
    installFetchMock("loading");
    render(<StrategyLabPage />);

    expect(screen.getByLabelText("Strategies loading")).toBeInTheDocument();
  });

  it("includes mobile layout wrappers and strategy accessibility basics", async () => {
    installFetchMock("success");
    render(<StrategyLabPage />);

    expect(screen.getByTestId("strategy-lab-mobile-wrapper")).toBeInTheDocument();
    expect(screen.getByTestId("strategy-lab-sections-grid")).toBeInTheDocument();

    expect(screen.getByRole("switch", { name: "Beginner Mode" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Research Journey" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "1) Choose Strategy" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByLabelText("Select strategy MA Crossover")).toBeInTheDocument();
    });

    expect(screen.getByLabelText("Select strategy MA Crossover")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "1) Choose Strategy" })).toHaveAttribute("id", "choose-strategy");
  });
});
