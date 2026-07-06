import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import StrategyLabPage from "@/app/strategy-lab/page";

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

  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname !== "/strategies") {
      return jsonResponse(404, {
        error: {
          message: `Unhandled route: ${url.pathname}`,
        },
      });
    }

    if (scenario === "loading") {
      return pending;
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
      items: [
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
      ],
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StrategyLabPage Prompt 4.2", () => {
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
    expect(screen.getByText("Parameter editor will appear in the next step.")).toBeInTheDocument();
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

  it("includes mobile layout wrappers and accessibility basics", async () => {
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
