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

  const width = 640;
  const height = 224;
  const padding = 16;

  const values = data.map((item) => item.equity);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const range = maxValue - minValue;
  const normalizedRange = range === 0 ? 1 : range;

  const points = data
    .map((item, index) => {
      const x = padding + (index / Math.max(data.length - 1, 1)) * (width - padding * 2);
      const y = padding + (1 - (item.equity - minValue) / normalizedRange) * (height - padding * 2);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div className="h-56 rounded-md border border-border bg-background/50 p-2">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-full w-full" role="img" aria-label="Paper equity curve">
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-cyan-300"
          data-testid="equity-curve-polyline"
        />
      </svg>
    </div>
  );
}
