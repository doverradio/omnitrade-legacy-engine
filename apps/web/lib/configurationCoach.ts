import type { ParameterDefinition, ParameterValidationResult } from "@/lib/parameterDefinitions";

export type CoachHealthState = "ready" | "needs-attention" | "invalid";

export type ChangedParameter = {
  key: string;
  label: string;
  oldValue: string | number | boolean;
  newValue: string | number | boolean;
  expectedEffects: string[];
  validationMessages: string[];
  validationImpact: "No issues detected." | "Needs attention." | "Invalid value.";
};

export type WhyThisMattersItem = {
  message: string;
  explanation: string;
};

export type BehaviorSummary = {
  tradeFrequency: "Low" | "Medium" | "High";
  responsiveness: "Low" | "Medium" | "High";
  noiseFiltering: "Low" | "Medium" | "High";
  trendSensitivity: "Low" | "Medium" | "High";
};

const BEHAVIOR_FALLBACK_MESSAGE = "Behavior estimates are not yet available for this strategy.";

const EXPECTED_EFFECT_NOTES: Record<string, {
  increasing?: string[];
  decreasing?: string[];
  enabled?: string[];
  disabled?: string[];
  options?: Record<string, string[]>;
}> = {
  fast_period: {
    increasing: ["Fewer trading signals", "Slower reactions to price movement", "More filtering of market noise"],
    decreasing: ["More trading signals", "Faster reactions to price movement", "Higher chance of noisy signals"],
  },
  slow_period: {
    increasing: ["Longer trend confirmation", "More filtering of short-term moves"],
    decreasing: ["Faster trend changes", "More crossover events"],
  },
  rsi_period: {
    increasing: ["Smoother RSI movement", "Fewer abrupt threshold crossings"],
    decreasing: ["Faster RSI reactions", "More threshold crossings"],
  },
  oversold: {
    increasing: ["More conservative oversold entries", "Potentially fewer trades"],
    decreasing: ["Earlier oversold entries", "Potentially more trades"],
  },
  overbought: {
    increasing: ["Later overbought exits", "Potentially longer holds"],
    decreasing: ["Earlier overbought exits", "Potentially more exits"],
  },
  lookback: {
    increasing: ["Requires larger breakouts", "Can reduce false breakouts"],
    decreasing: ["Triggers breakouts sooner", "Can increase noisy breakouts"],
  },
  min_volume_multiple: {
    increasing: ["Stronger volume confirmation", "Fewer weak breakout entries"],
    decreasing: ["Looser volume confirmation", "More breakout entries"],
  },
  volume_confirmation: {
    enabled: ["Signals require volume support", "Can reduce low-conviction entries"],
    disabled: ["Signals can trigger without volume support", "Can increase trigger frequency"],
  },
  conflict_resolution: {
    options: {
      net_strength: ["Resolves conflicts by net signal strength", "Can react quickly to strong disagreement"],
      majority_vote: ["Resolves conflicts by vote count", "Favors consensus before acting"],
    },
  },
};

const WHY_THIS_MATTERS_NOTES: Record<string, string> = {
  fast_period: "Small moving-average windows react quickly but can be more sensitive to market noise.",
  slow_period: "Large moving-average windows react slowly but usually filter more short-term noise.",
  rsi_period: "Short RSI windows react quickly, while longer windows smooth momentum signals.",
  oversold: "Oversold thresholds change how quickly mean-reversion entries can trigger.",
  overbought: "Overbought thresholds change how quickly exits can trigger.",
  lookback: "Lookback length controls how much price history is required before a breakout is recognized.",
  min_volume_multiple: "Volume requirements help filter weaker breakouts with less market participation.",
  atr_period: "ATR windows control how quickly volatility estimates react to new movement.",
  min_atr_pct: "Minimum volatility thresholds can block trades when markets are too quiet.",
  max_atr_pct: "Maximum volatility thresholds can block trades when markets are too unstable.",
  adx_period: "ADX windows control how quickly trend-strength classification updates.",
  adx_trend_threshold: "Trend thresholds define how much strength is needed before classifying as trending.",
  ma_slope_period: "Slope windows affect how strongly the model emphasizes long-term trend direction.",
  min_strategies_agreeing: "Higher agreement requirements can reduce activity but increase consensus.",
  conflict_resolution: "Conflict mode changes how competing strategy signals are combined.",
  volume_confirmation: "Volume confirmation can reduce weak breakouts but may also reduce total signals.",
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function toNumber(value: unknown): number {
  if (typeof value === "number") {
    return value;
  }

  if (typeof value === "string") {
    return Number(value);
  }

  return Number.NaN;
}

export function getHealthState(formValidation: ParameterValidationResult): CoachHealthState {
  if (formValidation.errors.length > 0) {
    return "invalid";
  }

  if (formValidation.warnings.length > 0) {
    return "needs-attention";
  }

  return "ready";
}

export function getReadinessScore(
  definitions: ParameterDefinition[],
  values: Record<string, string | number | boolean>,
  formValidation: ParameterValidationResult,
): number {
  const requiredKeys = definitions.filter((item) => item.required).map((item) => item.key);
  const missingRequired = requiredKeys.filter((key) => {
    const value = values[key];
    return value === "" || value === null || value === undefined;
  }).length;

  const score = 100 - formValidation.errors.length * 35 - formValidation.warnings.length * 10 - missingRequired * 10;
  return clamp(score, 0, 100);
}

export function getReadinessLabel(healthState: CoachHealthState): "Ready for Backtesting" | "Needs Attention" | "Invalid" {
  if (healthState === "invalid") {
    return "Invalid";
  }

  if (healthState === "needs-attention") {
    return "Needs Attention";
  }

  return "Ready for Backtesting";
}

export function getExpectedEffects(definition: ParameterDefinition, value: string | number | boolean): string[] {
  const notes = EXPECTED_EFFECT_NOTES[definition.key];
  if (!notes) {
    return ["Expected effects for this parameter are not yet documented."];
  }

  if (definition.type === "boolean") {
    const lines = value === true ? notes.enabled : notes.disabled;
    return lines && lines.length > 0
      ? lines
      : ["Expected effects for this parameter are not yet documented."];
  }

  if (definition.type === "enum") {
    const lines = typeof value === "string" ? notes.options?.[value] : undefined;
    return lines && lines.length > 0
      ? lines
      : ["Expected effects for this parameter are not yet documented."];
  }

  const previous = toNumber(definition.defaultValue);
  const current = toNumber(value);
  if (!Number.isFinite(previous) || !Number.isFinite(current)) {
    return ["Expected effects for this parameter are not yet documented."];
  }

  if (current > previous && notes.increasing?.length) {
    return notes.increasing;
  }

  if (current < previous && notes.decreasing?.length) {
    return notes.decreasing;
  }

  return notes.increasing ?? notes.decreasing ?? ["Expected effects for this parameter are not yet documented."];
}

export function getChangedParameters(
  definitions: ParameterDefinition[],
  values: Record<string, string | number | boolean>,
  fieldValidation: Record<string, ParameterValidationResult>,
): ChangedParameter[] {
  return definitions
    .map((definition) => {
      const oldValue = definition.defaultValue;
      const newValue = values[definition.key] ?? definition.defaultValue;
      const changed = oldValue !== newValue;
      if (!changed) {
        return null;
      }

      const validation = fieldValidation[definition.key] ?? { valid: true, warnings: [], errors: [] };
      const validationMessages = [...validation.errors, ...validation.warnings];

      let validationImpact: ChangedParameter["validationImpact"] = "No issues detected.";
      if (validation.errors.length > 0) {
        validationImpact = "Invalid value.";
      } else if (validation.warnings.length > 0) {
        validationImpact = "Needs attention.";
      }

      return {
        key: definition.key,
        label: definition.label,
        oldValue,
        newValue,
        expectedEffects: getExpectedEffects(definition, newValue),
        validationMessages,
        validationImpact,
      };
    })
    .filter((item): item is ChangedParameter => item !== null);
}

function fromThreeBands(value: number, lowThreshold: number, highThreshold: number): "Low" | "Medium" | "High" {
  if (value <= lowThreshold) {
    return "High";
  }

  if (value >= highThreshold) {
    return "Low";
  }

  return "Medium";
}

export function getBehaviorSummary(
  strategySlug: string,
  values: Record<string, string | number | boolean>,
): BehaviorSummary | null {
  if (strategySlug === "ma_crossover") {
    const fast = toNumber(values.fast_period);
    const slow = toNumber(values.slow_period);
    if (!Number.isFinite(fast) || !Number.isFinite(slow)) {
      return null;
    }

    return {
      tradeFrequency: fromThreeBands(fast, 12, 30),
      responsiveness: fromThreeBands(fast, 10, 35),
      noiseFiltering: slow >= 100 ? "High" : slow >= 50 ? "Medium" : "Low",
      trendSensitivity: slow >= 80 ? "High" : slow >= 30 ? "Medium" : "Low",
    };
  }

  if (strategySlug === "rsi_mean_reversion") {
    const period = toNumber(values.rsi_period);
    const oversold = toNumber(values.oversold);
    const overbought = toNumber(values.overbought);
    if (!Number.isFinite(period) || !Number.isFinite(oversold) || !Number.isFinite(overbought)) {
      return null;
    }

    const thresholdGap = overbought - oversold;
    return {
      tradeFrequency: thresholdGap <= 35 ? "High" : thresholdGap <= 50 ? "Medium" : "Low",
      responsiveness: fromThreeBands(period, 8, 21),
      noiseFiltering: period >= 21 ? "High" : period >= 12 ? "Medium" : "Low",
      trendSensitivity: "Low",
    };
  }

  if (strategySlug === "breakout") {
    const lookback = toNumber(values.lookback);
    const volumeMultiple = toNumber(values.min_volume_multiple);
    const volumeConfirmation = values.volume_confirmation === true;
    if (!Number.isFinite(lookback) || !Number.isFinite(volumeMultiple)) {
      return null;
    }

    const frequency = lookback <= 15 && (!volumeConfirmation || volumeMultiple <= 1.2)
      ? "High"
      : lookback <= 30
        ? "Medium"
        : "Low";

    return {
      tradeFrequency: frequency,
      responsiveness: fromThreeBands(lookback, 12, 40),
      noiseFiltering: volumeConfirmation && volumeMultiple >= 1.5 ? "High" : volumeConfirmation ? "Medium" : "Low",
      trendSensitivity: "High",
    };
  }

  return null;
}

export function getBehaviorFallbackMessage(): string {
  return BEHAVIOR_FALLBACK_MESSAGE;
}

export function getWhyThisMatters(
  definitions: ParameterDefinition[],
  fieldValidation: Record<string, ParameterValidationResult>,
): WhyThisMattersItem[] {
  const items: WhyThisMattersItem[] = [];

  for (const definition of definitions) {
    const validation = fieldValidation[definition.key];
    if (!validation) {
      continue;
    }

    const explanation = WHY_THIS_MATTERS_NOTES[definition.key] ?? "Why this matters details are not yet documented.";
    for (const message of validation.errors) {
      items.push({ message, explanation });
    }

    for (const message of validation.warnings) {
      items.push({ message, explanation });
    }
  }

  return items;
}

export function getBeginnerTopObservations(
  healthState: CoachHealthState,
  readinessScore: number,
  whyItems: WhyThisMattersItem[],
  behaviorSummary: BehaviorSummary | null,
): string[] {
  const observations: string[] = [];

  if (healthState === "invalid") {
    observations.push("Your configuration has invalid values that must be fixed before backtesting.");
  } else if (healthState === "needs-attention") {
    observations.push("Your configuration works, but some settings need attention.");
  } else {
    observations.push("Your configuration is valid.");
  }

  if (whyItems.length > 0) {
    observations.push(whyItems[0].explanation);
  }

  if (behaviorSummary) {
    observations.push(`Trade frequency looks ${behaviorSummary.tradeFrequency.toLowerCase()} and responsiveness looks ${behaviorSummary.responsiveness.toLowerCase()}.`);
  }

  observations.push(`Configuration readiness is ${readinessScore} out of 100.`);
  return observations.slice(0, 3);
}
