import type { ParameterDefinition, ParameterValidationResult } from "@/lib/parameterDefinitions";
import {
  getBehaviorFallbackMessage,
  getBehaviorSummary,
  getBeginnerTopObservations,
  getChangedParameters,
  getHealthState,
  getReadinessLabel,
  getReadinessScore,
  getWhyThisMatters,
} from "@/lib/configurationCoach";

type ConfigurationCoachProps = {
  strategySlug: string;
  isBeginnerMode: boolean;
  definitions: ParameterDefinition[];
  values: Record<string, string | number | boolean>;
  fieldValidation: Record<string, ParameterValidationResult>;
  formValidation: ParameterValidationResult;
};

function formatValue(value: string | number | boolean): string {
  if (typeof value === "boolean") {
    return value ? "Enabled" : "Disabled";
  }

  return String(value);
}

export function ConfigurationCoach({
  strategySlug,
  isBeginnerMode,
  definitions,
  values,
  fieldValidation,
  formValidation,
}: ConfigurationCoachProps) {
  const healthState = getHealthState(formValidation);
  const readinessScore = getReadinessScore(definitions, values, formValidation);
  const readinessLabel = getReadinessLabel(healthState);
  const changed = getChangedParameters(definitions, values, fieldValidation);
  const behaviorSummary = getBehaviorSummary(strategySlug, values);
  const whyThisMatters = getWhyThisMatters(definitions, fieldValidation);
  const beginnerObservations = isBeginnerMode
    ? getBeginnerTopObservations(healthState, readinessScore, whyThisMatters, behaviorSummary)
    : [];

  const healthToken =
    healthState === "ready"
      ? { icon: "🟢", label: "Ready", classes: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100" }
      : healthState === "needs-attention"
        ? { icon: "🟡", label: "Needs Attention", classes: "border-amber-500/40 bg-amber-500/10 text-amber-100" }
        : { icon: "🔴", label: "Invalid", classes: "border-red-500/40 bg-red-500/10 text-red-100" };

  return (
    <section
      aria-labelledby="configuration-coach"
      className="rounded-xl border border-border bg-muted/30 p-4"
      data-testid="strategy-lab-section-configuration-coach"
    >
      <h2 id="configuration-coach" className="text-base font-semibold sm:text-lg">
        3) Configuration Coach
      </h2>
      <p className="mt-1 text-sm text-foreground/75">
        Deterministic guidance based on your current parameter values and validation status.
      </p>

      <div className="mt-3 grid gap-3" data-testid="configuration-coach-cards">
        <article className="rounded-lg border border-border bg-background/30 p-4" data-testid="coach-card-health">
          <h3 className="text-sm font-semibold">Configuration Health</h3>
          <div className="mt-2 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-medium" data-testid="configuration-health-badge">
            <span aria-hidden="true">{healthToken.icon}</span>
            <span>{healthToken.label}</span>
          </div>
          <p className={["mt-2 rounded-md border px-2 py-1 text-sm", healthToken.classes].join(" ")}>
            Overall state is determined from existing validation results.
          </p>
        </article>

        <article className="rounded-lg border border-border bg-background/30 p-4" data-testid="coach-card-readiness">
          <h3 className="text-sm font-semibold">Configuration Readiness</h3>
          <p className="mt-2 text-2xl font-semibold" data-testid="readiness-score">
            {readinessScore} / 100
          </p>
          <p className="mt-1 text-sm text-foreground/80">{readinessLabel}</p>
        </article>

        <article className="rounded-lg border border-border bg-background/30 p-4" data-testid="coach-card-what-changed">
          <h3 className="text-sm font-semibold">What Changed?</h3>
          {changed.length === 0 ? (
            <p className="mt-2 text-sm text-foreground/75">No parameter changes yet. Adjust a control to see impact details.</p>
          ) : (
            <div className="mt-2 space-y-3">
              {changed.map((item) => (
                <section key={item.key} className="rounded-md border border-border/80 bg-muted/20 p-3" data-testid={`what-changed-${item.key}`}>
                  <h4 className="text-sm font-semibold">{item.label}</h4>
                  <p className="mt-1 text-sm text-foreground/85">
                    {formatValue(item.oldValue)} <span aria-hidden="true">↓</span> {formatValue(item.newValue)}
                  </p>
                  <p className="mt-2 text-xs font-semibold uppercase tracking-wide text-foreground/70">Expected Effect</p>
                  <ul className="mt-1 space-y-1 text-sm text-foreground/80">
                    {item.expectedEffects.map((effect) => (
                      <li key={effect}>- {effect}</li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs font-semibold uppercase tracking-wide text-foreground/70">Validation Impact</p>
                  <p className="mt-1 text-sm text-foreground/80">{item.validationImpact}</p>
                  {item.validationMessages.map((message) => (
                    <p key={message} className="mt-1 text-sm text-foreground/75">
                      {message}
                    </p>
                  ))}
                </section>
              ))}
            </div>
          )}
        </article>

        <article className="rounded-lg border border-border bg-background/30 p-4" data-testid="coach-card-estimated-behavior">
          <h3 className="text-sm font-semibold">Estimated Behavior</h3>
          {behaviorSummary ? (
            <dl className="mt-2 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              <div className="rounded-md border border-border/70 bg-muted/20 px-2 py-2">
                <dt className="text-foreground/60">Trade Frequency</dt>
                <dd className="font-medium" data-testid="behavior-trade-frequency">{behaviorSummary.tradeFrequency}</dd>
              </div>
              <div className="rounded-md border border-border/70 bg-muted/20 px-2 py-2">
                <dt className="text-foreground/60">Responsiveness</dt>
                <dd className="font-medium" data-testid="behavior-responsiveness">{behaviorSummary.responsiveness}</dd>
              </div>
              <div className="rounded-md border border-border/70 bg-muted/20 px-2 py-2">
                <dt className="text-foreground/60">Noise Filtering</dt>
                <dd className="font-medium" data-testid="behavior-noise-filtering">{behaviorSummary.noiseFiltering}</dd>
              </div>
              <div className="rounded-md border border-border/70 bg-muted/20 px-2 py-2">
                <dt className="text-foreground/60">Trend Sensitivity</dt>
                <dd className="font-medium" data-testid="behavior-trend-sensitivity">{behaviorSummary.trendSensitivity}</dd>
              </div>
            </dl>
          ) : (
            <p className="mt-2 text-sm text-foreground/75">{getBehaviorFallbackMessage()}</p>
          )}
        </article>

        <article className="rounded-lg border border-border bg-background/30 p-4" data-testid="coach-card-why-this-matters">
          <h3 className="text-sm font-semibold">Why This Matters</h3>
          {whyThisMatters.length === 0 ? (
            <p className="mt-2 text-sm text-foreground/75">No active warnings or errors to explain right now.</p>
          ) : (
            <ul className="mt-2 space-y-2 text-sm">
              {whyThisMatters.map((item, index) => (
                <li key={`${item.message}-${index}`} className="rounded-md border border-border/70 bg-muted/20 px-3 py-2">
                  <p className="font-medium text-foreground/90">{item.message}</p>
                  <p className="mt-1 text-foreground/80">Why this matters: {item.explanation}</p>
                </li>
              ))}
            </ul>
          )}
        </article>

        <article className="rounded-lg border border-border bg-background/30 p-4" data-testid="coach-card-things-to-know">
          <h3 className="text-sm font-semibold">Things to Know</h3>
          {isBeginnerMode ? (
            <ul className="mt-2 space-y-2 text-sm text-foreground/85">
              {beginnerObservations.map((observation) => (
                <li key={observation} className="rounded-md border border-border/70 bg-muted/20 px-3 py-2">
                  ✓ {observation}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-foreground/75">Enable Beginner Mode to show top educational observations.</p>
          )}
        </article>

        <article className="rounded-lg border border-border bg-background/30 p-4" data-testid="coach-card-advanced-details">
          <h3 className="text-sm font-semibold">Advanced Details</h3>
          <details className="mt-2" data-testid="advanced-details-collapsed">
            <summary className="cursor-pointer rounded-md border border-border/70 bg-muted/20 px-3 py-2 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
              Show validation warnings, ranges, and metadata
            </summary>
            <div className="mt-2 space-y-2 text-sm text-foreground/80">
              <div className="rounded-md border border-border/70 bg-muted/20 p-3">
                <p className="font-medium">Validation Warnings</p>
                {formValidation.warnings.length === 0 ? (
                  <p className="mt-1">No validation warnings.</p>
                ) : (
                  <ul className="mt-1 space-y-1">
                    {formValidation.warnings.map((warning) => (
                      <li key={warning}>- {warning}</li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="rounded-md border border-border/70 bg-muted/20 p-3">
                <p className="font-medium">Recommended Ranges and Constraints</p>
                <ul className="mt-1 space-y-1">
                  {definitions.map((definition) => (
                    <li key={definition.key}>
                      {definition.label}: {definition.recommendedRange
                        ? `${definition.recommendedRange.minimum}-${definition.recommendedRange.maximum}`
                        : "No recommended range"}
                      ; constraints {definition.minimum ?? "-"} to {definition.maximum ?? "-"}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="rounded-md border border-border/70 bg-muted/20 p-3">
                <p className="font-medium">Raw Parameter Metadata</p>
                <pre className="mt-1 whitespace-pre-wrap break-all text-xs" data-testid="advanced-raw-metadata">
                  {JSON.stringify(definitions, null, 2)}
                </pre>
              </div>
            </div>
          </details>
        </article>
      </div>
    </section>
  );
}
