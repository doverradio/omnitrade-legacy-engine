"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ValidationRunTimeline from "@/components/domain/ValidationRunTimeline";
import {
  type OperationalAlert,
  type OperationalHealthIndicator,
  type ValidationRunEvent,
} from "@/lib/api/arena";
import {
  getMissionControlIntelligence,
  getMissionControlIntelligenceHistory,
  getMissionControlProfit,
  type MissionControlIntelligenceHistoryPoint,
  type MissionControlIntelligenceMetric,
  type MissionControlIntelligenceRange,
  type MissionControlIntelligenceResponse,
  type MissionControlIntelligenceTimelineEvent,
  type MissionControlProfitAnnotation,
  type MissionControlProfitMode,
  type MissionControlProfitResponse,
  type MissionControlProfitSeriesPoint,
  type MissionControlSnapshotHistoryPoint,
  type MissionControlSnapshotHistoryResponse,
} from "@/lib/api/mission-control";
import { getExchangeConnections, type ExchangeConnection } from "@/lib/api/exchange-connections";
import { listCryptoOrderPreviews, type CryptoOrderPreview } from "@/lib/api/crypto-order-previews";
import { useStablePolling } from "@/lib/useStablePolling";

type AccordionKey = "intelligence" | "validationRuns" | "research" | "monitoring" | "infrastructure" | "paperTrading" | "alerts" | "recentTimeline";
type MissionControlTab = "overall" | "profit";

type DrawerRecord = {
  source: "snapshot" | "profit";
  id: string;
  timestamp: string;
  title: string;
  profit: string;
  equity: string;
  trades: number;
  fills: number;
  decisions: number;
  research: number;
  riskEvents: number;
  annotations: string[];
};

const RANGE_OPTIONS: Array<{ value: MissionControlIntelligenceRange; label: string }> = [
  { value: "24h", label: "24H" },
  { value: "72h", label: "72H" },
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
  { value: "90d", label: "90D" },
  { value: "all", label: "ALL" },
];

const PROFIT_MODE_OPTIONS: Array<{ value: MissionControlProfitMode; label: string }> = [
  { value: "paper", label: "PAPER" },
  { value: "live", label: "LIVE" },
  { value: "combined", label: "COMBINED" },
];

const DEFAULT_OPEN_SECTIONS: Record<AccordionKey, boolean> = {
  intelligence: true,
  validationRuns: true,
  research: false,
  monitoring: false,
  infrastructure: false,
  paperTrading: false,
  alerts: false,
  recentTimeline: false,
};

const ACCORDION_STORAGE_KEY = "mission-control-open-sections";

const PROFIT_LINE_COLORS = {
  equity: "#22d3ee",
  cumulative: "#34d399",
  realized: "#f59e0b",
  unrealized: "#a78bfa",
  fees: "#f97316",
  drawdown: "#fb7185",
};

function scoreClass(score: number): string {
  if (score >= 85) {
    return "text-emerald-100";
  }
  if (score >= 70) {
    return "text-amber-100";
  }
  return "text-rose-100";
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "Not available";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Not available";
  }
  return parsed.toLocaleString();
}

function formatDateInput(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return parsed.toISOString().slice(0, 10);
}

function formatCurrency(value: string | null | undefined): string {
  if (value == null) {
    return "Not available";
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(numeric);
}

function formatSignedCurrency(value: string | null | undefined): string {
  if (value == null) {
    return "Not available";
  }
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return value;
  }
  const formatted = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Math.abs(numeric));
  return numeric > 0 ? `+${formatted}` : numeric < 0 ? `-${formatted}` : formatted;
}

function parseNumber(value: string | null | undefined): number {
  if (value == null) {
    return 0;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function toPrettyLabel(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function healthBadgeClass(state: string): string {
  if (state === "green") {
    return "border-emerald-500/40 bg-emerald-500/15 text-emerald-100";
  }
  if (state === "yellow") {
    return "border-amber-500/40 bg-amber-500/15 text-amber-100";
  }
  return "border-rose-500/40 bg-rose-500/15 text-rose-100";
}

function severityBadgeClass(severity: string): string {
  if (severity === "green") {
    return "border-emerald-500/40 bg-emerald-500/15 text-emerald-100";
  }
  if (severity === "blue") {
    return "border-sky-500/40 bg-sky-500/15 text-sky-100";
  }
  if (severity === "purple") {
    return "border-violet-500/40 bg-violet-500/15 text-violet-100";
  }
  if (severity === "yellow") {
    return "border-amber-500/40 bg-amber-500/15 text-amber-100";
  }
  if (severity === "red") {
    return "border-rose-500/40 bg-rose-500/15 text-rose-100";
  }
  return "border-slate-500/40 bg-slate-500/15 text-slate-100";
}

function trendLabel(direction: MissionControlIntelligenceResponse["trend"]["direction"]): string {
  if (direction === "up") {
    return "↑ Improving";
  }
  if (direction === "down") {
    return "↓ Softening";
  }
  return "→ Stable";
}

function expectedGapMs(range: MissionControlIntelligenceRange): number {
  if (range === "24h") {
    return 16 * 60 * 1000;
  }
  if (range === "72h") {
    return 75 * 60 * 1000;
  }
  if (range === "7d") {
    return 4.5 * 60 * 60 * 1000;
  }
  if (range === "30d" || range === "90d") {
    return 30 * 60 * 60 * 1000;
  }
  return 30 * 60 * 60 * 1000;
}

function countByKeys(source: Record<string, number>, keys: string[]): number {
  return Object.entries(source).reduce((acc, [key, value]) => {
    const included = keys.some((needle) => key.toLowerCase().includes(needle));
    return included ? acc + value : acc;
  }, 0);
}

function classifyTimelineEvent(eventType: string, title: string): string {
  const hay = `${eventType} ${title}`.toLowerCase();
  if (hay.includes("validation") && hay.includes("start")) {
    return "Validation Run Started";
  }
  if (hay.includes("trade") && (hay.includes("paper") || hay.includes("fill"))) {
    return "Paper Trade";
  }
  if (hay.includes("reject")) {
    return "Execution Rejected";
  }
  if (hay.includes("research") || hay.includes("campaign") || hay.includes("lab")) {
    return "Research Cycle";
  }
  if (hay.includes("champion")) {
    return "Champion Change";
  }
  if (hay.includes("worker") && hay.includes("recover")) {
    return "Worker Recovery";
  }
  if (hay.includes("database") && hay.includes("recover")) {
    return "Database Recovery";
  }
  if (hay.includes("migration")) {
    return "Migration";
  }
  return toPrettyLabel(eventType);
}

function annotationTitle(annotation: Record<string, unknown>): string | null {
  if (typeof annotation.title === "string" && annotation.title.trim().length > 0) {
    return annotation.title;
  }
  if (typeof annotation.description === "string" && annotation.description.trim().length > 0) {
    return annotation.description;
  }
  if (typeof annotation.event_type === "string") {
    return toPrettyLabel(annotation.event_type);
  }
  return null;
}

function iconForProfitAnnotation(eventType: string): string {
  const normalized = eventType.toLowerCase();
  if (normalized.includes("buy")) {
    return "B";
  }
  if (normalized.includes("sell")) {
    return "S";
  }
  if (normalized.includes("reject") || normalized.includes("fail")) {
    return "!";
  }
  return "*";
}

function AccordionSection({
  id,
  title,
  count,
  open,
  onToggle,
  children,
}: {
  id: AccordionKey;
  title: string;
  count?: number;
  open: boolean;
  onToggle: (key: AccordionKey) => void;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border/80 bg-slate-950/60">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        onClick={() => onToggle(id)}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">{title}</h2>
          {typeof count === "number" ? (
            <span className="rounded-full border border-border bg-background/60 px-2 py-0.5 text-xs text-foreground/75">{count}</span>
          ) : null}
        </div>
        <span className="text-xs text-foreground/65">{open ? "Hide" : "Show"}</span>
      </button>
      {open ? <div className="border-t border-border px-4 py-4">{children}</div> : null}
    </section>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length === 0) {
    return <div className="h-10 rounded-md border border-dashed border-border/60 bg-background/40" />;
  }

  const width = 120;
  const height = 40;
  const padding = 4;
  const points = values
    .map((item, index) => {
      const x = padding + (index / Math.max(values.length - 1, 1)) * (width - padding * 2);
      const y = padding + (1 - Math.max(0, Math.min(100, item)) / 100) * (height - padding * 2);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-10 w-full" role="img" aria-label="Metric sparkline">
      <polyline points={points} fill="none" stroke="rgb(125 211 252)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function MetricCard({ metric }: { metric: MissionControlIntelligenceMetric }) {
  return (
    <article className="rounded-2xl border border-border bg-background/55 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-foreground">{metric.name}</p>
          <p className="mt-1 text-xs text-foreground/65">{metric.trend.label}</p>
        </div>
        <span className={`rounded-full border px-2 py-1 text-sm font-semibold ${scoreClass(metric.score)}`}>{metric.score}</span>
      </div>
      <div className="mt-3">
        <Sparkline values={metric.sparkline} />
      </div>
      <p className="mt-2 text-sm text-foreground/75">{metric.details}</p>
    </article>
  );
}

function MetricStat({ label, value, helper, href }: { label: string; value: string; helper?: string; href?: string }) {
  const content = (
    <>
      <p className="text-[11px] uppercase tracking-wide text-foreground/65">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-foreground">{value}</p>
      {helper ? <p className="mt-1 text-xs text-foreground/70">{helper}</p> : null}
    </>
  );

  if (href) {
    return (
      <Link href={href} className="rounded-2xl border border-border bg-background/55 p-4 transition hover:border-cyan-400/40 hover:bg-cyan-500/10">
        {content}
      </Link>
    );
  }

  return <article className="rounded-2xl border border-border bg-background/55 p-4">{content}</article>;
}

function SnapshotHistoryChart({
  points,
  range,
  events,
  selectedSnapshotId,
  onSelectSnapshot,
}: {
  points: MissionControlSnapshotHistoryPoint[];
  range: MissionControlIntelligenceRange;
  events: MissionControlIntelligenceTimelineEvent[];
  selectedSnapshotId: string | null;
  onSelectSnapshot: (snapshotId: string) => void;
}) {
  if (points.length === 0) {
    return (
      <div className="rounded-[2rem] border border-dashed border-border/80 bg-slate-950/40 p-6 text-sm text-foreground/70">
        No persisted snapshots for this range yet. Gaps are shown as missing, not interpolated.
      </div>
    );
  }

  const width = 1200;
  const height = 360;
  const padding = 30;
  const minX = Math.min(...points.map((item) => new Date(item.bucket_start).getTime()));
  const maxX = Math.max(...points.map((item) => new Date(item.bucket_start).getTime()));
  const validScores = points.map((item) => item.overall_score ?? 0);
  const minScore = Math.max(0, Math.min(...validScores) - 8);
  const maxScore = Math.min(100, Math.max(...validScores) + 8);

  const normalizedPoints = points.map((item) => {
    const ts = new Date(item.bucket_start).getTime();
    const score = item.overall_score ?? 0;
    const x = padding + ((ts - minX) / Math.max(maxX - minX, 1)) * (width - padding * 2);
    const y = padding + (1 - (score - minScore) / Math.max(maxScore - minScore, 1)) * (height - padding * 2);
    return { ...item, x, y, ts, score };
  });

  const maxGap = expectedGapMs(range);
  const segments: Array<{ start: (typeof normalizedPoints)[number]; end: (typeof normalizedPoints)[number] }> = [];
  const gaps: Array<{ left: (typeof normalizedPoints)[number]; right: (typeof normalizedPoints)[number] }> = [];

  for (let index = 0; index < normalizedPoints.length - 1; index += 1) {
    const left = normalizedPoints[index];
    const right = normalizedPoints[index + 1];
    if (right.ts - left.ts > maxGap) {
      gaps.push({ left, right });
      continue;
    }
    segments.push({ start: left, end: right });
  }

  const eventMarkers = events.map((item) => {
    const ts = new Date(item.timestamp).getTime();
    const x = padding + ((ts - minX) / Math.max(maxX - minX, 1)) * (width - padding * 2);
    return { ...item, x };
  });

  return (
    <div className="rounded-[2rem] border border-border/80 bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 p-4 shadow-[0_24px_80px_rgba(15,23,42,0.35)]">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-[22rem] w-full" role="img" aria-label="Intelligence timeline chart">
        {[25, 50, 75].map((level) => (
          <line
            key={level}
            x1={padding}
            x2={width - padding}
            y1={padding + (1 - level / 100) * (height - padding * 2)}
            y2={padding + (1 - level / 100) * (height - padding * 2)}
            className="stroke-border/50"
            strokeDasharray="5 9"
          />
        ))}

        {segments.map((segment, index) => (
          <line
            key={`${segment.start.snapshot_id}-${segment.end.snapshot_id}`}
            x1={segment.start.x}
            y1={segment.start.y}
            x2={segment.end.x}
            y2={segment.end.y}
            stroke="#22d3ee"
            strokeWidth={index === segments.length - 1 ? 4 : 3}
            strokeLinecap="round"
          />
        ))}

        {gaps.map((gap) => (
          <g key={`${gap.left.snapshot_id}-${gap.right.snapshot_id}`}>
            <line
              x1={(gap.left.x + gap.right.x) / 2}
              y1={padding + 10}
              x2={(gap.left.x + gap.right.x) / 2}
              y2={height - padding - 10}
              stroke="#f59e0b"
              strokeDasharray="6 6"
              strokeWidth={1.5}
            />
            <text
              x={(gap.left.x + gap.right.x) / 2}
              y={padding + 6}
              fill="#fbbf24"
              textAnchor="middle"
              fontSize="11"
            >
              GAP
            </text>
          </g>
        ))}

        {normalizedPoints.map((point) => {
          const selected = selectedSnapshotId === point.snapshot_id;
          return (
            <circle
              key={point.snapshot_id}
              cx={point.x}
              cy={point.y}
              r={selected ? 6 : 4}
              fill={selected ? "#38bdf8" : "#94a3b8"}
              stroke={selected ? "#e0f2fe" : "#0f172a"}
              strokeWidth={2}
            />
          );
        })}
      </svg>

      <div className="mt-3 flex flex-wrap gap-2">
        {normalizedPoints.map((point) => {
          const selected = selectedSnapshotId === point.snapshot_id;
          return (
            <button
              key={point.snapshot_id}
              type="button"
              className={`rounded-full border px-3 py-1 text-xs transition ${
                selected ? "border-cyan-300/50 bg-cyan-500/20 text-cyan-50" : "border-white/10 bg-white/5 text-slate-200/75 hover:border-white/20"
              }`}
              onClick={() => onSelectSnapshot(point.snapshot_id)}
              title={`Snapshot ${formatTimestamp(point.bucket_start)} | Score ${point.overall_score ?? 0}`}
            >
              {new Date(point.bucket_start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </button>
          );
        })}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {eventMarkers.map((event) => (
          <span
            key={event.event_id}
            className={`rounded-full border px-2 py-1 text-xs ${severityBadgeClass(event.severity)}`}
            title={`${classifyTimelineEvent(event.event_type, event.title)}: ${event.description}`}
          >
            {classifyTimelineEvent(event.event_type, event.title)}
          </span>
        ))}
      </div>

      {gaps.length > 0 ? <p className="mt-2 text-xs text-amber-200/80">Detected {gaps.length} missing snapshot gap(s). No synthetic interpolation applied.</p> : null}
    </div>
  );
}

function ProfitTimelineChart({
  profit,
  onSelectPoint,
}: {
  profit: MissionControlProfitResponse;
  onSelectPoint: (point: MissionControlProfitSeriesPoint) => void;
}) {
  const series = useMemo(
    () => [...profit.profit_series].sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime()),
    [profit.profit_series],
  );

  if (series.length === 0) {
    return (
      <div className="rounded-[2rem] border border-dashed border-border/80 bg-slate-950/40 p-6 text-sm text-foreground/70">
        No profit points exist for this range yet. Points appear only from durable records.
      </div>
    );
  }

  const width = 1200;
  const height = 360;
  const padding = 30;
  const minX = Math.min(...series.map((item) => new Date(item.timestamp).getTime()));
  const maxX = Math.max(...series.map((item) => new Date(item.timestamp).getTime()));

  function equityValue(point: MissionControlProfitSeriesPoint): number {
    if (profit.mode === "live") {
      return parseNumber(point.live_equity);
    }
    if (profit.mode === "combined") {
      return parseNumber(point.combined_equity);
    }
    return parseNumber(point.paper_equity);
  }

  const yValues = series.flatMap((point) => [
    equityValue(point),
    parseNumber(point.cumulative_net_profit),
    parseNumber(point.cumulative_realized_pnl),
    parseNumber(point.cumulative_unrealized_pnl),
    parseNumber(point.cumulative_fees),
    parseNumber(point.drawdown),
  ]);

  const yMin = Math.min(...yValues);
  const yMax = Math.max(...yValues);

  const plotted = series.map((point) => {
    const ts = new Date(point.timestamp).getTime();
    const x = padding + ((ts - minX) / Math.max(maxX - minX, 1)) * (width - padding * 2);
    const mapY = (value: number) => padding + (1 - (value - yMin) / Math.max(yMax - yMin, 1)) * (height - padding * 2);

    return {
      ...point,
      x,
      ts,
      yEquity: mapY(equityValue(point)),
      yCumulative: mapY(parseNumber(point.cumulative_net_profit)),
      yRealized: mapY(parseNumber(point.cumulative_realized_pnl)),
      yUnrealized: mapY(parseNumber(point.cumulative_unrealized_pnl)),
      yFees: mapY(parseNumber(point.cumulative_fees)),
      yDrawdown: mapY(parseNumber(point.drawdown)),
    };
  });

  function pathFor(points: Array<{ x: number; y: number }>): string {
    return points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x},${point.y}`).join(" ");
  }

  const annotations = profit.annotations.map((annotation) => {
    const ts = new Date(annotation.timestamp).getTime();
    const nearest = plotted.reduce((best, candidate) => {
      if (!best) {
        return candidate;
      }
      return Math.abs(candidate.ts - ts) < Math.abs(best.ts - ts) ? candidate : best;
    }, null as (typeof plotted)[number] | null);
    return { annotation, nearest };
  }).filter((entry) => entry.nearest !== null) as Array<{ annotation: MissionControlProfitAnnotation; nearest: (typeof plotted)[number] }>;

  return (
    <div className="rounded-[2rem] border border-border/80 bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 p-4">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-[22rem] w-full" role="img" aria-label="Profit intelligence timeline chart">
        {[25, 50, 75].map((level) => (
          <line
            key={level}
            x1={padding}
            x2={width - padding}
            y1={padding + (1 - level / 100) * (height - padding * 2)}
            y2={padding + (1 - level / 100) * (height - padding * 2)}
            className="stroke-border/50"
            strokeDasharray="5 9"
          />
        ))}

        <path d={pathFor(plotted.map((point) => ({ x: point.x, y: point.yEquity })))} fill="none" stroke={PROFIT_LINE_COLORS.equity} strokeWidth={3} />
        <path d={pathFor(plotted.map((point) => ({ x: point.x, y: point.yCumulative })))} fill="none" stroke={PROFIT_LINE_COLORS.cumulative} strokeWidth={2.5} />
        <path d={pathFor(plotted.map((point) => ({ x: point.x, y: point.yRealized })))} fill="none" stroke={PROFIT_LINE_COLORS.realized} strokeWidth={2} />
        <path d={pathFor(plotted.map((point) => ({ x: point.x, y: point.yUnrealized })))} fill="none" stroke={PROFIT_LINE_COLORS.unrealized} strokeWidth={2} />
        <path d={pathFor(plotted.map((point) => ({ x: point.x, y: point.yFees })))} fill="none" stroke={PROFIT_LINE_COLORS.fees} strokeWidth={2} />
        <path d={pathFor(plotted.map((point) => ({ x: point.x, y: point.yDrawdown })))} fill="none" stroke={PROFIT_LINE_COLORS.drawdown} strokeWidth={2} />

        {plotted.map((point) => (
          <circle key={point.timestamp} cx={point.x} cy={point.yCumulative} r={4} fill="#4ade80" stroke="#022c22" strokeWidth={1.5} />
        ))}

        {annotations.map((entry, index) => (
          <g key={`${entry.annotation.timestamp}-${index}`}>
            <circle
              cx={entry.nearest.x}
              cy={entry.nearest.yCumulative}
              r={8}
              fill={entry.annotation.event_type.toLowerCase().includes("reject") ? "#fb7185" : entry.annotation.event_type.toLowerCase().includes("buy") ? "#34d399" : "#f59e0b"}
              stroke="#020617"
              strokeWidth={1.5}
            />
            <text
              x={entry.nearest.x}
              y={entry.nearest.yCumulative + 3}
              textAnchor="middle"
              fontSize="9"
              fill="#f8fafc"
            >
              {iconForProfitAnnotation(entry.annotation.event_type)}
            </text>
          </g>
        ))}
      </svg>

      <div className="mt-3 flex flex-wrap gap-2">
        {[
          ["Equity", PROFIT_LINE_COLORS.equity],
          ["Cumulative Profit", PROFIT_LINE_COLORS.cumulative],
          ["Realized", PROFIT_LINE_COLORS.realized],
          ["Unrealized", PROFIT_LINE_COLORS.unrealized],
          ["Fees", PROFIT_LINE_COLORS.fees],
          ["Drawdown", PROFIT_LINE_COLORS.drawdown],
        ].map(([label, color]) => (
          <span key={label} className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-100">
            <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: color }} /> <span className="ml-1">{label}</span>
          </span>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {plotted.map((point) => (
          <button
            key={`point-${point.timestamp}`}
            type="button"
            className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-100 hover:border-cyan-300/50 hover:bg-cyan-500/20"
            onClick={() => onSelectPoint(point)}
            title={`Profit point ${formatTimestamp(point.timestamp)}`}
          >
            {new Date(point.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function MissionControlIntelligenceCenter() {
  const [range, setRange] = useState<MissionControlIntelligenceRange>("24h");
  const [payload, setPayload] = useState<MissionControlIntelligenceResponse | null>(null);
  const [profit, setProfit] = useState<MissionControlProfitResponse | null>(null);
  const [profit24hPaper, setProfit24hPaper] = useState<MissionControlProfitResponse | null>(null);
  const [profit72hPaper, setProfit72hPaper] = useState<MissionControlProfitResponse | null>(null);
  const [profitAllPaper, setProfitAllPaper] = useState<MissionControlProfitResponse | null>(null);
  const [snapshotHistory, setSnapshotHistory] = useState<MissionControlSnapshotHistoryResponse | null>(null);
  const [profitMode, setProfitMode] = useState<MissionControlProfitMode>("paper");
  const [activeTab, setActiveTab] = useState<MissionControlTab>("overall");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exchangeConnection, setExchangeConnection] = useState<ExchangeConnection | null>(null);
  const [latestCryptoPreview, setLatestCryptoPreview] = useState<CryptoOrderPreview | null>(null);
  const [openSections, setOpenSections] = useState<Record<AccordionKey, boolean>>(DEFAULT_OPEN_SECTIONS);
  const [historyCursor, setHistoryCursor] = useState(0);
  const [jumpDate, setJumpDate] = useState("");
  const [drawerRecord, setDrawerRecord] = useState<DrawerRecord | null>(null);
  const [isMobile, setIsMobile] = useState(false);
  const lastSelectedSnapshotIdRef = useRef<string | null>(null);

  useEffect(() => {
    setIsMobile(window.innerWidth < 768);
    const handleResize = () => {
      setIsMobile(window.innerWidth < 768);
    };

    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, []);

  useEffect(() => {
    const raw = window.localStorage.getItem(ACCORDION_STORAGE_KEY);
    if (!raw) {
      return;
    }

    try {
      const parsed = JSON.parse(raw) as Partial<Record<AccordionKey, boolean>>;
      setOpenSections((previous) => ({ ...previous, ...parsed }));
    } catch {
      // Ignore malformed persisted state.
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(ACCORDION_STORAGE_KEY, JSON.stringify(openSections));
  }, [openSections]);

  const fetchMissionControl = useCallback(
    async (signal: AbortSignal) => {
      const [
        next,
        selectedProfit,
        paper24h,
        paper72h,
        allPaper,
        history,
        exchangePayload,
        previewItems,
      ] = await Promise.all([
        getMissionControlIntelligence(range, signal),
        getMissionControlProfit(range, profitMode, signal),
        getMissionControlProfit("24h", "paper", signal),
        getMissionControlProfit("72h", "paper", signal),
        getMissionControlProfit("all", "paper", signal),
        getMissionControlIntelligenceHistory(range, null, signal),
        getExchangeConnections().catch(() => ({ items: [] as ExchangeConnection[] })),
        listCryptoOrderPreviews(1).catch(() => [] as CryptoOrderPreview[]),
      ]);

      return {
        next,
        selectedProfit,
        paper24h,
        paper72h,
        allPaper,
        history,
        exchangePayload,
        previewItems,
      };
    },
    [profitMode, range],
  );

  const polling = useStablePolling(fetchMissionControl, { intervalMs: 15000, enabled: true });

  useEffect(() => {
    setLoading(polling.initialLoading);
    if (!polling.data) {
      if (polling.error) {
        setError(polling.error);
      }
      return;
    }

    setPayload(polling.data.next);
    setProfit(polling.data.selectedProfit);
    setProfit24hPaper(polling.data.paper24h);
    setProfit72hPaper(polling.data.paper72h);
    setProfitAllPaper(polling.data.allPaper);
    setSnapshotHistory(polling.data.history);
    setExchangeConnection(polling.data.exchangePayload.items.find((item) => item.provider === "coinbase_advanced") ?? null);
    setLatestCryptoPreview(polling.data.previewItems[0] ?? null);
    setError(polling.error ?? null);
  }, [polling.data, polling.error, polling.initialLoading]);

  const timelineEvents = useMemo(() => payload?.timeline_events ?? [], [payload?.timeline_events]);

  const historyPoints = useMemo(
    () => [...(snapshotHistory?.points ?? [])].sort((left, right) => new Date(left.bucket_start).getTime() - new Date(right.bucket_start).getTime()),
    [snapshotHistory?.points],
  );

  useEffect(() => {
    if (historyPoints.length === 0) {
      setHistoryCursor(0);
      return;
    }

    if (lastSelectedSnapshotIdRef.current) {
      const index = historyPoints.findIndex((point) => point.snapshot_id === lastSelectedSnapshotIdRef.current);
      if (index >= 0) {
        setHistoryCursor(index);
        return;
      }
    }

    setHistoryCursor(historyPoints.length - 1);
    lastSelectedSnapshotIdRef.current = historyPoints[historyPoints.length - 1].snapshot_id;
  }, [historyPoints]);

  const selectedHistoryPoint = historyPoints[historyCursor] ?? null;

  function selectSnapshot(snapshotId: string): void {
    const index = historyPoints.findIndex((point) => point.snapshot_id === snapshotId);
    if (index < 0) {
      return;
    }
    setHistoryCursor(index);
    lastSelectedSnapshotIdRef.current = snapshotId;

    const point = historyPoints[index];
    setDrawerRecord({
      source: "snapshot",
      id: point.snapshot_id,
      timestamp: point.bucket_start,
      title: "Snapshot Detail",
      profit: formatSignedCurrency(point.paper_net_profit),
      equity: formatCurrency(point.paper_equity),
      trades: countByKeys(point.source_counts, ["trade", "paper_trades"]),
      fills: countByKeys(point.source_counts, ["fill"]),
      decisions: countByKeys(point.source_counts, ["decision"]),
      research: countByKeys(point.source_counts, ["research", "candidate", "campaign"]),
      riskEvents: (point.annotations ?? []).filter((item) => {
        const eventType = String(item.event_type ?? "").toLowerCase();
        return eventType.includes("risk") || eventType.includes("reject") || eventType.includes("alert") || eventType.includes("failure");
      }).length,
      annotations: (point.annotations ?? []).map(annotationTitle).filter((item): item is string => item != null).slice(0, 12),
    });
  }

  function selectProfitPoint(point: MissionControlProfitSeriesPoint): void {
    const timestampMs = new Date(point.timestamp).getTime();
    const nearbyAnnotations = (profit?.annotations ?? []).filter((item) => {
      const annotationMs = new Date(item.timestamp).getTime();
      return Math.abs(annotationMs - timestampMs) <= 30 * 60 * 1000;
    });

    const decisions = nearbyAnnotations.filter((item) => item.event_type.toLowerCase().includes("decision")).length;
    const research = nearbyAnnotations.filter((item) => {
      const normalized = item.event_type.toLowerCase();
      return normalized.includes("research") || normalized.includes("champion") || normalized.includes("campaign");
    }).length;
    const riskEvents = nearbyAnnotations.filter((item) => {
      const normalized = item.event_type.toLowerCase();
      return normalized.includes("risk") || normalized.includes("reject") || normalized.includes("failure");
    }).length;

    setDrawerRecord({
      source: "profit",
      id: point.timestamp,
      timestamp: point.timestamp,
      title: "Profit Point Detail",
      profit: formatSignedCurrency(point.cumulative_net_profit),
      equity: formatCurrency(profit?.mode === "live" ? point.live_equity : profit?.mode === "combined" ? point.combined_equity : point.paper_equity),
      trades: point.trade_count,
      fills: point.source_event_ids.length,
      decisions,
      research,
      riskEvents,
      annotations: nearbyAnnotations.map((item) => `${toPrettyLabel(item.event_type)}: ${item.title}`).slice(0, 12),
    });
  }

  function jumpToDate(): void {
    if (!jumpDate || historyPoints.length === 0) {
      return;
    }
    const target = new Date(`${jumpDate}T00:00:00Z`).getTime();
    if (Number.isNaN(target)) {
      return;
    }
    const nearest = historyPoints.reduce(
      (best, point, index) => {
        const diff = Math.abs(new Date(point.bucket_start).getTime() - target);
        if (diff < best.diff) {
          return { index, diff };
        }
        return best;
      },
      { index: historyPoints.length - 1, diff: Number.POSITIVE_INFINITY },
    );
    selectSnapshot(historyPoints[nearest.index].snapshot_id);
  }

  const selectedRun = useMemo(() => {
    if (!payload) {
      return null;
    }
    if (!payload.selected_validation_run_id) {
      return payload.validation_runs[0] ?? null;
    }
    return payload.validation_runs.find((item) => String(item.validation_run_id) === payload.selected_validation_run_id) ?? null;
  }, [payload]);

  const validationRunTimelineEvents = useMemo<ValidationRunEvent[]>(() => {
    if (!payload) {
      return [];
    }
    return payload.timeline_events.map((item, index) => ({
      id: index + 1,
      validation_run_id: item.related_validation_run ?? payload.selected_validation_run_id ?? "00000000-0000-0000-0000-000000000000",
      timestamp: item.timestamp,
      event_type: item.event_type,
      category: item.category,
      severity: item.severity,
      title: item.title,
      description: item.description,
      metadata: item.metadata,
    }));
  }, [payload]);

  const timelinePolishSummary = useMemo(() => {
    const tracked = [
      "Validation Run Started",
      "Paper Trade",
      "Execution Rejected",
      "Research Cycle",
      "Champion Change",
      "Worker Recovery",
      "Database Recovery",
      "Migration",
    ];

    const counts = new Map<string, { count: number; sample: string }>();
    for (const label of tracked) {
      counts.set(label, { count: 0, sample: "No matching event yet." });
    }

    for (const event of timelineEvents) {
      const normalized = classifyTimelineEvent(event.event_type, event.title);
      if (!counts.has(normalized)) {
        continue;
      }
      const current = counts.get(normalized);
      if (!current) {
        continue;
      }
      counts.set(normalized, {
        count: current.count + 1,
        sample: `${event.title}: ${event.description}`,
      });
    }

    return tracked.map((label) => ({ label, ...(counts.get(label) ?? { count: 0, sample: "No matching event yet." }) }));
  }, [timelineEvents]);

  const heroCards = useMemo(() => {
    return [
      {
        label: "Overall Intelligence",
        value: payload ? `${payload.current_score} / 100` : "Not available",
        helper: payload?.trend.label,
        href: "/mission-control",
      },
      {
        label: "Total Paper Profit",
        value: formatSignedCurrency(profitAllPaper?.net_profit ?? null),
        helper: "All range PAPER",
        href: "/capital",
      },
      {
        label: "24H Profit",
        value: formatSignedCurrency(profit24hPaper?.net_profit ?? null),
        helper: "Paper",
        href: "/capital",
      },
      {
        label: "72H Profit",
        value: formatSignedCurrency(profit72hPaper?.net_profit ?? null),
        helper: "Paper",
        href: "/capital",
      },
      {
        label: "Total Managed Capital",
        value: formatCurrency(payload?.total_managed_capital ?? null),
        helper: "Active campaigns",
        href: "/capital",
      },
      {
        label: "Current Equity",
        value: formatCurrency(profit?.ending_equity ?? null),
        helper: `${range.toUpperCase()} ${profitMode.toUpperCase()}`,
        href: "/capital",
      },
      {
        label: "Research Champion",
        value: String(payload?.operations.research_status.current_champion ?? "None"),
        helper: "Current paper champion",
        href: "/strategy-lab",
      },
      {
        label: "Campaigns Near Profit Target",
        value: String(payload?.campaigns_near_profit_target ?? 0),
        helper: "80-99% toward configured target",
        href: "/capital-campaigns",
      },
      {
        label: "Campaigns at Target",
        value: String(payload?.campaigns_at_target ?? 0),
        helper: "Target threshold reached",
        href: "/capital-campaigns",
      },
      {
        label: "Profit Eligible for Compounding",
        value: formatCurrency(payload?.profit_eligible_for_compounding ?? null),
        helper: "Recommendation-only",
        href: "/capital-campaigns",
      },
      {
        label: "Profit Recommended for Withdrawal",
        value: formatCurrency(payload?.profit_recommended_for_withdrawal ?? null),
        helper: "Recommendation-only",
        href: "/capital-campaigns",
      },
      {
        label: "Profit Awaiting Review",
        value: formatCurrency(payload?.profit_awaiting_review ?? null),
        helper: "Operator action required",
        href: "/capital-campaigns",
      },
      {
        label: "Active Compounding Policies",
        value: String(payload?.active_compounding_policies ?? 0),
        helper: "Policy count",
        href: "/capital-campaigns",
      },
    ];
  }, [payload, profitAllPaper?.net_profit, profit24hPaper?.net_profit, profit72hPaper?.net_profit, profit?.ending_equity, range, profitMode]);

  const selectedTimelineDetail = drawerRecord;

  function toggleAccordion(key: AccordionKey) {
    setOpenSections((previous) => ({ ...previous, [key]: !previous[key] }));
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">Mission Control</h1>
        <p className="max-w-3xl text-sm text-foreground/75">
          Mission Control is read-only operator intelligence. It tracks whether the system is improving without creating synthetic history.
        </p>
      </header>

      {error ? (
        <section className="rounded-2xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-100" role="alert">
          API unavailable: {error}
        </section>
      ) : null}

      {loading ? (
        <section className="rounded-2xl border border-border bg-muted/30 p-3 text-sm text-foreground/80">Loading mission control intelligence...</section>
      ) : null}

      {payload ? (
        <div className="space-y-4">
          <section className="rounded-2xl border border-cyan-400/25 bg-cyan-500/10 p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-cyan-100">Mission Control Hero</h2>
              {polling.refreshing ? <span className="text-xs text-cyan-100/75">Refreshing in background...</span> : null}
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {heroCards.map((card) => (
                <MetricStat key={card.label} label={card.label} value={card.value} helper={card.helper} href={card.href} />
              ))}
            </div>
          </section>

          <section className="rounded-2xl border border-border/80 bg-slate-950/50 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/85">Order Preview</h2>
                <p className="mt-1 text-sm text-foreground/75">Read-only preview evidence. No order has been placed.</p>
              </div>
              <Link href="/crypto-order-preview" className="rounded-full border border-cyan-400/40 bg-cyan-500/15 px-4 py-2 text-sm font-semibold text-cyan-50">
                Open Preview Workspace
              </Link>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <MetricStat label="Latest Status" value={latestCryptoPreview?.status ?? "None"} helper="Preview only" href="/crypto-order-preview" />
              <MetricStat label="Latest Amount" value={latestCryptoPreview ? formatCurrency(latestCryptoPreview.requested_amount) : "$0.00"} helper={latestCryptoPreview?.product_id ?? "BTC-USD"} href="/crypto-order-preview" />
              <MetricStat label="Side" value={latestCryptoPreview?.side ?? "BUY"} helper="Proposal side" href="/crypto-order-preview" />
              <MetricStat label="Risk Verdict" value={latestCryptoPreview?.risk_verdict?.toUpperCase().replaceAll("_", " ") ?? "UNKNOWN"} helper={latestCryptoPreview?.risk_explanation ?? "Awaiting preview"} href="/crypto-order-preview" />
              <MetricStat label="Age" value={latestCryptoPreview ? formatTimestamp(latestCryptoPreview.created_at) : "Not available"} helper={latestCryptoPreview ? `Expires ${formatTimestamp(latestCryptoPreview.expires_at)}` : "No preview yet"} href="/crypto-order-preview" />
            </div>
          </section>

          <AccordionSection id="intelligence" title="Intelligence" open={openSections.intelligence} onToggle={toggleAccordion}>
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricStat label="System Intelligence" value={`${payload.current_score} / 100`} helper={payload.notes} />
                <MetricStat label="Trend" value={trendLabel(payload.trend.direction)} helper={payload.trend.label} />
                <MetricStat label="Delta" value={payload.delta_label} helper="Compared with the start of selected range." />
                <MetricStat label="Confidence" value={payload.confidence} helper={payload.trend.confidence} />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${activeTab === "overall" ? "border-cyan-400/40 bg-cyan-500/20 text-cyan-50" : "border-white/10 bg-white/5 text-slate-200/75 hover:border-white/20 hover:bg-white/10"}`}
                  onClick={() => setActiveTab("overall")}
                >
                  Overall Intelligence
                </button>
                <button
                  type="button"
                  className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${activeTab === "profit" ? "border-emerald-400/40 bg-emerald-500/20 text-emerald-50" : "border-white/10 bg-white/5 text-slate-200/75 hover:border-white/20 hover:bg-white/10"}`}
                  onClick={() => setActiveTab("profit")}
                >
                  Profit
                </button>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {RANGE_OPTIONS.map((option) => {
                  const active = range === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
                        active
                          ? "border-cyan-400/40 bg-cyan-500/20 text-cyan-50"
                          : "border-white/10 bg-white/5 text-slate-200/75 hover:border-white/20 hover:bg-white/10"
                      }`}
                      onClick={() => setRange(option.value)}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>

              {activeTab === "overall" ? (
                <div className="space-y-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-200 hover:border-white/20"
                      disabled={historyCursor <= 0}
                      onClick={() => {
                        const nextIndex = Math.max(0, historyCursor - 1);
                        selectSnapshot(historyPoints[nextIndex].snapshot_id);
                      }}
                    >
                      Previous
                    </button>
                    <button
                      type="button"
                      className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-200 hover:border-white/20"
                      disabled={historyCursor >= historyPoints.length - 1}
                      onClick={() => {
                        const nextIndex = Math.min(historyPoints.length - 1, historyCursor + 1);
                        selectSnapshot(historyPoints[nextIndex].snapshot_id);
                      }}
                    >
                      Next
                    </button>
                    <button
                      type="button"
                      className="rounded-full border border-cyan-300/30 bg-cyan-500/20 px-3 py-1 text-xs text-cyan-50"
                      disabled={historyPoints.length === 0}
                      onClick={() => {
                        const latest = historyPoints[historyPoints.length - 1];
                        if (latest) {
                          selectSnapshot(latest.snapshot_id);
                        }
                      }}
                    >
                      Latest
                    </button>
                    <input
                      type="date"
                      className="rounded-md border border-border bg-background/60 px-2 py-1 text-xs"
                      value={jumpDate || formatDateInput(selectedHistoryPoint?.bucket_start)}
                      onChange={(event) => setJumpDate(event.target.value)}
                    />
                    <button
                      type="button"
                      className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-200 hover:border-white/20"
                      onClick={jumpToDate}
                    >
                      Jump To Date
                    </button>
                  </div>

                  <SnapshotHistoryChart
                    points={historyPoints}
                    range={range}
                    events={timelineEvents}
                    selectedSnapshotId={selectedHistoryPoint?.snapshot_id ?? null}
                    onSelectSnapshot={selectSnapshot}
                  />

                  {selectedHistoryPoint ? (
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                      <MetricStat label="Selected Snapshot" value={formatTimestamp(selectedHistoryPoint.bucket_start)} helper={selectedHistoryPoint.confidence ?? "No confidence value"} />
                      <MetricStat label="Snapshot Score" value={selectedHistoryPoint.overall_score == null ? "Not available" : `${selectedHistoryPoint.overall_score}`} helper="Persisted historical score" />
                      <MetricStat label="Snapshot Profit" value={formatSignedCurrency(selectedHistoryPoint.paper_net_profit)} helper="Paper net profit at snapshot" href="/capital" />
                      <MetricStat label="Snapshot Equity" value={formatCurrency(selectedHistoryPoint.paper_equity)} helper="Paper equity at snapshot" href="/capital" />
                    </div>
                  ) : null}

                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {payload.metric_breakdown.map((metric) => (
                      <MetricCard key={metric.name} metric={metric} />
                    ))}
                  </div>
                </div>
              ) : profit ? (
                <div className="space-y-4">
                  <div className="flex flex-wrap gap-2">
                    {PROFIT_MODE_OPTIONS.map((option) => {
                      const active = option.value === profitMode;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${
                            active
                              ? "border-emerald-300/40 bg-emerald-400/20 text-emerald-50"
                              : "border-white/10 bg-white/5 text-slate-200/75 hover:border-white/20 hover:bg-white/10"
                          }`}
                          onClick={() => setProfitMode(option.value)}
                        >
                          {option.label}
                        </button>
                      );
                    })}
                  </div>

                  <ProfitTimelineChart profit={profit} onSelectPoint={selectProfitPoint} />

                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <MetricStat label="Cumulative Profit" value={formatSignedCurrency(profit.net_profit)} helper="Range net profit" href="/capital" />
                    <MetricStat label="Equity" value={formatCurrency(profit.ending_equity)} helper="Range ending equity" href="/capital" />
                    <MetricStat label="Realized" value={formatSignedCurrency(profit.realized_pnl)} helper="Closed positions" href="/capital" />
                    <MetricStat label="Unrealized" value={formatSignedCurrency(profit.unrealized_pnl)} helper="Open positions" href="/capital" />
                    <MetricStat label="Fees" value={formatSignedCurrency(profit.fees)} helper={profit.fees_available ? "Attributed" : "Unavailable"} href="/capital" />
                    <MetricStat label="Drawdown" value={formatSignedCurrency(profit.max_drawdown_amount)} helper={profit.max_drawdown_percent ? `${profit.max_drawdown_percent}%` : "Not available"} href="/capital" />
                    <MetricStat label="Wins / Losses" value={`${profit.winning_trades} / ${profit.losing_trades}`} helper="Trade outcomes" />
                    <MetricStat label="Trades" value={String(profit.trade_count)} helper={`Open positions ${profit.open_position_count}`} />
                  </div>
                </div>
              ) : null}

              <div className="space-y-2 rounded-2xl border border-border bg-background/45 p-3">
                <p className="text-xs uppercase tracking-wide text-foreground/65">Timeline Annotation Coverage</p>
                <div className="flex flex-wrap gap-2">
                  {timelinePolishSummary.map((item) => (
                    <span
                      key={item.label}
                      className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-200"
                      title={item.sample}
                    >
                      {item.label}: {item.count}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </AccordionSection>

          <AccordionSection id="validationRuns" title="Validation Runs" count={payload.validation_runs.length} open={openSections.validationRuns} onToggle={toggleAccordion}>
            <div className="grid gap-3 lg:grid-cols-2">
              {payload.validation_runs.map((run) => (
                <article key={String(run.validation_run_id)} className="rounded-2xl border border-border bg-background/55 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <p className="text-sm font-semibold text-foreground">{run.name}</p>
                      <p className="mt-1 text-xs text-foreground/65">{run.objective}</p>
                    </div>
                    <span className={`rounded-full border px-2 py-1 text-xs font-medium uppercase tracking-wide ${healthBadgeClass(run.status === "RUNNING" ? "green" : "yellow")}`}>
                      {run.status}
                    </span>
                  </div>
                  <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                    <p>Duration: {run.duration_hours}h</p>
                    <p>Health: {run.health_score ?? "Not available"}</p>
                    <p>Paper capital: {formatCurrency(String(run.paper_capital))}</p>
                    <p>Result: {run.result_status}</p>
                  </div>
                </article>
              ))}
            </div>
          </AccordionSection>

          <AccordionSection id="research" title="Research" count={Number(payload.operations.monitoring.candidate_count)} open={openSections.research} onToggle={toggleAccordion}>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <MetricStat label="Current Campaign" value={String(payload.operations.research_status.current_campaign ?? "None")} />
              <MetricStat label="Current Champion" value={String(payload.operations.research_status.current_champion ?? "None")} />
              <MetricStat label="Campaign Status" value={String(payload.operations.research_status.campaign_status ?? "Unknown")} />
              <MetricStat label="Candidates" value={String(payload.operations.monitoring.candidate_count)} />
              <MetricStat label="Laboratory Runs" value={String(payload.operations.monitoring.laboratory_runs)} />
              <MetricStat label="Evolution Progress" value={String(payload.operations.monitoring.evolution_count)} />
              <MetricStat label="Memory Growth" value={String(payload.operations.monitoring.research_memory_growth)} />
            </div>
          </AccordionSection>

          <AccordionSection id="monitoring" title="Monitoring" count={payload.operations.monitoring.candles_processed} open={openSections.monitoring} onToggle={toggleAccordion}>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <MetricStat label="Candles Processed" value={String(payload.operations.monitoring.candles_processed)} />
              <MetricStat label="Signals Generated" value={String(payload.operations.monitoring.signals_generated)} />
              <MetricStat label="Decision Records" value={String(payload.operations.monitoring.decision_records_created)} />
              <MetricStat label="Replay Count" value={String(payload.operations.monitoring.replay_count)} />
              <MetricStat label="Signals Today" value={String(payload.operations.monitoring.signals_today)} />
              <MetricStat label="Trades Today" value={String(payload.operations.monitoring.trades_today)} />
              <MetricStat label="Paper Trades" value={String(payload.operations.monitoring.paper_trades_executed)} />
              <MetricStat label="Paper Equity" value={formatCurrency(payload.operations.monitoring.paper_equity)} href="/capital" helper="Open Capital Ledger" />
            </div>
          </AccordionSection>

          <AccordionSection id="infrastructure" title="Infrastructure" open={openSections.infrastructure} onToggle={toggleAccordion}>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" role="list" aria-label="System health indicators">
              {([
                ["API", payload.operations.system_health.api],
                ["Orchestrator", payload.operations.system_health.orchestrator],
                ["Database", payload.operations.system_health.database],
                ["Research Agent", payload.operations.system_health.research_agent],
              ] as Array<[string, OperationalHealthIndicator]>).map(([label, indicator]) => (
                <div key={label} className="rounded-2xl border border-border bg-background/55 p-4" role="listitem">
                  <p className="text-xs uppercase tracking-wide text-foreground/70">{label}</p>
                  <div className="mt-2 flex items-center gap-2">
                    <span className={`rounded-full border px-2 py-1 text-xs font-medium ${healthBadgeClass(indicator.state)}`}>{indicator.state.toUpperCase()}</span>
                    <span className="text-sm text-foreground/80">{indicator.detail}</span>
                  </div>
                </div>
              ))}
              <MetricStat label="Run Phase" value={payload.operations.run_status.current_phase} />
              <MetricStat label="Health Status" value={payload.operations.run_status.health_status.toUpperCase()} />
              <MetricStat label="Uptime" value={payload.operations.run_status.uptime} />
              <MetricStat label="Expected End" value={formatTimestamp(payload.operations.run_status.expected_end)} />
              <MetricStat
                label="Exchange Connection"
                value={exchangeConnection ? exchangeConnection.status.toUpperCase() : "DISCONNECTED"}
                helper={exchangeConnection
                  ? `Last Sync ${formatTimestamp(exchangeConnection.last_successful_sync_at)} | ${exchangeConnection.environment.toUpperCase()}`
                  : "No exchange configured"}
                href="/exchange-connections"
              />
            </div>
          </AccordionSection>

          <AccordionSection id="paperTrading" title="Paper Trading" open={openSections.paperTrading} onToggle={toggleAccordion}>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <MetricStat label="Paper Equity" value={formatCurrency(payload.operations.monitoring.paper_equity)} href="/capital" helper="Open Capital Ledger" />
              <MetricStat label="Current Champion" value={String(payload.operations.monitoring.current_champion ?? payload.operations.research_status.current_champion ?? "None")} />
              <MetricStat label="Paper Trades Executed" value={String(payload.operations.monitoring.paper_trades_executed)} />
              <MetricStat label="Signals Today" value={String(payload.operations.monitoring.signals_today)} />
              <MetricStat label="Trades Today" value={String(payload.operations.monitoring.trades_today)} />
            </div>
          </AccordionSection>

          <AccordionSection id="alerts" title="Alerts" count={payload.operations.alerts.length} open={openSections.alerts} onToggle={toggleAccordion}>
            {payload.operations.alerts.length === 0 ? (
              <p className="rounded-2xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-100">No active alerts.</p>
            ) : (
              <ul className="space-y-2">
                {payload.operations.alerts.map((alert: OperationalAlert) => (
                  <li key={alert.code} className={`rounded-2xl border p-3 text-sm ${healthBadgeClass(alert.severity === "red" ? "red" : alert.severity === "yellow" ? "yellow" : "green")}`}>
                    <p className="font-medium">{alert.message}</p>
                    <p className="text-xs opacity-80">{alert.code}</p>
                  </li>
                ))}
              </ul>
            )}
          </AccordionSection>

          <AccordionSection id="recentTimeline" title="Recent Timeline" count={validationRunTimelineEvents.length} open={openSections.recentTimeline} onToggle={toggleAccordion}>
            <ValidationRunTimeline
              title="Recent Timeline"
              events={validationRunTimelineEvents}
              query={{ order: "newest", window: "entire_run", category: "all", severity: "all", search: "" }}
              loading={false}
              showControls={false}
              maxHeightClass="max-h-[28rem]"
              emptyMessage="No validation timeline events available yet."
            />
          </AccordionSection>
        </div>
      ) : null}

      {selectedTimelineDetail ? (
        <div className={`${isMobile ? "fixed inset-0 z-50 flex items-end justify-center bg-slate-950/80 p-4 sm:items-center" : ""}`} role={isMobile ? "dialog" : undefined} aria-modal={isMobile ? "true" : undefined} aria-label={isMobile ? "Timeline event detail" : undefined}>
          <div className={`${isMobile ? "w-full max-w-xl" : ""} rounded-[2rem] border border-border bg-slate-950 p-4 shadow-2xl`}>
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-foreground/65">Point Detail</p>
                <h3 className="mt-1 text-lg font-semibold text-foreground">{selectedTimelineDetail.title}</h3>
              </div>
              <button
                type="button"
                className="rounded-full border border-border bg-background/60 px-3 py-1 text-sm"
                onClick={() => setDrawerRecord(null)}
              >
                Close
              </button>
            </div>
            <div className="mt-3 grid gap-2 text-sm text-foreground/75 sm:grid-cols-2">
              <div className="rounded-xl border border-border bg-background/40 p-3">
                <p className="text-[11px] uppercase tracking-wide text-foreground/60">Timestamp</p>
                <p className="mt-1 text-sm text-foreground">{formatTimestamp(selectedTimelineDetail.timestamp)}</p>
              </div>
              <div className="rounded-xl border border-border bg-background/40 p-3">
                <p className="text-[11px] uppercase tracking-wide text-foreground/60">Profit</p>
                <p className="mt-1 text-sm text-foreground">{selectedTimelineDetail.profit}</p>
              </div>
              <div className="rounded-xl border border-border bg-background/40 p-3">
                <p className="text-[11px] uppercase tracking-wide text-foreground/60">Equity</p>
                <p className="mt-1 text-sm text-foreground">{selectedTimelineDetail.equity}</p>
              </div>
              <div className="rounded-xl border border-border bg-background/40 p-3">
                <p className="text-[11px] uppercase tracking-wide text-foreground/60">Trades / Fills</p>
                <p className="mt-1 text-sm text-foreground">{selectedTimelineDetail.trades} / {selectedTimelineDetail.fills}</p>
              </div>
              <div className="rounded-xl border border-border bg-background/40 p-3">
                <p className="text-[11px] uppercase tracking-wide text-foreground/60">Decisions</p>
                <p className="mt-1 text-sm text-foreground">{selectedTimelineDetail.decisions}</p>
              </div>
              <div className="rounded-xl border border-border bg-background/40 p-3">
                <p className="text-[11px] uppercase tracking-wide text-foreground/60">Research</p>
                <p className="mt-1 text-sm text-foreground">{selectedTimelineDetail.research}</p>
              </div>
              <div className="rounded-xl border border-border bg-background/40 p-3 sm:col-span-2">
                <p className="text-[11px] uppercase tracking-wide text-foreground/60">Risk Events</p>
                <p className="mt-1 text-sm text-foreground">{selectedTimelineDetail.riskEvents}</p>
              </div>
            </div>
            <div className="mt-3 rounded-xl border border-border bg-background/40 p-3">
              <p className="text-[11px] uppercase tracking-wide text-foreground/60">Annotations</p>
              {selectedTimelineDetail.annotations.length === 0 ? (
                <p className="mt-1 text-sm text-foreground/80">No annotations for this point.</p>
              ) : (
                <ul className="mt-1 space-y-1 text-sm text-foreground/80">
                  {selectedTimelineDetail.annotations.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
