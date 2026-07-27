# CUSTODY_AND_SECURITY_MODEL.md

Version: 1.0
Status: Constitutional Architecture (proposed)
Layer: Physical Root of Trust — the foundation beneath every other constitutional document
Scope: Who actually possesses the physical ability to exercise authority

**Evidence tags:** `[VERIFIED]` confirmed in the repository this session · `[DECIDED]` prior ADR/authorization · `[RECOMMENDATION]` proposed here · `[CHALLENGE]` a deliberate departure from the proposed framing · `[UNKNOWN]` not established by inspection.

The prior document (`AUTHORITY_AND_ACCOUNTABILITY_MODEL.md`) governs *who may act*. This one governs *who can actually act* — and the two are not the same. The goal is not maximum security; it is trustworthy, governed, accountable stewardship of physical capability.

---

## 0. The one idea

**Authority is a claim; capability is a fact.** Every other constitutional document assumes that the entities it authorizes are the same entities that are *physically able* to act. That assumption is false by default. `[VERIFIED]` in OmniTrade today, anyone who possesses the `.env` file (or the process environment) plus database access holds the practical power to move real capital — because that combination yields both the encrypted exchange credentials and the key that decrypts them, regardless of what any mandate, Risk gate, or kill switch says. **This document exists to make the physical root of trust explicit, minimal, governed, and accountable — so that *can* and *may* coincide.** If they diverge, the entire constitution above is decorative.

---

## 1. Purpose — why custody is a different thing from authority

Six concepts, now completed downward from the authority model:

- **Authority** — the granted *right* to act (prior document).
- **Permission** — a scoped instance of authority.
- **Capability** — the physical *ability* to act, independent of any right.
- **Custody** — governed possession of the things that confer capability (keys, secrets, infrastructure).
- **Possession** — raw holding of those things, governed or not.
- **Control** — the effective power to direct outcomes, which flows from possession.

Authority answers *may*. Capability answers *can*. `[CHALLENGE]` Constitutional governance is incomplete without a custody model because **capability, not authority, is the true root of trust**: a person with no authority but possession of the encryption key and the database can act; a person with full authority but no key cannot. Security is the discipline of forcing *can* to track *may*. Everything above this document assumes that discipline exists; this document defines it.

---

## 2. Root of Trust

The complete chain, from the thing that ultimately decides to the thing ultimately at stake:

```
Human Owner            — the only entity that can be held accountable; the apex
  ↓ authenticates as
Identity               — the credential that proves "this is the owner/operator"
  ↓ unlocks
Credentials            — API keys, provider secrets, deployment access
  ↓ protected by
Secrets                — the encryption key, JWT secret, DB password
  ↓ hosted on
Infrastructure         — servers, database, storage, DNS, repository, CI/CD
  ↓ connects to
Execution Providers    — exchanges that hold and move
  ↓
Capital                — the thing being stewarded
```

Each layer depends on the integrity of the one above and is a *capability multiplier* for the one below. `[CHALLENGE]` The proposal draws this as a clean cascade; the sharp truth is that **the chain is only as strong as the layer where the encryption key and the ciphertext meet.** `[VERIFIED]` today that meeting point is a single deployment: `EXCHANGE_CREDENTIALS_ENCRYPTION_KEY` and `DATABASE_URL` live in the same `.env`, so the "Secrets" and "Infrastructure" layers are not compartmentalized from each other — collapsing five layers of defense into one. The root of trust is therefore, in practice, *possession of that one environment plus that one database* (§3, §18).

---

## 3. Physical Capability vs Authorized Authority

`[VERIFIED]` The concrete capability surface:

- **Exchange credentials** — encrypted at rest with Fernet in `ExchangeConnection.credentials_encrypted`; masked forms (`api_key_masked`) exposed in read models. Good practice for storage.
- **The master key** — `exchange_credentials_encryption_key` (Fernet symmetric key), a single env-held secret. Whoever holds it + DB access decrypts every stored credential.
- **Direct provider secrets** — `KRAKEN_API_KEY`/`KRAKEN_API_SECRET` can also be supplied directly via env, a second credential surface that bypasses the DB-encrypted path entirely.
- **Database credentials** — `DATABASE_URL` (with an inline `postgres:postgres` default in `.env.example`).
- **Platform secrets** — `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`.
- **Deployment/cloud/DNS access** — `[UNKNOWN]` in-repo; held externally by whoever operates the VPS and domain.

Whoever possesses any of these **already possesses tremendous practical authority**, independent of the mandate/campaign/Risk stack. This is the distinction the whole constitution has so far assumed away:

- **Authorized authority** is *revocable, bounded, and accountable* — the prior document's machinery.
- **Physical capability** is *none of those by default* — a leaked key is not "bounded," and a person with server access is not automatically "accountable."

`[CHALLENGE]` The most important sentence in this document: **every safety property the authority model provides can be bypassed by a holder of physical capability, so the authority model is only as trustworthy as the custody model beneath it.** Governance must therefore make physical capability itself scarce, compartmentalized, expiring, and audited.

---

## 4. Custody — what is held, by whom, and who may never hold it

| Custody domain | Owner (accountable) | May access | May never access |
|---|---|---|---|
| **Capital** | Human Owner | Providers (hold it), the authorized campaign (moves it, bounded) | AI; any unauthorized process |
| **Credential** (exchange keys) | Owner/Operator | The execution path at submission time, via decryption | AI; frontend; logs; read models (masked only) `[VERIFIED]` |
| **Secret** (encryption/JWT keys) | Owner | The backend process at runtime | Everyone else; version control; backups-in-plaintext |
| **Infrastructure** (servers, DB) | Operator | Operators | AI; non-operators |
| **Repository** | Owner + maintainers | Maintainers (write), public/authorized (read) | Unreviewed automated writes to protected paths |
| **Deployment** | Operator | Operators with deploy rights | AI; anyone lacking a recorded grant |
| **Identity** (owner/operator identity) | The individual | The individual | Anyone else (impersonation = total compromise) |
| **Dataset** (immutable evidence) | Owner | Read-wide; write only via the publish pipeline | Anyone attempting mutation (immutability, §10) |
| **Audit** (the record of everything) | Owner | Append via system; read by auditors | Anyone attempting to modify/delete `[VERIFIED]` append-only |

`[VERIFIED]` `[CHALLENGE]` **`ExchangeConnection` — the object that literally holds the keys to real capital — has no `owner` field**, unlike `capital_campaign` which does. The most capability-laden object in the system lacks an explicit accountable custodian. This is a concrete custody-accountability gap (§18).

---

## 5. Credential Governance

`[VERIFIED]` present: encryption at rest (Fernet), masking in read models, a `credentials_valid` flag, recorded `api_permissions`, and a per-connection lifecycle (`status`, `last_verified_at`, readiness verdicts).

`[RECOMMENDATION]` the constitutional lifecycle every credential must have:
- **Creation** — least privilege; a credential is issued with only the provider permissions its purpose requires (`[VERIFIED]` `api_permissions` is recorded, enabling this check).
- **Rotation** — mandatory and periodic. `[VERIFIED]` `[CHALLENGE]` the code uses a single `Fernet(key)`, not `MultiFernet` — there is no visible rotation path for the master key, which is a long-term liability. Rotation must become first-class.
- **Expiration** — credentials and the authority to use them should expire (ties to the authority model's expiry principle).
- **Replacement / emergency replacement** — a documented, tested path to swap a credential fast under compromise.
- **Recovery** — `[CHALLENGE]` exchange secrets are **not recoverable**; they are *re-issued*. DR for credentials is rotation + re-issuance, never backup-restore (§13).
- **Revocation** — immediate, and recorded as a custody event.
- **Delegation** — a credential's *use* may be delegated to the execution path; *custody of the secret* may not be delegated to AI or to any unaccountable party.
- **Human approval** — issuing or replacing a *live* credential is a human, recorded act.

Principle: **temporary beats long-term; least privilege beats convenience; every credential has an owner and an expiry.**

---

## 6. Secret Management

`[VERIFIED]` secrets are typed `SecretStr` (resisting accidental logging) and sourced from `.env`/environment; there is no external secret manager or KMS referenced. `[CHALLENGE]` the constitutional weakness this reveals:

- **Master-key co-location.** `EXCHANGE_CREDENTIALS_ENCRYPTION_KEY` and `DATABASE_URL` share one `.env`. A single environment compromise yields both ciphertext and key. **The single most important custody recommendation in this document: the encryption key and the encrypted data must not share a custody boundary** — envelope encryption with the master key held by a distinct authority (KMS/HSM, or at minimum a separately-custodied secret), so one compromise ≠ total compromise.
- **No master/backup secret hierarchy.** `[RECOMMENDATION]` introduce a key hierarchy (a data-encryption key wrapped by a key-encryption key), enabling rotation without re-encrypting everything and enabling split custody of the top key.
- **Secrets in backups.** `[RECOMMENDATION]` backups may contain *ciphertext* freely but must never contain plaintext secrets; the master key is backed up separately, under separate custody, ideally split.

Constitutional governance of secrets: **secrets are never permanent, never co-located with what they protect, never in version control, never in plaintext at rest, and never held by an entity that cannot be held accountable.**

---

## 7. Infrastructure Custody

`[UNKNOWN]` in-repo (held externally): servers/VPS, cloud accounts, database hosting, object storage, DNS, domains, monitoring, logging, networking. `[VERIFIED]` the repo references a VPS-based operator workflow and a Supabase/Postgres + container deployment shape.

`[RECOMMENDATION]` because infrastructure custody is largely *outside* the codebase, it is the least self-documenting and therefore the highest drift risk. The constitution requires a **custody roster** — a maintained, out-of-band record of *which human holds which infrastructure account*, kept with the same seriousness as the code. Infrastructure serves stewardship; it is never an unowned dependency. Each infrastructure account is a capability multiplier and must have a named, accountable custodian and a documented successor (§12).

---

## 8. Repository Governance

`[VERIFIED]` `[CHALLENGE]` there is **no `CODEOWNERS` file and no CI-governance workflow present in this snapshot** (and three stray junk files at the repo root — `= [`, `operator`, `t \` — a minor hygiene signal). So "who may merge, deploy, or change protected paths" is **not encoded in the repository**; it rests entirely on external platform settings and convention.

`[RECOMMENDATION]` repository custody must be made explicit and enforced:
- **Code changes** — require review; protected branches.
- **Merges** — require an accountable approver; `[RECOMMENDATION]` a `CODEOWNERS` mapping so that **constitutional documents and ADRs require owner-level approval** to change, distinct from ordinary code.
- **Deploys / releases** — gated, recorded, and tied to an operator identity.
- **Constitutional documents & ADRs** — the highest bar: owner-only, recorded, and (per the authority model) non-delegable to AI.

The repository is itself a custody domain: whoever can merge to the deploy branch can, in effect, redefine the running system — a capability at least as powerful as holding a credential.

---

## 9. Execution Credential Custody

`[VERIFIED]` the strongest-governed part of the current system: exchange credentials are encrypted at rest, masked in read models, permission-recorded, environment-scoped (`sandbox`/`production` CHECK), and real submission occurs only at gated sites requiring decrypted credentials + a dry-run boundary.

`[RECOMMENDATION]` the residual gaps to close constitutionally:
- **Wallets / signing authority** — `[UNKNOWN]`/not present; if self-custody of on-chain assets is ever added, signing keys become the highest-value secret in the system and demand hardware/threshold custody from day one.
- **Provider onboarding/removal** — a recorded, human-approved act; adding a provider is granting a new physical capability.
- **Credential replacement/compromise** — a rehearsed runbook, not an improvised response.
- **Credential auditing** — `[RECOMMENDATION]` every *decryption/access* of a live credential should emit a custody audit event, so credential *use* is as reconstructable as credential *change* (§15). `[VERIFIED]` the append-only `audit_log` substrate exists to carry this.

---

## 10. Data Custody

`[VERIFIED]` the data-custody substrate is strong and largely correct by construction:
- **Decision Records / Snapshots / Arena risk-gate decisions** — immutable (event-enforced); no one may modify or delete them.
- **Audit log** — append-only.
- **Immutable historical datasets** — content-addressed, never overwritten (`IMMUTABLE_HISTORICAL_DATASETS.md`).

| Data | Owner | Protects it | May destroy | May never modify |
|---|---|---|---|---|
| Historical datasets | Owner | publish pipeline + hashing | no one (archival) | everyone (immutable) |
| Production datasets | Owner | backups | governed retention only | anyone bypassing the pipeline |
| Research datasets | Owner | isolation (ADR-0010) | governed | anyone (versioned) |
| Backups / logs | Operator | encryption | governed retention | tamperers |
| Audit / Decision records | Owner | append-only enforcement | **no one** | **everyone** `[VERIFIED]` |

Principle: **evidence is immutable; backups are encrypted; destruction is governed and rare; the audit trail is destructible by no one.**

---

## 11. Identity and Continuity (absorbing the prior Identity & Continuity gap)

`[RECOMMENDATION]` **Identity is logical and survives implementation.** The owner, an operator, a campaign, a provider, and an asset each have a *canonical identity* independent of any server, symbol, or credential that currently represents them:
- **Canonical identity** — a stable logical key, decoupled from venue symbols and infrastructure. `[VERIFIED]` the platform already has the instinct: campaigns carry a `canonical_campaign_id` distinct from runtime ids.
- **Ownership / campaign / provider continuity** — a change of key, server, or symbol does not change *who* or *what* something is; the mapping is versioned (a `REFERENCE`-type dataset, per the datasets doc).
- **Asset identity through forks / provider migrations** — an asset that forks, redenominates, or migrates venues retains one canonical identity with a versioned symbol history, so a grant authorized over "the asset" cannot silently drift to a different thing.
- **Historical continuity** — canonical identity is what lets a decade-old decision still be interpreted: the thing it referenced is still resolvable.

Identity survives implementation because authority and accountability are attached to *identities*, not to the transient credentials or symbols that happen to represent them today.

---

## 12. Succession (absorbing the prior Succession gap)

`[CHALLENGE]` `[UNKNOWN]` in the repository, and the most serious long-term gap for a decades-long, family-legacy system: **the entire physical root of trust currently terminates at whichever human holds the `.env`, the database, and the infrastructure accounts — and there is no defined succession if that human becomes unavailable.**

`[RECOMMENDATION]` constitutional succession requirements:
- **Owner succession** — a named successor with a defined, tested path to assume owner authority and custody.
- **Operator succession** — documented custody roster (§7) so any infrastructure account has a known successor.
- **Emergency succession / bus-factor** — the highest-value secrets (master encryption key, exchange keys) must not have a *single* point of failure *or* a single point of compromise. `[RECOMMENDATION]` **split/threshold custody** (e.g., M-of-N control) so that no single lost, incapacitated, or malicious individual can either destroy or unilaterally seize the root of trust.
- **Inheritance / death / disability / disappearance** — a legal-and-technical plan for capital and custody to transfer to successors, tested periodically rather than assumed.

`[CHALLENGE]` This is not operational paperwork — it is the *continuity of the accountability chain itself*. A steward who cannot pass the trust intact has not fully discharged the stewardship (Article X). For a system explicitly built to outlast its creators, an unaddressed succession plan is a first-order constitutional defect, not a future nicety.

---

## 13. Disaster Recovery

`[RECOMMENDATION]` recovery, by asset class of failure:
- **Credential loss** — not recoverable; **re-issue and rotate** (§5). Design assumes credentials are replaceable, never precious.
- **Infrastructure destruction / hardware failure** — rebuild from code + encrypted backups; `[VERIFIED]` the simulation DB is a *rebuildable projection* of immutable datasets (ADR-0010, datasets doc), so research state is reconstructable by design.
- **Database corruption** — restore from encrypted backup; immutable records make integrity verifiable on restore.
- **Repository loss** — mirrored, immutable history; the constitution and ADRs are the recoverable spine.
- **Provider shutdown** — provider neutrality (`[VERIFIED]` string-keyed provider registry) means a venue can be replaced without redesign.
- **Cloud compromise** — assume it; compartmentalization (§6) bounds the blast radius; rotate everything the compromised boundary could reach.
- **Recovery testing** — `[CHALLENGE]` a backup never tested is a backup that does not exist; recovery drills are constitutionally required, not optional.
- **Catastrophic failure** — the immutable evidence trail + re-issuable credentials + rebuildable infrastructure mean the system can be *reconstituted*; the one thing that cannot be reconstituted — the audit/decision history — is exactly the thing made immutable.

---

## 14. Security Philosophy

Permanent principles: **least privilege** (only the permissions a purpose requires); **least capability** `[CHALLENGE]` (a stronger sibling — minimize who is physically *able*, not only who is *permitted*); **defense in depth**; **zero trust** (authenticate and authorize every access, assume no implicit safe zone); **compartmentalization** (keys and ciphertext in different custody boundaries); **separation of duties** (no one holds initiate + approve + custody for the same capital act); **immutable evidence**; **fail closed**; **minimal authority**; **human accountability**. The organizing goal is not maximal security (which would freeze the system) but **capability that always tracks authority and always names an accountable human.**

---

## 15. Auditability of Custody

`[VERIFIED]` strong substrate: append-only `audit_log`; immutable `DecisionRecord`/`DecisionSnapshot`/`ArenaRiskGateDecision`; the sequenced, hash-chained `LiveApprovalEvent` ledger (`approver_id`, `approver_role`, `rationale`, `approval_scope`, `expires_at`); masked credentials in read models.

`[RECOMMENDATION]` extend the same discipline to *custody* events so every one is reconstructable — **who accessed what, when, why, and with whose approval**: credential creation/rotation/decryption/revocation, secret rotation, infrastructure access grants, deploys, and repository merges to protected paths. `[CHALLENGE]` today the system audits *authority* (decisions, approvals) far better than it audits *capability* (key access, deploys, merges). Closing that asymmetry is the auditability work this document adds. Custody events are preserved with the same immutability as decision evidence — the record of who could act is as permanent as the record of who did.

---

## 16. Constitutional Principles

1. Custody is never assumed — possession without a recorded grant is a defect, not a state.
2. Capability is always accountable — no physical ability to move capital exists without a named human custodian.
3. **Physical capability must track authorized authority — *can* may never exceed *may*.**
4. Secrets are never permanent — they rotate, expire, and are re-issuable, never precious.
5. **Keys and the data they protect never share a custody boundary.**
6. No credential exists without an owner; no owner exists without accountability.
7. Physical authority is always governed — there is no unmanaged root.
8. Identity survives implementation — authority attaches to canonical identities, not to transient keys or symbols.
9. **The root of trust must survive any single person** — split custody, tested succession, no single point of loss or seizure.
10. Infrastructure serves stewardship; it is never an unowned dependency.
11. Evidence and audit are immutable; the record of who *could* act is as permanent as the record of who *did*.
12. Recovery is tested, not assumed; credentials are replaceable, evidence is not.
13. **The most capable component is the most governed** — the master key and execution credentials get the strictest custody, not the loosest.

---

## 17. Relationship to Existing Architecture

`[CHALLENGE]` This document is unusual: it sits *beneath* all the others physically while they sit *above* it logically. The authority model defines who *should* act; this defines who *can*, and asserts that the whole edifice is trustworthy only where the two coincide.

- **`PROJECT_CONSTITUTION.md`** — supreme; this operationalizes Article VIII (Safety) and Article X (Stewardship) at the physical layer.
- **`AUTHORITY_AND_ACCOUNTABILITY_MODEL.md`** — its necessary complement: authority is meaningful only when capability is governed to match it. Together they close the de jure/de facto gap that document identified.
- **`WORLD_STATE_AND_KNOWLEDGE_MODEL.md`** — custody protects the *integrity* of the evidence that model reasons over.
- **`IMMUTABLE_HISTORICAL_DATASETS.md`** — dataset custody (§10) is where that document's immutability is physically enforced.
- **`SYSTEM_ARCHITECTURE.md`** — the concrete components whose secrets, infrastructure, and repository this governs.
- **Decision Records / Risk Engine / Campaigns / Execution Providers / Historical Intelligence Platform** — each depends on credentials, secrets, and infrastructure whose custody is defined here; the execution boundary and provider trust boundary (authority model §11) are physically rooted in credential custody (§9).

Per the operator's constraint, none of these documents is modified; they rest on this one.

---

## 18. Architectural Critique

The sharpest custody risks and where governance could fail:

1. **Key/ciphertext co-location — the deepest risk.** `[VERIFIED]` `EXCHANGE_CREDENTIALS_ENCRYPTION_KEY` and `DATABASE_URL` in one `.env`: a single environment compromise is total compromise. Highest-priority hardening (§6).
2. **Single, non-rotating symmetric master key.** `[VERIFIED]` one `Fernet(key)`, no rotation/hierarchy — a long-term liability and an incident-response weakness.
3. **The keyholder bypasses the entire constitution.** `[CHALLENGE]` mandates, Risk, and kill switches are enforced *in the application*; a holder of the DB + key + deploy access can act beneath all of them. Governance of physical capability is the only control at that layer.
4. **`ExchangeConnection` has no owner.** `[VERIFIED]` the object holding real-capital keys lacks an accountable custodian field.
5. **API authorization is partially scaffolded.** `[VERIFIED]` `get_current_user`'s full claim verification is "outside this workflow's scope"; the human-identity layer that the accountability chain depends on is not yet fully enforcing — a gap between the accountability model's assumptions and the current auth implementation.
6. **Repository governance is unencoded.** `[VERIFIED]` no `CODEOWNERS`/CI-governance in the snapshot; who may merge/deploy/change constitutional documents rests on external settings and convention, which drift.
7. **Succession is undefined.** `[UNKNOWN]` the root of trust terminates at one or few humans with no tested transfer — the gravest long-term risk for a legacy system (§12).
8. **Second credential surface.** `[VERIFIED]` raw `KRAKEN_API_KEY`/`SECRET` via env bypasses the encrypted-at-rest path — a parallel custody channel to govern.
9. **Implementation drift.** `[CHALLENGE]` this is the meta-risk: a custody model is only real if enforced, and enforcement lives largely *outside* the codebase (env, cloud, repo settings). The document must be paired with an out-of-band, maintained custody roster, or it becomes aspirational.

None of these is a reason for alarm; all are the ordinary hardening a system takes on *before* it holds meaningful real capital. They are surfaced here precisely so they are governed deliberately rather than discovered under duress.

---

## 19. Future Vision

The custody model is what lets trust scale without diluting:

- **Growth / more capital** — the strictest custody attaches to the most capable secrets; scaling capital scales custody rigor, not attack surface.
- **Multiple operators / maintainers** — least capability + separation of duties + a custody roster let more hands participate without any one hand holding the whole root.
- **Future AI systems** — however capable, they never hold custody; capability without accountability is constitutionally forbidden (authority model §7).
- **Additional asset classes** — each new provider/wallet is a new capability admitted only through governed onboarding and, for self-custody, hardware/threshold keys.
- **Family succession / institutional stewardship** — split custody and tested succession let the root of trust pass intact across people and generations, which is the whole point of a legacy engine: the capital and the trust outlive any individual, and both remain accountable.

---

## 20. Final Reflection — is the constitutional architecture complete, and should it now freeze?

### Is it complete?

`[RECOMMENDATION]` **Effectively yes — the constitutional layer is now sufficient.** The five documents form a coherent, closed foundation:

- `PROJECT_CONSTITUTION.md` — *what we value*
- `WORLD_STATE_AND_KNOWLEDGE_MODEL.md` — *what we can know*
- `IMMUTABLE_HISTORICAL_DATASETS.md` — *how knowledge is preserved*
- `AUTHORITY_AND_ACCOUNTABILITY_MODEL.md` — *who may act*
- `CUSTODY_AND_SECURITY_MODEL.md` — *who can actually act*

This custody document deliberately **absorbed** the two gaps its predecessor flagged (Identity & Continuity → §11; Succession → §12), so those are no longer open. Values, epistemics, evidence, authority, and physical capability are now all covered.

**One residual item remains, and it should be a *section*, not a sixth document:** a defined **constitutional amendment process** — how these documents themselves are changed, versioned, and superseded over decades, and who (owner-level, human, non-delegable) may change them. `[CHALLENGE]` This does **not** merit its own constitutional document; it belongs as a short governing section appended to `PROJECT_CONSTITUTION.md`, because it is *about* the constitution rather than a new domain of it. Minting a sixth document for it would be the very over-proliferation the stewardship principle warns against.

### Should OmniTrade freeze the constitutional layer and shift to implementation?

`[RECOMMENDATION]` **Yes — decisively, now.** The justification is not that the documents are perfect but that they are *sufficient*, and that the marginal value of a sixth, seventh, or eighth constitutional document has fallen below the mounting cost of deferring the platform's actual objective.

Consider the honest state: `[VERIFIED]` the governing project documents themselves — `00_PROJECT_STATE.md` ("First Autonomous Profit" as the north star), `02_DECISIONS.md` ("Production Before Expansion," "Runtime Evidence Before Expansion"), and `06_NEXT_SESSION.md` (the immediate task is diagnosing the Risk-Engine BUY rejection) — all say the same thing: **stop expanding, prove the milestone.** We have now produced five substantial constitutional documents and zero lines of implementation, while a running system sits blocked on a rejection whose *candidate causes we have already diagnosed in code* (the unwired `campaign_authorized_notional` and the small-account minimum-order tension). A constitution that never governs a running, proven system governs nothing.

`[CHALLENGE]` There is also a pattern worth naming plainly, in the spirit of the criticism this project asks for: each constitutional document has ended by identifying the next, and that is a loop that can continue indefinitely because there is always a deeper foundation to formalize. The discipline that ends the loop is the same one the platform preaches — *evidence before elaboration*. The constitutional layer has reached the point where further formalization is refinement, not foundation, and refinement is better done against a running system that reveals which principles actually bind.

**Recommended pivot, concretely:**
1. **Freeze the constitutional layer** at these five documents; record the amendment process as a section in the Constitution.
2. **Adopt the four ADRs** (0008–0011) and **ADR-0012** (immutable datasets) as the decision records these documents point to.
3. **Resolve the Section E decisions** from the Phase 1 plan (dataset sourcing, namespace, Golden-Path fixture).
4. **Execute the Golden Historical Path** — the smallest production-isolated slice — which doubles as the deterministic harness to reproduce and finally resolve the BUY-rejection blocker.
5. **Return to constitutional edits only when a running result demonstrates a real, specific gap** — amendment driven by evidence, exactly as Article VII requires.

The goal was never the most complete constitution; it was trustworthy, governed, accountable autonomy that *actually stewards capital*. The architecture is now ready to be proven. The most constitutional act available today is to build.

---

*This document is architecture and governance only. No code was written, the repository was not modified, and no deployment is proposed. It is submitted for adoption as the physical-root-of-trust foundation beneath OmniTrade's constitutional architecture, with the recommendation that the constitutional layer now freeze and primary effort shift to implementing and proving First Autonomous Profit.*
