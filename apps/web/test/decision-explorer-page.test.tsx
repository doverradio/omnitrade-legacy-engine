import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import DecisionExplorerPage from "@/app/decisions/page";

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

describe("DecisionExplorerPage", () => {
  it("renders summary strip, result cards, and inspector links", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const method = input instanceof Request ? input.method : init?.method ?? "GET";
      const url = new URL(rawUrl);

      if (url.pathname === "/decisions/records" && method === "GET") {
        return jsonResponse(200, {
          items: [
            {
              decision_id: "11111111-1111-1111-1111-111111111111",
              timestamp: "2026-07-10T10:00:00Z",
              asset_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
              trade_accepted: true,
              review_status: "unreviewed",
              outcome: null,
              action: "buy",
              provider: "kraken_spot",
              environment: "production",
              product_id: "BTC-USD",
              confidence: "0.91",
              risk_verdict: "approved",
              first_failing_risk_rule: null,
              requested_notional: "5.00",
              approved_notional: "0.0005",
              preview_status: "ready",
              approval_status: "none",
              rehearsal_status: "none",
              execution_status: "not_submitted",
              has_decision_snapshot: true,
              has_price_evidence: true,
              has_risk_event: true,
              evidence_completeness: "complete",
              decision_explanation: {
                trade_rejected_reason: null,
                ai_reflection: null,
                post_trade_notes: null,
                human_notes: null,
                lessons_learned: null,
              },
              linked_signal: {
                signal_id: null,
                strategy_id: null,
                asset_id: null,
                action: "buy",
                status: "generated",
                signal_time: null,
              },
              quality_score: {
                availability_state: "known",
                state_reason: null,
                scoring_model_version: "dqe_v1",
                composite_score: "0.8100",
                created_at: "2026-07-10T10:01:00Z",
              },
              future_outcome_tracking: {
                availability_state: "unavailable",
                state_reason: "counterfactual_outcomes_unavailable",
                horizons_evaluated: [],
                resolved_horizons: 0,
                total_horizons: 0,
                latest_evaluated_at: null,
                latest_horizon_label: null,
                latest_evaluation_state: null,
                latest_best_action: null,
                latest_actual_action_correct: null,
              },
              recommendation_history: {
                count: 0,
                latest_recommendation_at: null,
                latest_recommendation_type: null,
                latest_recommendation_state: "unavailable",
                recommendation_ids: [],
              },
            },
          ],
          page: 1,
          page_size: 20,
          total: 1,
        });
      }

      if (url.pathname === "/decisions/explorer/summary" && method === "GET") {
        return jsonResponse(200, {
          total_decisions: 1,
          accepted: 1,
          risk_rejected: 0,
          hold_wait: 0,
          preview_ready: 1,
          submitted: 0,
          executed: 0,
          needs_review: 1,
          missing_linkage: 0,
        });
      }

      return jsonResponse(404, {
        error: {
          message: `Unhandled route in test: ${method} ${url.pathname}`,
        },
      });
    });

    vi.stubGlobal("fetch", fetchMock);

    render(<DecisionExplorerPage />);
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "Decision Explorer" })).toBeInTheDocument();
    expect(await screen.findByText("Total decisions")).toBeInTheDocument();
    expect(screen.getByText("BTC-USD • kraken_spot")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Inspector" })).toHaveAttribute(
      "href",
      "/decisions/11111111-1111-1111-1111-111111111111",
    );

    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(fetchMock).toHaveBeenCalled();
  });
});
