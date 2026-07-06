import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import StrategyLabPage from "@/app/strategy-lab/page";

describe("StrategyLabPage Prompt 4.1", () => {
  it("renders the research workspace page shell", () => {
    render(<StrategyLabPage />);

    expect(screen.getByRole("heading", { name: "Strategy Lab Research Workspace" })).toBeInTheDocument();
    expect(screen.getByText("Build confidence with historical testing first, then review results before any execution phase.")).toBeInTheDocument();
  });

  it("supports the Beginner Mode toggle", async () => {
    render(<StrategyLabPage />);
    const user = userEvent.setup();

    const toggle = screen.getByRole("switch", { name: "Beginner Mode" });
    expect(toggle).toHaveAttribute("aria-checked", "true");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("renders the beginner welcome card with glossary-aligned terms", () => {
    render(<StrategyLabPage />);

    expect(screen.getByTestId("beginner-welcome-card")).toBeInTheDocument();
    expect(
      screen.getByText(
        "This workspace helps you safely experiment with trading strategies using historical data before risking real money.",
      ),
    ).toBeInTheDocument();

    expect(screen.getByText(/Strategy:/)).toBeInTheDocument();
    expect(screen.getByText(/Backtest:/)).toBeInTheDocument();
    expect(screen.getByText(/Parameter:/)).toBeInTheDocument();
    expect(screen.getByText(/Starting Capital:/)).toBeInTheDocument();
  });

  it("renders all six placeholder sections", () => {
    render(<StrategyLabPage />);

    expect(screen.getByRole("heading", { name: "1) Choose Strategy" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "2) Configure Parameters" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "3) Configuration Intelligence" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "4) Run Backtest" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "5) Compare Results" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "6) Learn Why" })).toBeInTheDocument();
  });

  it("includes mobile-first layout wrappers and loading placeholders", () => {
    render(<StrategyLabPage />);

    expect(screen.getByTestId("strategy-lab-mobile-wrapper")).toBeInTheDocument();
    expect(screen.getByTestId("strategy-lab-sections-grid")).toBeInTheDocument();
    expect(screen.getByLabelText("1) Choose Strategy loading placeholder")).toBeInTheDocument();
    expect(screen.getByLabelText("4) Run Backtest loading placeholder")).toBeInTheDocument();
  });

  it("provides practical accessibility basics", () => {
    render(<StrategyLabPage />);

    expect(screen.getByRole("switch", { name: "Beginner Mode" })).toBeInTheDocument();
    expect(screen.getAllByRole("region").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "1) Choose Strategy" })).toHaveAttribute("id", "choose-strategy");
  });
});
