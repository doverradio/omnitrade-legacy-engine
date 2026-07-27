# IMMUTABLE_HISTORICAL_DATASETS.md

Version: 1.0
Status: Constitutional Architecture (proposed)
Scope: The permanent architecture governing every historical dataset OmniTrade will ever consume
Depends-on: `PROJECT_CONSTITUTION.md`, `HISTORICAL_INTELLIGENCE_PLATFORM.md`, ADR-0010, ADR-0011
Depended-on-by: Historical Simulation, Historical Media Intelligence, Synthetic Evidence, Decision Records, Future AI Research

**Evidence tags:** `[VERIFIED]` confirmed in the repository · `[DECIDED]` established in a prior ADR/authorization · `[RECOMMENDATION]` proposed here · `[CHALLENGE]` a departure from the proposed framing.

---

## 0. How to read this document (three challenges up front)

The operator's brief asked me to challenge rather than agree. Three framing corrections are load-bearing for everything below; if only these survive, the document has done its job.

**0.1 Immutability is not the actual guarantee we need — it is one of two.** `[CHALLENGE]` A dataset can be perfectly immutable and still poison a simulation with future knowledge. Immutability buys **reproducibility** (rerun → same result). It does *not* buy **point-in-time correctness** (a decision at T saw only what T could see). These are orthogonal. This document must deliver both, and it does so with two different mechanisms: content-addressing (§3, §6) for immutability, and bitemporal availability metadata (§5, §9) for point-in-time correctness. Conflating them is the most common and most dangerous error in research infrastructure.

**0.2 "Corrections create Version 2" is only half right.** `[CHALLENGE]` There are two things that both look like corrections and must be handled oppositely:
- A **source error** discovered later (an exchange republishes bad candles) → yes, a new dataset **version**.
- A **genuine historical revision** (a macro statistic that was really revised in 2019, an article really edited after publication) → this is *not* an error. It is part of the historical truth the simulation must replay. It must be captured **inside a single version** as a bitemporal fact ("value was X as-of t1, became Y as-of t2"), never collapsed into a "correction."
Folding real revisions into corrections destroys point-in-time correctness; overwriting to "fix" them destroys reproducibility. Both are constitutional violations. §4 and §5 formalize the split.

**0.3 The database is not the dataset.** `[CHALLENGE]` `[VERIFIED]` OmniTrade's data lives in Postgres (`models/candle.py`, single engine in `db/session.py`). A Postgres table is a mutable operational store and a poor ten-year archival medium. The **authoritative** immutable dataset must be a **file-based, open-format, self-describing, content-addressed artifact plus a manifest**; the database is a *rebuildable projection* of it, never its source of truth. If the sim database is lost, every dataset must be reconstructable, byte-identical, from the archived artifacts. §7 makes this explicit.

---

## 1. Purpose

Historical research and live trading have opposite relationships with change.

- **Live production data** is a moving present. It is correct precisely *because* it updates — the latest price is the point.
- **Mutable operational databases** (`[VERIFIED]` OmniTrade's Postgres) are optimized for the current operational question. Rows are inserted, back-filled, corrected, re-ingested. `models/candle.py` has a `created_at` insert time and a `UNIQUE(asset_id, interval, open_time)` — a later re-ingest can replace the *understanding* of a past candle, and nothing records that it changed.
- **Immutable historical datasets** are frozen universes. Once published, the bytes never change; a new understanding is a new version, and the old version remains forever retrievable.

Historical research requires the third because a research result is only meaningful relative to the exact evidence that produced it. If the evidence can silently change, then last year's promotion decision, this year's tournament, and next year's counterfactual are all computed against different, unrecoverable worlds — and none of them can be audited, compared, or trusted. A platform that will one day allocate real capital on the strength of historical evidence (Article IX, Article X) cannot let that evidence be written in sand.

The guarantee this document exists to provide: **any historical result OmniTrade ever produces can be re-derived, byte-for-byte, by someone who was not present when it was first produced, years later, using only the archived artifacts and the recorded versions.**

---

## 2. Dataset Philosophy

A **Historical Dataset** is a frozen, versioned, content-addressed representation of a bounded slice of a historical universe, carrying enough provenance and availability metadata to be both reproduced exactly and queried point-in-time.

A dataset is defined by four things it fixes:
- **A universe** — which assets/instruments/sources it covers.
- **A time range** — the historical span it represents.
- **A content** — the exact bytes, fixed by hash.
- **An availability model** — for every fact, when that fact became knowable (§5).

Example:
```
dataset_id:      btcusd.price.spot
dataset_version: 1   (content hash: sha256:9f2c… / merkle_root:…)
universe:        BTC/USD spot
time_range:      2016-01-01T00:00:00Z … 2020-12-31T23:59:59Z
```
Version 1, once published, never changes. A correction to the *source* produces Version 2. Version 1 is never overwritten and never deleted — every simulation that ever bound Version 1 must remain re-runnable forever. `[CHALLENGE]` "Never overwrite" is necessary but not sufficient; see §0.2 and §4 for why some changes are not new versions at all but bitemporal facts inside a version.

---

## 3. Dataset Identity

`[RECOMMENDATION]` Canonical identity is content-derived, not just assigned. The human-readable `dataset_id` + `dataset_version` are the *label*; the **content hash / Merkle root is the true identity** (§6). Two artifacts with the same bytes are the same dataset; two with different bytes cannot share a version, structurally.

Canonical manifest fields:

| Field | Meaning |
|---|---|
| `dataset_id` | Stable logical name (e.g., `btcusd.price.spot`) |
| `dataset_version` | Monotonic integer within `dataset_id` |
| `content_hash` / `merkle_root` | The true, content-derived identity (§6) |
| `schema_version` | The record/format schema this dataset conforms to (§14) |
| `dataset_type` | Taxonomy value (§8) |
| `asset_class`, `asset_ids` | Universe |
| `time_range` | `[start, end)` in UTC, half-open |
| `source`, `source_version` | Upstream origin and its version |
| `created_at`, `created_by` | Publication timestamp and responsible actor |
| `description` | Human summary of what and why |
| `license` | Usage/redistribution terms (real constraint for a multi-decade archive) |
| `storage_format`, `compression` | e.g., Parquet + zstd (§7) |
| `row_count`, `record_count` | Cardinality (row = physical, record = logical fact) |
| `timezone_policy` | Normalization applied (§5) |
| `missing_data_policy` | How gaps are represented (§5, §14) |
| `dedup_policy` | Duplicate-removal rule (§5, §9) |
| `revision_model` | `bitemporal` or `point_in_time_only` (§5) |
| `availability_basis` | Which timestamp defines "knowable" (e.g., candle `close_time`; media `knowledge_available_at`) |
| `provenance_ref` | Pointer to the full provenance record (§5) |
| `manifest_signature` | Optional detached signature over the manifest (§6) |
| `predecessor_version` | The version this supersedes, if any (never implies deletion) |

`[RECOMMENDATION]` Additional fields for long-term reproducibility: `builder_toolchain` (extraction+normalization software versions), `build_environment_digest` (container/lockfile hash used to produce it), `verification_report_ref` (the validation run that certified it), and `retention_class` (permanent vs. reproducible-on-demand).

---

## 4. Dataset Versioning

A new **version** is created when the *source understanding of history* changes:
- exchange-corrected or re-published candles;
- previously-missing candles discovered and back-filled;
- timezone or timestamp corrections in the source;
- corporate-action corrections (splits, dividends, redenominations) that fix earlier errors;
- better media extraction or newly-obtained archival media;
- corrected symbol/identity mappings (forks, mergers, delistings) discovered later.

**No version is ever modified after publication.** A correction is always a new version with a new content hash; the predecessor remains retrievable forever.

`[CHALLENGE]` **Crucial distinction (see §0.2).** The list above is *source corrections*. It must be kept separate from *genuine historical revisions* — facts that really changed in history (a GDP print revised weeks later; an article edited after publication; a candle an exchange itself revised *and stamped with the revision time*). Those are not errors and do not create a new dataset version. They are recorded as bitemporal facts **inside** a version (§5), so that a point-in-time query as-of a moment *before* the revision returns the pre-revision value. The test for which path applies: *"Did the world change, or did our copy of the world get fixed?"* World changed → bitemporal fact inside the version. Copy got fixed → new version.

Versioning is per-`dataset_id`. Because a simulation binds an explicit `(dataset_id, dataset_version)` (§10), publishing Version 2 never disturbs any prior result bound to Version 1.

---

## 5. Provenance

Every dataset answers, for the dataset as a whole and — where it matters — per record:

- **Origin:** `source`, `source_version`, acquisition method, acquisition time.
- **Transformation:** the ordered pipeline of steps (extraction → normalization → validation → packaging), each with the software version that performed it (`builder_toolchain`).
- **Timezone normalization:** the exact policy (all UTC, half-open intervals, DST handling).
- **Missing-data policy:** how absence is represented (explicit gap markers vs. omission) — never silent interpolation in a *raw* or *normalized* dataset; any imputation belongs only to a *derived* dataset and must be labeled and confidence-scored.
- **Duplicate-removal policy:** the canonical-record rule (§9 for media, where this is acute).
- **Validation:** which checks passed (monotonic timestamps, no `close_time > next open_time`, hash continuity), captured as a `verification_report`.

`[RECOMMENDATION]` **Bitemporality is the core of provenance, not an add-on.** Every fact carries at least two time axes:
- **event/valid time** — when the thing happened (candle `open_time`/`close_time`; a stat's reference period; an article's `event_time`);
- **knowledge/available time** — when the fact first became knowable to a contemporaneous observer (candle `close_time`; a stat's `published_at`; an article's `knowledge_available_at`).

`[VERIFIED]` The current `Candle` model carries `open_time`, `close_time`, `source`, and a DB `created_at`, but **no** `published_at`/`revision_time`/`available_at`. For completed OHLCV the `close_time` *is* a sound availability basis (a candle is knowable once it closes), which is exactly what ADR-0011 enforces — so price history can be made point-in-time-correct today. But any source with revisions (macro, fundamentals, media) requires the explicit knowledge/available axis, which does not yet exist and must be a first-class column in those dataset types. The dataset architecture must therefore assume bitemporality platform-wide even though the price path can start with `close_time` alone.

---

## 6. Hashing and Integrity

`[RECOMMENDATION]` Layered, content-addressed integrity:

1. **Chunk hashes.** The dataset is stored as ordered chunks (e.g., Parquet row-groups or file shards); each chunk has a SHA-256.
2. **Merkle root.** Chunk hashes roll up into a Merkle tree; the **root is the dataset's true identity** (§3). This gives *partial* verification (check one chunk without reading centuries of data), *localized* corruption detection (which chunk broke), and cheap append-with-new-version.
3. **Manifest.** A plain-text (JSON/TOML) manifest lists every chunk, its hash, the Merkle root, the schema version, and all §3 fields. The manifest is itself hashed.
4. **Optional detached signature.** A signature over the manifest establishes *authenticity* (who published it) across decades and maintainer turnover.

`[CHALLENGE]` A **single SHA-256 over the whole dataset is insufficient** at century scale — it forbids partial verification and makes append expensive. And **reproducibility must not depend on a signing key surviving ten years.** Keys get lost, rotated, or compromised; a family-legacy system will outlive its keys. Therefore the **content hash is the primary integrity anchor**, and signatures are a secondary authenticity layer. A dataset whose signature is unverifiable but whose Merkle root matches is still *usable evidence* (flagged as unsigned); a dataset whose Merkle root does not match **fails closed** and is unusable, signature or not.

Corruption detection: on load, recompute chunk hashes lazily against the manifest; a mismatch aborts the run (`DatasetIntegrityError`) rather than proceeding. Periodic background re-verification sweeps the archive.

---

## 7. Storage Architecture

`[RECOMMENDATION]` Five layers, each derivable from the one above, each independently versioned and content-addressed:

1. **Raw datasets** — bytes as acquired from the source, minimally touched. The archival root of trust. Never consumed directly by simulation.
2. **Normalized datasets** — schema-conformed, UTC, deduplicated, gap-marked. The first point-in-time-correct layer.
3. **Derived datasets** — corporate-action-adjusted series, imputed values (labeled), reconstructions. Explicitly lower-confidence; carries the derivation recipe.
4. **Feature datasets** — indicators/statistics computed *only* from point-in-time inputs (§ HIP Historical Feature Engine), each feature carrying its observation cutoff and source dataset version.
5. **Simulation-ready datasets** — the exact, indexed, gateway-loadable projection a run consumes (may live in the sim DB as a *cache*).
6. **Media datasets** — a parallel stack (§9) with its own raw/normalized/derived layers, bound to price datasets by time and version.

**Relationships:** each layer records the `(dataset_id, dataset_version)` of its inputs, so any derived/feature/simulation dataset is traceable to raw, and the whole chain is re-derivable. `[CHALLENGE]` The **file artifacts (layers 1–4) are the source of truth**; the sim database (layer 5) is a rebuildable cache. Format recommendation for ten-year validity: open, self-describing, columnar (Apache Parquet/Arrow) + plain-text manifests — never a proprietary or DB-locked binary as the archival form. `[VERIFIED]` this is compatible with the platform's Decimal discipline (store as decimal/string logical types, not float — see §13).

---

## 8. Dataset Types

`[RECOMMENDATION]` Taxonomy (the `dataset_type` field), extensible but with a governed enum so a value means the same thing in ten years:

`PRICE_HISTORY`, `ORDER_BOOK`, `TRADE_HISTORY`, `NEWS`, `SOCIAL`, `ONCHAIN`, `MACRO`, `CORPORATE_ACTIONS`, `FUNDAMENTALS`, `FEATURES`, `SIMULATION`.

Additions recommended: `REFERENCE` (asset registry / symbol-identity / instrument metadata over time — the backbone that resolves forks/renames/delistings, §14), `PROVIDER_CAPABILITY` (historical venue availability, fees, limits — required for provider-neutral replay), `CALENDAR` (sessions, holidays, halts), and `SYNTHETIC` (stress/scenario/generative — must be labeled `GENERATIVE_SYNTHETIC` per the HIP and never presented as real history).

Each type may carry type-specific required provenance (e.g., `MACRO` and `FUNDAMENTALS` *must* be bitemporal; `PRICE_HISTORY` may use `close_time` as availability basis).

---

## 9. Historical Media Integration

Media is the hardest case and therefore the clearest proof that bitemporality (§5) must be platform-wide.

`[RECOMMENDATION]` Every media record carries:
- `event_time` — when the described event occurred;
- `published_at` — when the item was published;
- `knowledge_available_at` — when it *first became observable to OmniTrade's contemporaneous vantage* (which may lag `published_at`: paywalls, syndication delay, late ingestion);
- `revision_history` — edits/retractions as bitemporal facts (an article's meaning at T is its state as-of T, not its final edited form);
- `source_authority` — a graded trust/provenance for the outlet;
- `dedup_key` — canonical-story identity so the same wire story across fifty outlets is one fact, not fifty.

**Simulation must key media visibility on `knowledge_available_at`, not `published_at` or `event_time`.** `[CHALLENGE]` If `knowledge_available_at` is unknown for an item, the platform must **fail closed or downgrade the evidence class** (per HIP anti-leakage) rather than guess — an unknown availability time is a leakage risk, not a rounding error.

**Relationship to price datasets:** media and price datasets are **independently versioned** and bound together only at simulation time through the binding manifest (§10). A media Version 2 (better extraction) does not force a price re-version. A simulation's reproducibility key is the *set* of `(dataset_id, dataset_version)` bindings plus the cutoff — so media and price versions co-vary explicitly, never implicitly.

---

## 10. Simulation Integration

`[DECIDED via ADR-0010/0011]` A Historical Simulation binds explicit dataset versions and advances a knowledge cutoff through them.

Every run references:
- `dataset_id` + `dataset_version` for **each** dataset it consumes (price, media, macro, reference, provider-capability);
- a `knowledge_cutoff` pointer that advances monotonically with the Historical Clock.

`[RECOMMENDATION]` Introduce a **content-addressed binding manifest** (a "dataset bundle"): the set of `(dataset_id, dataset_version)` a run binds is itself hashed, and the run records that `bundle_hash`. This is what makes multi-source, multi-asset runs reproducible with a single reference.

Why this guarantees reproducibility: the **dataset version fixes what exists**; the **knowledge cutoff fixes how far into it the run has advanced**; the **gateway (ADR-0011) fixes visibility** (a fact is visible only once its availability time ≤ cutoff, fail-closed). `[CHALLENGE]` Note the clean separation the operator's brief slightly blurred: `dataset_version` and `knowledge_cutoff` are **different axes** — one dataset version supports countless simulations at different cutoffs. The pair, not either alone, defines the observable world at each tick.

---

## 11. AI Research Integration

`[RECOMMENDATION]` Every future research activity — historical experiments, feature generation, strategy discovery, tournaments, counterfactuals — binds immutable datasets by version and records the bundle hash, exactly as simulations do. The promotion gate (`HISTORICAL_INTELLIGENCE_PLATFORM.md`: train → validate → **untouched test** → forward-paper → bounded live) is only meaningful if the "untouched test" history is *provably* untouched. `[CHALLENGE]` **Content-addressing is what makes "untouched" falsifiable.** Without it, "we didn't tune against the test set" is an unverifiable promise; with it, the test dataset's Merkle root is fixed before tuning begins and checked after, so leakage-by-iteration becomes detectable rather than deniable. This is the single strongest argument for this document's existence.

Feature and model artifacts produced by research are themselves versioned datasets (§7 layer 4) referencing their input versions, so a discovered strategy's entire evidentiary lineage is a re-executable chain.

---

## 12. Decision Record Integration

`[VERIFIED]` The production `DecisionRecord`/`DecisionSnapshot` (`models/decision_record.py`, `models/decision_snapshot.py`) are immutable (event-enforced), carry `field_provenance`/`source_lineage` and five version pins, but have no dataset lineage. `[DECIDED via ADR-0010]` Synthetic decision records live in separate `simulation_*` tables and carry provenance.

`[RECOMMENDATION]` Every **synthetic** Decision Record references, at minimum:
`dataset_id`, `dataset_version` (per bound dataset, or a single `bundle_hash`), `simulation_id`, `branch_id`, `knowledge_cutoff`, `run_mode`, `evidence_class`, `execution_model_version`, `risk_policy_version`, `decision_engine_version`, `strategy_version`, `random_seed`, `created_at`.

Additional provenance recommended: `bundle_hash` (the §10 binding), `builder_toolchain`/`build_environment_digest` (the exact code+environment that produced the decision, so the *engine* is as pinned as the *data*), `feature_dataset_versions` (if features were precomputed), and `gateway_contract_version` (the ADR-0011 visibility rule in force). This closes the loop: a synthetic decision names not only what it decided and why, but the exact frozen world, engine, and rules that produced it.

---

## 13. Reproducibility Contract

**The constitutional guarantee:** given identical `dataset bundle (by hash)`, `knowledge cutoff`, `random_seed`, `strategy_version`, `risk_policy_version`, `execution_model_version`, `decision_engine_version`, and `build environment`, a rerun yields **identical** results.

`[CHALLENGE]` "Identical" must be *defined*, and the full assumption set must be honest — the operator's list is necessary but not complete. Bit-identical reproducibility additionally requires:

1. **Deterministic arithmetic.** `[VERIFIED]` OmniTrade's price/decision path uses `Numeric`/`Decimal` (candle OHLCV, `risk_engine`, orchestrator) — no binary floats in the decision math. This makes bit-identical results *feasible*, which most quant stacks cannot claim. Any future component that introduces floats must define a tolerance and drop the guarantee from "bit-identical" to "numerically-equal within ε".
2. **Deterministic control flow.** `[VERIFIED]` `evaluate_signal_risk` and the registered strategies are pure and have no wall-clock/network I/O on the decision path (two `now()` *fallbacks* in `strategies/helpers.py`/`ma_crossover.py` are routed through the clock in simulation). Iteration order over datasets must be fixed (ordered chunks, §6).
3. **Seeded stochasticity + fixed hash seed.** All randomness seeded; `PYTHONHASHSEED` pinned so dict/set ordering is stable.
4. **No non-deterministic external calls on the decision path.** `[VERIFIED]` no LLM/model SDK is imported on the trading path today. `[CHALLENGE]` Any future ML/LLM component must **capture its output into the immutable record**, not regenerate it — because a hosted model is not reproducible, only its recorded output is. Reproducibility then means "the recorded output replays identically," not "the model would answer identically."
5. **Pinned build environment.** Library versions and the toolchain digest are part of the pinned set; "same code" means same lockfile, not same repo branch.
6. **Frozen data by hash, not by name.** The bundle is bound by Merkle root; a dataset silently re-pointed to new bytes breaks the contract — which content-addressing makes impossible.

Where any assumption cannot be met, the platform must **downgrade the reproducibility claim explicitly** (e.g., `reproducibility_class: numerically_equivalent` or `output_replayed`) rather than assert a guarantee it cannot honor.

---

## 14. Failure Modes and Mitigations

| Risk | Mitigation |
|---|---|
| Dataset corruption | Merkle/manifest verification on load; fail closed; periodic archive sweeps (§6) |
| Partial / interrupted imports | A dataset is unpublished (no manifest, no root) until complete and verified; no half-datasets are bindable |
| Timezone drift | Single UTC policy recorded in `timezone_policy`; validation rejects non-UTC; half-open intervals |
| Future-data leakage | Bitemporal availability + fail-closed gateway (ADR-0011); `knowledge_available_at` required for revisable types (§5, §9) |
| Exchange corrections | New **version** (§4), predecessor retained; runs bound to the old version unaffected |
| Genuine historical revisions | Bitemporal facts **within** a version (§0.2, §5), not a new version — else point-in-time correctness breaks |
| Duplicate news / stories | `dedup_key` canonicalization; source-authority grading (§9) |
| Missing data | Explicit gap markers in raw/normalized; imputation only in labeled derived layers; `missing_data_policy` recorded |
| Clock skew | Simulated time comes only from the Historical Clock (ADR-0008); wall-clock never enters the decision path in simulation |
| Schema evolution | `schema_version` in the manifest and in the content hash; versioned readers; a schema change produces a **new** dataset version, never an in-place edit `[CHALLENGE]` this is the quiet ten-year killer and must be designed for now |
| Media revisions | Revision history as bitemporal facts; simulation sees the as-of state (§9) |
| Symbol changes / forks / splits / migrations | A `REFERENCE` dataset type (§8) records identity-over-time as bitemporal facts; `asset_ids` are canonical, decoupled from venue symbols (aligns with the HIP Asset Registry) |
| Lost/compromised signing keys | Content hash is the primary anchor; signatures secondary; unsigned-but-hash-valid data remains usable-with-flag (§6) |
| Silent re-point of a dataset name to new bytes | Impossible under content-addressing — different bytes cannot be the same version (§3) |

---

## 15. ADR Recommendation

`[RECOMMENDATION]` **Do both, in the pattern the project already uses.** `[VERIFIED]` the repo's own convention (`PROJECT_CONSTITUTION.md` §"Relationship to Other Documents", `docs/adr/`) is that ADRs are the terse, durable *why* and specifications are the detailed *how*. Accordingly:

- Create **ADR-0012 — Immutable Historical Datasets**: a short, permanent decision record capturing the principle (historical datasets are immutable, content-addressed, bitemporal; every simulation/experiment/synthetic Decision Record binds explicit dataset versions by hash), the alternatives rejected (mutable operational store as source of truth; single-hash integrity; corrections-only revision model), and the consequences.
- Keep **this document** as the full architecture specification ADR-0012 points to.

`[VERIFIED]` This requires **no revision** of ADR-0008/0009/0010/0011. ADR-0010 and ADR-0011 already *assume* `dataset_id`/`dataset_version`/`knowledge_cutoff`; ADR-0012 formalizes the upstream dependency they rest on, rather than changing them. (Per the operator's constraint, no existing ADR is modified.) One forward note for the eventual implementation, not a change now: the provenance field lists in ADR-0010/§12 should be read as *superseded-compatible* with the additions here (`bundle_hash`, `build_environment_digest`, `gateway_contract_version`) when those ADRs move to implementation.

---

## 16. Future Evolution

Immutable datasets are the substrate that lets OmniTrade climb the ladder without losing its footing:

- **Historical Simulation** becomes trustworthy because every run names a frozen, hash-verified world.
- **Historical Intelligence** becomes cumulative because results computed years apart are comparable — they reference retrievable evidence, not a market history that has since changed underneath them.
- **Autonomous Research** becomes safe to trust because the promotion gate's "untouched test" is *provably* untouched (§11), making leakage-by-iteration detectable rather than deniable — the precondition for ever letting research influence capital.
- **Production Capital Allocation** becomes defensible because when the platform commits real capital on historical evidence, "why did we do this?" is answerable by **re-execution**, not by narrative — the operational form of Article X (Stewardship): a maintainer who was not present can re-derive the reasoning byte-for-byte.

The through-line: as OmniTrade's authority over real capital grows, the standard of proof its evidence must meet also grows. Immutable, content-addressed, bitemporal datasets are what let the standard of proof rise without the past becoming unrecoverable. A platform built to outlast its creators must be able to hand them a frozen world and say: *run it yourself, and you will see exactly what we saw.*

---

*This document is architecture and governance only. No code was written, the repository was not modified, and no deployment is proposed. It is submitted for adoption as permanent constitutional architecture, with the recommendation that its principle be recorded as ADR-0012.*
