import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DashboardIntelligenceConsole from "@/components/domain/DashboardIntelligenceConsole";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function installFetchMock(scenario: "empty" | "with-data") {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/dashboard/intelligence-score") {
      if (scenario === "empty") {
        return jsonResponse(200, {
          score: 0,
          data_completeness: 0,
          range: url.searchParams.get("range") ?? "24h",
          generated_at: "2026-07-09T10:00:00Z",
          components: [
            { name: "Decision Outcome Quality", score: 0, weight: 30, explanation: "No data available in the selected range." },
            { name: "Paper Performance", score: 0, weight: 20, explanation: "No data available in the selected range." },
            { name: "Risk Discipline", score: 0, weight: 15, explanation: "No data available in the selected range." },
            { name: "Replay / Decision Quality", score: 0, weight: 15, explanation: "No data available in the selected range." },
            { name: "Research Improvement", score: 0, weight: 10, explanation: "No data available in the selected range." },
            { name: "Operational Health", score: 0, weight: 10, explanation: "No data available in the selected range." },
          ],
          timeline: [],
        });
      }

      return jsonResponse(200, {
        score: 82,
        data_completeness: 100,
        range: url.searchParams.get("range") ?? "24h",
        generated_at: "2026-07-09T10:00:00Z",
        components: [
          { name: "Decision Outcome Quality", score: 81, weight: 30, explanation: "Based on 2 paper trades and 1 replay quality score." },
          { name: "Paper Performance", score: 85, weight: 20, explanation: "Equity return, drawdown, and stability are derived from the selected equity window." },
          { name: "Risk Discipline", score: 72, weight: 15, explanation: "Risk rejects and drawdown are taken from 1 risk event and the current equity path." },
          { name: "Replay / Decision Quality", score: 84, weight: 15, explanation: "Average decision quality from 1 replay scores." },
          { name: "Research Improvement", score: 76, weight: 10, explanation: "Research improvement is based on 1 candidate evaluations and 1 campaigns." },
          { name: "Operational Health", score: 95, weight: 10, explanation: "Operational health is green with 0 active alerts." },
        ],
        timeline: [
          { timestamp: "2026-07-09T07:00:00Z", score: 72, equity: "1000", decision_quality: 60, research_quality: 55, operational_health: 95 },
          { timestamp: "2026-07-09T08:00:00Z", score: 78, equity: "1008", decision_quality: 70, research_quality: 65, operational_health: 95 },
          { timestamp: "2026-07-09T09:00:00Z", score: 82, equity: "1012", decision_quality: 81, research_quality: 76, operational_health: 95 },
        ],
      });
    }

    if (url.pathname === "/paper/equity-curve") {
      return jsonResponse(200, {
        account_id: "11111111-1111-1111-1111-111111111111",
        window_minutes: 720,
        interval: 15,
        starting_balance: "1000",
        current_equity: "1012",
        total_return_usd: "12",
        total_return_pct: "0.012",
        latest_point_timestamp: "2026-07-09T09:00:00Z",
        points: [
          { timestamp: "2026-07-09T07:00:00Z", equity: "1000", cash_balance: "1000", realized_pnl: "0", unrealized_pnl: "0", trade_count_at_point: 0 },
          { timestamp: "2026-07-09T09:00:00Z", equity: "1012", cash_balance: "1008", realized_pnl: "12", unrealized_pnl: "4", trade_count_at_point: 2 },
        ],
      });
    }

    if (url.pathname === "/paper/performance-summary") {
      return jsonResponse(200, {
        account_id: "11111111-1111-1111-1111-111111111111",
        starting_balance: "1000",
        current_cash_balance: "1008",
        equity: "1012",
        realized_pnl: "12",
        unrealized_pnl: "4",
        total_return_usd: "12",
        total_return_pct: "0.012",
        trade_count: 2,
        win_count: 1,
        loss_count: 0,
        win_rate: "0.5",
        latest_trade: null,
        positions: [],
        by_asset: [],
        by_strategy: [{ strategy_id: "33333333-3333-3333-3333-333333333333", trade_count: 1, win_count: 1, loss_count: 0, win_rate: "1", realized_pnl: "12" }],
      });
    }

    if (url.pathname === "/paper/pipeline-health") {
      return jsonResponse(200, {
        window_minutes: 120,
        candles: 120,
        signals_created: 12,
        hold_signals: 3,
        buy_sell_signals: 9,
        execution_candidates: 9,
        executions_attempted: 9,
        risk_events: 1,
        risk_rejected: 0,
        trades: 2,
        decision_records: 12,
        latest_rejection_reason: null,
        latest_updated_at: "2026-07-09T09:00:00Z",
        recent_activity: [],
        strategy_metrics: [],
      });
    }

    if (url.pathname === "/paper/trade-history") {
      return jsonResponse(200, {
        items: [
          {
            trade_id: "22222222-2222-2222-2222-222222222222",
            executed_at: "2026-07-09T09:00:00Z",
            asset: "BTCUSD",
            side: "sell",
            quantity: "1",
            execution_price: "112",
            notional: "112",
            signal_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            strategy_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            decision_record_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
            realized_pnl: "12",
            paper_account_id: "11111111-1111-1111-1111-111111111111",
          },
        ],
        limit: 10,
        offset: 0,
        total: 1,
        has_more: false,
      });
    }

    return jsonResponse(404, { error: { message: `Unhandled route in test: GET ${url.pathname}` } });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DashboardIntelligenceConsole", () => {
  it("renders tabs and the intelligence timeline", async () => {
    installFetchMock("with-data");
    render(<DashboardIntelligenceConsole />);

    expect(await screen.findByRole("heading", { name: "System Intelligence Console" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Intelligence Timeline" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Paper Equity" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "System intelligence timeline" })).toBeInTheDocument();
    expect(await screen.findByText("Decision Outcome Quality")).toBeInTheDocument();
    expect(await screen.findByText("Paper Performance")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Latest equity/i })).toHaveAttribute("href", "/capital");
  });

  it("renders an empty intelligence state when no timeline data exists", async () => {
    installFetchMock("empty");
    render(<DashboardIntelligenceConsole />);

    expect(await screen.findByRole("heading", { name: "System Intelligence Console" })).toBeInTheDocument();
    expect(screen.getByText("No intelligence data yet. The chart remains flat until paper, decision, and research evidence accumulate.")).toBeInTheDocument();
  });

  it("keeps existing dashboard sections accessible via tabs", async () => {
    installFetchMock("with-data");
    render(<DashboardIntelligenceConsole />);

    expect(await screen.findByRole("heading", { name: "System Intelligence Console" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Paper Equity" }));
    expect(await screen.findByRole("heading", { name: "Paper Equity Curve" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Strategy Performance" }));
    expect(await screen.findByRole("heading", { name: "Paper Performance Summary" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Pipeline Flow" }));
    expect(await screen.findByRole("heading", { name: "Paper Pipeline Flow" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Recent Activity" }));
    expect(await screen.findByRole("heading", { name: "Paper Trade History" })).toBeInTheDocument();
  });

  it("switches ranges for the intelligence timeline", async () => {
    const fetchMock = installFetchMock("with-data");
    render(<DashboardIntelligenceConsole />);

    expect(await screen.findByRole("heading", { name: "System Intelligence Console" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Last 90 days" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/dashboard/intelligence-score?range=90d"), expect.any(Object));
    });
  });
});