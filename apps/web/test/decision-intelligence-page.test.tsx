import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import DecisionIntelligencePage from "@/app/decision-intelligence/page";

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

    if (url.pathname === "/decisions/timeline") {
      return jsonResponse(200, {
        items: [
          {
            decision_id: "11111111-1111-1111-1111-111111111111",
            timestamp: "2026-07-06T00:00:00Z",
            narrative: "WAIT decision for asset:known; context strategy:known on account:unknown.",
            status: "wait",
            account_id: { value: null, state: "unknown" },
            asset_id: { value: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", state: "known" },
            strategy_id: { value: null, state: "unavailable" },
            source_lineage: { signals: [], model_outputs: [], risk_events: [], trades: [] },
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
      });
    }

    if (url.pathname === "/decisions/11111111-1111-1111-1111-111111111111/explainability") {
      return jsonResponse(200, {
        decision_id: "11111111-1111-1111-1111-111111111111",
        decision_status: "wait",
        explanation: "WAIT selected.",
        supporting_evidence: [
          {
            evidence_name: "supporting_state",
            evidence_payload: { value: null },
            provenance: {},
            availability_state: "unavailable",
            state_reason: "supporting_evidence_unavailable",
          },
        ],
        opposing_evidence: [],
        confidence_factors: [],
        risk_adjustments: [],
      });
    }

    if (url.pathname === "/decisions/counterfactuals") {
      return jsonResponse(200, {
        items: [
          {
            id: "c1",
            decision_id: "11111111-1111-1111-1111-111111111111",
            horizon_label: "15m",
            horizon_minutes: 15,
            decision_timestamp: "2026-07-06T00:00:00Z",
            evaluated_at: "2026-07-06T00:15:00Z",
            asset_symbol: "BTCUSDT",
            actual_action: "wait",
            shadow_buy_return_pct: "0.0010",
            shadow_sell_return_pct: "-0.0010",
            shadow_wait_return_pct: "0.0000",
            best_action: "buy",
            actual_action_correct: false,
            evaluation_state: "resolved",
            state_reason: null,
            lesson_tags: [{ tag: "missed_breakout", reason: "buy_outperformed_non_buy_action" }],
            feature_snapshot: {},
            created_at: "2026-07-06T00:15:00Z",
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
      });
    }

    if (url.pathname === "/decisions/11111111-1111-1111-1111-111111111111/counterfactuals") {
      return jsonResponse(200, {
        decision_id: "11111111-1111-1111-1111-111111111111",
        availability_state: "known",
        state_reason: null,
        items: [
          {
            id: "c1",
            horizon_label: "15m",
            horizon_minutes: 15,
            evaluation_state: "resolved",
            actual_action: "wait",
            best_action: "buy",
            actual_action_correct: false,
            lesson_tags: [],
            feature_snapshot: {},
          },
        ],
      });
    }

    if (url.pathname === "/decisions/quality") {
      return jsonResponse(200, {
        items: [
          {
            decision_id: "11111111-1111-1111-1111-111111111111",
            availability_state: "unavailable",
            state_reason: "quality_score_unavailable",
            scoring_model_version: null,
            composite_score: null,
            component_scores: [],
            weight_profile: {},
            provenance: {},
            created_at: null,
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
      });
    }

    if (url.pathname === "/decisions/recommendations") {
      return jsonResponse(200, {
        items: [
          {
            id: "r1",
            recommendation_type: "hypothesis_test",
            recommendation_category: "hypothesis",
            confidence_level: "low",
            expected_impact: "medium",
            required_human_review_level: "priority",
            supporting_evidence_refs: [],
            originating_decision_ids: ["11111111-1111-1111-1111-111111111111"],
            explanation: "Counterfactual unresolved.",
            suggested_experiment: { name: "wait_vs_directional_resolution" },
            provenance: {},
            availability_state: "unknown",
            state_reason: "counterfactual_unresolved",
            advisory_only: true,
            created_at: "2026-07-06T00:00:00Z",
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
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

describe("DecisionIntelligencePage Prompt 7.10", () => {
  it("renders read-only dashboard and shows known/unknown/unavailable states", async () => {
    const fetchMock = installFetchMock();
    render(<DecisionIntelligencePage />);

    expect(await screen.findByRole("heading", { name: "Decision Intelligence Dashboard" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText(/unknown|unavailable|known/i).length).toBeGreaterThan(0);
    });

    expect(screen.getByText("Observational only: no execution controls, no risk controls, no strategy editing, and no recommendation approval actions.")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Apply Filters" }));

    const writeCalls = fetchMock.mock.calls.filter((call) => {
      const init = call[1] as RequestInit | undefined;
      return (init?.method ?? "GET") !== "GET";
    });

    expect(writeCalls).toHaveLength(0);
  });
});
