"use client";

import { useEffect, useState } from "react";

import { ApiRequestError } from "@/lib/api/arena";
import {
  getCampaignOrchestrationHistory,
  listCapitalCampaignDomainDefinitions,
  type CampaignOrchestrationCycleSummary,
  type CapitalCampaignDomainDefinition,
  type ProcessTraceEvent,
} from "@/lib/api/capital-campaigns";

const STAGE_ORDER = [
  "OBSERVE_MARKET",
  "DETERMINE_MARKET_STATE",
  "DETERMINE_OPPORTUNITY",
  "CONSTRUCT_TRADE",
  "AUTHORIZE_TRADE",
  "EXECUTE",
  "MONITOR",
  "EXIT",
  "RETURN_CAPITAL",
] as const;

type Stage = (typeof STAGE_ORDER)[number];

const STAGE_LABELS: Record<Stage, string> = {
  OBSERVE_MARKET: "Observe Market",
  DETERMINE_MARKET_STATE: "Determine Market State",
  DETERMINE_OPPORTUNITY: "Determine Opportunity",
  CONSTRUCT_TRADE: "Construct Trade",
  AUTHORIZE_TRADE: "Authorize Trade",
  EXECUTE: "Execute",
  MONITOR: "Monitor",
  EXIT: "Exit",
  RETURN_CAPITAL: "Return Capital",
};

// A candidate is treated as having stopped here -- everything else (PASS,
// APPROVE, WAIT, SELECTED, REPLACED, BUY_LIMIT, ...) is either forward
// progress or an expected retry-later state, not a stop.
const STOPPING_VERDICTS = new Set(["REJECT", "BLOCKED", "NO_OPPORTUNITY"]);

const VERDICT_CLASS: Record<string, string> = {
  PASS: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
  APPROVE: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
  SELECTED: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
  BUY_LIMIT: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
  BUY_NOW: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
  REPLACED: "border-sky-500/40 bg-sky-500/10 text-sky-100",
  RESIZE: "border-sky-500/40 bg-sky-500/10 text-sky-100",
  REJECT: "border-rose-500/40 bg-rose-500/10 text-rose-100",
  BLOCKED: "border-rose-500/40 bg-rose-500/10 text-rose-100",
  NO_OPPORTUNITY: "border-amber-500/40 bg-amber-500/10 text-amber-100",
  NOT_SELECTED: "border-amber-500/40 bg-amber-500/10 text-amber-100",
  WAIT: "border-amber-500/40 bg-amber-500/10 text-amber-100",
  DEFER: "border-amber-500/40 bg-amber-500/10 text-amber-100",
  EXIT_REQUESTED: "border-amber-500/40 bg-amber-500/10 text-amber-100",
  TERMINAL: "border-foreground/30 bg-foreground/5 text-foreground/70",
};

function verdictClass(verdict: string): string {
  return VERDICT_CLASS[verdict] ?? "border-border bg-muted/30 text-foreground/75";
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return "Unable to load the worker trace.";
}

function groupByStage(events: ProcessTraceEvent[]): Record<Stage, ProcessTraceEvent[]> {
  const grouped = Object.fromEntries(STAGE_ORDER.map((stage) => [stage, [] as ProcessTraceEvent[]])) as Record<Stage, ProcessTraceEvent[]>;
  for (const event of events) {
    const stage = event.process_stage as Stage;
    if (grouped[stage]) {
      grouped[stage].push(event);
    }
  }
  for (const stage of STAGE_ORDER) {
    grouped[stage].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  }
  return grouped;
}

function findFirstStop(events: ProcessTraceEvent[]): ProcessTraceEvent | null {
  const chronological = [...events].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  return chronological.find((event) => STOPPING_VERDICTS.has(event.verdict)) ?? null;
}

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "no_campaign" }
  | { status: "ready"; campaign: CapitalCampaignDomainDefinition; cycles: CampaignOrchestrationCycleSummary[] };

export default function ProcessTraceView() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [selectedIndex, setSelectedIndex] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    (async () => {
      try {
        // Reuses the same "governing campaign" discovery the real worker
        // itself relies on -- no hard-coded campaign id.
        const campaigns = await listCapitalCampaignDomainDefinitions();
        if (campaigns.length === 0) {
          if (!cancelled) setState({ status: "no_campaign" });
          return;
        }
        const campaign = campaigns[0];
        const history = await getCampaignOrchestrationHistory(campaign.campaign_id, { version: campaign.version, limit: 10 });
        if (!cancelled) {
          setState({ status: "ready", campaign, cycles: history.items });
          setSelectedIndex(0);
        }
      } catch (error) {
        if (!cancelled) setState({ status: "error", message: errorMessage(error) });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") {
    return <p className="rounded-lg border border-border bg-muted/30 p-4 text-sm text-foreground/60">Loading latest worker cycle...</p>;
  }
  if (state.status === "error") {
    return <p className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-4 text-sm text-rose-100">{state.message}</p>;
  }
  if (state.status === "no_campaign") {
    return <p className="rounded-lg border border-border bg-muted/30 p-4 text-sm text-foreground/60">No governing campaign found. There is no worker cycle to trace yet.</p>;
  }

  const { campaign, cycles } = state;
  if (cycles.length === 0) {
    return <p className="rounded-lg border border-border bg-muted/30 p-4 text-sm text-foreground/60">No worker cycle has been recorded yet for {campaign.name}.</p>;
  }

  const cycle = cycles[selectedIndex] ?? cycles[0];
  const grouped = groupByStage(cycle.process_trace);
  const firstStop = findFirstStop(cycle.process_trace);
  const tracedInstruments = Array.from(
    new Set(cycle.process_trace.map((event) => event.instrument).filter((value): value is string => Boolean(value))),
  );
  const instruments = tracedInstruments.length > 0 ? tracedInstruments : campaign.allowed_instruments;

  return (
    <div className="space-y-4">
      <CycleContextCard campaign={campaign} cycle={cycle} instruments={instruments} />

      {cycles.length > 1 ? (
        <label className="flex flex-wrap items-center gap-2 text-xs text-foreground/65">
          Cycle
          <select
            aria-label="Select worker cycle"
            className="rounded-md border border-border bg-background/60 px-2 py-1 text-xs text-foreground"
            value={selectedIndex}
            onChange={(event) => setSelectedIndex(Number(event.target.value))}
          >
            {cycles.map((item, index) => (
              <option key={item.cycle_id ?? index} value={index}>
                {index === 0 ? "Latest — " : ""}
                {item.started_at ? formatTimestamp(item.started_at) : `Cycle ${index + 1}`}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {firstStop ? <StoppedAtCard event={firstStop} /> : null}

      <ol className="space-y-3">
        {STAGE_ORDER.map((stage, index) => (
          <li key={stage} className="flex items-start gap-3">
            <span
              className="mt-3 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-background/60 text-xs font-semibold text-foreground/75"
              aria-hidden="true"
            >
              {index + 1}
            </span>
            <StageAccordion stage={stage} events={grouped[stage]} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function CycleContextCard({
  campaign,
  cycle,
  instruments,
}: {
  campaign: CapitalCampaignDomainDefinition;
  cycle: CampaignOrchestrationCycleSummary;
  instruments: string[];
}) {
  const fields: Array<[string, string]> = [
    ["Cycle", cycle.cycle_id ?? "—"],
    ["Campaign", `${campaign.name} (v${campaign.version})`],
    ["Started", formatTimestamp(cycle.started_at)],
    ["Completed", formatTimestamp(cycle.completed_at)],
    ["State", cycle.state ?? "—"],
    ["Instrument(s)", instruments.length > 0 ? instruments.join(", ") : "—"],
    ["Termination stage", cycle.termination_stage ?? "—"],
    ["Failure reason", cycle.failure_reason ?? "none"],
  ];
  return (
    <section className="rounded-lg border border-border bg-muted/30 p-4">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-foreground/70">Cycle context</h2>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-2 sm:justify-start">
            <dt className="text-foreground/55">{label}</dt>
            <dd className="text-right text-foreground/85 sm:text-left">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function StoppedAtCard({ event }: { event: ProcessTraceEvent }) {
  return (
    <section className="rounded-lg border border-rose-500/30 bg-rose-500/5 p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-rose-100">Cycle stopped at</p>
      <p className="mt-1 text-sm text-foreground/90">
        {STAGE_LABELS[event.process_stage as Stage] ?? event.process_stage} → {event.gate}
        {event.instrument ? ` (${event.instrument})` : ""}
      </p>
      {event.reason ? (
        <p className="mt-2 text-xs text-foreground/65">
          Reason: <span className="text-foreground/85">{event.reason}</span>
        </p>
      ) : null}
    </section>
  );
}

function StageAccordion({ stage, events }: { stage: Stage; events: ProcessTraceEvent[] }) {
  return (
    <details className="w-full rounded-lg border border-border bg-muted/30">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-foreground/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
        <span>{STAGE_LABELS[stage]}</span>
        {events.length > 0 ? (
          <span className="ml-2 rounded-full border border-border bg-background/60 px-2 py-0.5 text-[11px] text-foreground/65">{events.length}</span>
        ) : null}
      </summary>
      <div className="space-y-2 border-t border-border px-4 py-3">
        {events.length === 0 ? (
          <p className="text-sm text-foreground/55">No trace events recorded for this stage in this cycle.</p>
        ) : (
          events.map((event, index) => <TraceEventCard key={`${event.gate}-${event.timestamp}-${event.instrument ?? "none"}-${index}`} event={event} />)
        )}
      </div>
    </details>
  );
}

function TraceEventCard({ event }: { event: ProcessTraceEvent }) {
  const hasIdentity = Boolean(event.candidate_id || event.decision_record_id || event.attempt_id || event.next);
  return (
    <article className="rounded-md border border-border bg-background/40 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${verdictClass(event.verdict)}`}>{event.verdict}</span>
        <span className="font-mono text-xs text-foreground/75">{event.gate}</span>
        {event.instrument ? <span className="text-xs text-foreground/55">{event.instrument}</span> : null}
      </div>
      {event.reason ? (
        <p className="mt-1 text-xs text-foreground/70">
          Reason: <span className="text-foreground/85">{event.reason}</span>
        </p>
      ) : null}
      {event.observed_value !== null || event.threshold !== null ? (
        <p className="mt-1 text-xs text-foreground/60">
          {event.observed_value !== null ? (
            <>
              Observed: <span className="text-foreground/80">{event.observed_value}</span>
            </>
          ) : null}
          {event.observed_value !== null && event.threshold !== null ? " · " : null}
          {event.threshold !== null ? (
            <>
              Threshold: <span className="text-foreground/80">{event.threshold}</span>
            </>
          ) : null}
        </p>
      ) : null}
      <p className="mt-1 text-[11px] text-foreground/45">{formatTimestamp(event.timestamp)}</p>
      {hasIdentity ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-foreground/50">Details</summary>
          <dl className="mt-1 space-y-0.5 text-[11px] text-foreground/55">
            {event.candidate_id ? <div>candidate_id: {event.candidate_id}</div> : null}
            {event.decision_record_id ? <div>decision_record_id: {event.decision_record_id}</div> : null}
            {event.attempt_id ? <div>attempt_id: {event.attempt_id}</div> : null}
            {event.next ? <div>next: {event.next}</div> : null}
          </dl>
        </details>
      ) : null}
    </article>
  );
}
