import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProcessTraceView from "@/components/workflow/ProcessTraceView";
import type { ProcessTraceEvent } from "@/lib/api/capital-campaigns";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const CAMPAIGN = {
  campaign_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  version: 3,
  name: "BTC Proving Campaign",
  status: "READY",
  allowed_instruments: ["BTC-USD"],
  created_at: "2026-08-01T00:00:00Z",
};

function traceEvent(overrides: Partial<ProcessTraceEvent>): ProcessTraceEvent {
  return {
    schema_version: "v1",
    process_stage: "OBSERVE_MARKET",
    gate: "market_evidence_gate",
    verdict: "PASS",
    reason: null,
    instrument: "BTC-USD",
    candidate_id: "candidate-1",
    decision_record_id: null,
    attempt_id: null,
    cycle_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    observed_value: null,
    threshold: null,
    next: null,
    timestamp: "2026-08-07T21:00:00Z",
    ...overrides,
  };
}

// Deliberately out of chronological order in the array to prove the
// component sorts by timestamp rather than trusting array order.
const BTC_TRACE: ProcessTraceEvent[] = [
  traceEvent({
    process_stage: "DETERMINE_OPPORTUNITY",
    gate: "net_edge_gate",
    verdict: "REJECT",
    reason: "non_positive_net_edge",
    candidate_id: "candidate-btc",
    decision_record_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    observed_value: "-0.0018",
    threshold: "0",
    next: "terminal",
    timestamp: "2026-08-07T21:00:03Z",
  }),
  traceEvent({
    process_stage: "OBSERVE_MARKET",
    gate: "market_evidence_gate",
    verdict: "PASS",
    reason: "fresh",
    candidate_id: "candidate-btc",
    timestamp: "2026-08-07T21:00:01Z",
    next: "determine_market_state",
  }),
  traceEvent({
    process_stage: "DETERMINE_MARKET_STATE",
    gate: "strategy_evidence_gate",
    verdict: "PASS",
    reason: "strategy_identity=ma_crossover@1",
    candidate_id: "candidate-btc",
    decision_record_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    timestamp: "2026-08-07T21:00:02Z",
    next: "determine_opportunity",
  }),
];

const ETH_TRACE: ProcessTraceEvent[] = [
  traceEvent({
    process_stage: "OBSERVE_MARKET",
    gate: "market_evidence_gate",
    verdict: "REJECT",
    reason: "stale_market_data",
    instrument: "ETH-USD",
    candidate_id: "candidate-eth",
    timestamp: "2026-08-07T21:00:00Z",
    next: "terminal",
  }),
];

const CYCLE = {
  cycle_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  state: "COMPLETE",
  cycle_kind: "campaign",
  capital_campaign_id: CAMPAIGN.campaign_id,
  capital_campaign_version: CAMPAIGN.version,
  started_at: "2026-08-07T21:00:00Z",
  completed_at: "2026-08-07T21:00:05Z",
  termination_stage: "hold_no_package_created",
  failure_reason: null,
  deterministic_explanation: [],
  process_trace: [...BTC_TRACE, ...ETH_TRACE],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockFetch(options?: { historyItems?: unknown[]; domainStatus?: number; historyStatus?: number }) {
  const calls: Array<{ url: string; method: string }> = [];
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    calls.push({ url: rawUrl, method: init?.method ?? "GET" });
    const url = new URL(rawUrl);

    if (url.pathname === "/capital-campaigns/domain") {
      if (options?.domainStatus && options.domainStatus !== 200) {
        return jsonResponse(options.domainStatus, { error: { message: "domain unavailable" } });
      }
      return jsonResponse(200, { items: [CAMPAIGN] });
    }
    if (url.pathname === `/capital-campaigns/domain/${CAMPAIGN.campaign_id}/orchestration/history`) {
      if (options?.historyStatus && options.historyStatus !== 200) {
        return jsonResponse(options.historyStatus, { error: { message: "history unavailable" } });
      }
      return jsonResponse(200, {
        mode: "campaign_orchestration_history",
        campaign_id: CAMPAIGN.campaign_id,
        version: CAMPAIGN.version,
        count: 1,
        campaign_snapshot: { campaign_id: CAMPAIGN.campaign_id, status: "READY", allowed_instruments: ["BTC-USD"] },
        items: options?.historyItems ?? [CYCLE],
      });
    }
    return jsonResponse(404, { error: { message: `unhandled route in test: ${url.pathname}` } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

describe("ProcessTraceView", () => {
  it("loads real trace data from the read-only API and shows cycle context", async () => {
    mockFetch();
    render(<ProcessTraceView />);

    expect(await screen.findByText("Cycle context")).toBeInTheDocument();
    expect(screen.getByText(CYCLE.cycle_id)).toBeInTheDocument();
    expect(screen.getByText(`${CAMPAIGN.name} (v${CAMPAIGN.version})`)).toBeInTheDocument();
    expect(screen.getByText("hold_no_package_created")).toBeInTheDocument();
  });

  it("groups trace events under the correct PROCESS accordion", async () => {
    mockFetch();
    render(<ProcessTraceView />);
    await screen.findByText("Cycle context");

    const observeMarket = screen.getByText("Observe Market").closest("details") as HTMLElement;
    expect(within(observeMarket).getAllByText("market_evidence_gate")).toHaveLength(2); // BTC pass + ETH reject

    const determineOpportunity = screen.getByText("Determine Opportunity").closest("details") as HTMLElement;
    expect(within(determineOpportunity).getByText("net_edge_gate")).toBeInTheDocument();

    // A gate that only ever fires under DETERMINE_MARKET_STATE must not
    // leak into an unrelated accordion.
    const authorizeTrade = screen.getByText("Authorize Trade").closest("details") as HTMLElement;
    expect(within(authorizeTrade).queryByText("net_edge_gate")).not.toBeInTheDocument();
    expect(within(authorizeTrade).queryByText("strategy_evidence_gate")).not.toBeInTheDocument();
  });

  it("preserves chronological event order within a stage regardless of API array order", async () => {
    mockFetch();
    render(<ProcessTraceView />);
    await screen.findByText("Cycle context");

    const observeMarket = screen.getByText("Observe Market").closest("details") as HTMLElement;
    const instrumentNodes = within(observeMarket).getAllByText(/^(BTC-USD|ETH-USD)$/);
    // ETH-USD's event (21:00:00Z) happened before BTC-USD's (21:00:01Z).
    expect(instrumentNodes.map((node) => node.textContent)).toEqual(["ETH-USD", "BTC-USD"]);
  });

  it("makes the first rejection/blocking event immediately visible as the stop summary", async () => {
    mockFetch();
    render(<ProcessTraceView />);
    await screen.findByText("Cycle context");

    expect(await screen.findByText("Cycle stopped at")).toBeInTheDocument();
    // The overall-earliest stopping event is ETH-USD's OBSERVE_MARKET
    // rejection (21:00:00Z), not the later BTC-USD net-edge rejection.
    const summary = screen.getByText("Cycle stopped at").closest("section") as HTMLElement;
    expect(within(summary).getByText(/Observe Market/)).toBeInTheDocument();
    expect(within(summary).getByText(/market_evidence_gate/)).toBeInTheDocument();
    expect(within(summary).getByText("stale_market_data")).toBeInTheDocument();
  });

  it("displays observed value and threshold when present", async () => {
    mockFetch();
    render(<ProcessTraceView />);
    await screen.findByText("Cycle context");

    const determineOpportunity = screen.getByText("Determine Opportunity").closest("details") as HTMLElement;
    expect(within(determineOpportunity).getByText("-0.0018")).toBeInTheDocument();
    expect(within(determineOpportunity).getByText("0")).toBeInTheDocument();
    expect(within(determineOpportunity).getByText("non_positive_net_edge")).toBeInTheDocument();
  });

  it("shows an honest empty state for stages with no recorded trace events", async () => {
    mockFetch();
    render(<ProcessTraceView />);
    await screen.findByText("Cycle context");

    const execute = screen.getByText("Execute").closest("details") as HTMLElement;
    expect(within(execute).getByText("No trace events recorded for this stage in this cycle.")).toBeInTheDocument();
    expect(within(execute).queryByText("Not configured yet.")).not.toBeInTheDocument();

    const monitor = screen.getByText("Monitor").closest("details") as HTMLElement;
    expect(within(monitor).getByText("No trace events recorded for this stage in this cycle.")).toBeInTheDocument();
  });

  it("never issues a mutating request -- every call is a plain GET", async () => {
    const { fetchMock, calls } = mockFetch();
    render(<ProcessTraceView />);
    await screen.findByText("Cycle context");

    expect(fetchMock).toHaveBeenCalled();
    for (const call of calls) {
      expect(call.method).toBe("GET");
    }
  });

  it("keeps multiple instruments/candidates distinguishable rather than merging their events", async () => {
    mockFetch();
    render(<ProcessTraceView />);
    await screen.findByText("Cycle context");

    expect(screen.getByText(/BTC-USD, ETH-USD|ETH-USD, BTC-USD/)).toBeInTheDocument(); // cycle-context instrument summary

    const observeMarket = screen.getByText("Observe Market").closest("details") as HTMLElement;
    const btcCard = within(observeMarket).getByText("fresh").closest("article") as HTMLElement;
    const ethCard = within(observeMarket).getByText("stale_market_data").closest("article") as HTMLElement;
    expect(within(btcCard).getByText("BTC-USD")).toBeInTheDocument();
    expect(within(ethCard).getByText("ETH-USD")).toBeInTheDocument();
    expect(btcCard).not.toBe(ethCard);
  });

  it("handles an API error without crashing and without showing fake data", async () => {
    mockFetch({ domainStatus: 500 });
    render(<ProcessTraceView />);

    expect(await screen.findByText(/domain unavailable|Unable to load/)).toBeInTheDocument();
    expect(screen.queryByText("Cycle context")).not.toBeInTheDocument();
  });

  it("shows an honest empty state when the campaign has no recorded cycles", async () => {
    mockFetch({ historyItems: [] });
    render(<ProcessTraceView />);

    expect(await screen.findByText(/No worker cycle has been recorded yet/)).toBeInTheDocument();
  });
});
