import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MissionControlIntelligenceCenter from "@/components/domain/MissionControlIntelligenceCenter";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function buildPayload(range: string, severity: "green" | "yellow" | "red" = "green") {
  return {
    version: "v1",
    range,
    generated_at: "2026-07-09T10:00:00Z",
    current_score: range === "all" ? 79 : 82,
    delta_label: range === "all" ? "+1 this period" : "+4 this week",
    confidence: "High",
    trend: {
      direction: range === "all" ? "flat" : "up",
      label: range === "all" ? "Stable" : "Improving",
      delta_label: range === "all" ? "+1 this period" : "+4 this week",
      confidence: "High",
    },
    history: [
      { timestamp: "2026-07-09T08:00:00Z", score: 78, paper_equity: "104000.00", paper_pnl: "0.00", signals: 20, trades: 4, decision_count: 40, health: 80 },
      { timestamp: "2026-07-09T09:00:00Z", score: 80, paper_equity: "104200.00", paper_pnl: "200.00", signals: 30, trades: 6, decision_count: 60, health: 82 },
      { timestamp: "2026-07-09T10:00:00Z", score: range === "all" ? 79 : 82, paper_equity: "104523.55", paper_pnl: "523.55", signals: 42, trades: 8, decision_count: 82, health: 84 },
    ],
    timeline_events: [
      {
        event_id: "validation-1",
        timestamp: "2026-07-09T09:00:00Z",
        title: "Validation Run Started",
        description: "Validation run is now active.",
        related_validation_run: "11111111-1111-1111-1111-111111111111",
        health_at_that_moment: 80,
        paper_equity: "104200.00",
        paper_pnl: "200.00",
        signals: 30,
        trades: 6,
        decision_count: 60,
        severity,
        category: "system",
        event_type: "VALIDATION_RUN_STARTED",
        metadata: {},
      },
      {
        event_id: "validation-2",
        timestamp: "2026-07-09T10:00:00Z",
        title: "Champion Changed",
        description: "The lead strategy changed.",
        related_validation_run: "11111111-1111-1111-1111-111111111111",
        health_at_that_moment: 84,
        paper_equity: "104523.55",
        paper_pnl: "523.55",
        signals: 42,
        trades: 8,
        decision_count: 82,
        severity: "purple",
        category: "research",
        event_type: "CHAMPION_STRATEGY_CHANGED",
        metadata: {},
      },
    ],
    metric_breakdown: [
      {
        name: "Prediction Quality",
        score: 82,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [74, 76, 78, 79, 81, 82],
        details: "Validation health, signal generation, and decision activity.",
      },
      {
        name: "Risk Discipline",
        score: 73,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [68, 69, 70, 71, 72, 73],
        details: "Alerts, operational risk, and validation stability.",
      },
      {
        name: "Research Activity",
        score: 77,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [70, 71, 73, 74, 76, 77],
        details: "Campaigns, laboratory runs, evolution, and memory growth.",
      },
      {
        name: "Execution Health",
        score: 81,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [73, 75, 77, 78, 80, 81],
        details: "Paper trade throughput and decision execution velocity.",
      },
      {
        name: "Infrastructure Health",
        score: 94,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [90, 91, 92, 93, 94, 94],
        details: "API, worker, database, and research adapter health.",
      },
      {
        name: "Paper Trading Health",
        score: 84,
        trend: { direction: "up", label: "Improving", delta_label: "+4 this week", confidence: "High" },
        sparkline: [76, 78, 79, 81, 83, 84],
        details: "Paper equity, fills, and overall proving throughput.",
      },
    ],
    operations: {
      overall_health: severity === "red" ? "red" : severity === "yellow" ? "yellow" : "green",
      run_status: {
        run_id: "run-1",
        started_at: "2026-07-09T00:00:00Z",
        expected_end: "2026-07-12T00:00:00Z",
        uptime: "24:00:00",
        current_phase: "researching",
        health_status: severity === "red" ? "red" : severity === "yellow" ? "yellow" : "green",
      },
      system_health: {
        api: { state: "green", detail: "API responsive" },
        orchestrator: { state: severity === "red" ? "red" : severity === "yellow" ? "yellow" : "green", detail: severity === "red" ? "Heartbeat stale" : "Heartbeat active" },
        database: { state: "green", detail: "Database connected" },
        research_agent: { state: "green", detail: "OpenAI research adapter available" },
      },
      research_status: { current_campaign: "Campaign Alpha", current_champion: "RSI Mean Reversion", campaign_status: "RUNNING" },
      monitoring: {
        candles_processed: 120000,
        signals_generated: 900,
        paper_trades_executed: 120,
        decision_records_created: 900,
        replay_count: 140,
        candidate_count: 80,
        campaign_count: 3,
        laboratory_runs: 25,
        evolution_count: 44,
        current_champion: "RSI Mean Reversion",
        paper_equity: "104523.55",
        signals_today: 42,
        trades_today: 8,
        research_memory_growth: 350,
      },
      alerts: severity === "green" ? [] : [{ code: "worker_stopped", severity, message: "Worker stopped" }],
    },
    total_managed_capital: "125.00",
    validation_runs: [
      {
        validation_run_id: "11111111-1111-1111-1111-111111111111",
        name: "72h Proving",
        objective: "Validate stability",
        duration_hours: 72,
        status: "RUNNING",
        started_at: "2026-07-09T00:00:00Z",
        expected_end_at: "2026-07-12T00:00:00Z",
        completed_at: null,
        paper_capital: "100000",
        enabled_strategies: ["MA Crossover", "RSI"],
        enabled_research_agents: ["Baseline", "OpenAI Sandbox"],
        enabled_research_features: ["Laboratory", "Evolution"],
        health_score: 88,
        result_status: "INCOMPLETE",
      },
    ],
    selected_validation_run_id: "11111111-1111-1111-1111-111111111111",
    notes: "Mission Control Intelligence Center V1 is a deterministic placeholder built from available operational metrics. It is informational only and does not change trading, research, or allocation behavior.",
  };
}

function installFetchMock(scenario: "healthy" | "empty" | "degraded" = "healthy") {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl);

    if (url.pathname === "/mission-control/profit") {
      return jsonResponse(200, {
        range: url.searchParams.get("range") ?? "24h",
        mode: url.searchParams.get("mode") ?? "paper",
        start_at: "2026-07-09T00:00:00Z",
        end_at: "2026-07-09T10:00:00Z",
        starting_equity: "100000.00",
        ending_equity: "104523.55",
        gross_profit: "700.00",
        gross_loss: "176.45",
        realized_pnl: "523.55",
        unrealized_pnl: "120.00",
        fees: "12.50",
        fees_available: true,
        net_profit: "523.55",
        total_economic_pnl: "643.55",
        return_percent: "0.52",
        peak_equity: "104700.00",
        max_drawdown_amount: "95.00",
        max_drawdown_percent: "0.09",
        winning_trades: 6,
        losing_trades: 2,
        breakeven_trades: 0,
        win_rate: "75.00",
        profit_factor: "3.97",
        average_win: "116.67",
        average_loss: "88.22",
        largest_win: "200.00",
        largest_loss: "-95.00",
        trade_count: 8,
        open_position_count: 2,
        equity_series: [],
        profit_series: [],
        annotations: [],
        source_counts: { paper_accounts: 1, paper_trades: 8 },
        data_completeness: 100,
        calculation_explanation: "Profit derived from paper trades and marked positions.",
        generated_at: "2026-07-09T10:00:00Z",
      });
    }

    if (url.pathname === "/mission-control/intelligence") {
      if (scenario === "empty") {
        return jsonResponse(200, buildPayload(url.searchParams.get("range") ?? "24h"));
      }
      if (scenario === "degraded") {
        return jsonResponse(200, buildPayload(url.searchParams.get("range") ?? "24h", "yellow"));
      }
      return jsonResponse(200, buildPayload(url.searchParams.get("range") ?? "24h"));
    }

    if (url.pathname === "/mission-control/intelligence/history") {
      return jsonResponse(200, {
        range: url.searchParams.get("range") ?? "24h",
        dimension: null,
        generated_at: "2026-07-09T10:00:00Z",
        points: [
          {
            snapshot_id: "snapshot-1",
            captured_at: "2026-07-09T08:00:00Z",
            bucket_start: "2026-07-09T08:00:00Z",
            bucket_end: "2026-07-09T08:15:00Z",
            overall_score: 78,
            confidence: "High",
            data_completeness: 100,
            market_awareness_score: 75,
            decision_quality_score: 80,
            execution_reliability_score: 79,
            risk_discipline_score: 74,
            research_progress_score: 73,
            adaptation_rate_score: 72,
            operational_health_score: 90,
            capital_efficiency_score: 81,
            profit_performance_score: 77,
            paper_net_profit: "0.00",
            live_net_profit: "0.00",
            combined_net_profit: "0.00",
            paper_equity: "104000.00",
            live_equity: "0.00",
            combined_equity: "104000.00",
            realized_pnl: "0.00",
            unrealized_pnl: "0.00",
            fees: "0.00",
            drawdown_percent: "0.00",
            source_counts: { paper_trades: 4, decision_records: 40 },
            annotations: [],
            schema_version: "v1",
          },
          {
            snapshot_id: "snapshot-2",
            captured_at: "2026-07-09T10:00:00Z",
            bucket_start: "2026-07-09T10:00:00Z",
            bucket_end: "2026-07-09T10:15:00Z",
            overall_score: 82,
            confidence: "High",
            data_completeness: 100,
            market_awareness_score: 80,
            decision_quality_score: 82,
            execution_reliability_score: 83,
            risk_discipline_score: 75,
            research_progress_score: 77,
            adaptation_rate_score: 76,
            operational_health_score: 94,
            capital_efficiency_score: 84,
            profit_performance_score: 82,
            paper_net_profit: "523.55",
            live_net_profit: "0.00",
            combined_net_profit: "523.55",
            paper_equity: "104523.55",
            live_equity: "0.00",
            combined_equity: "104523.55",
            realized_pnl: "523.55",
            unrealized_pnl: "120.00",
            fees: "12.50",
            drawdown_percent: "0.09",
            source_counts: { paper_trades: 8, decision_records: 82 },
            annotations: [
              {
                event_type: "risk_guardrail_triggered",
                title: "Guardrail Triggered",
                required_action: "operator_review",
                metadata: { severity: "high" },
              },
            ],
            schema_version: "v1",
          },
        ],
      });
    }

    if (url.pathname === "/exchange-connections") {
      return jsonResponse(200, {
        items: [
          {
            exchange_connection_id: "11111111-1111-1111-1111-111111111111",
            provider: "coinbase_advanced",
            provider_label: "Coinbase Advanced",
            connection_name: "Primary Coinbase",
            environment: "production",
            status: "connected",
            credentials_valid: true,
            credential_mask: { api_key_name: "******1234", private_key: "********", passphrase: "********" },
            api_permissions: ["view", "trade"],
            account_status: "active",
            balances: [],
            total_equity_usd: "100.00",
            last_successful_sync_at: "2026-07-09T10:00:00Z",
            last_heartbeat_at: "2026-07-09T10:00:00Z",
            last_api_error: null,
            readiness: {
              verdict: "READY_FOR_PREVIEW",
              checked_at: "2026-07-09T10:00:00Z",
              checks: [],
            },
            updated_at: "2026-07-09T10:00:00Z",
          },
        ],
      });
    }

    if (url.pathname === "/crypto-order-previews") {
      return jsonResponse(200, {
        items: [
          {
            crypto_order_preview_id: "preview-1",
            preview_version: 1,
            status: "PREVIEW_READY",
            provider: "coinbase_advanced",
            environment: "production",
            product_id: "BTC-USD",
            side: "BUY",
            order_type: "MARKET",
            quote_size: "5.00",
            base_size: null,
            requested_amount: "5.00",
            requested_amount_currency: "USD",
            readiness_verdict: "READY_FOR_PREVIEW",
            risk_verdict: "approved_for_preview",
            risk_explanation: "Risk engine approved the proposed preview.",
            strategy_id: null,
            strategy_name: null,
            decision_record_id: null,
            validation_run_id: null,
            preview_id: "preview-123",
            estimated_average_price: "10000.00",
            estimated_total_value: "5.10",
            estimated_base_size: "0.0005",
            estimated_quote_size: "5.00",
            estimated_fee: "0.10",
            estimated_fee_currency: "USD",
            estimated_slippage: "0.01",
            estimated_commission_total: "0.10",
            best_bid: "9995.00",
            best_ask: "10005.00",
            available_balance_before: "100.00",
            estimated_balance_after: "94.90",
            failure_reason: null,
            warning_messages: [],
            exchange_response_summary: {},
            expires_at: "2026-07-09T10:05:00Z",
            generated_by: "operator",
            audit_correlation_id: null,
            order_submitted: false,
            execution_available: false,
            created_at: "2026-07-09T10:00:00Z",
            updated_at: "2026-07-09T10:00:00Z",
            refreshed_from_preview_id: null,
          },
        ],
      });
    }

    return jsonResponse(404, {
      error: {
        message: `Unhandled route in test: GET ${url.pathname}`,
      },
    });
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("MissionControlIntelligenceCenter", () => {
  it("renders the hero intelligence timeline, score cards, and recent timeline", async () => {
    installFetchMock("healthy");

    render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();
    expect(await screen.findByText("System Intelligence")).toBeInTheDocument();
    expect((await screen.findAllByText("82 / 100")).length).toBeGreaterThan(0);
    expect(screen.getByText("Prediction Quality")).toBeInTheDocument();
    expect(screen.getByText("Infrastructure Health")).toBeInTheDocument();
    expect(screen.getByText("Order Preview")).toBeInTheDocument();
    expect(screen.getByText("Latest Status")).toBeInTheDocument();
    expect(screen.getByText("Recent Timeline")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Intelligence timeline chart" })).toBeInTheDocument();
  });

  it("switches intelligence range tabs and refetches the selected range", async () => {
    const fetchMock = installFetchMock("healthy");

    render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "7D" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) => {
          const input = call[0];
          const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
          return new URL(rawUrl).pathname === "/mission-control/intelligence" && new URL(rawUrl).searchParams.get("range") === "7d";
        }),
      ).toBe(true);
    });

    fireEvent.click(await screen.findByRole("button", { name: "ALL" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) => {
          const input = call[0];
          const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
          return new URL(rawUrl).pathname === "/mission-control/intelligence" && new URL(rawUrl).searchParams.get("range") === "all";
        }),
      ).toBe(true);
    });
  });

  it("keeps accordion sections open together", async () => {
    installFetchMock("healthy");

    render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Research/i }));
    fireEvent.click(screen.getByRole("button", { name: /Alerts/i }));
    fireEvent.click(screen.getByRole("button", { name: /Monitoring/i }));

    expect(screen.getByText("Current Campaign")).toBeInTheDocument();
    expect(screen.getByText("No active alerts.")).toBeInTheDocument();
    expect(screen.getAllByText("Validation Run Started").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Open Capital Ledger/i })).toHaveAttribute("href", "/capital");
  });

  it("keeps mobile event detail closed by default and closed after refresh until reselected", async () => {
    const fetchMock = installFetchMock("healthy");
    Object.defineProperty(window, "innerWidth", { value: 375, configurable: true, writable: true });

    const { rerender } = render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Timeline event detail" })).not.toBeInTheDocument();

    const snapshotButtons = await screen.findAllByTitle(/Snapshot .*Score/i);
    fireEvent.click(snapshotButtons[0]);

    expect(await screen.findByRole("dialog", { name: "Timeline event detail" })).toBeInTheDocument();
    expect(screen.getByText("Timestamp")).toBeInTheDocument();
    expect(screen.getByText("Trades / Fills")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Timeline event detail" })).not.toBeInTheDocument();
    });

    rerender(<MissionControlIntelligenceCenter />);

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(0);
    });
    expect(screen.queryByRole("dialog", { name: "Timeline event detail" })).not.toBeInTheDocument();
  });

  it("renders persisted snapshot annotations in the event detail drawer", async () => {
    installFetchMock("healthy");

    render(<MissionControlIntelligenceCenter />);

    expect(await screen.findByRole("heading", { name: "Mission Control" })).toBeInTheDocument();
    const snapshotButtons = await screen.findAllByTitle(/Snapshot .*Score/i);
    fireEvent.click(snapshotButtons[snapshotButtons.length - 1]);

    expect(await screen.findByRole("dialog", { name: "Timeline event detail" })).toBeInTheDocument();
    expect(screen.getByText("Guardrail Triggered")).toBeInTheDocument();
  });
});
