import { describe, expect, it } from "vitest";

import {
  generateParameterDefinitions,
  getParameterDefinitions,
  validateParameterValue,
  validateParameterValues,
} from "@/lib/parameterDefinitions";

describe("parameterDefinitions", () => {
  it("returns definitions for supported strategies", () => {
    const maDefinitions = getParameterDefinitions("ma_crossover");
    const breakoutDefinitions = getParameterDefinitions("breakout");

    expect(maDefinitions.length).toBeGreaterThan(0);
    expect(breakoutDefinitions.length).toBeGreaterThan(0);
    expect(maDefinitions.some((item) => item.key === "fast_period")).toBe(true);
    expect(breakoutDefinitions.some((item) => item.key === "volume_confirmation")).toBe(true);
  });

  it("returns empty definitions for unknown strategy", () => {
    expect(getParameterDefinitions("unknown_strategy")).toEqual([]);
  });

  it("uses deterministic fallback metadata for unknown keys", () => {
    const definitions = generateParameterDefinitions({
      custom_threshold: 2.5,
    });

    expect(definitions).toHaveLength(1);
    expect(definitions[0]).toMatchObject({
      key: "custom_threshold",
      label: "Custom Threshold",
      description: "Description not yet available.",
      beginnerDescription: "Beginner explanation coming soon.",
      type: "decimal",
      defaultValue: 2.5,
      required: true,
      advanced: false,
    });
  });

  it("validates enum values", () => {
    const maTypeDefinition = getParameterDefinitions("ma_crossover").find((item) => item.key === "ma_type");
    expect(maTypeDefinition).toBeDefined();

    const valid = validateParameterValue(maTypeDefinition!, "sma");
    const invalid = validateParameterValue(maTypeDefinition!, "wma");

    expect(valid.valid).toBe(true);
    expect(valid.errors).toEqual([]);
    expect(invalid.valid).toBe(false);
    expect(invalid.errors[0]).toContain("must be one of");
  });

  it("validates numeric ranges and emits recommendation warnings", () => {
    const slowPeriodDefinition = getParameterDefinitions("ma_crossover").find((item) => item.key === "slow_period");
    expect(slowPeriodDefinition).toBeDefined();

    const belowMinimum = validateParameterValue(slowPeriodDefinition!, 2);
    const outsideRecommended = validateParameterValue(slowPeriodDefinition!, 300);

    expect(belowMinimum.valid).toBe(false);
    expect(belowMinimum.errors[0]).toContain("at least");

    expect(outsideRecommended.valid).toBe(true);
    expect(outsideRecommended.warnings[0]).toContain("outside the recommended range");
  });

  it("validates full strategy values and reports unknown keys as warnings", () => {
    const result = validateParameterValues("rsi_mean_reversion", {
      rsi_period: 14,
      oversold: 30,
      overbought: 70,
      extra_field: true,
    });

    expect(result.valid).toBe(true);
    expect(result.errors).toEqual([]);
    expect(result.warnings[0]).toContain("Unknown parameter key ignored");
  });

  it("returns deterministic unknown strategy error during aggregate validation", () => {
    const result = validateParameterValues("not_real", {});

    expect(result.valid).toBe(false);
    expect(result.errors).toEqual(["Unknown strategy slug: not_real"]);
    expect(result.warnings).toEqual([]);
  });
});
