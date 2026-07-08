import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import DecisionRecordsPage from "@/app/dashboard/decisions/page";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DecisionRecordsPage", () => {
  it("renders learn-layer enrichments from decision records endpoint", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const method = input instanceof Request ? input.method : init?.method ?? "GET";
      const url = new URL(rawUrl);

      if (url.pathname === "/decisions/records" && method === "GET") {
        return jsonResponse(200, {
          items: [
            {
              decision_id: "11111111-1111-1111-1111-111111111111",
              timestamp: "2026-07-06T00:00:00Z",
              asset_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
              trade_accepted: true,
              review_status: "unreviewed",
              outcome: null,
              action: "buy",
              decision_explanation: {
                trade_rejected_reason: null,
                ai_reflection: null,
                post_trade_notes: null,
                human_notes: null,
                lessons_learned: null,
              },
              linked_signal: {
                signal_id: "22222222-2222-2222-2222-222222222222",
                strategy_id: "33333333-3333-3333-3333-333333333333",
                asset_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                action: "buy",
                status: "generated",
                signal_time: "2026-07-06T00:00:00Z",
              },
              quality_score: {
                availability_state: "known",
                state_reason: null,
                scoring_model_version: "dqe_v1",
                composite_score: "0.8200",
                created_at: "2026-07-06T00:30:00Z",
              },
              future_outcome_tracking: {
                availability_state: "known",
                state_reason: null,
                horizons_evaluated: ["15m", "1h"],
                resolved_horizons: 1,
                total_horizons: 2,
                latest_evaluated_at: "2026-07-06T01:00:00Z",
                latest_horizon_label: "1h",
                latest_evaluation_state: "resolved",
                latest_best_action: "buy",
                latest_actual_action_correct: true,
              },
              recommendation_history: {
                count: 2,
                latest_recommendation_at: "2026-07-06T01:30:00Z",
                latest_recommendation_type: "recurring_decision_pattern",
                latest_recommendation_state: "known",
                recommendation_ids: ["r1", "r2"],
              },
            },
          ],
          page: 1,
          page_size: 50,
          total: 1,
        });
      }

      if (url.pathname === "/decisions/recommendations" && method === "GET") {
        return jsonResponse(200, {
          items: [
            {
              id: "44444444-4444-4444-4444-444444444444",
              recommendation_type: "recurring_decision_pattern",
              recommendation_category: "pattern",
              confidence_level: "medium",
              expected_impact: "medium",
              required_human_review_level: "required",
              supporting_evidence_refs: [{ source: "decision_records", state: "known" }],
              originating_decision_ids: ["11111111-1111-1111-1111-111111111111"],
              explanation: "AI Coach batch review generated for recent paper-mode decisions.",
              suggested_experiment: { name: "ai_coach_batch_review" },
              provenance: { source: "ai_coach_batch_review" },
              availability_state: "known",
              state_reason: null,
              advisory_only: true,
              created_at: "2026-07-06T02:00:00Z",
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

    render(<DecisionRecordsPage />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Load Decision Records" }));

    expect(await screen.findByRole("columnheader", { name: "Quality Score" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Future Outcome" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Recommendations" })).toBeInTheDocument();

    expect(screen.getByText("0.8200 (dqe_v1)")).toBeInTheDocument();
    expect(screen.getByText("1/2 resolved (resolved @ 1h)")).toBeInTheDocument();
    expect(screen.getByText("2 (recurring_decision_pattern)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Load AI Coach Learn Queue" }));
    expect(await screen.findByText("AI Coach batch review generated for recent paper-mode decisions.")).toBeInTheDocument();
    expect(screen.getByText("Suggested experiment: ai_coach_batch_review")).toBeInTheDocument();
  });
});
