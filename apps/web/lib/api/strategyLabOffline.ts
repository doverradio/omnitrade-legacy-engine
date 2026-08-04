export type ResearchPeriod = "training" | "validation" | "out_of_sample" | "entire_dataset";
export type StrategyVersion = "001" | "002";

export type StrategyLabDataset = {
  id: string;
  name: string;
  asset: string;
  exchange: string;
  interval: string;
  candle_count: number;
  first_timestamp: string;
  last_timestamp: string;
  missing_candles: number;
  duplicate_timestamps: number;
  invalid_rows: number;
};

export type CsvValidationReport = {
  valid: boolean;
  required_columns: string[];
  missing_columns: string[];
  total_rows: number;
  candle_count: number;
  first_timestamp: string | null;
  last_timestamp: string | null;
  missing_candles: number;
  duplicate_timestamps: number;
  invalid_rows: number;
  errors: string[];
};

export type DatasetUpload = {
  csv_text: string;
  asset: string;
  exchange: string;
  interval: string;
  name: string;
};

export type StrategyLabParameters = {
  entry_offset_pct: string;
  initial_stop_pct: string;
  profit_activation_pct: string;
  trailing_distance_pct: string;
  required_declining_candles: number;
  fee_pct: string;
  slippage_pct: string;
  initial_capital: string;
  trade_deployment_pct: string;
  profit_compound_pct: string;
  profit_withdrawal_pct: string;
  profit_tax_reserve_pct: string;
};

export type ReplayRequest = {
  dataset_id: string;
  strategy_version: StrategyVersion;
  start_time?: string;
  end_time?: string;
  research_period: ResearchPeriod;
  parameters: StrategyLabParameters;
};

export type ReplayCandle = {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
};

export type ReplayEvent = {
  candle_index: number;
  timestamp: string;
  kind: "buy_limit" | "cancelled_order" | "filled_order" | "entry" | "protective_stop" | "trailing_floor" | "profit_activation" | "exit";
  price: string;
  reason: string | null;
};

export type ReplayTrade = Record<string, string | number> & {
  entry_candle_index: number;
  entry_timestamp: string;
  exit_candle_index: number;
  exit_timestamp: string;
  exit_reason: string;
  effective_entry_price: string;
  effective_exit_price: string;
  net_return_pct: string;
  net_pnl: string;
  mfe_pct: string;
  mae_pct: string;
  holding_candles: number;
};

export type ReplayMetrics = Record<string, string | number | null> & {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  net_return_pct: string;
  max_drawdown_pct: string;
  win_rate_pct: string;
  fees_paid: string;
  estimated_slippage: string;
  starting_capital: string;
  ending_trading_capital: string;
  withdrawn_profit: string;
  tax_reserve: string;
  total_economic_value: string;
  buy_and_hold_value: string;
  buy_and_hold_return_pct: string;
  outperformance: string;
  verdict: "PROFITABLE" | "MARGINAL" | "UNPROFITABLE" | "INSUFFICIENT DATA";
};

export type ReplayResult = {
  dataset: StrategyLabDataset & { research_period: ResearchPeriod };
  strategy_version: StrategyVersion;
  parameters: StrategyLabParameters;
  candles: ReplayCandle[];
  events: ReplayEvent[];
  trades: ReplayTrade[];
  equity_curve: Array<{ timestamp: string; equity: string }>;
  capital_curve: Array<{ timestamp: string; trading_capital: string; withdrawn_profit: string; total_economic_value: string; buy_and_hold: string }>;
  metrics: ReplayMetrics;
};

export type PatternRecurrence = {
  partition: "training" | "validation" | "final_test" | "entire_dataset";
  forward_horizon: number;
  occurrence_count: number;
  average_forward_return: string | null;
  median_forward_return: string | null;
  positive_return_frequency: string | null;
  net_positive_frequency: string | null;
  maximum_favorable_excursion: string | null;
  maximum_adverse_excursion: string | null;
  target_before_stop_frequency: string | null;
  confidence_interval_95: [string, string] | null;
  sufficient_evidence: boolean;
};

export type PatternFinding = {
  finding_id: string;
  detector_id: string;
  detector_version: string;
  category: "OBSERVATION" | "STATISTICAL_EVIDENCE" | "INSUFFICIENT_EVIDENCE" | "CONTRADICTION";
  group: "Price Structure" | "Volatility" | "Momentum" | "Volume" | "Breakouts" | "Strategy Behavior";
  pattern_name: string;
  start_index: number;
  end_index: number;
  start_time: string;
  end_time: string;
  measurements: Record<string, string | number | boolean | null>;
  thresholds: Record<string, string | number | boolean | null>;
  evidence: string[];
  conditions: string[];
  sufficient_evidence: boolean;
  recurrence: PatternRecurrence[];
};

export type PatternAnnotation = {
  annotation_id: string;
  pattern_id: string;
  start_time: string;
  end_time: string;
  label: string;
  chart_region: "price" | "volume";
  details_ref: string;
};

export type PatternAnalysis = {
  analysis_id: string;
  engine_version: string;
  feature_version: string;
  dataset_id: string;
  dataset_hash: string;
  selected_range: [number, number];
  partition: string;
  configuration: Record<string, unknown>;
  data_quality: Array<{ issue_type: string; message: string; index: number | null; timestamp: string | null }>;
  findings: PatternFinding[];
  annotations: PatternAnnotation[];
  detector_versions: Record<string, string>;
  content_hash: string;
  elapsed_ms: string;
};

export type PatternAnalysisRequest = {
  dataset_id: string;
  strategy_version: StrategyVersion;
  selected_start_index: number;
  selected_end_index: number;
  partition: "training" | "validation" | "final_test" | "entire_dataset";
  parameters: StrategyLabParameters;
};

export type ResearchStatementLabel = "OBSERVATION" | "STATISTICAL EVIDENCE" | "HYPOTHESIS" | "RECOMMENDATION" | "WARNING" | "INSUFFICIENT EVIDENCE";

export type ResearchStatement = {
  statement_id: string;
  label: ResearchStatementLabel;
  section: string;
  text: string;
  source_finding_ids: string[];
  measurements: Record<string, unknown>;
};

export type CandidateExperiment = {
  experiment_id: string;
  question: string;
  suggested_controlled_change: string;
  required_tests: string[];
  source_finding_ids: string[];
  status: "PROPOSED";
  executable_rule: false;
};

export type ResearchExplanation = {
  analysis_type: "EXPLAIN_SELECTION" | "SHOW_MISSED" | "EXPLAIN_TRADE" | "EXPLAIN_SUCCESS" | "OVERFITTING_WARNINGS";
  provider: "DeterministicTemplateProvider";
  provider_version: string;
  template_version: string;
  source_analysis_id: string;
  statements: ResearchStatement[];
  primary_cause: null | {
    classification: string;
    supporting_finding_ids: string[];
    measurements: Record<string, unknown>;
    historical_recurrence: Array<Record<string, unknown>>;
    confidence: string;
    alternatives_considered: string[];
    limitations: string[];
  };
  counterfactual_improvement: null | Record<string, unknown>;
  candidate_experiments: CandidateExperiment[];
  content_hash: string;
  evidence: {
    source_findings: PatternFinding[];
    detector_versions: Record<string, string>;
    selected_candles: [number, number];
    recurrence_evidence: Record<string, PatternRecurrence[]>;
    partition: string;
    cost_model: Record<string, string>;
    configuration: Record<string, unknown>;
  };
};

export type ResearchTradeRequest = Omit<PatternAnalysisRequest, "selected_start_index" | "selected_end_index"> & { trade_index: number };

export type RuleCondition = { feature: string; operator: string; value?: string | [string, string]; reference?: string; lookback: number };
export type RuleDocument = {
  schema_version: "1.0.0";
  when: { all: RuleCondition[] } | { any: RuleCondition[] };
  then: { action: string; value?: string };
  risk_controls: { minimum_occurrences: number; maximum_drawdown_pct: string; final_test_used_for_tuning: boolean };
};
export type CandidateRuleRecord = {
  candidate_rule_id: string; name: string; description: string; status: string; source_analysis_id: string;
  source_finding_ids: string[]; source_candidate_experiment_id: string; parent_strategy_version: StrategyVersion;
  conditions: RuleDocument["when"]; action: RuleDocument["then"]; risk_controls: RuleDocument["risk_controls"];
  created_by: string; created_at: string; rule_schema_version: string; content_hash: string; research_notes: string;
  evidence: Record<string, unknown>;
};
export type StrategyBranchRecord = { strategy_branch_id: string; parent_strategy_version: StrategyVersion; candidate_rule_id: string; content_hash: string; simulator_version: string };
export type BranchReplayReport = {
  partition: "training" | "validation" | "final_test" | "entire_dataset"; rule_match_count: number; rule_action_count: number;
  parent: Record<string, string | number | null>; candidate: Record<string, string | number | null>; buy_and_hold: Record<string, string>;
  parent_delta: Record<string, string>; buy_and_hold_delta: string; content_hash: string;
};
export type BranchComparison = {
  strategy_branch_id: string; dataset_id: string; reports: Record<string, BranchReplayReport>;
  overfitting_warnings: Array<{ code: string; message: string }>;
  promotion: { eligible: boolean; status: "PROMOTABLE" | "REJECTED"; checks: Record<string, boolean> };
};
export type StrategyPackageExport = { strategy_package: Record<string, unknown> & { content_hash: string; promotion_status: string }; certificate: Record<string, unknown> & { content_hash: string } };

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store", ...init });
  const body = (await response.json()) as T & { error?: { message?: string } };
  if (!response.ok) {
    throw new Error(body.error?.message ?? `Strategy Laboratory request failed (${response.status})`);
  }
  return body;
}

export async function getOfflineDatasets(): Promise<StrategyLabDataset[]> {
  const response = await requestJson<{ items: StrategyLabDataset[] }>("/strategy-lab/datasets");
  return response.items;
}

export function validateCandleCsv(csvText: string, interval?: string): Promise<CsvValidationReport> {
  return requestJson<CsvValidationReport>("/strategy-lab/datasets/validate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ csv_text: csvText, interval }),
  });
}

export function uploadCandleDataset(payload: DatasetUpload): Promise<StrategyLabDataset> {
  return requestJson<StrategyLabDataset>("/strategy-lab/datasets", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

export function replayOfflineStrategy(payload: ReplayRequest, signal?: AbortSignal): Promise<ReplayResult> {
  return requestJson<ReplayResult>("/strategy-lab/replay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

export function analyzePatternSelection(payload: PatternAnalysisRequest): Promise<PatternAnalysis> {
  return requestJson<PatternAnalysis>("/strategy-lab/pattern-intelligence/analyze-selection", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

export function analyzePatternVisibleWindow(payload: PatternAnalysisRequest): Promise<PatternAnalysis> {
  return requestJson<PatternAnalysis>("/strategy-lab/pattern-intelligence/analyze-visible-window", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

export function analyzePatternTrade(payload: Omit<PatternAnalysisRequest, "selected_start_index" | "selected_end_index"> & { trade_index: number }): Promise<PatternAnalysis> {
  return requestJson<PatternAnalysis>("/strategy-lab/pattern-intelligence/analyze-trade", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

function researchRequest(path: string, payload: PatternAnalysisRequest | ResearchTradeRequest): Promise<ResearchExplanation> {
  return requestJson<ResearchExplanation>(`/strategy-lab/research-copilot/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

export const explainResearchSelection = (payload: PatternAnalysisRequest) => researchRequest("explain-selection", payload);
export const showResearchMissed = (payload: PatternAnalysisRequest) => researchRequest("show-missed", payload);
export const explainResearchTrade = (payload: ResearchTradeRequest) => researchRequest("explain-trade", payload);
export const explainResearchSuccess = (payload: ResearchTradeRequest) => researchRequest("explain-success", payload);
export const getResearchOverfittingWarnings = (payload: PatternAnalysisRequest) => researchRequest("overfitting-warnings", payload);

const ruleRequest = <T>(path: string, init?: RequestInit) => requestJson<T>(`/api/v1/strategy-lab${path}`, init);
const jsonPost = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const validateRuleDocument = (ruleDocument: RuleDocument) => ruleRequest<{ valid: true; normalized_rule: RuleDocument }>("/rules/validate-document", jsonPost({ rule_document: ruleDocument }));
export const createCandidateRule = (payload: Record<string, unknown>) => ruleRequest<CandidateRuleRecord>("/rules", jsonPost(payload));
export const createStrategyBranch = (ruleId: string) => ruleRequest<StrategyBranchRecord>(`/rules/${encodeURIComponent(ruleId)}/create-branch`, jsonPost({}));
export const replayStrategyBranch = (branchId: string, datasetId: string, partition: BranchReplayReport["partition"], parameters: StrategyLabParameters) => ruleRequest<BranchReplayReport>(`/branches/${encodeURIComponent(branchId)}/replay`, jsonPost({ dataset_id: datasetId, partition, parameters }));
export const getBranchComparison = (branchId: string, datasetId: string) => ruleRequest<BranchComparison>(`/branches/${encodeURIComponent(branchId)}/comparison?dataset_id=${encodeURIComponent(datasetId)}`);
export const getStrategyPackage = (branchId: string, datasetId: string) => ruleRequest<StrategyPackageExport>(`/branches/${encodeURIComponent(branchId)}/package?dataset_id=${encodeURIComponent(datasetId)}`);