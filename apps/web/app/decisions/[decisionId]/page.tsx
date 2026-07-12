"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ApiRequestError,
  getDecisionCounterfactualDetail,
  getDecisionExplainability,
  getDecisionRecords,
  type CounterfactualDetail,
  type DecisionExplainability,
  type DecisionRecordItem,
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
  return "Request failed";
}

export default function DecisionInspectorPage({ params }: DecisionInspectorProps) {
  const [record, setRecord] = useState<DecisionRecordItem | null>(null);
  const [explainability, setExplainability] = useState<DecisionExplainability | null>(null);
  const [counterfactual, setCounterfactual] = useState<CounterfactualDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    async function load(): Promise<void> {
      setLoading(true);
      setError(null);
      try {
        const [recordsPayload, explainabilityPayload, counterfactualPayload] = await Promise.all([
          getDecisionRecords({ decision_id: params.decisionId, page: 1, page_size: 1 }),
          getDecisionExplainability(params.decisionId),
          getDecisionCounterfactualDetail(params.decisionId),
        ]);

        setRecord(recordsPayload.items[0] ?? null);
        setExplainability(explainabilityPayload);
        setCounterfactual(counterfactualPayload);
      } catch (requestError) {
        setError(errorMessage(requestError));
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, [params.decisionId]);

  return (
    <div className="space-y-6">
      <header className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">Decision Inspector</h1>
            <p className="mt-1 text-sm text-foreground/80">Detail view for one Decision Record.</p>
            <p className="mt-2 font-mono text-xs text-foreground/70">{params.decisionId}</p>
          </div>
          <Link href="/decisions" className="rounded-md border border-border px-3 py-2 text-sm hover:bg-background/60">
            Back to Explorer
          </Link>
        </div>
      </header>

      {loading ? <p className="text-sm text-foreground/70">Loading decision package...</p> : null}
      {error ? <p className="text-sm text-rose-200">{error}</p> : null}

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5">
        <h2 className="text-lg font-semibold">Decision Summary</h2>
        {!record ? (
          <p className="mt-2 text-sm text-foreground/75">Decision summary unavailable.</p>
        ) : (
          <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
            <p><span className="text-foreground/70">Timestamp:</span> {new Date(record.timestamp).toLocaleString()}</p>
            <p><span className="text-foreground/70">Product:</span> {record.product_id ?? "unknown"}</p>
            <p><span className="text-foreground/70">Provider:</span> {record.provider ?? "unknown"}</p>
            <p><span className="text-foreground/70">Risk verdict:</span> {record.risk_verdict}</p>
            <p><span className="text-foreground/70">Execution status:</span> {record.execution_status}</p>
            <p><span className="text-foreground/70">Evidence completeness:</span> {record.evidence_completeness}</p>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5">
        <h2 className="text-lg font-semibold">Why</h2>
        {!explainability ? (
          <p className="mt-2 text-sm text-foreground/75">Explainability unavailable.</p>
        ) : (
          <div className="mt-3 space-y-3 text-sm">
            <p><span className="text-foreground/70">Decision status:</span> {explainability.decision_status}</p>
            <p className="text-foreground/90">{explainability.explanation}</p>
            <p className="text-xs text-foreground/75">Supporting evidence: {explainability.supporting_evidence.length}</p>
            <p className="text-xs text-foreground/75">Opposing evidence: {explainability.opposing_evidence.length}</p>
            <p className="text-xs text-foreground/75">Risk adjustments: {explainability.risk_adjustments.length}</p>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-border bg-muted/30 p-4 sm:p-5">
        <h2 className="text-lg font-semibold">Counterfactual</h2>
        {!counterfactual ? (
          <p className="mt-2 text-sm text-foreground/75">Counterfactual outcomes unavailable.</p>
        ) : (
          <div className="mt-3 space-y-2 text-sm">
            <p><span className="text-foreground/70">Availability:</span> {counterfactual.availability_state}</p>
            {counterfactual.state_reason ? <p><span className="text-foreground/70">Reason:</span> {counterfactual.state_reason}</p> : null}
            <p><span className="text-foreground/70">Horizon evaluations:</span> {counterfactual.items.length}</p>
          </div>
        )}
      </section>
    </div>
  );
}
