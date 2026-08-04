"use client";

import { useEffect, useState } from "react";

import {
  explainResearchSelection,
  explainResearchSuccess,
  explainResearchTrade,
  getResearchOverfittingWarnings,
  showResearchMissed,
  type PatternAnalysisRequest,
  type PatternFinding,
  type ResearchExplanation,
  type ResearchStatement,
  type ResearchTradeRequest,
} from "@/lib/api/strategyLabOffline";

type Tab = "Observed Patterns" | "Missed Opportunities" | "Failure Analysis" | "Success Analysis" | "Candidate Experiments" | "Overfitting Warnings" | "Research Notes";

const TABS: Tab[] = ["Observed Patterns", "Missed Opportunities", "Failure Analysis", "Success Analysis", "Candidate Experiments", "Overfitting Warnings", "Research Notes"];
const STORAGE_PREFIX = "omnitrade.researchCopilot.notes.";

type Props = {
  selectionPayload: PatternAnalysisRequest | null;
  visiblePayload: PatternAnalysisRequest | null;
  tradePayload: ResearchTradeRequest | null;
  tradeIsLoss: boolean;
  tradeIsWin: boolean;
};

export default function ResearchCopilotPanel({ selectionPayload, visiblePayload, tradePayload, tradeIsLoss, tradeIsWin }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>("Observed Patterns");
  const [results, setResults] = useState<Partial<Record<Tab, ResearchExplanation>>>({});
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const payload = selectionPayload ?? visiblePayload;
  const noteKey = `${STORAGE_PREFIX}${payload?.dataset_id ?? "none"}.${payload?.selected_start_index ?? "none"}.${payload?.selected_end_index ?? "none"}.${tradePayload?.trade_index ?? "none"}`;

  useEffect(() => {
    const stored = window.localStorage.getItem(noteKey);
    if (!stored) { setNote(""); return; }
    try { setNote(String((JSON.parse(stored) as { note?: string }).note ?? "")); }
    catch { setNote(stored); }
  }, [noteKey]);

  const run = async (tab: Tab, request: () => Promise<ResearchExplanation>) => {
    setStatus("loading");
    setError("");
    try {
      const result = await request();
      setResults((current) => ({ ...current, [tab]: result, ...(result.candidate_experiments.length ? { "Candidate Experiments": result } : {}) }));
      setActiveTab(tab);
      setStatus("idle");
    } catch (reason: unknown) {
      setStatus("error");
      setError(reason instanceof Error ? reason.message : "Research explanation failed");
    }
  };

  const saveNote = () => {
    const trimmed = note.trim();
    if (trimmed) window.localStorage.setItem(noteKey, JSON.stringify({
      note: trimmed,
      dataset_id: payload?.dataset_id ?? null,
      selected_range: payload ? [payload.selected_start_index, payload.selected_end_index] : null,
      trade_index: tradePayload?.trade_index ?? null,
      source_analysis_ids: Object.values(results).map((result) => result?.source_analysis_id).filter(Boolean),
      explanation_hashes: Object.values(results).map((result) => result?.content_hash).filter(Boolean),
      candidate_experiment_ids: Object.values(results).flatMap((result) => result?.candidate_experiments.map((item) => item.experiment_id) ?? []),
    }));
    else window.localStorage.removeItem(noteKey);
  };

  const current = results[activeTab];
  return <details className="mb-5 border border-[#416357] bg-[#08110f]" open>
    <summary className="cursor-pointer px-3 py-3 font-mono text-xs uppercase text-[#dce7e1]">AI Research Copilot</summary>
    <div className="border-t border-[#294139] p-3">
      <p className="font-mono text-[9px] text-[#82978e]">DeterministicTemplateProvider · local evidence only</p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button className="lab-button" disabled={!payload || status === "loading"} onClick={() => payload && void run("Observed Patterns", () => explainResearchSelection(payload))}>Explain Selection</button>
        <button className="lab-button" disabled={!payload || status === "loading"} onClick={() => payload && void run("Missed Opportunities", () => showResearchMissed(payload))}>Show Me What I Missed</button>
        <button className="lab-button" disabled={!tradePayload || !tradeIsLoss || status === "loading"} onClick={() => tradePayload && void run("Failure Analysis", () => explainResearchTrade(tradePayload))}>Explain This Loss</button>
        <button className="lab-button" disabled={!tradePayload || !tradeIsWin || status === "loading"} onClick={() => tradePayload && void run("Success Analysis", () => explainResearchSuccess(tradePayload))}>Why Did This Work?</button>
      </div>
      {status === "loading" && <p className="mt-2 font-mono text-[9px] text-[#e0b34c]" role="status">EXPLAINING LOCAL EVIDENCE…</p>}
      {error && <p className="mt-2 font-mono text-[9px] text-[#ef6b5f]" role="alert">{error}</p>}
      <div className="mt-4 flex gap-1 overflow-x-auto pb-1" role="tablist" aria-label="Research Copilot views">
        {TABS.map((tab) => <button key={tab} id={`copilot-tab-${tab.replaceAll(" ", "-")}`} className={`lab-segment min-w-max px-2 text-[9px] ${activeTab === tab ? "lab-segment-active" : ""}`} role="tab" aria-selected={activeTab === tab} aria-controls="research-copilot-panel" onClick={() => setActiveTab(tab)}>{tab}</button>)}
      </div>
      <div id="research-copilot-panel" role="tabpanel" aria-labelledby={`copilot-tab-${activeTab.replaceAll(" ", "-")}`} className="mt-3 min-h-24">
        {activeTab === "Research Notes" ? <div>
          <label htmlFor="research-copilot-note" className="font-mono text-[9px] uppercase text-[#6fa78f]">Local research note</label>
          <textarea id="research-copilot-note" className="lab-input mt-2 min-h-24 resize-y font-mono text-[10px]" value={note} onChange={(event) => setNote(event.target.value)} />
          <button className="lab-button mt-2" type="button" onClick={saveNote}>Save Local Note</button>
          <p className="mt-2 font-mono text-[8px] text-[#82978e]">Stored in this browser and linked to the current range, trade, analysis, and experiment context.</p>
        </div> : activeTab === "Candidate Experiments" ? <ExperimentList result={current} /> : activeTab === "Overfitting Warnings" && !current && payload ? <button className="lab-button" onClick={() => void run("Overfitting Warnings", () => getResearchOverfittingWarnings(payload))}>Check Overfitting Warnings</button> : current ? <StatementList result={current} /> : <p className="border-l border-[#294139] pl-2 font-mono text-[9px] leading-4 text-[#82978e]">Run the relevant action to generate a reproducible explanation.</p>}
      </div>
    </div>
  </details>;
}

function StatementList({ result }: { result: ResearchExplanation }) {
  return <div className="space-y-2" aria-label={`${result.analysis_type} statements`}>
    {result.primary_cause && <p className="border-l-2 border-[#e0b34c] pl-2 font-mono text-[9px] text-[#dce7e1]">PRIMARY CLASSIFICATION · {result.primary_cause.classification.replaceAll("_", " ")} · {result.primary_cause.confidence}</p>}
    {result.statements.map((statement) => <StatementItem key={statement.statement_id} statement={statement} result={result} />)}
  </div>;
}

function StatementItem({ statement, result }: { statement: ResearchStatement; result: ResearchExplanation }) {
  const findings = result.evidence.source_findings.filter((finding) => statement.source_finding_ids.includes(finding.finding_id));
  return <article className="border-l-2 border-[#416357] bg-[#0c1714] p-2">
    <p className={`font-mono text-[8px] font-semibold ${statement.label === "WARNING" ? "text-[#e0b34c]" : statement.label === "INSUFFICIENT EVIDENCE" ? "text-[#ef8b82]" : "text-[#6fa78f]"}`}>{statement.label}</p>
    <p className="mt-1 font-mono text-[9px] uppercase text-[#9fb0aa]">{statement.section}</p>
    <p className="mt-1 text-xs leading-5 text-[#dce7e1]">{statement.text}</p>
    <details className="mt-2">
      <summary className="cursor-pointer font-mono text-[9px] text-[#58b8d8]">View Evidence</summary>
      <div className="mt-2 space-y-2 border-t border-[#294139] pt-2">{findings.map((finding) => <FindingEvidence key={finding.finding_id} finding={finding} result={result} />)}</div>
    </details>
  </article>;
}

function FindingEvidence({ finding, result }: { finding: PatternFinding; result: ResearchExplanation }) {
  return <div className="font-mono text-[8px] leading-4 text-[#9fb0aa]">
    <p className="text-[#dce7e1]">{finding.finding_id} · {finding.detector_id} · v{finding.detector_version}</p>
    <p>Selected candles: {finding.start_index}–{finding.end_index}</p>
    <p>Partition: {result.evidence.partition.replaceAll("_", " ")}</p>
    <p>Cost model: fee {result.evidence.cost_model.fee_pct} · slippage {result.evidence.cost_model.slippage_pct}</p>
    <EvidenceValues title="Measurements" values={finding.measurements} />
    <EvidenceValues title="Thresholds" values={finding.thresholds} />
    <p>Recurrence records: {result.evidence.recurrence_evidence[finding.finding_id]?.length ?? 0}</p>
  </div>;
}

function EvidenceValues({ title, values }: { title: string; values: Record<string, unknown> }) {
  return <div><p className="mt-1 text-[#6fa78f]">{title}</p>{Object.entries(values).map(([key, value]) => <p key={key}>{key.replaceAll("_", " ")}: {String(value)}</p>)}</div>;
}

function ExperimentList({ result }: { result: ResearchExplanation | undefined }) {
  if (!result?.candidate_experiments.length) return <p className="border-l border-[#294139] pl-2 font-mono text-[9px] text-[#82978e]">No evidence-linked candidate experiment has been proposed.</p>;
  return <div className="space-y-2">{result.candidate_experiments.map((experiment) => <article key={experiment.experiment_id} className="border border-[#294139] p-3">
    <p className="font-mono text-[9px] text-[#6fa78f]">Candidate Experiment {experiment.experiment_id}</p>
    <p className="mt-2 text-xs text-[#dce7e1]">{experiment.question}</p>
    <p className="mt-2 font-mono text-[8px] uppercase text-[#82978e]">Suggested controlled change</p>
    <p className="mt-1 text-xs text-[#c7d4ce]">{experiment.suggested_controlled_change}</p>
    <p className="mt-2 font-mono text-[8px] text-[#e0b34c]">NOT AN EXECUTABLE RULE · Required: {experiment.required_tests.join(" · ")}</p>
  </article>)}</div>;
}