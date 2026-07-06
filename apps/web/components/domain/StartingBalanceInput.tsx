type StartingBalanceInputProps = {
  id?: string;
  value: string;
  onChange: (nextValue: string) => void;
  min?: number;
  disabled?: boolean;
  label?: string;
};

const PRESET_VALUES = [25, 50, 100, 250, 500, 1000] as const;

export default function StartingBalanceInput({
  id,
  value,
  onChange,
  min = 25,
  disabled = false,
  label = "Backtest Starting Capital",
}: StartingBalanceInputProps) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {PRESET_VALUES.map((preset) => {
          const isSelected = Number(value) === preset;
          return (
            <button
              key={preset}
              type="button"
              disabled={disabled}
              onClick={() => onChange(String(preset))}
              className={[
                "rounded-md border px-2 py-1.5 text-xs font-medium transition",
                isSelected
                  ? "border-accent bg-accent/20 text-foreground"
                  : "border-border bg-muted text-foreground/90 hover:bg-foreground/10",
                disabled ? "cursor-not-allowed opacity-60" : "",
              ].join(" ")}
              aria-pressed={isSelected}
            >
              ${preset}
            </button>
          );
        })}
      </div>

      <label htmlFor={id} className="flex flex-col gap-1 text-sm text-foreground/90">
        <span>{label}</span>
        <input
          id={id}
          type="number"
          inputMode="decimal"
          min={min}
          step="0.01"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-accent disabled:cursor-not-allowed disabled:opacity-60"
          aria-describedby={id ? `${id}-help` : undefined}
        />
      </label>

      <p id={id ? `${id}-help` : undefined} className="text-xs text-foreground/70">
        Minimum allowed: ${min}
      </p>
    </div>
  );
}
