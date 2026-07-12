import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DecisionInspectorPage from "@/app/decisions/[decisionId]/page";

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

describe("DecisionInspectorPage", () => {
  it("renders complete narrative layout for linked decision", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const method = input instanceof Request ? input.method : init?.method ?? "GET";
      const url = new URL(rawUrl);

      if (url.pathname === "/decisions/11111111-1111-1111-1111-111111111111/inspector" && method === "GET") {
        return jsonResponse(200, {
          decision_id: "11111111-1111-1111-1111-111111111111",
          header: {
            title: "BTC-USD HOLD Recommendation",
            decision_id: "11111111-1111-1111-1111-111111111111",
            current_status: "rejected",
            timestamp: "2026-07-11T10:00:00Z",
            strategy: "22222222-2222-2222-2222-222222222222",
            campaign: null,
            provider: "kraken_spot",
            environment: "production",
            market: "kraken_spot / BTC-USD",
            confidence: "0.74",
            decision_quality: null,
            review_status: "unreviewed",
            environment_badge: "PRODUCTION",
            paper_live_badge: "PAPER",
          },
          timeline: [
            { stage: "Signal Generated", status: "completed", label: "✓ Completed", detail: "Signal linkage resolved" },
            { stage: "Risk Evaluation", status: "completed", label: "✓ Completed", detail: "Risk evaluation recorded" },
            { stage: "Submission", status: "not_applicable", label: "— Not Applicable", detail: "No submission linked" },
          ],
          narrative: {
            title: "Why",
            explanation: "The decision record indicates an action of HOLD, but direct signal linkage is unavailable.",
            evidence_gaps: ["Preview linkage missing"],
          },
          execution_price_evidence: {
            availability: "linked",
            provider: "kraken_spot",
            venue: "kraken_spot",
            product: "BTC-USD",
            base_currency: "BTC",
            quote_currency: "USD",
            observed_price: "10000.00",
            bid: "9999.00",
            ask: "10001.00",
            reference_price: "10000.00",
            observed_timestamp: "2026-07-11T09:59:30Z",
            retrieved_timestamp: "2026-07-11T10:00:00Z",
            evidence_age_seconds: 30,
            freshness_seconds: 30,
            validation_status: "valid",
            evidence_id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
          },
          risk_evaluation: {
            verdict: "rejected",
            first_failing_rule: { rule_name: "minimum_order_size", reason: "position_below_minimum_order_size" },
            stopped_after_first_fail: true,
            risk_adjusted_sizing: "0",
            checks: [
              {
                rule_name: "global_kill_switch",
                policy: "risk_engine_final",
                observed_value: null,
                threshold: null,
                result: "PASS",
                reason: null,
              },
              {
                rule_name: "minimum_order_size",
                policy: "risk_engine_final",
                observed_value: null,
                threshold: null,
                result: "FAIL",
                reason: "position_below_minimum_order_size",
              },
            ],
          },
          decision_intelligence: {
            decision_record: "completed",
            decision_snapshot: "completed",
            execution_evidence: "completed",
          },
          preview: {
            availability: "unavailable",
            state_reason: "no_preview_linked",
            preview_id: null,
            requested_amount: null,
            approved_amount: null,
            estimated_quantity: null,
            estimated_fees: null,
            expiration: null,
            submission_state: "not_applicable",
            execution_state: "not_applicable",
            human_approval_state: "not_applicable",
          },
          audit_timeline: [
            {
              actor: "operator",
              timestamp: "2026-07-11T10:00:00Z",
              action: "crypto_order_preview_initiated",
              entity_type: "crypto_order_preview",
              correlation_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
            },
          ],
          counterfactual: {
            availability: "unavailable",
            state_reason: "counterfactual_outcomes_unavailable",
            items: [],
            summary: "Counterfactual package unavailable because no horizon evaluations are linked yet.",
          },
          linkage_health: [
            { component: "Decision Record", status: "completed", reason: "Decision Record is present." },
            { component: "Preview", status: "missing", reason: "preview_missing" },
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

    render(<DecisionInspectorPage params={{ decisionId: "11111111-1111-1111-1111-111111111111" }} />);

    expect(await screen.findByRole("heading", { name: "Decision Inspector" })).toBeInTheDocument();
    expect(await screen.findByText("Decision Timeline")).toBeInTheDocument();
    expect(await screen.findByText("Why")).toBeInTheDocument();
    expect(await screen.findByText("Execution Price Evidence")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Risk Evaluation" })).toBeInTheDocument();
    expect(await screen.findByText("Audit Timeline")).toBeInTheDocument();
    expect(await screen.findByText("Linkage Health")).toBeInTheDocument();
    expect(await screen.findByText("First failing rule: minimum_order_size (position_below_minimum_order_size)")).toBeInTheDocument();
  });

  it("renders API failure", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(500, { error: { message: "server failure" } }));
    vi.stubGlobal("fetch", fetchMock);

    render(<DecisionInspectorPage params={{ decisionId: "11111111-1111-1111-1111-111111111111" }} />);

    expect(await screen.findByText("server failure")).toBeInTheDocument();
  });
});
