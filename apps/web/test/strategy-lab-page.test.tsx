import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import StrategyLabPage from "@/app/strategy-lab/page";

vi.mock("next/dynamic", () => ({
  default: () => function MockDynamicChart(props: {
    run?: unknown;
    trades?: unknown[];
    onLayerToggle: (layer: string) => void;
    onTradeSelect: (trade: unknown) => void;
    onAnalysisRangeChange: (range: [number, number]) => void;
  }) {
    if (props.run) return <div aria-label="Mock capital chart">Independent capital axis</div>;
    return <div aria-label="Mock replay chart">
      <button onClick={() => props.onLayerToggle("buy_limit")}>Toggle BUY limit layer</button>
      <button onClick={() => props.onTradeSelect(props.trades?.[0])}>Select first chart trade</button>
      <button onClick={() => props.onAnalysisRangeChange([0, 2])}>Drag select candles</button>
    </div>;
  },
}));

const dataset = {
  id: "btc_15m", name: "BTC 15m historical candles", asset: "BTC", exchange: "offline_csv", interval: "15m",
  candle_count: 3, first_timestamp: "2026-07-05T17:30:00+00:00", last_timestamp: "2026-07-05T18:00:00+00:00",
  missing_candles: 0, duplicate_timestamps: 0, invalid_rows: 0,
};

const replay = {
  dataset: { ...dataset, research_period: "training" }, strategy_version: "002",
  parameters: { entry_offset_pct: "0.01", initial_stop_pct: "0.01", profit_activation_pct: "0.03", trailing_distance_pct: "0.01", required_declining_candles: 2, fee_pct: "0.002", slippage_pct: "0.0005", initial_capital: "100", trade_deployment_pct: "100", profit_compound_pct: "100", profit_withdrawal_pct: "0", profit_tax_reserve_pct: "0" },
  candles: [
    { timestamp: "2026-07-05T17:30:00+00:00", open: "100", high: "102", low: "99", close: "101", volume: "4" },
    { timestamp: "2026-07-05T17:45:00+00:00", open: "101", high: "103", low: "98", close: "102", volume: "5" },
    { timestamp: "2026-07-05T18:00:00+00:00", open: "102", high: "104", low: "97", close: "98", volume: "6" },
  ],
  events: [
    { candle_index: 0, timestamp: "2026-07-05T17:30:00+00:00", kind: "buy_limit", price: "99", reason: null },
    { candle_index: 1, timestamp: "2026-07-05T17:45:00+00:00", kind: "filled_order", price: "99", reason: null },
    { candle_index: 1, timestamp: "2026-07-05T17:45:00+00:00", kind: "entry", price: "99", reason: null },
    { candle_index: 1, timestamp: "2026-07-05T17:45:00+00:00", kind: "protective_stop", price: "98", reason: null },
    { candle_index: 2, timestamp: "2026-07-05T18:00:00+00:00", kind: "profit_activation", price: "102", reason: null },
    { candle_index: 2, timestamp: "2026-07-05T18:00:00+00:00", kind: "trailing_floor", price: "101", reason: null },
    { candle_index: 2, timestamp: "2026-07-05T18:00:00+00:00", kind: "exit", price: "98", reason: "declining_closes" },
  ],
  trades: [{
    entry_candle_index: 1, entry_timestamp: "2026-07-05T17:45:00+00:00", exit_candle_index: 2, exit_timestamp: "2026-07-05T18:00:00+00:00",
    exit_reason: "declining_closes", effective_entry_price: "99.25", effective_exit_price: "97.75", net_return_pct: "-0.015", net_pnl: "-1.50", mfe_pct: "4.2", mae_pct: "-2.2", holding_candles: 2,
  }],
  equity_curve: [],
  capital_curve: [
    { timestamp: "2026-07-05T17:30:00+00:00", trading_capital: "100", withdrawn_profit: "0", total_economic_value: "100", buy_and_hold: "100" },
    { timestamp: "2026-07-05T17:45:00+00:00", trading_capital: "100", withdrawn_profit: "0", total_economic_value: "100", buy_and_hold: "101" },
    { timestamp: "2026-07-05T18:00:00+00:00", trading_capital: "98.5", withdrawn_profit: "0", total_economic_value: "98.5", buy_and_hold: "99" },
  ],
  metrics: {
    total_trades: 1, winning_trades: 0, losing_trades: 1, net_return_pct: "-1.5", max_drawdown_pct: "1.5", win_rate_pct: "0", fees_paid: "0.40", estimated_slippage: "0.10",
    starting_capital: "100", ending_trading_capital: "98.50", withdrawn_profit: "0", tax_reserve: "0", total_economic_value: "98.50", buy_and_hold_value: "99", buy_and_hold_return_pct: "-1", outperformance: "-0.50", verdict: "UNPROFITABLE",
  },
};

const patternAnalysis = {
  analysis_id: "analysis_test", engine_version: "1.0.0", feature_version: "1.0.0", dataset_id: "btc_15m", dataset_hash: "abc",
  selected_range: [0, 2], partition: "training", elapsed_ms: "4.2", content_hash: "def", data_quality: [],
  detector_versions: { volatility: "1.0.0" }, configuration: { fee_pct: "0.002", slippage_pct: "0.0005" },
  annotations: [{ annotation_id: "ann_0001", pattern_id: "volatility_contraction_v1", start_time: replay.candles[0].timestamp, end_time: replay.candles[2].timestamp, label: "Volatility Contraction", chart_region: "price", details_ref: "finding_0001" }],
  findings: [{
    finding_id: "finding_0001", detector_id: "volatility_contraction_v1", detector_version: "1.0.0", category: "INSUFFICIENT_EVIDENCE",
    group: "Volatility", pattern_name: "Volatility Contraction", start_index: 0, end_index: 2,
    start_time: replay.candles[0].timestamp, end_time: replay.candles[2].timestamp,
    measurements: { range_contraction_pct: "42.6" }, thresholds: { maximum_range_ratio: "0.75" },
    evidence: ["range_contraction_pct=42.6"], conditions: ["range_ratio <= 0.75"], sufficient_evidence: false,
    recurrence: [1, 2, 4, 8, 16].flatMap((forward_horizon) => ["training", "validation", "final_test", "entire_dataset"].map((partition) => ({ partition, forward_horizon, occurrence_count: partition === "entire_dataset" ? 3 : 1, average_forward_return: "0.01", median_forward_return: "0.01", positive_return_frequency: "0.67", net_positive_frequency: "0.50", maximum_favorable_excursion: "0.02", maximum_adverse_excursion: "-0.01", target_before_stop_frequency: "0.60", confidence_interval_95: null, sufficient_evidence: false }))),
  }],
};

function installFetchMock() {
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const path = new URL(rawUrl).pathname;
    const body = path === "/strategy-lab/datasets" ? { items: [dataset] } : path.includes("pattern-intelligence") ? patternAnalysis : replay;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("Visual Strategy Laboratory Phase 2B", () => {
  it("explains chart objects and metrics in the Help dialog", async () => {
    installFetchMock();
    const user = userEvent.setup();
    render(<StrategyLabPage />);

    await user.click(screen.getByRole("button", { name: "What am I looking at?" }));
    const dialog = screen.getByRole("dialog", { name: "What am I looking at?" });
    expect(within(dialog).getByText("BUY limit / replacement")).toBeInTheDocument();
    expect(within(dialog).getByText("Initial stop")).toBeInTheDocument();
    expect(within(dialog).getByText("Trailing floor")).toBeInTheDocument();
    expect(within(dialog).getByText("Max drawdown")).toBeInTheDocument();
    expect(within(dialog).getByText(/Capital and equity are intentionally kept out/)).toBeInTheDocument();
  });

  it("reports synchronized visible-layer count", async () => {
    installFetchMock();
    const user = userEvent.setup();
    render(<StrategyLabPage />);

    expect(screen.getByText("9 / 9")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Mock replay chart")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Toggle BUY limit layer" }));
    expect(screen.getByText("8 / 9")).toBeInTheDocument();
  });

  it("shows the engine-ordered timeline when a completed trade is selected", async () => {
    installFetchMock();
    const user = userEvent.setup();
    render(<StrategyLabPage />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Select first chart trade" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Select first chart trade" }));

    expect(screen.getByText("Trade Inspector")).toBeInTheDocument();
    expect(screen.getByText("Held 2 candles")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Complete Engine Trace" }));
    expect(screen.getByText("BUY LIMIT")).toBeInTheDocument();
    expect(screen.getByText("BUY FILLED")).toBeInTheDocument();
    expect(screen.getByText("STOP INITIALIZED")).toBeInTheDocument();
    expect(screen.getByText("PROFIT MODE")).toBeInTheDocument();
    expect(screen.getByText("TRAILING UPDATED")).toBeInTheDocument();
    expect(screen.getByText("SELL FILLED")).toBeInTheDocument();
  });

  it("offers clarified historical periods and immutable CSV upload", async () => {
    installFetchMock();
    const user = userEvent.setup();
    render(<StrategyLabPage />);

    const period = screen.getByRole("combobox", { name: /Historical Test Period/ });
    expect(within(period).getByRole("option", { name: "Training · develop strategies" })).toBeInTheDocument();
    expect(within(period).getByRole("option", { name: "Final Test · out-of-sample" })).toBeInTheDocument();
    expect(within(period).getByRole("option", { name: "Entire Dataset · exploratory only" })).toBeInTheDocument();
    expect(screen.getByText(/Separation reduces overfitting/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Upload Candle CSV" }));
    expect(screen.getByRole("dialog", { name: "Upload Candle CSV" })).toBeInTheDocument();
    expect(screen.getByText(/Required columns: timestamp, open, high, low, close, volume/)).toBeInTheDocument();
  });

  it("runs deterministic playback controls over the existing replay", async () => {
    installFetchMock();
    const user = userEvent.setup();
    render(<StrategyLabPage />);
    await waitFor(() => expect(screen.getByLabelText("Mock capital chart")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Playback Mode" }));
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous Candle" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next Candle" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Replay speed" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run Simulation" }));
    expect(screen.getByRole("button", { name: "Stop Simulation" })).toBeInTheDocument();
    const entryOffset = screen.getByRole("spinbutton", { name: "Entry offset" });
    await user.clear(entryOffset);
    await user.type(entryOffset, "0.02");
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeInTheDocument();
  });

  it("saves a dated experiment and reopens it from the research journal", async () => {
    installFetchMock();
    const user = userEvent.setup();
    render(<StrategyLabPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Save run" })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Save run" }));
    expect(screen.getByText("Research journal")).toBeInTheDocument();
    const journalEntry = screen.getByRole("button", { name: /BTC · #002 · UNPROFITABLE/ });
    expect(journalEntry).toBeInTheDocument();
    await user.click(journalEntry);
    expect(screen.getByText("BTC · #002 · UNPROFITABLE")).toBeInTheDocument();
  });

  it("drag-selects candles and renders backend Pattern Intelligence evidence", async () => {
    installFetchMock();
    const user = userEvent.setup();
    render(<StrategyLabPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Drag select candles" })).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Drag select candles" }));
    expect(screen.getByText("Candles 0–2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Analyze Selection" }));

    await waitFor(() => expect(screen.getByRole("button", { name: /Volatility Contraction/ })).toBeInTheDocument());
    expect(screen.getByText("Why was this detected?")).toBeInTheDocument();
    expect(screen.getByText("range_ratio <= 0.75")).toBeInTheDocument();
    expect(screen.getByText("range contraction pct")).toBeInTheDocument();
    expect(screen.getByText(/fee 0.002 \+ slippage 0.0005 per side/)).toBeInTheDocument();

    const fetchMock = vi.mocked(fetch);
    const analysisCall = fetchMock.mock.calls.find(([input]) => String(input).includes("analyze-selection"));
    expect(JSON.parse(String(analysisCall?.[1]?.body))).toMatchObject({ selected_start_index: 0, selected_end_index: 2, dataset_id: "btc_15m" });
  });
});