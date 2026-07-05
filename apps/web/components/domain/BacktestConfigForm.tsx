import StartingBalanceInput from "@/components/domain/StartingBalanceInput";
import type { MarketAsset } from "@/lib/api/markets";
import type { ParameterSetItem } from "@/lib/api/parameterSets";
import type { StrategyItem } from "@/lib/api/strategies";

export type BacktestFormValues = {
  strategyId: string;
  parameterSetId: string;
  assetId: string;
  interval: "1m" | "5m" | "15m" | "1h" | "1d";
  startDateTime: string;
  endDateTime: string;
  startingBalance: string;
  feeBps: string;
  slippageBps: string;
};

type BacktestConfigFormProps = {
  values: BacktestFormValues;
  strategies: StrategyItem[];
  parameterSets: ParameterSetItem[];
  assets: MarketAsset[];
  disabled?: boolean;
  errorMessage?: string | null;
  onChange: <K extends keyof BacktestFormValues>(key: K, value: BacktestFormValues[K]) => void;
  onSubmit: () => void;
};

export default function BacktestConfigForm({
  values,
  strategies,
  parameterSets,
  assets,
  disabled = false,
  errorMessage,
  onChange,
  onSubmit,
}: BacktestConfigFormProps) {
  const filteredParameterSets = parameterSets.filter((parameterSet) => {
    return !values.strategyId || parameterSet.strategy_id === values.strategyId;
  });

  return (
    <section className="rounded-xl border border-border bg-muted/40 p-4">
      <h2 className="text-lg font-semibold">Backtest Configuration</h2>
      <p className="mt-1 text-xs text-foreground/70">Set strategy, market, and Backtest Starting Capital before running.</p>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span>Strategy</span>
          <select
            value={values.strategyId}
            disabled={disabled}
            onChange={(event) => onChange("strategyId", event.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="">Select strategy</option>
            {strategies.map((strategy) => (
              <option key={strategy.id} value={strategy.id}>
                {strategy.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Parameter Set</span>
          <select
            value={values.parameterSetId}
            disabled={disabled || filteredParameterSets.length === 0}
            onChange={(event) => onChange("parameterSetId", event.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="">Select parameter set</option>
            {filteredParameterSets.map((parameterSet) => (
              <option key={parameterSet.id} value={parameterSet.id}>
                {parameterSet.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Asset</span>
          <select
            value={values.assetId}
            disabled={disabled}
            onChange={(event) => onChange("assetId", event.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="">Select asset</option>
            {assets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.symbol}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Interval</span>
          <select
            value={values.interval}
            disabled={disabled}
            onChange={(event) => onChange("interval", event.target.value as BacktestFormValues["interval"])}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="1m">1m</option>
            <option value="5m">5m</option>
            <option value="15m">15m</option>
            <option value="1h">1h</option>
            <option value="1d">1d</option>
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Start Time</span>
          <input
            type="datetime-local"
            value={values.startDateTime}
            disabled={disabled}
            onChange={(event) => onChange("startDateTime", event.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>End Time</span>
          <input
            type="datetime-local"
            value={values.endDateTime}
            disabled={disabled}
            onChange={(event) => onChange("endDateTime", event.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Fee (bps)</span>
          <input
            type="number"
            min="0"
            step="0.01"
            value={values.feeBps}
            disabled={disabled}
            onChange={(event) => onChange("feeBps", event.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Slippage (bps)</span>
          <input
            type="number"
            min="0"
            step="0.01"
            value={values.slippageBps}
            disabled={disabled}
            onChange={(event) => onChange("slippageBps", event.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>
      </div>

      <div className="mt-4">
        <StartingBalanceInput
          id="backtest-starting-balance"
          value={values.startingBalance}
          disabled={disabled}
          onChange={(nextValue) => onChange("startingBalance", nextValue)}
          min={25}
        />
      </div>

      {errorMessage ? (
        <p className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">{errorMessage}</p>
      ) : null}

      <div className="mt-4 flex justify-end">
        <button
          type="button"
          disabled={disabled}
          onClick={onSubmit}
          className="rounded-md border border-accent bg-accent/20 px-4 py-2 text-sm font-medium transition hover:bg-accent/30 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Run Backtest
        </button>
      </div>
    </section>
  );
}
