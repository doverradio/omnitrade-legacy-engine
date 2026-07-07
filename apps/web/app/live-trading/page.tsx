"use client";

import { useCallback, useMemo, useState } from "react";

import {
  ApiRequestError,
  exportLiveComplianceBundle,
  getLiveApprovalsStatus,
  getLiveComplianceEvidence,
  getLiveExecutionQuality,
  getLiveReconciliationStatus,
  getLiveRegistrationStatus,
  type LiveApprovalStatusResponse,
  type LiveComplianceExportResponse,
  type LiveComplianceReadResponse,
  type LiveExecutionQualityResponse,
  type LiveOperatorWarning,
  type LiveReconciliationSummaryResponse,
  type LiveRegistrationStatusResponse,
} from "@/lib/api/live";

type LiveDashboardState = {
  registration: LiveRegistrationStatusResponse | null;
  approvals: LiveApprovalStatusResponse | null;
  reconciliation: LiveReconciliationSummaryResponse | null;
  executionQuality: LiveExecutionQualityResponse | null;
  compliance: LiveComplianceReadResponse | null;
  complianceExport: LiveComplianceExportResponse | null;
};

const initialState: LiveDashboardState = {
  registration: null,
  approvals: null,
  reconciliation: null,
  executionQuality: null,
  compliance: null,
  complianceExport: null,
};

function mergeWarnings(state: LiveDashboardState): LiveOperatorWarning[] {
  const buckets = [
    ...(state.registration?.warnings ?? []),
    ...(state.approvals?.warnings ?? []),
    ...(state.reconciliation?.warnings ?? []),
    ...(state.executionQuality?.warnings ?? []),
    ...(state.compliance?.warnings ?? []),
    ...(state.complianceExport?.warnings ?? []),
  ];

  const deduped = new Map<string, LiveOperatorWarning>();
  for (const item of buckets) {
    deduped.set(item.code, item);
  }

  return [...deduped.values()];
}

function describeStateLabel(state: "available" | "unknown" | "unavailable" | null): string {
  if (state === "available") {
    return "Available";
  }
  if (state === "unknown") {
    return "Unknown (fail visible)";
  }
  if (state === "unavailable") {
    return "Unavailable (fail visible)";
  }
  return "Not loaded";
}

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Failed to load live operational surfaces.";
}

function isoDateTime(value: string | null | undefined): string {
  if (!value) {
    return "Unknown";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown";
  }
  return parsed.toLocaleString();
}

export default function LiveTradingPage() {
  const [profileId, setProfileId] = useState("");
  const [exportedBy, setExportedBy] = useState("operator:compliance");
  const [data, setData] = useState<LiveDashboardState>(initialState);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const warnings = useMemo(() => mergeWarnings(data), [data]);

  const loadSurfaces = useCallback(async () => {
    const trimmed = profileId.trim();
    if (!trimmed) {
      setError("Enter a live trading profile ID to load operational surfaces.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const [registration, approvals, reconciliation, executionQuality, compliance] = await Promise.all([
        getLiveRegistrationStatus(trimmed),
        getLiveApprovalsStatus(trimmed),
        getLiveReconciliationStatus(trimmed),
        getLiveExecutionQuality(trimmed),
        getLiveComplianceEvidence(trimmed),
      ]);

      setData((previous) => ({
        ...previous,
        registration,
        approvals,
        reconciliation,
        executionQuality,
        compliance,
      }));
    } catch (unknownError) {
      setError(getErrorMessage(unknownError));
    } finally {
      setLoading(false);
    }
  }, [profileId]);

  const runComplianceExport = useCallback(async () => {
    const trimmed = profileId.trim();
    const operator = exportedBy.trim();

    if (!trimmed) {
      setError("Enter a live trading profile ID before exporting compliance records.");
      return;
    }
    if (!operator) {
      setError("Export operator is required for attributable compliance export.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const bundle = await exportLiveComplianceBundle(trimmed, operator);
      setData((previous) => ({ ...previous, complianceExport: bundle }));
    } catch (unknownError) {
      setError(getErrorMessage(unknownError));
    } finally {
      setLoading(false);
    }
  }, [exportedBy, profileId]);

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-amber-400/70 bg-amber-100/80 p-4 text-amber-950 dark:bg-amber-900/30 dark:text-amber-100">
        <h1 className="text-2xl font-semibold">Live Trading Operations</h1>
        <p className="mt-2 text-sm">
          Operator-facing control plane only. This page never submits live orders directly. Paper mode remains default and
          Risk Engine remains mandatory final authority.
        </p>
      </section>

      <section className="grid gap-3 rounded-xl border border-border bg-background/70 p-4 md:grid-cols-3">
        <label className="flex flex-col gap-1 text-sm">
          <span>Live trading profile ID</span>
          <input
            className="rounded-md border border-border bg-background px-3 py-2"
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
            placeholder="Enter live_trading_profile_id"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Compliance export operator</span>
          <input
            className="rounded-md border border-border bg-background px-3 py-2"
            value={exportedBy}
            onChange={(event) => setExportedBy(event.target.value)}
            placeholder="operator:compliance"
          />
        </label>

        <div className="flex items-end gap-2">
          <button
            type="button"
            className="rounded-md bg-emerald-700 px-4 py-2 text-sm text-white disabled:opacity-50"
            onClick={() => {
              void loadSurfaces();
            }}
            disabled={loading}
          >
            Load Operational Status
          </button>
          <button
            type="button"
            className="rounded-md border border-emerald-700 px-4 py-2 text-sm text-emerald-700 disabled:opacity-50"
            onClick={() => {
              void runComplianceExport();
            }}
            disabled={loading}
          >
            Export Compliance Bundle
          </button>
        </div>
      </section>

      {error ? (
        <section className="rounded-xl border border-rose-500/50 bg-rose-500/10 p-4 text-sm text-rose-700 dark:text-rose-300">
          {error}
        </section>
      ) : null}

      <section className="grid gap-4 md:grid-cols-2">
        <article className="rounded-xl border border-border bg-background/70 p-4">
          <h2 className="text-lg font-semibold">Registration / Status</h2>
          <p className="mt-1 text-sm">State: {describeStateLabel(data.registration?.status_state ?? null)}</p>
          <dl className="mt-3 grid grid-cols-1 gap-1 text-sm">
            <div>Readiness: {data.registration?.readiness_state ?? "Unknown"}</div>
            <div>Operating mode: {data.registration?.operating_mode ?? "Unknown"}</div>
            <div>Approval state: {data.registration?.approval_state ?? "Unknown"}</div>
            <div>Risk authority: {data.registration?.risk_authority_model ?? "Unknown"}</div>
          </dl>
        </article>

        <article className="rounded-xl border border-border bg-background/70 p-4">
          <h2 className="text-lg font-semibold">Approvals Workflow</h2>
          <p className="mt-1 text-sm">State: {describeStateLabel(data.approvals?.status_state ?? null)}</p>
          <p className="mt-2 text-sm">Total approval events: {data.approvals?.total_events ?? 0}</p>
          <p className="text-sm">
            Latest checkpoint: {data.approvals?.items[0]?.checkpoint_type ?? "Unknown"} /{" "}
            {data.approvals?.items[0]?.approval_state ?? "Unknown"}
          </p>
        </article>

        <article className="rounded-xl border border-border bg-background/70 p-4">
          <h2 className="text-lg font-semibold">Reconciliation Status</h2>
          <p className="mt-1 text-sm">State: {describeStateLabel(data.reconciliation?.status_state ?? null)}</p>
          <p className="mt-2 text-sm">Unresolved: {data.reconciliation?.unresolved_count ?? 0}</p>
          <p className="text-sm">
            Latest: {data.reconciliation?.latest_reconciliation_status ?? "Unknown"} at{" "}
            {isoDateTime(data.reconciliation?.latest_recorded_at)}
          </p>
        </article>

        <article className="rounded-xl border border-border bg-background/70 p-4">
          <h2 className="text-lg font-semibold">Execution Quality</h2>
          <p className="mt-1 text-sm">State: {describeStateLabel(data.executionQuality?.status_state ?? null)}</p>
          <p className="mt-2 text-sm">Records: {data.executionQuality?.total_records ?? 0}</p>
          <p className="text-sm">Average slippage (bps): {data.executionQuality?.average_slippage_bps ?? "Unknown"}</p>
          <p className="text-sm">
            Unknown/unavailable records: {data.executionQuality?.unknown_or_unavailable_records ?? 0}
          </p>
        </article>

        <article className="rounded-xl border border-border bg-background/70 p-4">
          <h2 className="text-lg font-semibold">Audit / Compliance Evidence</h2>
          <p className="mt-1 text-sm">State: {describeStateLabel(data.compliance?.status_state ?? null)}</p>
          <p className="mt-2 text-sm">Evidence records: {data.compliance?.total_records ?? 0}</p>
          <p className="text-sm">Latest event type: {data.compliance?.items[0]?.event_type ?? "Unknown"}</p>
        </article>

        <article className="rounded-xl border border-border bg-background/70 p-4">
          <h2 className="text-lg font-semibold">Compliance Export</h2>
          <p className="mt-1 text-sm">State: {describeStateLabel(data.complianceExport?.status_state ?? null)}</p>
          <p className="mt-2 text-sm">Exported by: {data.complianceExport?.exported_by ?? "Not exported"}</p>
          <p className="text-sm">Exported at: {isoDateTime(data.complianceExport?.exported_at)}</p>
          <p className="text-sm">Exported records: {data.complianceExport?.total_records ?? 0}</p>
        </article>
      </section>

      <section className="rounded-xl border border-border bg-background/70 p-4">
        <h2 className="text-lg font-semibold">Operator Warnings</h2>
        {warnings.length === 0 ? (
          <p className="mt-2 text-sm">No warnings emitted.</p>
        ) : (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
            {warnings.map((warning) => (
              <li key={warning.code}>
                <strong>{warning.code}</strong>: {warning.message}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
