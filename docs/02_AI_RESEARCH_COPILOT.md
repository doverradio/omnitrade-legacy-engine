# 02 — AI Research Copilot

## Purpose

The AI Research Copilot explains evidence from the Pattern Intelligence Engine and helps the researcher understand:

- what patterns were detected
- what the strategy missed
- why a trade lost
- why a trade succeeded
- which hypotheses deserve testing
- where overfitting is likely

It expands human perception but is not the final authority.

## Dependency

```text
01_PATTERN_INTELLIGENCE_ENGINE.md
```

The Copilot must not duplicate pattern detection.

## Trust Model

Every statement must be linked to structured evidence and labeled:

```text
OBSERVATION
STATISTICAL EVIDENCE
HYPOTHESIS
RECOMMENDATION
WARNING
```

The Copilot may explain evidence. It may not invent evidence.

## Phase 1 Provider

Phase 1 requires no paid service, external API, Ollama, or neural network.

Use deterministic explanation templates over structured findings.

Example:

```text
PRIMARY CAUSE
Late Entry

OBSERVATION
The position opened after 78% of the selected upward move had already occurred.

STATISTICAL EVIDENCE
Similar conditions appeared 42 times in Training and produced an average expected-cost return of -0.31%.

CONTRIBUTING FACTORS
Low relative volume
Negative short-term slope
Weak follow-through

RECOMMENDATION
Create a candidate rule that prevents entry after momentum deceleration.
```

## Replaceable Explanation Interface

Define:

```text
ResearchExplanationProvider
```

Future implementations may include:

- DeterministicTemplateProvider
- LocalSmallModelProvider
- LocalOllamaProvider
- FutureOmniTradeNeuralExplainer

The deterministic provider remains the fallback.

## User Interface

Add an **AI Research Copilot** panel with tabs:

- Observed Patterns
- Missed Opportunities
- Failure Analysis
- Success Analysis
- Candidate Rules
- Overfitting Warnings
- Research Notes

## Main Actions

### Analyze Selection
Explain Pattern Intelligence findings for a selected candle range.

### Show Me What I Missed
Identify patterns present in the visible range that the current strategy did not exploit.

### Explain This Loss
Report:
- primary cause
- contributing factors
- alternative entries
- alternative exits
- historical recurrence
- estimated improvement range
- confidence

### Why Did This Work?
Report:
- primary success factors
- supporting evidence
- repeatability
- whether the result appears robust or accidental

## Estimated Improvement

Estimated improvement must come from deterministic counterfactual replay:

```text
Observed result: -0.72%
Candidate variation: +0.08%
Estimated delta: +0.80 percentage points
```

Always identify:

- exact rule variation
- partition
- cost model
- sample size
- whether Validation and Final Test agree

## Overfitting Warnings

Warn when:

- Training improves while Validation deteriorates
- performance depends on a narrow threshold
- sample size is small
- one outlier dominates
- too many rules were tried
- Final Test was used for tuning

## Restrictions

The Copilot may create Candidate Rule proposals.

It may not:

- edit an active strategy
- deploy a rule
- change parameters silently
- modify live trading
- present unsupported claims as facts

## Persistence

Store:

```text
source_findings
generated_explanation
provider
provider_version
template_or_prompt_version
user_decision
linked_candidate_rule
linked_strategy_branch
```

## Definition of Done

A user can select a replay range or trade and receive a clear, evidence-linked explanation of what happened, what may have been missed, and what experiments could be tested next—without any external AI dependency.
