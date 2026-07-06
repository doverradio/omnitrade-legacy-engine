"use client";

import { useState } from "react";

type PlaceholderSectionProps = {
  id: string;
  title: string;
  description: string;
  placeholder: string;
  tone?: "empty" | "loading";
};

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

export default function StrategyLabPage() {
  const [isBeginnerMode, setIsBeginnerMode] = useState(true);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-4 sm:space-y-6" data-testid="strategy-lab-mobile-wrapper">
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
        <PlaceholderSection
          id="choose-strategy"
          title="1) Choose Strategy"
          description="Pick a strategy to research before changing any settings."
          placeholder="No strategy selected yet. Strategy list placeholder reserved."
          tone="loading"
        />

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
