import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import RuleBuilder from "@/components/strategy-lab/RuleBuilder";
import type { CandidateExperiment, PatternAnalysisRequest, ResearchExplanation } from "@/lib/api/strategyLabOffline";

const parameters = { entry_offset_pct: "0.01", initial_stop_pct: "0.01", profit_activation_pct: "0.03", trailing_distance_pct: "0.01", required_declining_candles: 2, fee_pct: "0.002", slippage_pct: "0.0005", initial_capital: "100", trade_deployment_pct: "100", profit_compound_pct: "100", profit_withdrawal_pct: "0", profit_tax_reserve_pct: "0" };
const payload: PatternAnalysisRequest = { dataset_id: "btc_15m", strategy_version: "002", selected_start_index: 10, selected_end_index: 13, partition: "training", parameters };
const experiment: CandidateExperiment = { experiment_id: "CE-001", question: "Would blocking entries during a negative slope improve expected-cost results?", suggested_controlled_change: "Change exactly one entry condition.", required_tests: ["Training", "Validation", "Final Test"], source_finding_ids: ["finding_0001"], status: "PROPOSED", executable_rule: false };
const result = {
  analysis_type: "EXPLAIN_SELECTION", provider: "DeterministicTemplateProvider", provider_version: "1.0.0", template_version: "1.0.0", source_analysis_id: "analysis_test", statements: [], primary_cause: null, counterfactual_improvement: null, candidate_experiments: [experiment], content_hash: "research-hash",
  evidence: { source_findings: [{ finding_id: "finding_0001", detector_id: "negative_slope_v1", detector_version: "1.0.0", category: "OBSERVATION", group: "Price Structure", pattern_name: "Negative Slope", start_index: 10, end_index: 13, start_time: "2026-01-01T00:00:00Z", end_time: "2026-01-01T00:45:00Z", measurements: { slope: "-2.5" }, thresholds: { maximum_slope: "0" }, evidence: ["slope=-2.5"], conditions: ["slope < 0"], sufficient_evidence: true, recurrence: [] }], detector_versions: { negative_slope_v1: "1.0.0" }, selected_candles: [10, 13], recurrence_evidence: {}, partition: "training", cost_model: { fee_pct: "0.002", slippage_pct: "0.0005" }, configuration: {} },
} as ResearchExplanation;

afterEach(() => { vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("Candidate Rule Builder", () => {
  it("requires validation and replays every partition before presenting a verdict", async () => {
    const calls: Array<{ path: string; body: Record<string, unknown> }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const path = new URL(typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url).pathname;
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      calls.push({ path, body });
      let response: unknown = { valid: true, normalized_rule: body.rule_document };
      if (path === "/api/v1/strategy-lab/rules") response = { candidate_rule_id: "CR-000001", name: body.name, description: body.description, status: "DRAFT", source_analysis_id: "analysis_test", source_finding_ids: ["finding_0001"], source_candidate_experiment_id: "CE-001", parent_strategy_version: "002", conditions: body.rule_document.when, action: body.rule_document.then, risk_controls: body.rule_document.risk_controls, created_by: "human_with_copilot", created_at: "2026-01-01T00:00:00Z", rule_schema_version: "1.0.0", content_hash: "rule-hash", research_notes: "", evidence: body.evidence };
      if (path.endsWith("/create-branch")) response = { strategy_branch_id: "SB-002-rule-hash-draft", parent_strategy_version: "002", candidate_rule_id: "CR-000001", content_hash: "branch-hash", simulator_version: "strategy-lab-1.0.0" };
      if (path.endsWith("/replay")) response = report(String(body.partition));
      if (path.endsWith("/comparison")) response = { strategy_branch_id: "SB-002-rule-hash-draft", dataset_id: "btc_15m", reports: { training: report("training"), validation: report("validation"), final_test: report("final_test"), entire_dataset: report("entire_dataset") }, overfitting_warnings: [], promotion: { eligible: true, status: "PROMOTABLE", checks: { validation_positive: true, final_test_positive: true } } };
      return new Response(JSON.stringify(response), { status: path === "/api/v1/strategy-lab/rules" ? 201 : 200, headers: { "Content-Type": "application/json" } });
    }));

    const user = userEvent.setup();
    render(<RuleBuilder experiment={experiment} result={result} payload={payload} onClose={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "Candidate Rule Builder" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Block entries during negative slope")).toBeInTheDocument();
    expect(screen.getByText(/Suggested by Research Copilot/)).toBeInTheDocument();
    expect(screen.getByText(/finding_0001 · negative_slope_v1 · v1.0.0/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save as Draft" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Validate Rule" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save as Draft" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Save as Draft" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create Branch" })).toBeEnabled());
    expect(calls.find((call) => call.path === "/api/v1/strategy-lab/rules")?.body).toMatchObject({ source_candidate_experiment_id: "CE-001", parent_strategy_version: "002", created_by: "human_with_copilot" });

    await user.click(screen.getByRole("button", { name: "Create Branch" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Replay All Partitions" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Replay All Partitions" }));
    await waitFor(() => expect(screen.getByText("PROMOTABLE")).toBeInTheDocument());

    const replayPartitions = calls.filter((call) => call.path.endsWith("/replay")).map((call) => call.body.partition);
    expect(replayPartitions).toEqual(["training", "validation", "final_test", "entire_dataset"]);
    expect(screen.getByText("final test")).toBeInTheDocument();
    expect(window.localStorage.getItem("omnitrade.ruleDiscovery.rules.CR-000001")).toContain("rule-hash");
  });
});

function report(partition: string) { return { partition, rule_match_count: 8, rule_action_count: 8, parent: { net_return_pct: "1.0" }, candidate: { net_return_pct: "1.4" }, buy_and_hold: { return_pct: "0.5" }, parent_delta: { net_return_pct: "0.4" }, buy_and_hold_delta: "0.9", content_hash: `${partition}-hash` }; }