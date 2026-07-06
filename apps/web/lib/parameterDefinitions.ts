export type ParameterType = "integer" | "decimal" | "percentage" | "boolean" | "enum";

export type ValidationRule = {
  rule: "required" | "type" | "minimum" | "maximum" | "allowedValues";
  message: string;
};

export type RecommendedRange = {
  minimum: number;
  maximum: number;
};

export type ParameterDefinition = {
  key: string;
  label: string;
  description: string;
  beginnerDescription: string;
  type: ParameterType;
  defaultValue: string | number | boolean;
  minimum?: number;
  maximum?: number;
  step?: number;
  allowedValues?: string[];
  required: boolean;
  units?: string;
  recommendedRange?: RecommendedRange;
  advanced: boolean;
  validationRules: ValidationRule[];
};

export type ParameterValidationResult = {
  valid: boolean;
  warnings: string[];
  errors: string[];
};

type StrategyDefaults = Record<string, Record<string, string | number | boolean>>;

type ParameterMetadata = {
  label?: string;
  description?: string;
  beginnerDescription?: string;
  type?: ParameterType;
  minimum?: number;
  maximum?: number;
  step?: number;
  allowedValues?: string[];
  required?: boolean;
  units?: string;
  recommendedRange?: RecommendedRange;
  advanced?: boolean;
};

const STRATEGY_DEFAULTS: StrategyDefaults = {
  ma_crossover: {
    fast_period: 10,
    slow_period: 50,
    ma_type: "sma",
  },
  rsi_mean_reversion: {
    rsi_period: 14,
    oversold: 30,
    overbought: 70,
  },
  breakout: {
    lookback: 20,
    volume_confirmation: true,
    min_volume_multiple: 1.5,
  },
  volatility_filter: {
    atr_period: 14,
    min_atr_pct: 0.2,
    max_atr_pct: 5.0,
  },
  trend_regime_filter: {
    adx_period: 14,
    adx_trend_threshold: 25,
    ma_slope_period: 50,
  },
  ensemble_scorer: {
    min_strategies_agreeing: 1,
    conflict_resolution: "net_strength",
  },
};

const PARAMETER_METADATA: Record<string, ParameterMetadata> = {
  fast_period: {
    label: "Fast Period",
    description: "Number of candles used to calculate the fast moving average.",
    beginnerDescription: "A smaller number reacts faster to price changes.",
    type: "integer",
    minimum: 2,
    maximum: 200,
    step: 1,
    required: true,
    recommendedRange: { minimum: 5, maximum: 30 },
  },
  slow_period: {
    label: "Slow Period",
    description: "Number of candles used to calculate the slow moving average.",
    beginnerDescription: "A larger number reacts slower and tracks the bigger trend.",
    type: "integer",
    minimum: 5,
    maximum: 400,
    step: 1,
    required: true,
    recommendedRange: { minimum: 20, maximum: 100 },
  },
  ma_type: {
    label: "Moving Average Type",
    description: "Moving average method used by the strategy.",
    beginnerDescription: "This changes how the average line is calculated.",
    type: "enum",
    allowedValues: ["sma", "ema"],
    required: true,
  },
  rsi_period: {
    label: "RSI Period",
    description: "Number of candles used to calculate RSI.",
    beginnerDescription: "Controls how quickly RSI reacts to price movement.",
    type: "integer",
    minimum: 2,
    maximum: 100,
    step: 1,
    required: true,
    recommendedRange: { minimum: 7, maximum: 21 },
  },
  oversold: {
    label: "Oversold Threshold",
    description: "RSI level that can indicate a market is oversold.",
    beginnerDescription: "Lower values make buy conditions more strict.",
    type: "percentage",
    minimum: 0,
    maximum: 100,
    step: 1,
    required: true,
    units: "%",
    recommendedRange: { minimum: 20, maximum: 40 },
  },
  overbought: {
    label: "Overbought Threshold",
    description: "RSI level that can indicate a market is overbought.",
    beginnerDescription: "Higher values make sell conditions more strict.",
    type: "percentage",
    minimum: 0,
    maximum: 100,
    step: 1,
    required: true,
    units: "%",
    recommendedRange: { minimum: 60, maximum: 80 },
  },
  lookback: {
    label: "Breakout Lookback",
    description: "Number of candles used to define breakout highs and lows.",
    beginnerDescription: "Longer lookback windows require bigger breakouts.",
    type: "integer",
    minimum: 2,
    maximum: 300,
    step: 1,
    required: true,
    recommendedRange: { minimum: 10, maximum: 60 },
  },
  volume_confirmation: {
    label: "Volume Confirmation",
    description: "Requires volume confirmation before allowing breakout signals.",
    beginnerDescription: "Turning this on asks for stronger confirmation before trading.",
    type: "boolean",
    required: true,
  },
  min_volume_multiple: {
    label: "Minimum Volume Multiple",
    description: "Minimum volume ratio required for breakout confirmation.",
    beginnerDescription: "Higher numbers require stronger volume spikes.",
    type: "decimal",
    minimum: 0,
    maximum: 10,
    step: 0.1,
    required: true,
    recommendedRange: { minimum: 1, maximum: 3 },
  },
  atr_period: {
    label: "ATR Period",
    description: "Number of candles used to calculate ATR.",
    beginnerDescription: "Controls how quickly the volatility measure reacts.",
    type: "integer",
    minimum: 2,
    maximum: 200,
    step: 1,
    required: true,
    recommendedRange: { minimum: 10, maximum: 30 },
    advanced: true,
  },
  min_atr_pct: {
    label: "Minimum ATR Percent",
    description: "Minimum ATR percentage needed before signals are allowed.",
    beginnerDescription: "If volatility is below this level, signals can be filtered out.",
    type: "percentage",
    minimum: 0,
    maximum: 100,
    step: 0.1,
    required: true,
    units: "%",
    recommendedRange: { minimum: 0.1, maximum: 2.0 },
    advanced: true,
  },
  max_atr_pct: {
    label: "Maximum ATR Percent",
    description: "Maximum ATR percentage allowed before signals are filtered out.",
    beginnerDescription: "If volatility is too high, signals can be filtered out.",
    type: "percentage",
    minimum: 0,
    maximum: 100,
    step: 0.1,
    required: true,
    units: "%",
    recommendedRange: { minimum: 2.0, maximum: 8.0 },
    advanced: true,
  },
  adx_period: {
    label: "ADX Period",
    description: "Number of candles used to calculate ADX trend strength.",
    beginnerDescription: "Controls how quickly trend-strength estimates update.",
    type: "integer",
    minimum: 2,
    maximum: 200,
    step: 1,
    required: true,
    recommendedRange: { minimum: 10, maximum: 30 },
    advanced: true,
  },
  adx_trend_threshold: {
    label: "ADX Trend Threshold",
    description: "ADX level used to classify trend strength.",
    beginnerDescription: "Higher values require stronger trends before classifying as trending.",
    type: "integer",
    minimum: 0,
    maximum: 100,
    step: 1,
    required: true,
    recommendedRange: { minimum: 20, maximum: 35 },
    advanced: true,
  },
  ma_slope_period: {
    label: "MA Slope Period",
    description: "Number of candles used when estimating moving-average slope.",
    beginnerDescription: "Longer windows smooth trend direction and reduce noise.",
    type: "integer",
    minimum: 2,
    maximum: 400,
    step: 1,
    required: true,
    recommendedRange: { minimum: 20, maximum: 100 },
    advanced: true,
  },
  min_strategies_agreeing: {
    label: "Minimum Strategies Agreeing",
    description: "Minimum number of strategies that must align before acting.",
    beginnerDescription: "Higher values require more agreement before a signal is accepted.",
    type: "integer",
    minimum: 1,
    maximum: 10,
    step: 1,
    required: true,
    recommendedRange: { minimum: 1, maximum: 4 },
    advanced: true,
  },
  conflict_resolution: {
    label: "Conflict Resolution Mode",
    description: "Method used when strategy signals disagree.",
    beginnerDescription: "Chooses how to settle disagreements between multiple strategy signals.",
    type: "enum",
    allowedValues: ["net_strength", "majority_vote"],
    required: true,
    advanced: true,
  },
};

function toTitleCaseLabel(raw: string): string {
  return raw
    .split("_")
    .filter(Boolean)
    .map((chunk) => chunk.charAt(0).toUpperCase() + chunk.slice(1))
    .join(" ");
}

function inferType(key: string, value: string | number | boolean): ParameterType {
  if (typeof value === "boolean") {
    return "boolean";
  }

  if (typeof value === "string") {
    return "enum";
  }

  const normalizedKey = key.toLowerCase();
  if (normalizedKey.includes("pct") || normalizedKey.includes("percent") || normalizedKey === "oversold" || normalizedKey === "overbought") {
    return "percentage";
  }

  if (Number.isInteger(value)) {
    return "integer";
  }

  return "decimal";
}

function defaultStepForType(type: ParameterType): number | undefined {
  if (type === "integer") {
    return 1;
  }

  if (type === "decimal") {
    return 0.01;
  }

  if (type === "percentage") {
    return 0.1;
  }

  return undefined;
}

function buildValidationRules(definition: ParameterDefinition): ValidationRule[] {
  const rules: ValidationRule[] = [];

  if (definition.required) {
    rules.push({
      rule: "required",
      message: `${definition.label} is required.`,
    });
  }

  rules.push({
    rule: "type",
    message: `${definition.label} must be a valid ${definition.type} value.`,
  });

  if (typeof definition.minimum === "number") {
    rules.push({
      rule: "minimum",
      message: `${definition.label} must be at least ${definition.minimum}.`,
    });
  }

  if (typeof definition.maximum === "number") {
    rules.push({
      rule: "maximum",
      message: `${definition.label} must be at most ${definition.maximum}.`,
    });
  }

  if (definition.allowedValues && definition.allowedValues.length > 0) {
    rules.push({
      rule: "allowedValues",
      message: `${definition.label} must be one of: ${definition.allowedValues.join(", ")}.`,
    });
  }

  return rules;
}

function buildParameterDefinition(
  key: string,
  defaultValue: string | number | boolean,
  metadata: ParameterMetadata | undefined,
): ParameterDefinition {
  const inferredType = inferType(key, defaultValue);
  const type = metadata?.type ?? inferredType;
  const required = metadata?.required ?? true;

  const definition: ParameterDefinition = {
    key,
    label: metadata?.label ?? toTitleCaseLabel(key),
    description: metadata?.description ?? "Description not yet available.",
    beginnerDescription: metadata?.beginnerDescription ?? "Beginner explanation coming soon.",
    type,
    defaultValue,
    minimum: metadata?.minimum,
    maximum: metadata?.maximum,
    step: metadata?.step ?? defaultStepForType(type),
    allowedValues: metadata?.allowedValues,
    required,
    units: metadata?.units ?? (type === "percentage" ? "%" : undefined),
    recommendedRange: metadata?.recommendedRange,
    advanced: metadata?.advanced ?? false,
    validationRules: [],
  };

  definition.validationRules = buildValidationRules(definition);
  return definition;
}

export function generateParameterDefinitions(defaultParams: Record<string, string | number | boolean>): ParameterDefinition[] {
  return Object.entries(defaultParams).map(([key, value]) => {
    return buildParameterDefinition(key, value, PARAMETER_METADATA[key]);
  });
}

export function getParameterDefinitions(strategySlug: string): ParameterDefinition[] {
  const defaults = STRATEGY_DEFAULTS[strategySlug];
  if (!defaults) {
    return [];
  }

  return generateParameterDefinitions(defaults);
}

function isNumericType(type: ParameterType): boolean {
  return type === "integer" || type === "decimal" || type === "percentage";
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

export function validateParameterValue(definition: ParameterDefinition, value: unknown): ParameterValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (definition.required && (value === null || value === undefined || value === "")) {
    errors.push(`${definition.label} is required.`);
    return { valid: false, warnings, errors };
  }

  if (value === null || value === undefined || value === "") {
    return { valid: true, warnings, errors };
  }

  if (definition.type === "boolean") {
    if (typeof value !== "boolean") {
      errors.push(`${definition.label} must be true or false.`);
    }
    return { valid: errors.length === 0, warnings, errors };
  }

  if (definition.type === "enum") {
    if (typeof value !== "string") {
      errors.push(`${definition.label} must be a text value.`);
      return { valid: false, warnings, errors };
    }

    if (definition.allowedValues && !definition.allowedValues.includes(value)) {
      errors.push(`${definition.label} must be one of: ${definition.allowedValues.join(", ")}.`);
    }

    return { valid: errors.length === 0, warnings, errors };
  }

  if (isNumericType(definition.type)) {
    const numeric = toNumber(value);
    if (!Number.isFinite(numeric)) {
      errors.push(`${definition.label} must be a number.`);
      return { valid: false, warnings, errors };
    }

    if (definition.type === "integer" && !Number.isInteger(numeric)) {
      errors.push(`${definition.label} must be a whole number.`);
    }

    if (typeof definition.minimum === "number" && numeric < definition.minimum) {
      errors.push(`${definition.label} must be at least ${definition.minimum}.`);
    }

    if (typeof definition.maximum === "number" && numeric > definition.maximum) {
      errors.push(`${definition.label} must be at most ${definition.maximum}.`);
    }

    if (definition.recommendedRange && Number.isFinite(numeric)) {
      if (numeric < definition.recommendedRange.minimum || numeric > definition.recommendedRange.maximum) {
        warnings.push(
          `${definition.label} is outside the recommended range (${definition.recommendedRange.minimum} to ${definition.recommendedRange.maximum}).`,
        );
      }
    }

    return { valid: errors.length === 0, warnings, errors };
  }

  return { valid: errors.length === 0, warnings, errors };
}

export function validateParameterValues(
  strategySlug: string,
  values: Record<string, unknown>,
): ParameterValidationResult {
  const definitions = getParameterDefinitions(strategySlug);
  if (definitions.length === 0) {
    return {
      valid: false,
      warnings: [],
      errors: [`Unknown strategy slug: ${strategySlug}`],
    };
  }

  const allWarnings: string[] = [];
  const allErrors: string[] = [];

  for (const definition of definitions) {
    const result = validateParameterValue(definition, values[definition.key]);
    allWarnings.push(...result.warnings);
    allErrors.push(...result.errors);
  }

  for (const key of Object.keys(values)) {
    if (!definitions.some((definition) => definition.key === key)) {
      allWarnings.push(`Unknown parameter key ignored: ${key}.`);
    }
  }

  return {
    valid: allErrors.length === 0,
    warnings: allWarnings,
    errors: allErrors,
  };
}
