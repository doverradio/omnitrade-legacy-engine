"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { ValidationRunEvent } from "@/lib/api/arena";

export type TimelineQuery = {
  order: "newest" | "oldest";
  window: "last_hour" | "last_24_hours" | "entire_run";
  category: "all" | "system" | "market" | "strategy" | "risk" | "execution" | "research" | "database" | "warnings" | "failures" | "manual_notes";
  severity: "all" | "green" | "blue" | "purple" | "yellow" | "red" | "gray";
  search: string;
};

type ValidationRunTimelineProps = {
  events: ValidationRunEvent[];
  query: TimelineQuery;
  loading?: boolean;
  showControls?: boolean;
  hasMore?: boolean;
  emptyMessage?: string;
  title?: string;
  maxHeightClass?: string;
  onQueryChange?: (next: TimelineQuery) => void;
  onLoadMore?: () => void;
};

type RelatedLinks = {
  strategyId?: string;
  campaignId?: string;
  decisionId?: string;
  tradeId?: string;
};

function absoluteTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function relativeTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "unknown time";
  }

  const diffMs = parsed.getTime() - Date.now();
  const absSeconds = Math.floor(Math.abs(diffMs) / 1000);
  if (absSeconds < 60) {
    return diffMs >= 0 ? `in ${absSeconds}s` : `${absSeconds}s ago`;
  }

  const absMinutes = Math.floor(absSeconds / 60);
  if (absMinutes < 60) {
    return diffMs >= 0 ? `in ${absMinutes}m` : `${absMinutes}m ago`;
  }

  const absHours = Math.floor(absMinutes / 60);
  if (absHours < 24) {
    return diffMs >= 0 ? `in ${absHours}h` : `${absHours}h ago`;
  }

  const absDays = Math.floor(absHours / 24);
  return diffMs >= 0 ? `in ${absDays}d` : `${absDays}d ago`;
}

function severityBadgeClass(severity: ValidationRunEvent["severity"]): string {
  if (severity === "green") {
    return "border-emerald-400/40 bg-emerald-500/20 text-emerald-100";
  }
  if (severity === "blue") {
    return "border-sky-400/40 bg-sky-500/20 text-sky-100";
  }
  if (severity === "purple") {
    return "border-violet-400/40 bg-violet-500/20 text-violet-100";
  }
  if (severity === "yellow") {
    return "border-amber-400/40 bg-amber-500/20 text-amber-100";
  }
  if (severity === "red") {
    return "border-rose-400/40 bg-rose-500/20 text-rose-100";
  }
  return "border-slate-400/40 bg-slate-500/20 text-slate-100";
}

function iconBadgeClass(severity: ValidationRunEvent["severity"]): string {
  if (severity === "green") {
    return "border-emerald-300/40 bg-emerald-400/20 text-emerald-100";
  }
  if (severity === "blue") {
    return "border-sky-300/40 bg-sky-400/20 text-sky-100";
  }
  if (severity === "purple") {
    return "border-violet-300/40 bg-violet-400/20 text-violet-100";
  }
  if (severity === "yellow") {
    return "border-amber-300/40 bg-amber-400/20 text-amber-100";
  }
  if (severity === "red") {
    return "border-rose-300/40 bg-rose-400/20 text-rose-100";
  }
  return "border-slate-300/40 bg-slate-400/20 text-slate-100";
}

function eventGlyph(category: ValidationRunEvent["category"], eventType: string): string {
  if (category === "market" || category === "strategy" || category === "execution") {
    return "$";
  }
  if (category === "research") {
    return "R";
  }
  if (category === "database") {
    return "D";
  }
  if (category === "risk") {
    return "E";
  }
  if (category === "warnings" || category === "failures") {
    return "!";
  }
  if (eventType.includes("STARTED") || eventType.includes("RECOVERY")) {
    return "!";
  }
  if (eventType.includes("COMPLETED")) {
    return "*";
  }
  if (eventType.includes("FAILURE") || eventType.includes("WARNING") || eventType.includes("ALERT")) {
    return "x";
  }
  if (eventType.includes("TRADE") || eventType.includes("BUY") || eventType.includes("SELL")) {
    return "$";
  }
  if (eventType.includes("RESEARCH") || eventType.includes("EVOLUTION") || eventType.includes("TOURNAMENT")) {
    return "R";
  }
  return "o";
}

function sanitizeLinkValue(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function extractRelatedLinks(metadata: Record<string, unknown>): RelatedLinks {
  return {
    strategyId: sanitizeLinkValue(metadata.strategy_id),
    campaignId: sanitizeLinkValue(metadata.research_campaign_id),
    decisionId: sanitizeLinkValue(metadata.decision_id),
    tradeId: sanitizeLinkValue(metadata.trade_id),
  };
}

export default function ValidationRunTimeline({
  events,
  query,
  loading = false,
  showControls = true,
  hasMore = false,
  emptyMessage = "No timeline events found.",
  title = "Validation Timeline",
  maxHeightClass = "max-h-[36rem]",
  onQueryChange,
  onLoadMore,
}: ValidationRunTimelineProps) {
  const [expandedEventId, setExpandedEventId] = useState<number | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const previousRef = useRef<{ firstEventId: number | null; height: number }>({
    firstEventId: null,
    height: 0,
  });

  useEffect(() => {
    const node = scrollerRef.current;
    if (!node) {
      return;
    }

    const firstEventId = events[0]?.id ?? null;
    const currentHeight = node.scrollHeight;
    const previous = previousRef.current;

    if (previous.firstEventId !== null && firstEventId !== null && previous.firstEventId !== firstEventId) {
      if (query.order === "newest") {
        const atNewest = node.scrollTop <= 24;
        if (atNewest) {
          node.scrollTop = 0;
        } else {
          node.scrollTop += currentHeight - previous.height;
        }
      } else {
        const nearBottom = node.scrollHeight - node.clientHeight - node.scrollTop <= 24;
        if (nearBottom) {
          node.scrollTop = node.scrollHeight;
        }
      }
    }

    previousRef.current = {
      firstEventId,
      height: currentHeight,
    };
  }, [events, query.order]);

  const eventCountSummary = useMemo(() => `${events.length} event${events.length === 1 ? "" : "s"}`, [events.length]);

  return (
    <section className="rounded-lg border border-border bg-slate-950/50 p-4" aria-label={title}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">{title}</h3>
        <p className="text-xs text-foreground/65">{eventCountSummary}</p>
      </div>

      {showControls ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          <label className="text-xs text-foreground/70">
            Order
            <select
              className="mt-1 w-full rounded-md border border-border bg-background/60 px-2 py-1 text-sm"
              value={query.order}
              onChange={(event) => onQueryChange?.({ ...query, order: event.target.value as TimelineQuery["order"] })}
            >
              <option value="newest">Newest First</option>
              <option value="oldest">Oldest First</option>
            </select>
          </label>

          <label className="text-xs text-foreground/70">
            Time Window
            <select
              className="mt-1 w-full rounded-md border border-border bg-background/60 px-2 py-1 text-sm"
              value={query.window}
              onChange={(event) => onQueryChange?.({ ...query, window: event.target.value as TimelineQuery["window"] })}
            >
              <option value="last_hour">Last Hour</option>
              <option value="last_24_hours">Last 24 Hours</option>
              <option value="entire_run">Entire Run</option>
            </select>
          </label>

          <label className="text-xs text-foreground/70">
            Filter
            <select
              className="mt-1 w-full rounded-md border border-border bg-background/60 px-2 py-1 text-sm"
              value={query.category}
              onChange={(event) => onQueryChange?.({ ...query, category: event.target.value as TimelineQuery["category"] })}
            >
              <option value="all">All</option>
              <option value="system">System</option>
              <option value="market">Market</option>
              <option value="strategy">Strategy</option>
              <option value="risk">Risk</option>
              <option value="execution">Execution</option>
              <option value="research">Research</option>
              <option value="database">Database</option>
              <option value="warnings">Warnings</option>
              <option value="failures">Failures</option>
              <option value="manual_notes">Manual Notes</option>
            </select>
          </label>

          <label className="text-xs text-foreground/70">
            Severity
            <select
              className="mt-1 w-full rounded-md border border-border bg-background/60 px-2 py-1 text-sm"
              value={query.severity}
              onChange={(event) => onQueryChange?.({ ...query, severity: event.target.value as TimelineQuery["severity"] })}
            >
              <option value="all">All</option>
              <option value="green">Healthy</option>
              <option value="blue">Information</option>
              <option value="purple">Research / AI</option>
              <option value="yellow">Warning</option>
              <option value="red">Failure</option>
              <option value="gray">System</option>
            </select>
          </label>

          <label className="text-xs text-foreground/70">
            Search
            <input
              value={query.search}
              onChange={(event) => onQueryChange?.({ ...query, search: event.target.value })}
              placeholder="Search title or description"
              className="mt-1 w-full rounded-md border border-border bg-background/60 px-2 py-1 text-sm"
            />
          </label>
        </div>
      ) : null}

      <div
        ref={scrollerRef}
        data-testid="validation-run-timeline-scroll"
        className={`relative mt-4 overflow-y-auto rounded-md border border-border/70 bg-background/40 p-3 ${maxHeightClass}`}
      >
        <div className="absolute bottom-3 left-[1.09rem] top-3 w-px bg-foreground/20" aria-hidden="true" />

        {loading ? (
          <div className="space-y-3" role="status" aria-label="Loading timeline events">
            <div className="h-20 animate-pulse rounded-lg border border-border bg-background/50" />
            <div className="h-20 animate-pulse rounded-lg border border-border bg-background/50" />
            <div className="h-20 animate-pulse rounded-lg border border-border bg-background/50" />
          </div>
        ) : null}

        {!loading && events.length === 0 ? (
          <p className="rounded-md border border-border bg-background/50 p-3 text-sm text-foreground/75">{emptyMessage}</p>
        ) : null}

        {!loading && events.length > 0 ? (
          <ul className="space-y-3" aria-label="Timeline events">
            {events.map((item) => {
              const isExpanded = expandedEventId === item.id;
              const links = extractRelatedLinks(item.metadata);

              return (
                <li key={item.id} className="relative pl-9">
                  <button
                    type="button"
                    className="group w-full text-left"
                    onClick={() => setExpandedEventId((previous) => (previous === item.id ? null : item.id))}
                    aria-expanded={isExpanded}
                  >
                    <span
                      className={`absolute left-0 top-1.5 inline-flex h-8 w-8 items-center justify-center rounded-full border text-xs font-semibold transition-transform duration-300 group-hover:scale-105 ${iconBadgeClass(item.severity)}`}
                      aria-hidden="true"
                    >
                      {eventGlyph(item.category, item.event_type)}
                    </span>

                    <article className="rounded-lg border border-border bg-slate-900/60 p-3 shadow-sm transition-all duration-300 group-hover:border-foreground/35 group-hover:shadow-md">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <p className="text-sm font-semibold text-foreground">{item.title}</p>
                          <p className="mt-1 text-xs text-foreground/65">{item.event_type.replaceAll("_", " ")}</p>
                        </div>
                        <span className={`rounded-full border px-2 py-1 text-[11px] font-medium uppercase tracking-wide ${severityBadgeClass(item.severity)}`}>
                          {item.severity}
                        </span>
                      </div>

                      <p className="mt-2 text-sm text-foreground/80">{item.description}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-foreground/60">
                        <span>{relativeTimestamp(item.timestamp)}</span>
                        <span aria-hidden="true">|</span>
                        <time dateTime={item.timestamp}>{absoluteTimestamp(item.timestamp)}</time>
                      </div>
                    </article>
                  </button>

                  {isExpanded ? (
                    <div className="ml-1 mt-2 rounded-md border border-border bg-background/70 p-3 text-xs text-foreground/80 transition-all duration-300">
                      <p className="font-medium text-foreground/90">Event Details</p>
                      <p className="mt-1">Timestamp: {absoluteTimestamp(item.timestamp)}</p>

                      <div className="mt-2 grid gap-1 sm:grid-cols-2">
                        <p>Related strategy: {links.strategyId ?? "Not provided"}</p>
                        <p>Related research campaign: {links.campaignId ?? "Not provided"}</p>
                        <p>Decision ID: {links.decisionId ?? "Not provided"}</p>
                        <p>Trade ID: {links.tradeId ?? "Not provided"}</p>
                      </div>

                      <div className="mt-2 flex flex-wrap gap-2">
                        {links.strategyId ? <a className="text-sky-300 hover:text-sky-200" href="/strategy-lab">Open strategy view</a> : null}
                        {links.campaignId ? <a className="text-violet-300 hover:text-violet-200" href="/strategy-lab">Open campaign context</a> : null}
                        {links.decisionId ? <a className="text-emerald-300 hover:text-emerald-200" href="/decision-intelligence">Open decision intelligence</a> : null}
                        {links.tradeId ? <a className="text-amber-300 hover:text-amber-200" href="/paper-trading">Open paper trading</a> : null}
                      </div>

                      <div className="mt-2 rounded border border-border/70 bg-slate-950/60 p-2">
                        <p className="mb-1 text-[11px] uppercase tracking-wide text-foreground/60">Metadata</p>
                        <pre className="whitespace-pre-wrap break-words text-[11px] text-foreground/80">{JSON.stringify(item.metadata, null, 2)}</pre>
                      </div>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>

      {hasMore && onLoadMore ? (
        <div className="mt-3 flex justify-center">
          <button
            type="button"
            onClick={onLoadMore}
            className="rounded-md border border-border bg-background/60 px-3 py-1.5 text-sm text-foreground/85 hover:bg-background/80"
          >
            Load more events
          </button>
        </div>
      ) : null}
    </section>
  );
}
