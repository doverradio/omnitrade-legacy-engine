"use client";

const INTERVALS = ["1m", "5m", "15m", "1h", "1d"] as const;

export type MarketInterval = (typeof INTERVALS)[number];

type IntervalSelectorProps = {
  value: MarketInterval;
  onChange: (next: MarketInterval) => void;
};

export default function IntervalSelector({ value, onChange }: IntervalSelectorProps) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-muted p-1">
      {INTERVALS.map((interval) => {
        const isSelected = value === interval;
        return (
          <button
            key={interval}
            type="button"
            onClick={() => onChange(interval)}
            className={[
              "rounded-md px-3 py-1.5 text-xs font-medium transition",
              isSelected ? "bg-accent text-white" : "text-foreground/80 hover:bg-foreground/10",
            ].join(" ")}
          >
            {interval}
          </button>
        );
      })}
    </div>
  );
}
