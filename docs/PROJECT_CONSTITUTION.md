# PROJECT_CONSTITUTION.md

## OmniTrade Legacy Engine — Project Constitution

### Preamble

OmniTrade Legacy Engine exists to build a continuously improving financial decision platform — one that learns from every market opportunity it evaluates, preserves the reasoning behind every decision it makes, and compounds knowledge over decades rather than merely executing trades. It is built to be understood, trusted, and extended long after the circumstances of its creation are forgotten. What follows are the principles that do not change with each new feature, phase, or engine — the standard against which every future addition to this platform should be measured.

---

### Article I — Explainability

Every decision made by the platform must be explainable. No black-box trading decisions are permitted, regardless of how well they perform. The platform preserves not only what it did, but why it did it, in a form a person can read and understand — not merely in a form a machine can technically reconstruct.

### Article II — Decision Intelligence

The Decision Intelligence Engine is the institutional memory of OmniTrade. Every evaluated opportunity — taken, rejected, or left to pass — becomes structured knowledge rather than a transient log line. Reasoning is an asset of the platform, to be preserved and drawn upon, not a byproduct to be discarded once a trade is closed.

### Article III — Continuous Learning

The platform learns from executed trades, rejected trades, WAIT decisions, the Counterfactual Outcome Ledger, and Decision Quality evaluations alike. No category of decision is exempt from scrutiny, and no category is treated as more instructive than another simply because it resulted in a trade. Learning never stops, and no decision is ever truly final — it remains available for future review as the platform's understanding improves.

### Article IV — Decision Snapshot

Every Decision Record must preserve an immutable Decision Snapshot of the exact context that produced it. Future analysis must be reproducible, not reconstructed from approximation or memory. When the original context can be preserved, the platform preserves it — it does not settle for what can be inferred later.

### Article V — Decision Quality

The platform optimizes for decision quality, not merely for profit. Profit matters, but it is a noisy and sometimes misleading signal in the short term: good decisions can occasionally lose, and bad decisions can occasionally win. Decision quality is the platform's primary long-term metric, and the platform is expected to continuously improve the quality of its reasoning even in periods where profit alone would suggest nothing needs to change.

### Article VI — Architectural Stability

The platform permanently consists of four core engines: the Market Intelligence Engine, the Strategy Evolution Engine, the Decision Intelligence Engine, and the Portfolio Intelligence Engine. This structure is not incidental and is not to be casually expanded. Major architectural changes require a formal Architecture Decision Record. Architecture evolves intentionally, through documented decisions — never by accumulation of undocumented exceptions.

### Article VII — Evidence

Features are adopted because evidence supports them — not because they are fashionable, not because they are exciting, and not because a competing platform has them. The burden of proof belongs to the new idea, not to the existing, working system it proposes to change.

### Article VIII — Safety

Capital preservation is a first-class concern, equal in standing to performance, not subordinate to it. Risk controls are never bypassed for convenience, speed, or a compelling short-term opportunity. Safety is not a constraint imposed on the platform's intelligence — it is part of what intelligence means for a system entrusted with capital, however small.

### Article IX — Institutional Memory

Knowledge compounds. Every decision, whatever its outcome, contributes to a growing body of evidence about how markets behave and how well the platform reasons about them. The platform is expected to become wiser over time, in a way that is visible and demonstrable, not merely asserted. The knowledge base this produces may ultimately prove more valuable than any single strategy it ever ran.

### Article X — Stewardship

The project is built to outlast its original creators. Code should be understandable by someone who did not write it. Architectural decisions should be documented at the time they are made, not reconstructed afterward from memory or inference. Future maintainers should inherit clarity, not confusion — and the platform, as a whole, should remain understandable decades into the future, to people who were not present for any of the decisions recorded here.

### Article XI — Capital Stewardship

OmniTrade exists to steward capital responsibly, not merely to trade it. Trading is one mechanism by which capital may be increased, preserved, allocated, or protected. The platform is designed to optimize long-term capital stewardship across any future asset class, market, or investment domain while preserving explainability, safety, and human accountability.

---
## Git Branch Invariant

The canonical default branch for the OmniTrade repository is:

master

AI assistants must never assume repository conventions.

Before generating git commands, verify or use the documented repository invariants, including:

• Default branch
• Canonical remote
• Repository root
• Deployment workflow

Unless this document is intentionally amended, all examples must target master.

---
When generating terminal commands:

• Emit single-line commands.
• Leave exactly one blank line after every command.
• Use the repository's canonical branch (`master`) unless the repository itself changes.


WARNING

This repository intentionally uses `master` as its canonical branch.

Historically, AI assistants have repeatedly assumed `main`, causing failed push and pull commands.

Treat `master` as a permanent repository invariant unless this Constitution is explicitly amended.


---
## API-First Operations Principle

Any operational capability expected to be performed more than once should be exposed as a secure, authenticated backend API. Manual terminal commands are considered temporary operational tooling and should eventually be replaced by governed APIs unless they are infrastructure administration tasks.


---
## Constitutional Principle: Maximize Risk-Adjusted Returns

OmniTrade exists to maximize long-term risk-adjusted capital growth.

All architectural, strategic, and operational decisions shall optimize for the highest sustainable risk-adjusted returns rather than maximizing trade count, raw profit, or excessive conservatism.

Risk controls exist to prevent catastrophic loss—not to unnecessarily constrain profitable capital deployment.

When multiple implementations satisfy the same safety objectives, the implementation that enables greater autonomous capital efficiency, scalability, and compounding shall be preferred.

Every hard limit must have a clearly defined risk rationale. Rules that exist only because they are simple or conservative, rather than because they reduce meaningful risk, should be redesigned or removed.


---
### Relationship to Other Documents

This Constitution sits above the rest of the documentation set, but does not replace or duplicate any of it:

- **`PROJECT_VISION.md`** describes the platform's mission, philosophy, non-goals, and long-term horizon — the *purpose* the platform is pursuing and the shape its growth is expected to take. The Constitution describes the fixed principles that vision is pursued *within*; the vision may be revised as the platform's circumstances change, but it may not be revised to contradict this Constitution.
- **`SYSTEM_ARCHITECTURE.md`** and the other architecture documents describe **how** the platform is built — the concrete components, data flows, and technical structure. The Constitution does not specify implementation; it specifies the principles that any valid implementation must honor.
- **ADR documents** (`docs/adr/`) describe **why** a specific architectural decision was made — the context, the alternatives considered, and the trade-offs accepted at a particular point in time. Where an ADR and the Constitution appear to be in tension, the ADR's reasoning should be revisited, since the Constitution is the more stable of the two.
- **`DECISION_INTELLIGENCE_ENGINE.md`** describes the concrete design of the subsystem that exists to fulfill Articles II, III, and IV in practice — it is the technical home for principles this Constitution states but does not itself implement.

In short: the Constitution defines what OmniTrade fundamentally believes; the vision defines what it is trying to achieve; the architecture defines how it is built; the ADRs defines why each significant building choice was made the way it was. These documents are expected to complement, not restate, one another. This Constitution should change extremely rarely — far more rarely than any of the documents it sits above.

---

## Constitutional Amendment

This Constitution is intended to remain stable across decades, not development cycles. It should evolve only when the platform's most fundamental principles require clarification, correction, or expansion. New features, new engines, new markets, new asset classes, and new implementation techniques should ordinarily be accommodated beneath this Constitution rather than by changing it.

Amendments should therefore be exceedingly rare and undertaken only after deliberate architectural review. Every amendment should preserve the internal consistency of the constitutional architecture and should avoid redefining principles that have already become foundational to the platform.

Whenever this Constitution is amended:

- The reason for the amendment should be documented in a corresponding Architecture Decision Record (ADR).
- The amendment should be evaluated for consistency with every existing constitutional architecture document.
- Existing constitutional principles should be preferred over introducing new ones whenever practical.
- Constitutional additions should represent enduring governance principles rather than implementation details or temporary operational concerns.

The constitutional architecture exists to provide long-term stability. It should grow only when a genuinely new constitutional concern is discovered, not merely because additional documentation could be written. Constitutional restraint is itself a form of stewardship.

### Documentation Governance

Operational documentation is considered part of OmniTrade's long-term architectural stewardship.

Whenever a pull request introduces or materially changes any production environment file, systemd unit, infrastructure component, deployment dependency, operational entry point, or other critical operational artifact, the corresponding updates shall be made to `00_OPERATIONS_MAP.md` within the same pull request.

`00_OPERATIONS_MAP.md` is the canonical operational index for the platform and should remain the fastest way for a new maintainer or AI assistant to locate and understand OmniTrade's production infrastructure.