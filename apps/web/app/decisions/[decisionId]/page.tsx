"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  ApiRequestError,
  getDecisionInspector,
  type DecisionInspectorResponse,
  type DecisionInspectorStage,
} from "@/lib/api/decisions";

type DecisionInspectorProps = {
  params: {
    decisionId: string;
  };
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Inspector request failed";
}

function stageTone(status: DecisionInspectorStage["status"]): string {
  if (status === "completed") {
    return "border-emerald-500/50 bg-emerald-500/15 text-emerald-100";
  }
  if (status === "rejected" || status === "missing") {
    return "border-rose-500/50 bg-rose-500/15 text-rose-100";
  }
  if (status === "pending") {
    return "border-amber-500/50 bg-amber-500/15 text-amber-100";
  }
  return "border-slate-500/50 bg-slate-500/15 text-slate-100";
}

function linkageTone(status: string): string {
  if (status === "linked") {
    return "border-emerald-500/50 bg-emerald-500/15 text-emerald-100";
  }
  if (status === "missing") {
    return "border-rose-500/50 bg-rose-500/15 text-rose-100";
  }
  if (status === "unavailable") {
    return "border-amber-500/50 bg-amber-500/15 text-amber-100";
  }
  return "border-slate-500/50 bg-slate-500/15 text-slate-100";
}

function cleanGaps(gaps: Array<string | null>): string[] {
  return gaps.filter((item): item is string => Boolean(item && item.trim()));
}

export default function DecisionInspectorPage({ params }: DecisionInspectorProps) {
  const [data, setData] = useState<DecisionInspectorResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load(): Promise<void> {
      setLoading(true);
      setError(null);
      try {
        const payload = await getDecisionInspector(params.decisionId);
        setData(payload);
      } catch (requestError) {
        setError(errorMessage(requestError));
        setData(null);
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, [params.decisionId]);

  const evidenceGaps = useMemo(() => {
    if (!data) {
      return [];
    }
    return cleanGaps(data.narrative.evidence_gaps);
  }, [data]);

  return (
    <div className="space-y-6">
      <header className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Decision header">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Decision Inspector</h1>
            <p className="mt-1 text-sm text-foreground/80">Complete decision narrative for governed investment decisions.</p>
          </div>
          <Link href="/decisions" className="rounded-md border border-border px-3 py-2 text-sm hover:bg-background/60">
            Back to Explorer
          </Link>
        </div>

        {data ? (
          <div className="mt-4 space-y-3">
            <p className="text-lg font-semibold">{data.header.title}</p>
            <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <p className="break-all"><span className="text-foreground/70">Decision ID:</span> {data.header.decision_id}</p>
              <p><span className="text-foreground/70">Status:</span> {data.header.current_status}</p>
              <p><span className="text-foreground/70">Timestamp:</span> {new Date(data.header.timestamp).toLocaleString()}</p>
              <p className="break-all"><span className="text-foreground/70">Strategy:</span> {data.header.strategy ?? "Unavailable"}</p>
              <p className="break-all"><span className="text-foreground/70">Campaign:</span> {data.header.campaign ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Provider:</span> {data.header.provider ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Environment:</span> {data.header.environment}</p>
              <p><span className="text-foreground/70">Market:</span> {data.header.market}</p>
              <p><span className="text-foreground/70">Confidence:</span> {data.header.confidence ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Decision quality:</span> {data.header.decision_quality ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Review status:</span> {data.header.review_status}</p>
            </div>
            <div className="flex flex-wrap gap-2 text-[11px] uppercase tracking-wide">
              <span className="rounded-full border border-slate-500/50 bg-slate-500/15 px-2 py-1">{data.header.environment_badge}</span>
              <span className="rounded-full border border-slate-500/50 bg-slate-500/15 px-2 py-1">{data.header.paper_live_badge}</span>
            </div>
          </div>
        ) : null}
      </header>

      {loading ? <p className="text-sm text-foreground/75">Loading inspector...</p> : null}
      {error ? <p className="text-sm text-rose-200">{error}</p> : null}

      {data ? (
        <>
          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Decision timeline">
            <h2 className="text-lg font-semibold">Decision Timeline</h2>
            <p className="mt-1 text-xs text-foreground/70">Workflow spine from signal to outcome.</p>
            <ol className="mt-4 space-y-3" aria-label="Decision stage timeline">
              {data.timeline.map((stage) => (
                <li key={stage.stage} className="rounded-lg border border-border bg-background/40 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-semibold">{stage.stage}</p>
                    <span className={`rounded-full border px-2 py-1 text-[11px] uppercase tracking-wide ${stageTone(stage.status)}`}>
                      {stage.label}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-foreground/75">{stage.detail}</p>
                </li>
              ))}
            </ol>
          </section>

          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Decision narrative">
            <h2 className="text-lg font-semibold">Why</h2>
            <p className="mt-2 text-sm text-foreground/90">{data.narrative.explanation}</p>
            {evidenceGaps.length > 0 ? (
              <div className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-amber-100">Evidence Gaps</p>
                <ul className="mt-2 space-y-1 text-xs text-amber-50">
                  {evidenceGaps.map((gap) => (
                    <li key={gap}>{gap}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Execution price evidence">
            <h2 className="text-lg font-semibold">Execution Price Evidence</h2>
            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
              <p><span className="text-foreground/70">Validation:</span> {data.execution_price_evidence.validation_status}</p>
              <p><span className="text-foreground/70">Provider:</span> {data.execution_price_evidence.provider ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Venue:</span> {data.execution_price_evidence.venue ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Product:</span> {data.execution_price_evidence.product ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Base:</span> {data.execution_price_evidence.base_currency ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Quote:</span> {data.execution_price_evidence.quote_currency ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Observed price:</span> {data.execution_price_evidence.observed_price ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Bid:</span> {data.execution_price_evidence.bid ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Ask:</span> {data.execution_price_evidence.ask ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Reference:</span> {data.execution_price_evidence.reference_price ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Observed at:</span> {data.execution_price_evidence.observed_timestamp ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Retrieved at:</span> {data.execution_price_evidence.retrieved_timestamp ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Evidence age:</span> {data.execution_price_evidence.evidence_age_seconds ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Freshness:</span> {data.execution_price_evidence.freshness_seconds ?? "Unavailable"}</p>
              <p className="break-all"><span className="text-foreground/70">Evidence ID:</span> {data.execution_price_evidence.evidence_id ?? "Unavailable"}</p>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Risk evaluation">
            <h2 className="text-lg font-semibold">Risk Evaluation</h2>
            <p className="mt-2 text-xs text-foreground/75">Verdict: {data.risk_evaluation.verdict}</p>
            {data.risk_evaluation.first_failing_rule ? (
              <p className="mt-1 text-xs text-rose-200">First failing rule: {String(data.risk_evaluation.first_failing_rule.rule_name)} ({String(data.risk_evaluation.first_failing_rule.reason)})</p>
            ) : null}
            <p className="mt-1 text-xs text-foreground/75">Stopped after first fail: {String(data.risk_evaluation.stopped_after_first_fail)}</p>
            <p className="mt-1 text-xs text-foreground/75">Risk-adjusted sizing: {data.risk_evaluation.risk_adjusted_sizing ?? "Unavailable"}</p>

            <div className="mt-3 overflow-x-auto rounded-lg border border-border">
              <table className="min-w-full text-left text-xs" aria-label="Risk rule ordering">
                <thead className="bg-background/60 uppercase tracking-wide text-foreground/70">
                  <tr>
                    <th className="px-3 py-2">Rule</th>
                    <th className="px-3 py-2">Policy</th>
                    <th className="px-3 py-2">Observed</th>
                    <th className="px-3 py-2">Threshold</th>
                    <th className="px-3 py-2">Result</th>
                    <th className="px-3 py-2">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.risk_evaluation.checks.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-3 py-4 text-center text-foreground/70">Risk rule evidence unavailable.</td>
                    </tr>
                  ) : (
                    data.risk_evaluation.checks.map((check, index) => (
                      <tr key={`${String(check.rule_name)}-${index}`} className="border-t border-border">
                        <td className="px-3 py-2">{String(check.rule_name ?? "Unknown")}</td>
                        <td className="px-3 py-2">{String(check.policy ?? "Unknown")}</td>
                        <td className="px-3 py-2">{String(check.observed_value ?? "Unavailable")}</td>
                        <td className="px-3 py-2">{String(check.threshold ?? "Unavailable")}</td>
                        <td className="px-3 py-2">{String(check.result ?? "UNKNOWN")}</td>
                        <td className="px-3 py-2">{String(check.reason ?? "Unavailable")}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Decision intelligence linkage">
            <h2 className="text-lg font-semibold">Decision Intelligence</h2>
            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
              {Object.entries(data.decision_intelligence).map(([key, value]) => (
                <div key={key} className="rounded-md border border-border bg-background/40 p-2">
                  <p className="uppercase tracking-wide text-foreground/65">{key.replaceAll("_", " ")}</p>
                  <p className="mt-1 font-semibold">{value}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Preview panel">
            <h2 className="text-lg font-semibold">Preview</h2>
            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
              <p><span className="text-foreground/70">Preview ID:</span> {data.preview.preview_id ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Requested amount:</span> {data.preview.requested_amount ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Approved amount:</span> {data.preview.approved_amount ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Estimated quantity:</span> {data.preview.estimated_quantity ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Estimated fees:</span> {data.preview.estimated_fees ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Expiration:</span> {data.preview.expiration ?? "Unavailable"}</p>
              <p><span className="text-foreground/70">Submission state:</span> {data.preview.submission_state}</p>
              <p><span className="text-foreground/70">Execution state:</span> {data.preview.execution_state}</p>
              <p><span className="text-foreground/70">Human approval:</span> {data.preview.human_approval_state}</p>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Audit timeline">
            <h2 className="text-lg font-semibold">Audit Timeline</h2>
            <div className="mt-3 overflow-x-auto rounded-lg border border-border">
              <table className="min-w-full text-left text-xs" aria-label="Chronological audit events">
                <thead className="bg-background/60 uppercase tracking-wide text-foreground/70">
                  <tr>
                    <th className="px-3 py-2">Actor</th>
                    <th className="px-3 py-2">Timestamp</th>
                    <th className="px-3 py-2">Action</th>
                    <th className="px-3 py-2">Correlation ID</th>
                  </tr>
                </thead>
                <tbody>
                  {data.audit_timeline.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-3 py-4 text-center text-foreground/70">No audit timeline entries linked.</td>
                    </tr>
                  ) : (
                    data.audit_timeline.map((event, index) => (
                      <tr key={`${event.action}-${index}`} className="border-t border-border">
                        <td className="px-3 py-2">{event.actor}</td>
                        <td className="px-3 py-2">{new Date(event.timestamp).toLocaleString()}</td>
                        <td className="px-3 py-2">{event.action}</td>
                        <td className="px-3 py-2 break-all">{event.correlation_id ?? "Unavailable"}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Counterfactual panel">
            <h2 className="text-lg font-semibold">Counterfactual</h2>
            <p className="mt-2 text-xs text-foreground/75">{data.counterfactual.summary}</p>
            {data.counterfactual.state_reason ? <p className="mt-1 text-xs text-amber-200">Reason: {data.counterfactual.state_reason}</p> : null}
            <div className="mt-3 overflow-x-auto rounded-lg border border-border">
              <table className="min-w-full text-left text-xs" aria-label="Counterfactual outcomes">
                <thead className="bg-background/60 uppercase tracking-wide text-foreground/70">
                  <tr>
                    <th className="px-3 py-2">Horizon</th>
                    <th className="px-3 py-2">Buy</th>
                    <th className="px-3 py-2">Sell</th>
                    <th className="px-3 py-2">Wait</th>
                    <th className="px-3 py-2">Best action</th>
                  </tr>
                </thead>
                <tbody>
                  {data.counterfactual.items.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-3 py-4 text-center text-foreground/70">Counterfactual package unavailable.</td>
                    </tr>
                  ) : (
                    data.counterfactual.items.map((item, index) => (
                      <tr key={`${String(item.horizon)}-${index}`} className="border-t border-border">
                        <td className="px-3 py-2">{String(item.horizon)} ({String(item.evaluation_horizon_minutes)}m)</td>
                        <td className="px-3 py-2">{String((item.alternative_actions as Record<string, unknown>).buy_return_pct ?? "Unavailable")}</td>
                        <td className="px-3 py-2">{String((item.alternative_actions as Record<string, unknown>).sell_return_pct ?? "Unavailable")}</td>
                        <td className="px-3 py-2">{String((item.alternative_actions as Record<string, unknown>).wait_return_pct ?? "Unavailable")}</td>
                        <td className="px-3 py-2">{String(item.best_action ?? "Unavailable")}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5" aria-label="Linkage health">
            <h2 className="text-lg font-semibold">Linkage Health</h2>
            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
              {data.linkage_health.map((item) => (
                <div key={item.component} className="rounded-md border border-border bg-background/40 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-semibold">{item.component}</p>
                    <span className={`rounded-full border px-2 py-1 text-[11px] uppercase tracking-wide ${linkageTone(item.status)}`}>
                      {item.status}
                    </span>
                  </div>
                  <p className="mt-1 text-foreground/75">{item.reason}</p>
                </div>
              ))}
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
