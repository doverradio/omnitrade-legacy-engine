"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiRequestError } from "@/lib/api/backtests";
import { getStrategies, type StrategyItem } from "@/lib/api/strategies";

type PlaceholderSectionProps = {
  id: string;
  title: string;
  description: string;
  placeholder: string;
  tone?: "empty" | "loading";
};

type StrategyMetadata = {
  description: string;
  difficulty: string;
  primaryStyle: string;
  worksBestIn: string;
  worksPoorlyIn: string;
  tradeFrequency: string;
  beginnerExplanation: string;
};

const STRATEGY_METADATA: Record<string, Partial<StrategyMetadata>> = {
  ma_crossover: {
    description: "Tracks short-term and long-term moving averages to identify directional trend shifts.",
    difficulty: "Beginner",
    primaryStyle: "Trend Following",
    worksBestIn: "Clear directional trends",
    worksPoorlyIn: "Choppy sideways markets",
    tradeFrequency: "Not yet available",
    beginnerExplanation:
      "This strategy compares short-term and long-term price averages. When the short-term average rises above the long-term average, it suggests a possible upward trend. When it falls below, it suggests the trend may be weakening.",
  },
  rsi_mean_reversion: {
    description: "Uses RSI threshold behavior to look for potential reversals after stretched moves.",
    difficulty: "Intermediate",
    primaryStyle: "Mean Reversion",
    worksBestIn: "Range-bound markets",
    worksPoorlyIn: "Strong one-direction trends",
    tradeFrequency: "Not yet available",
    beginnerExplanation: "Coming Soon",
  },
  breakout: {
    description: "Looks for price breaking above or below recent ranges to capture momentum continuation.",
    difficulty: "Intermediate",
    primaryStyle: "Breakout",
    worksBestIn: "Expanding volatility with follow-through",
    worksPoorlyIn: "False breakout periods",
    tradeFrequency: "Not yet available",
    beginnerExplanation: "Coming Soon",
  },
  volatility_filter: {
    description: "Filters signals based on whether current volatility conditions are acceptable.",
    difficulty: "Advanced",
    primaryStyle: "Volatility",
    worksBestIn: "Risk control and signal filtering workflows",
    worksPoorlyIn: "As a standalone trading strategy",
    tradeFrequency: "Not yet available",
    beginnerExplanation: "Coming Soon",
  },
  trend_regime_filter: {
    description: "Classifies broad market regime to help determine when certain strategies should act.",
    difficulty: "Advanced",
    primaryStyle: "Trend Following",
    worksBestIn: "Regime-aware strategy selection",
    worksPoorlyIn: "As a standalone trading strategy",
    tradeFrequency: "Not yet available",
    beginnerExplanation: "Coming Soon",
  },
  ensemble_scorer: {
    description: "Combines multiple strategy signals into one blended decision path.",
    difficulty: "Advanced",
    primaryStyle: "Not yet available",
    worksBestIn: "Multi-strategy portfolios",
    worksPoorlyIn: "Single-strategy-only workflows",
    tradeFrequency: "Not yet available",
    beginnerExplanation: "Coming Soon",
  },
};

function normalizeStrategyMetadata(strategy: StrategyItem): StrategyMetadata {
  const mapped = STRATEGY_METADATA[strategy.slug] ?? {};
  return {
    description: mapped.description ?? "Not yet available",
    difficulty: mapped.difficulty ?? "Not yet available",
    primaryStyle: mapped.primaryStyle ?? "Not yet available",
    worksBestIn: mapped.worksBestIn ?? "Not yet available",
    worksPoorlyIn: mapped.worksPoorlyIn ?? "Not yet available",
    tradeFrequency: mapped.tradeFrequency ?? "Not yet available",
    beginnerExplanation: mapped.beginnerExplanation ?? "Coming Soon",
  };
}

function formatDefaultParamsSummary(defaultParams: StrategyItem["default_params"]): string {
  if (!defaultParams || Object.keys(defaultParams).length === 0) {
    return "Not yet available";
  }

  return Object.entries(defaultParams)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(", ");
}

function getErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }

  return "Could not load strategies right now.";
}

function PlaceholderSection({ id, title, description, placeholder, tone = "empty" }: PlaceholderSectionProps) {
  return (
    <section
      aria-labelledby={id}
      className="rounded-xl border border-border bg-muted/30 p-4"
      data-testid={`strategy-lab-section-${id}`}
    >
      <h2 id={id} className="text-base font-semibold sm:text-lg">
        {title}
      </h2>
      <p className="mt-1 text-sm text-foreground/75">{description}</p>
      {tone === "loading" ? (
        <div className="mt-3 space-y-2" role="status" aria-live="polite" aria-label={`${title} loading placeholder`}>
          <div className="h-3 w-3/4 animate-pulse rounded bg-foreground/20" />
          <div className="h-3 w-2/3 animate-pulse rounded bg-foreground/20" />
        </div>
      ) : (
        <p className="mt-3 rounded-md border border-dashed border-border bg-background/30 px-3 py-2 text-sm text-foreground/70">
          {placeholder}
        </p>
      )}
    </section>
  );
}

function ResearchJourney() {
  const steps = [
    "Choose Strategy",
    "Configure Parameters",
    "Configuration Intelligence",
    "Run Backtest",
    "Compare Results",
    "Learn Why",
  ];

  return (
    <nav aria-label="Research Journey" className="rounded-xl border border-border bg-background/40 p-4" data-testid="research-journey">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground/80">Research Journey</h2>
      <ol className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {steps.map((step, index) => {
          const isCurrent = index === 0;
          return (
            <li
              key={step}
              className={[
                "rounded-md border px-3 py-2 text-sm",
                isCurrent ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-200" : "border-border bg-muted/20 text-foreground/75",
              ].join(" ")}
              aria-current={isCurrent ? "step" : undefined}
            >
              <span className="mr-2" aria-hidden="true">{isCurrent ? "✓" : "○"}</span>
              <span>{step}</span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export default function StrategyLabPage() {
  const [isBeginnerMode, setIsBeginnerMode] = useState(true);
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState<string | null>(null);
  const [isLoadingStrategies, setIsLoadingStrategies] = useState(true);
  const [strategiesError, setStrategiesError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadStrategies = async () => {
      setIsLoadingStrategies(true);
      setStrategiesError(null);

      try {
        const items = await getStrategies();
        if (cancelled) {
          return;
        }
        setStrategies(items);
        setSelectedStrategyId((previous) => previous ?? items[0]?.id ?? null);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setStrategiesError(getErrorMessage(error));
      } finally {
        if (!cancelled) {
          setIsLoadingStrategies(false);
        }
      }
    };

    void loadStrategies();

    return () => {
      cancelled = true;
    };
  }, []);

  const selectedStrategy = useMemo(() => {
    if (!selectedStrategyId) {
      return null;
    }
    return strategies.find((item) => item.id === selectedStrategyId) ?? null;
  }, [selectedStrategyId, strategies]);

  const selectedMetadata = selectedStrategy ? normalizeStrategyMetadata(selectedStrategy) : null;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-4 sm:space-y-6" data-testid="strategy-lab-mobile-wrapper">
      <ResearchJourney />

      <header className="space-y-2 rounded-xl border border-border bg-background/40 p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Strategy Lab Research Workspace</h1>
            <p className="mt-1 text-sm text-foreground/75">
              Build confidence with historical testing first, then review results before any execution phase.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={isBeginnerMode}
            aria-label="Beginner Mode"
            onClick={() => setIsBeginnerMode((previous) => !previous)}
            className="inline-flex items-center justify-center rounded-md border border-border bg-muted px-3 py-2 text-sm font-medium transition hover:bg-foreground/10"
          >
            Beginner Mode: {isBeginnerMode ? "On" : "Off"}
          </button>
        </div>
      </header>

      {isBeginnerMode ? (
        <section className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4" data-testid="beginner-welcome-card">
          <h2 className="text-base font-semibold sm:text-lg">Welcome</h2>
          <p className="mt-1 text-sm text-foreground/85">
            This workspace helps you safely experiment with trading strategies using historical data before risking real money.
          </p>
          <ul className="mt-3 space-y-2 text-sm text-foreground/80">
            <li>
              <strong>Strategy:</strong> A rule set that decides when to buy, sell, or hold.
            </li>
            <li>
              <strong>Backtest:</strong> A simulation showing how a strategy would have behaved in historical data.
            </li>
            <li>
              <strong>Parameter:</strong> A setting value that changes how a strategy behaves.
            </li>
            <li>
              <strong>Starting Capital:</strong> The amount a backtest starts with, such as $25.
            </li>
          </ul>
        </section>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2" data-testid="strategy-lab-sections-grid">
        <section
          aria-labelledby="choose-strategy"
          className="rounded-xl border border-border bg-muted/30 p-4"
          data-testid="strategy-lab-section-choose-strategy"
        >
          <h2 id="choose-strategy" className="text-base font-semibold sm:text-lg">
            1) Choose Strategy
          </h2>
          <p className="mt-1 text-sm text-foreground/75">Pick a strategy card to start this research workflow.</p>

          {isLoadingStrategies ? (
            <div className="mt-3 space-y-3" role="status" aria-live="polite" aria-label="Strategies loading">
              <div className="h-24 animate-pulse rounded-lg bg-foreground/15" />
              <div className="h-24 animate-pulse rounded-lg bg-foreground/15" />
            </div>
          ) : null}

          {!isLoadingStrategies && strategiesError ? (
            <div className="mt-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-100" data-testid="strategy-error-state">
              {strategiesError}
            </div>
          ) : null}

          {!isLoadingStrategies && !strategiesError && strategies.length === 0 ? (
            <p className="mt-3 rounded-md border border-dashed border-border bg-background/30 px-3 py-2 text-sm text-foreground/70" data-testid="strategy-empty-state">
              No strategies registered yet. Strategy cards will appear here when available.
            </p>
          ) : null}

          {!isLoadingStrategies && !strategiesError && strategies.length > 0 ? (
            <div className="mt-3 grid gap-3" data-testid="strategy-cards-wrapper">
              {strategies.map((strategy) => {
                const metadata = normalizeStrategyMetadata(strategy);
                const isSelected = selectedStrategyId === strategy.id;

                return (
                  <article
                    key={strategy.id}
                    className={[
                      "rounded-lg border bg-background/35 p-3",
                      isSelected ? "border-accent ring-1 ring-accent/50" : "border-border",
                    ].join(" ")}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedStrategyId(strategy.id)}
                      className="w-full text-left"
                      aria-pressed={isSelected}
                      aria-label={`Select strategy ${strategy.name}`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <h3 className="text-base font-semibold" data-testid={`strategy-card-title-${strategy.slug}`}>
                          {strategy.name}
                        </h3>
                        <span
                          className={[
                            "rounded-full border px-2 py-0.5 text-xs font-medium",
                            strategy.is_active
                              ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-200"
                              : "border-border bg-muted/30 text-foreground/75",
                          ].join(" ")}
                        >
                          {strategy.is_active ? "Active" : "Inactive"}
                        </span>
                      </div>

                      <p className="mt-1 text-sm text-foreground/80">{metadata.description}</p>

                      <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                        <div>
                          <dt className="text-foreground/60">Difficulty</dt>
                          <dd>{metadata.difficulty}</dd>
                        </div>
                        <div>
                          <dt className="text-foreground/60">Primary Style</dt>
                          <dd>{metadata.primaryStyle}</dd>
                        </div>
                        <div>
                          <dt className="text-foreground/60">Works Best In</dt>
                          <dd>{metadata.worksBestIn}</dd>
                        </div>
                        <div>
                          <dt className="text-foreground/60">Works Poorly In</dt>
                          <dd>{metadata.worksPoorlyIn}</dd>
                        </div>
                        <div>
                          <dt className="text-foreground/60">Typical Trade Frequency</dt>
                          <dd>{metadata.tradeFrequency}</dd>
                        </div>
                        <div>
                          <dt className="text-foreground/60">Default Parameters</dt>
                          <dd>{formatDefaultParamsSummary(strategy.default_params)}</dd>
                        </div>
                      </dl>

                      {isBeginnerMode ? (
                        <div className="mt-3 rounded-md border border-border bg-muted/20 px-3 py-2 text-sm text-foreground/80">
                          <p className="font-medium">What does this strategy do?</p>
                          <p className="mt-1">{metadata.beginnerExplanation}</p>
                        </div>
                      ) : null}
                    </button>
                  </article>
                );
              })}

              {selectedStrategy && selectedMetadata ? (
                <section
                  className="rounded-lg border border-border bg-background/25 p-4"
                  aria-label="Strategy Detail"
                  data-testid="strategy-detail-panel"
                >
                  <h3 className="text-base font-semibold">Strategy Detail</h3>
                  <p className="mt-1 text-sm text-foreground/80">{selectedMetadata.description}</p>
                  <p className="mt-2 text-sm text-foreground/80">
                    <span className="font-medium">Beginner explanation:</span> {selectedMetadata.beginnerExplanation}
                  </p>
                  <p className="mt-2 text-sm text-foreground/80">
                    <span className="font-medium">Default parameters:</span> {formatDefaultParamsSummary(selectedStrategy.default_params)}
                  </p>
                  <p className="mt-3 rounded-md border border-dashed border-border bg-muted/20 px-3 py-2 text-sm text-foreground/70">
                    Parameter editor will appear in the next step.
                  </p>
                </section>
              ) : null}
            </div>
          ) : null}
        </section>

        <PlaceholderSection
          id="configure-parameters"
          title="2) Configure Parameters"
          description="Adjust strategy settings once you understand what each setting does."
          placeholder="Parameter Editor placeholder reserved."
        />

        <PlaceholderSection
          id="configuration-intelligence"
          title="3) Configuration Intelligence"
          description="Review a plain-language readiness summary before running a backtest."
          placeholder="Configuration Intelligence Panel placeholder reserved."
        />

        <PlaceholderSection
          id="run-backtest"
          title="4) Run Backtest"
          description="Launch a historical simulation to test this setup without risking money."
          placeholder="Backtest launch placeholder reserved."
          tone="loading"
        />

        <PlaceholderSection
          id="compare-results"
          title="5) Compare Results"
          description="Compare runs side by side to understand trade-offs before choosing a preset."
          placeholder="Comparison Workspace placeholder reserved."
        />

        <PlaceholderSection
          id="learn-why"
          title="6) Learn Why"
          description="Read clear explanations of what happened and what each metric means."
          placeholder="Explainability Panel placeholder reserved."
        />
      </div>
    </div>
  );
}
