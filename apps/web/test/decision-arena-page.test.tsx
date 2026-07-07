import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

    if (method !== "GET") {
      return jsonResponse(405, {
        error: {
          message: `Unexpected method ${method}`,
        },
      });
    }

    if (url.pathname === "/decisions/arena-leaderboard/latest") {
      return jsonResponse(200, {
        snapshot_scope: "tournament",
        competition_id: "11111111-1111-1111-1111-111111111111",
        tournament_id: "22222222-2222-2222-2222-222222222222",
        cycle_id: null,
        availability_state: "known",
        state_reason: null,
        ranking_hash: "rank-hash",
        ranking_methodology_version: "v1",
        snapshot_timestamp: "2026-07-06T00:00:00Z",
        filters: {
          included_agent_ids: null,
          limit: null,
          availability_mode: "all",
        },
        entries: [
          {
            rank: 1,
            agent_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            composite_rank_score: { value: "0.9000", status: "available", reason: null },
            decision_quality: { value: "0.9000", status: "available", reason: null },
            profit: { value: "10", status: "available", reason: null },
            drawdown: { value: "0.1", status: "available", reason: null },
            fee_drag: { value: "1", status: "available", reason: null },
            consistency: { value: "0.9", status: "available", reason: null },
            risk_discipline: { value: "0.9", status: "available", reason: null },
            explainability: { value: "0.8", status: "available", reason: null },
            evidence_provenance: {},
          },
        ],
        evidence_sources: {},
        provenance: {},
      });
    }

    if (url.pathname === "/decisions/arena-comparisons/latest") {
      return jsonResponse(200, {
        comparison_scope: "tournament",
        competition_id: "11111111-1111-1111-1111-111111111111",
        tournament_id: "22222222-2222-2222-2222-222222222222",
        cycle_id: null,
        availability_state: "known",
        state_reason: null,
        comparison_hash: "cmp-hash",
        compared_agent_ids: ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
        comparison_timestamp: "2026-07-06T00:00:00Z",
        agent_summaries: [
          {
            agent_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            decision_quality: { value: "0.9000", status: "available", reason: null },
            explainability_support_ratio: { value: "0.8000", status: "available", reason: null },
            counterfactual_correctness: { value: "0.7000", status: "available", reason: null },
            evidence_provenance: {},
          },
        ],
        portfolio_dimensions: {},
        evidence_sources: {},
        provenance: {},
      });
    }

    if (url.pathname === "/decisions/arena-tournaments/history") {
      return jsonResponse(200, {
        competition_id: "11111111-1111-1111-1111-111111111111",
        tournament_id: "22222222-2222-2222-2222-222222222222",
        availability_state: "known",
        state_reason: null,
        current_state: "active",
        latest_event_type: "standings_recorded",
        latest_event_timestamp: "2026-07-06T00:10:00Z",
        history_count: 1,
        replay_metadata: { deterministic_replay: true },
        latest_schedule_payload: { cycle_interval_minutes: 30 },
        latest_standings: [],
        history: [
          {
            history_record_id: "h1",
            event_hash: "hash",
            sequence_number: 1,
            event_type: "standings_recorded",
            lifecycle_state: "active",
            event_timestamp: "2026-07-06T00:10:00Z",
            schedule_payload: { cycle_interval_minutes: 30 },
            replay_metadata: { deterministic_replay: true },
            tie_break_rules: ["decision_quality_desc"],
            ordering_rules: ["composite_score_desc"],
            standings: [],
            provenance: {},
          },
        ],
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
});

describe("DecisionArenaPage Prompt 8.10", () => {
  it("renders read-only arena dashboard and uses GET-only API integration", async () => {
    const fetchMock = installFetchMock();
    render(<DecisionArenaPage />);

    expect(await screen.findByRole("heading", { name: "Decision Arena Dashboard" })).toBeInTheDocument();
    expect(screen.getByText(/Observational only/i)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Competition ID"), "11111111-1111-1111-1111-111111111111");
    await user.type(screen.getByLabelText("Tournament ID"), "22222222-2222-2222-2222-222222222222");
    await user.click(screen.getByRole("button", { name: "Load Arena Dashboard" }));

    await waitFor(() => {
      expect(screen.getByText("Leaderboard")).toBeInTheDocument();
      expect(screen.getByText("Comparisons")).toBeInTheDocument();
      expect(screen.getByText("Tournament Replay Viewer")).toBeInTheDocument();
    });

    const nonGetCalls = fetchMock.mock.calls.filter((call) => {
      const init = call[1] as RequestInit | undefined;
      return (init?.method ?? "GET") !== "GET";
    });

    expect(nonGetCalls).toHaveLength(0);
  });
});
