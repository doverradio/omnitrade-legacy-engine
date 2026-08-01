# ADR-0017: Canonical Market Slot and Event Identity

## Status
Accepted

## Context

Phase 2 needs stable identity for an economic instrument and its candle intervals before a non-authoritative canonical shadow can be placed between Kraken normalization and existing candle persistence. The current `Asset.id` identifies a database asset row, provider aliases such as `XBTUSD` and `XXBTZUSD` identify Kraken-facing representations, and `Candle.id` identifies a mutable database projection. None of those identities is the provider-independent BTC/USD spot instrument, and none is an immutable canonical event.

Using one UUID for more than one of these domains would collapse materially different meanings. In particular, assigning one `EventId` to a candle slot and then changing the canonical content under that ID would make immutable audit, revision history, replay, and integrity verification ambiguous.

## Decision

### Economic instrument identity

- `InstrumentId` identifies a provider-independent economic instrument.
- The first supported key domain is unleveraged crypto spot. Its versioned key contains exactly `asset_class`, canonical `base_asset`, canonical `quote_asset`, and `instrument_kind`.
- BTC/USD spot uses canonical base `BTC`, canonical quote `USD`, and instrument kind `spot`. Kraken's `XBT` alias is not canonical BTC and is not silently normalized by the identity foundation.
- Provider, venue, requested pair, and returned response-series aliases do not participate in `InstrumentId`.
- Provider-market identity and effective-dated provider aliases are separate future domains.

### Economic candle-slot identity

- `CandleSlotId` identifies a provider-independent economic interval, not an event.
- Its versioned key contains exactly `InstrumentId`, canonical interval, and timezone-aware open time normalized to UTC.
- Provider, venue, provider pair, OHLCV content, acquisition time, ingestion run, and database environment do not participate in `CandleSlotId`.

Both identities use deterministic UUIDv5 derivation over explicitly versioned canonical JSON keys. They use separate fixed namespaces so equal text cannot collapse the identity families. The pure helpers access no clock, random source, environment, configuration, filesystem, database, network, or provider.

### Canonical event identity

- `EventId` identifies exactly one immutable admitted canonical envelope instance.
- It does not identify an economic candle slot, provider candle slot, acquisition attempt, mutable database row, or integrity hash.
- One `EventId` cannot validly map to multiple canonical serializations or integrity hashes. The future admission layer may treat identical admitted content idempotently.
- Revised content requires a new `EventId` and explicit revision or supersession evidence.
- EventId generation and canonical admission are deferred. This ADR does not define or generate EventId values.
- Acquisition, provider-observation, provider-market, and revision identities are also deferred.

### Time and finality

For a future interval-eligible admitted candle observation, `occurred_at` is the nominal close boundary. That boundary may be derived from open time and interval. Interval elapsed does not mean provider final, and an incomplete candle must never be represented as a completed canonical candle event.

Receipt, availability, provider finality, revision, and admission policies remain future work. This decision does not claim that those mechanisms exist today.

This ADR authorizes no production integration, persistence, provider access, or trading authority. The new identity foundation remains unused until a later, separately reviewed boundary connects it.

## Alternatives Considered

- Reuse `Asset.id` as `InstrumentId`. Rejected because a database asset row and an economic instrument are distinct identity domains, and independently seeded databases allocate different asset UUIDs.
- Include Kraken, XBT, or provider pairs in `InstrumentId`. Rejected because provider aliases and venue listings must not fracture provider-independent economic identity.
- Use `EventId` as the candle-slot ID. Rejected because revisions would either mutate immutable event content or require one EventId to map to multiple integrity hashes.
- Generate an EventId in the pure adapter. Rejected because admission, idempotency, revision, and finality evidence are not available there.
- Include OHLCV in `CandleSlotId`. Rejected because content integrity and economic interval identity are separate concerns.

## Consequences

Benefits:
- BTC/USD spot has stable identity across providers, processes, deployments, and independently seeded databases.
- Repeated observations of one interval share a stable economic slot without predetermining event or revision identity.
- Provider aliases, canonical events, content hashes, and database rows remain explicitly separate.
- Fixed identity vectors can be tested before any runtime integration exists.

Trade-offs:
- No complete `CandleObservationV1` can yet be created truthfully at the production shadow seam because EventId admission remains deferred.
- Provider-market mapping, exact Kraken pair provenance, receipt/availability time, finality, and immutable revision persistence require later decisions and implementation.
