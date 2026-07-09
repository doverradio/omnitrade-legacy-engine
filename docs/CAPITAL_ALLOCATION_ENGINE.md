# Capital Allocation Engine v1

## Purpose
Capital Allocation Engine v1 generates deterministic, read-only paper capital allocation recommendations across active strategies.

The engine is recommendation-only and is designed for human approval workflows.

## Inputs
- Tournament rankings
- Decision Intelligence recommendation
- Decision Quality results

## Outputs
Capital Allocation Recommendation:
- recommendation_id
- generated_at
- total_paper_capital
- allocations[]

Each allocation contains:
- strategy_name
- allocation_percent
- allocation_amount
- rationale

## Repository Boundaries
Allowed:
- Read deterministic ranking and quality evidence
- Produce deterministic paper-capital recommendations
- Expose read-only recommendation data in Strategy Arena

Not allowed:
- AI or LLM usage
- Automatic execution
- Live trading
- Production writes
- Strategy mutation
- Portfolio mutation

## Future Evolution
Future versions may add policy-versioned allocation templates, broader deterministic evidence windows, and configurable paper-capital baselines.

Any evolution must preserve read-only recommendations and explicit human approval requirements.
