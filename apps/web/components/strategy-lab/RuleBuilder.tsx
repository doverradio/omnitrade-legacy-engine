"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import {
  createCandidateRule, createStrategyBranch, getBranchComparison, getStrategyPackage, replayStrategyBranch,
  validateRuleDocument, type BranchComparison, type CandidateExperiment, type CandidateRuleRecord,
  type PatternAnalysisRequest, type PatternFinding, type ResearchExplanation, type RuleCondition, type RuleDocument,
  type StrategyBranchRecord, type StrategyPackageExport,
} from "@/lib/api/strategyLabOffline";

const FEATURES = ["close", "close_above_previous_high", "close_below_previous_low", "consecutive_higher_closes", "consecutive_lower_closes", "higher_lows", "lower_highs", "slope", "rolling_range_pct", "volatility_contraction_pct", "volatility_expansion_pct", "volatility_percentile", "short_window_momentum", "momentum_acceleration", "momentum_deceleration", "rapid_recovery", "rapid_decline", "volume_above_rolling_mean", "volume_above_rolling_median", "volume_expansion_pct", "price_volume_confirmation", "price_volume_divergence", "strategy_state", "capital_vs_baseline_pct"];
const OPERATORS = ["<", "<=", ">", ">=", "==", "between", "crosses_above", "crosses_below"];
const ACTIONS = ["ALLOW_LONG_ENTRY", "BLOCK_LONG_ENTRY", "WAIT_FOR_CONFIRMATION", "CHANGE_ENTRY_OFFSET"];
const VALUE_ACTIONS = new Set(["CHANGE_ENTRY_OFFSET"]);
const PARTITIONS = ["training", "validation", "final_test", "entire_dataset"] as const;

type Props = { experiment: CandidateExperiment; result: ResearchExplanation; payload: PatternAnalysisRequest; onClose: () => void };

export default function RuleBuilder({ experiment, result, payload, onClose }: Props) {
  const sourceFindings = result.evidence.source_findings.filter((finding) => experiment.source_finding_ids.includes(finding.finding_id));
  const suggested = suggestionFromEvidence(sourceFindings, result.evidence.selected_candles);
  const [name, setName] = useState(suggested.name);
  const [combine, setCombine] = useState<"all" | "any">("all");
  const [conditions, setConditions] = useState<RuleCondition[]>([suggested.condition]);
  const [action, setAction] = useState(suggested.action);
  const [actionValue, setActionValue] = useState("0.01");
  const [minimumOccurrences, setMinimumOccurrences] = useState(5);
  const [maximumDrawdown, setMaximumDrawdown] = useState("10");
  const [notes, setNotes] = useState("");
  const [validated, setValidated] = useState(false);
  const [candidate, setCandidate] = useState<CandidateRuleRecord | null>(null);
  const [branch, setBranch] = useState<StrategyBranchRecord | null>(null);
  const [comparison, setComparison] = useState<BranchComparison | null>(null);
  const [packageExport, setPackageExport] = useState<StrategyPackageExport | null>(null);
  const [status, setStatus] = useState("Review the evidence-prefilled condition before validation.");
  const [busy, setBusy] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const ruleDocument: RuleDocument = {
    schema_version: "1.0.0",
    when: combine === "all" ? { all: conditions } : { any: conditions },
    then: { action, ...(VALUE_ACTIONS.has(action) ? { value: actionValue } : {}) },
    risk_controls: { minimum_occurrences: minimumOccurrences, maximum_drawdown_pct: maximumDrawdown, final_test_used_for_tuning: false },
  };
  const invalidate = () => { setValidated(false); setCandidate(null); setBranch(null); setComparison(null); setPackageExport(null); };

  const validate = async () => execute(async () => {
    await validateRuleDocument(ruleDocument);
    setValidated(true);
    setStatus("Rule schema valid. Human approval is still required to save the draft.");
  });
  const save = async () => execute(async () => {
    const created = await createCandidateRule({
      name, description: experiment.question, source_analysis_id: result.source_analysis_id,
      source_finding_ids: experiment.source_finding_ids, source_candidate_experiment_id: experiment.experiment_id,
      parent_strategy_version: payload.strategy_version, rule_document: ruleDocument, created_by: "human_with_copilot",
      research_notes: notes,
      evidence: {
        suggested_by: "Research Copilot", detector_versions: result.evidence.detector_versions,
        measurements: Object.fromEntries(sourceFindings.map((finding) => [finding.finding_id, finding.measurements])),
        why: suggested.why, explanation_content_hash: result.content_hash,
      },
    });
    setCandidate(created);
    window.localStorage.setItem(`omnitrade.ruleDiscovery.rules.${created.candidate_rule_id}`, JSON.stringify(created));
    setStatus(`Draft ${created.candidate_rule_id} saved locally and to the offline artifact store.`);
  });
  const createBranch = async () => candidate && execute(async () => {
    const created = await createStrategyBranch(candidate.candidate_rule_id);
    setBranch(created);
    window.localStorage.setItem(`omnitrade.ruleDiscovery.branches.${created.strategy_branch_id}`, JSON.stringify(created));
    setStatus(`Immutable branch ${created.strategy_branch_id} created. Parent Strategy #${created.parent_strategy_version} remains unchanged.`);
  });
  const replayAll = async () => branch && execute(async () => {
    for (const partition of PARTITIONS) await replayStrategyBranch(branch.strategy_branch_id, payload.dataset_id, partition, payload.parameters);
    const compared = await getBranchComparison(branch.strategy_branch_id, payload.dataset_id);
    setComparison(compared);
    window.localStorage.setItem(`omnitrade.ruleDiscovery.replays.${branch.strategy_branch_id}.${payload.dataset_id}`, JSON.stringify(compared));
    setStatus(`Training, Validation, Final Test, and Entire Dataset replay completed: ${compared.promotion.status}.`);
  });
  const loadPackage = async () => branch && execute(async () => {
    const exported = await getStrategyPackage(branch.strategy_branch_id, payload.dataset_id);
    setPackageExport(exported);
    window.localStorage.setItem(`omnitrade.ruleDiscovery.certificates.${branch.strategy_branch_id}.${payload.dataset_id}`, JSON.stringify(exported.certificate));
    setStatus("Hashed strategy package and Candidate Rule Certificate generated.");
  });
  async function execute(operation: () => Promise<void>) {
    setBusy(true);
    try { await operation(); }
    catch (reason) { setStatus(reason instanceof Error ? reason.message : "Rule Discovery operation failed"); }
    finally { setBusy(false); }
  }

  if (!mounted) return null;
  return createPortal(<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-3" role="dialog" aria-modal="true" aria-label="Candidate Rule Builder">
    <div className="max-h-[94vh] w-full max-w-6xl overflow-auto border border-[#416357] bg-[#0c1714] p-4 shadow-2xl sm:p-5">
      <div className="flex items-start justify-between gap-4 border-b border-[#294139] pb-3"><div><p className="font-mono text-[9px] uppercase text-[#6fa78f]">Rule Discovery · Human approval required</p><h2 className="mt-1 font-serif text-2xl text-[#e6eee9]">Candidate Rule Builder</h2></div><button className="lab-button" type="button" onClick={onClose}>Discard</button></div>
      <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(320px,.85fr)]">
        <div className="min-w-0 space-y-4">
          <Field label="Rule Name"><input className="lab-input" value={name} onChange={(event) => { setName(event.target.value); invalidate(); }} /></Field>
          <div className="grid gap-3 sm:grid-cols-2"><ReadOnly label="Parent Strategy" value={`Strategy #${payload.strategy_version}`} /><ReadOnly label="Source Experiment" value={experiment.experiment_id} /></div>
          <section aria-labelledby="source-evidence"><h3 id="source-evidence" className="font-mono text-[10px] uppercase text-[#6fa78f]">Source Evidence</h3><p className="mt-2 text-xs text-[#dce7e1]">Suggested by Research Copilot · explicit human review required</p><p className="mt-1 font-mono text-[9px] text-[#82978e]">{suggested.why}</p>{sourceFindings.map((finding) => <Evidence finding={finding} key={finding.finding_id} />)}</section>
          <section aria-labelledby="when-heading"><div className="flex items-center justify-between gap-3"><h3 id="when-heading" className="font-mono text-[10px] uppercase text-[#6fa78f]">WHEN Conditions</h3><label className="font-mono text-[9px] text-[#aebdb7]">Combine <select className="lab-input ml-2 inline-block w-auto" value={combine} onChange={(event) => { setCombine(event.target.value as "all" | "any"); invalidate(); }}><option value="all">ALL</option><option value="any">ANY</option></select></label></div><div className="mt-2 space-y-2">{conditions.map((condition, index) => <ConditionEditor key={index} condition={condition} index={index} onChange={(next) => { setConditions((items) => items.map((item, itemIndex) => itemIndex === index ? next : item)); invalidate(); }} onRemove={() => { setConditions((items) => items.filter((_, itemIndex) => itemIndex !== index)); invalidate(); }} />)}</div><button className="lab-button mt-2" type="button" onClick={() => { setConditions((items) => [...items, { feature: "slope", operator: "<", value: "0", lookback: 4 }]); invalidate(); }}>Add Condition</button></section>
          <section aria-labelledby="then-heading"><h3 id="then-heading" className="font-mono text-[10px] uppercase text-[#6fa78f]">THEN Action</h3><div className="mt-2 grid gap-2 sm:grid-cols-2"><Field label="Action"><select className="lab-input" value={action} onChange={(event) => { setAction(event.target.value); invalidate(); }}>{ACTIONS.map((item) => <option key={item}>{item}</option>)}</select></Field>{VALUE_ACTIONS.has(action) && <Field label="Action value"><input className="lab-input" value={actionValue} onChange={(event) => { setActionValue(event.target.value); invalidate(); }} /></Field>}</div></section>
          <section aria-labelledby="risk-heading"><h3 id="risk-heading" className="font-mono text-[10px] uppercase text-[#6fa78f]">Risk Controls</h3><div className="mt-2 grid gap-2 sm:grid-cols-2"><Field label="Minimum occurrences"><input className="lab-input" type="number" min={1} value={minimumOccurrences} onChange={(event) => { setMinimumOccurrences(Number(event.target.value)); invalidate(); }} /></Field><Field label="Maximum drawdown %"><input className="lab-input" type="number" min={0} step="0.1" value={maximumDrawdown} onChange={(event) => { setMaximumDrawdown(event.target.value); invalidate(); }} /></Field></div></section>
          <Field label="Research Notes"><textarea className="lab-input min-h-20 resize-y" value={notes} onChange={(event) => setNotes(event.target.value)} /></Field>
        </div>
        <aside className="min-w-0 border-l-0 border-[#294139] lg:border-l lg:pl-5"><h3 className="font-mono text-[10px] uppercase text-[#6fa78f]">Generated Rule JSON</h3><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all border border-[#294139] bg-[#08110f] p-3 font-mono text-[9px] leading-4 text-[#c7d4ce]">{JSON.stringify(ruleDocument, null, 2)}</pre><div className="mt-3 grid grid-cols-2 gap-2"><button className="lab-button" disabled={busy || !conditions.length} onClick={() => void validate()}>Validate Rule</button><button className="lab-button" disabled={busy || !validated} onClick={() => void save()}>Save as Draft</button><button className="lab-button" disabled={busy || !candidate} onClick={() => void createBranch()}>Create Branch</button><button className="lab-button" disabled={busy || !branch} onClick={() => void replayAll()}>Replay All Partitions</button><button className="lab-button" onClick={() => void copyJson(ruleDocument)}>Copy Rule JSON</button><button className="lab-button" onClick={() => downloadJson(`${candidate?.candidate_rule_id ?? "candidate-rule"}.json`, ruleDocument)}>Download Rule JSON</button><button className="lab-button col-span-2" disabled={busy || !comparison} onClick={() => void loadPackage()}>Generate Candidate Rule Certificate</button>{packageExport && <><button className="lab-button" onClick={() => void copyJson(packageExport.strategy_package)}>Copy Strategy Package JSON</button><button className="lab-button" onClick={() => downloadJson(`${branch?.strategy_branch_id}.package.json`, packageExport)}>Download Strategy Package</button></>}</div><p className="mt-3 border-l-2 border-[#e0b34c] pl-2 font-mono text-[9px] leading-4 text-[#dce7e1]" role="status">{busy ? "RUNNING DETERMINISTIC LOCAL OPERATION…" : status}</p>{comparison && <Comparison comparison={comparison} />}</aside>
      </div>
    </div>
  </div>, document.body);
}

function suggestionFromEvidence(findings: PatternFinding[], selected: [number, number]) {
  const finding = findings[0];
  const lookback = Math.max(2, Math.min(50, selected[1] - selected[0] + 1));
  if (finding?.detector_id === "negative_slope_v1") return { name: "Block entries during negative slope", condition: { feature: "slope", operator: "<", value: "0", lookback }, action: "BLOCK_LONG_ENTRY", why: `${finding.finding_id} measured a negative least-squares slope; the proposal tests only an entry block while that measured structure is present.` };
  if (finding?.detector_id.includes("momentum")) return { name: "Block entries during negative momentum", condition: { feature: "short_window_momentum", operator: "<", value: "0", lookback }, action: "BLOCK_LONG_ENTRY", why: `${finding.finding_id} supplied structured momentum measurements; no additional market claim was introduced.` };
  if (finding?.detector_id === "volatility_contraction_v1") return { name: "Wait during measured volatility contraction", condition: { feature: "volatility_contraction_pct", operator: ">=", value: String(finding.measurements.range_contraction_pct ?? "0"), lookback }, action: "WAIT_FOR_CONFIRMATION", why: `${finding.finding_id} supplied the contraction threshold used by this condition.` };
  return { name: "Evidence-linked controlled condition", condition: { feature: "slope", operator: "<", value: "0", lookback }, action: "WAIT_FOR_CONFIRMATION", why: "No automatic detector mapping was available. Review or replace this draft condition before validation." };
}

function ConditionEditor({ condition, index, onChange, onRemove }: { condition: RuleCondition; index: number; onChange: (condition: RuleCondition) => void; onRemove: () => void }) {
  const threshold = Array.isArray(condition.value) ? condition.value.join(", ") : String(condition.value ?? "0");
  return <fieldset className="grid gap-2 border border-[#294139] p-2 sm:grid-cols-[1fr_90px_90px_80px_auto]"><legend className="px-1 font-mono text-[8px] text-[#82978e]">Condition {index + 1}</legend><label className="font-mono text-[8px] text-[#82978e]">Feature<select className="lab-input mt-1" value={condition.feature} onChange={(event) => onChange({ ...condition, feature: event.target.value, value: event.target.value === "strategy_state" ? "flat" : "0" })}>{FEATURES.map((item) => <option key={item}>{item}</option>)}</select></label><label className="font-mono text-[8px] text-[#82978e]">Operator<select className="lab-input mt-1" value={condition.operator} onChange={(event) => onChange({ ...condition, operator: event.target.value, value: event.target.value === "between" ? ["0", "1"] : "0" })}>{OPERATORS.map((item) => <option key={item}>{item}</option>)}</select></label><label className="font-mono text-[8px] text-[#82978e]">{condition.operator === "between" ? "Range" : "Threshold"}<input className="lab-input mt-1" value={threshold} onChange={(event) => onChange({ ...condition, value: condition.operator === "between" ? rangeValue(event.target.value) : event.target.value })} /></label><label className="font-mono text-[8px] text-[#82978e]">Lookback<input className="lab-input mt-1" type="number" min={1} max={500} value={condition.lookback} onChange={(event) => onChange({ ...condition, lookback: Number(event.target.value) })} /></label><button className="lab-button self-end" type="button" disabled={index === 0} onClick={onRemove}>Remove</button></fieldset>;
}
function Evidence({ finding }: { finding: PatternFinding }) { return <div className="mt-2 border-l-2 border-[#416357] pl-2 font-mono text-[8px] leading-4 text-[#9fb0aa]"><p className="text-[#dce7e1]">{finding.finding_id} · {finding.detector_id} · v{finding.detector_version}</p><p>Measurements: {JSON.stringify(finding.measurements)}</p></div>; }
function Comparison({ comparison }: { comparison: BranchComparison }) { return <section className="mt-4" aria-label="Parent candidate and buy and hold comparison"><div className="flex items-center justify-between"><h3 className="font-mono text-[10px] uppercase text-[#6fa78f]">Partition Comparison</h3><span className={`font-mono text-[9px] ${comparison.promotion.eligible ? "text-[#31c48d]" : "text-[#ef6b5f]"}`}>{comparison.promotion.status}</span></div><div className="mt-2 overflow-auto"><table className="w-full min-w-[520px] border-collapse font-mono text-[9px]"><thead><tr className="text-left text-[#82978e]"><th>Partition</th><th>Parent</th><th>Candidate</th><th>Buy & Hold</th><th>Matches</th></tr></thead><tbody>{(["training", "validation", "final_test"] as const).map((partition) => { const report = comparison.reports[partition]; return report ? <tr key={partition} className="border-t border-[#294139]"><td>{partition.replaceAll("_", " ")}</td><td>{String(report.parent.net_return_pct)}%</td><td>{String(report.candidate.net_return_pct)}%</td><td>{report.buy_and_hold.return_pct}%</td><td>{report.rule_match_count}</td></tr> : null; })}</tbody></table></div>{comparison.overfitting_warnings.map((warning) => <p key={warning.code} className="mt-2 font-mono text-[8px] text-[#e0b34c]">{warning.code}: {warning.message}</p>)}</section>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block font-mono text-[9px] text-[#aebdb7]"><span className="mb-1 block">{label}</span>{children}</label>; }
function ReadOnly({ label, value }: { label: string; value: string }) { return <div><p className="font-mono text-[8px] uppercase text-[#82978e]">{label}</p><p className="mt-1 font-mono text-xs text-[#dce7e1]">{value}</p></div>; }
function rangeValue(value: string): [string, string] { const [minimum = "", maximum = ""] = value.split(",", 2); return [minimum.trim(), maximum.trim()]; }
async function copyJson(value: unknown) { await navigator.clipboard?.writeText(JSON.stringify(value, null, 2)); }
function downloadJson(name: string, value: unknown) { const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" })); link.download = name; link.click(); URL.revokeObjectURL(link.href); }