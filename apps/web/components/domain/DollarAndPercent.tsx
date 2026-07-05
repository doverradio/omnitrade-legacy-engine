type DollarAndPercentProps = {
  usd: string | number;
  pct: string | number;
  className?: string;
};

function toNumber(value: string | number): number {
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

export default function DollarAndPercent({ usd, pct, className }: DollarAndPercentProps) {
  const usdValue = toNumber(usd);
  const pctValue = toNumber(pct);
  const isPositive = usdValue >= 0;
  const tone = isPositive ? "text-emerald-300" : "text-red-300";
  const sign = isPositive ? "+" : "";

  return (
    <span className={["font-medium", tone, className ?? ""].join(" ")}>
      {`${sign}$${usdValue.toFixed(2)} (${sign}${(pctValue * 100).toFixed(2)}%)`}
    </span>
  );
}
