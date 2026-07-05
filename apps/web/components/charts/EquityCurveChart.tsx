type EquityCurveChartProps = {
  data: Array<{ time: string; equity: number }>;
};

export default function EquityCurveChart({ data }: EquityCurveChartProps) {
  if (data.length === 0) {
    return (
      <div className="flex h-56 items-center justify-center rounded-md border border-dashed border-border bg-background/50 text-sm text-foreground/70">
        No data yet
      </div>
    );
  }

  return (
    <div className="flex h-56 items-center justify-center rounded-md border border-border bg-background/50 text-sm text-foreground/70">
      Chart placeholder
    </div>
  );
}
