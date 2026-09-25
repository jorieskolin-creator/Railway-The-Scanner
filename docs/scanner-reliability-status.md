# Scanner Reliability v2 — Implementation Status

## Authority and branch policy

- Canonical implementation branch: `reliability-v2`.
- Baseline: current `main` at 84a3d498b51118200866de01be25b5a67ab8cae1.
- Intended outcome: replace `main` only after all phase exit gates pass.
- The existing `staging` branch is an historical reference for the abandoned two-service API/worker design. It must not be merged into `reliability-v2`.
- Cognitive flow remains unchanged: `Intent → five Juries → Diagnosis → Foresight → Strategic Synthesis → Verification`.

## Current phase status

| Phase | Status | Exit gate |
|---|---|---|
| 0 — Canonicalize and freeze | Complete | One canonical plan, no shadowed trust functions, focused deterministic CI green |
| 1 — Repair and freeze trust contracts | Complete | Full hermetic suite green with no external network/model calls |
| 2 — PostgreSQL orchestration primitives | Complete — lifecycle wired, execution intentionally blocked | Process-kill reclaim and stale-attempt rejection proven |
| 3 — Material durability and checkpoints | In progress — material foundation committed, request path unchanged | Restart at every stage preserves immutable lineage |
| 4 — Typed envelopes and deterministic gates | Not started | Invalid/stale/hash-mismatched inputs block explicitly |
| 5 — Summary and tagging hardening | Not started | Evidence-ID mutations reject publication |
| 6 — Transactional final index | Not started | One current pointer; no duplicate final artifact |
| 7 — Orchestrator decomposition | Not started | Golden checkpoint replay unchanged |

## Operational slice register

Remaining Phase 3 implementation is executed from
`docs/scanner-reliability-slice-backlog.md`. That register does not change
plan ideas, trust boundaries, topology, or phase exit gates. It splits the
stakeholder steps 3G–3J into one-concern cards so a single run cannot wrap
multiple cognitive stages and lose context.

Immediate next product card: **3I-V3 — Verification fenced commit + reclaim**
(specify after the 3I-V2 evidence; do not implement it in the 3G0 change).
Do not register a Supervisor, and do not reopen `POST /scan`. Process card
**3G0** is complete. Product cards **3G1**, **3G2**, **3H**, **3I-J0**,
**3I-J1**, **3I-J2**, **3I-J3**, **3I-D1**, **3I-D2**, **3I-D3**, **3I-F1**,
**3I-F2**, **3I-F3**, **3I-S1**, **3I-S2**, **3I-S3**, **3I-V1**, and
**3I-V2** are complete. The post-3I-D3 `postgres-supervisor-contracts`
NameError hotfix is merged.

## Slice 0A — baseline integrity

This first slice is deliberately behaviour-preserving:

1. establish the canonical v2 plan and status register;
2. remove earlier duplicate top-level trust/sanitizer definitions while retaining the effective final runtime implementations;
3. prevent duplicate top-level definitions from returning;
4. run focused evidence, Foresight, Summary and post-synthesis contract tests in CI.

No database schema, deployment topology, scan-state transition, Summary publication rule, model route or cognitive-stage behaviour changes in this slice.

## Slice 0B — focused trust repair

The first CI run classified three pre-existing failures without weakening any gate:

| Failure | Classification | Resolution |
|---|---|---|
| Seed lifecycle omitted `quarantined` | Stale expectation | Freeze the intentional quarantine counter in the lifecycle contract |
| Foresight sanitation changed text on a second pass | Implementation regression | Make the final company-obligation rewrite modal-aware and version the sanitizer as v4 |
| KB cleanup retained a stale positive URL verdict | Implementation regression | Reverify every stored source URL at the cleanup trust boundary |

Verification evidence:

- Corrective commit: [e787e32](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/e787e32d65983b949c5e777254a057a9f981aacc).
- [Reliability contracts run 33139505262](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33139505262): backend compilation passed, duplicate-definition guard passed, and 127 focused contracts passed with five deprecation warnings.
- Phase 0 is closed. Phase 1 remains in progress until explicit no-network enforcement and full-suite failure classification are complete.


## Slice 1A — hermetic trust-contract exit

Phase 1 is closed without changing the Scanner's cognitive flow, Railway topology,
or the historical `staging` branch.

The full suite exposed and repaired the following production-boundary defects:

- Coresignal search fallbacks now preserve provider record IDs for downstream
  stakeholder lookup.
- Claim-cleaner accounting includes `unsupported_for_this_claim`.
- Foresight eligibility rejects any dead/unreadable/excluded/rejected audit
  state, even when a separate signal status is eligible.
- Official-domain acquisition packets explicitly begin unverified, and recovered
  Firecrawl passages are applied only when they match the provider's quoted
  passage.
- Jury merging preserves verification obtained from the current matched source
  passage; stale pre-jury flags cannot overwrite it.
- Overstrong Jury claims are preserved only as a narrower, source-derived fact
  or bounded context—never as the original unsupported claim.

Test isolation now blocks socket and DNS access for the entire session. The
Company Summary/Buying Potential payload test injects its tactics dependency
locally, so its Coresignal contract no longer depends on remote knowledge
artifacts.

Verification evidence:

- Corrective commits: [b7e1ec2](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/b7e1ec2dbb5bbbba868d3581b665ed0bec3886d5), [959045a](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/959045a7627649412bfd4229866f32faefc58167), [994ac8d](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/994ac8dfa24553fee4e52588b305c4e74c1d65ab), and [0192f1b](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/0192f1bda80e3f8fdbdff2f387c7c286c6d83535).
- [Reliability contracts run 33140984217](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33140984217): compilation, duplicate-definition guard, 127 focused trust contracts, and the complete hermetic suite passed (`357 passed`, 59 deprecation warnings).


## Slice 2A — execution-state foundation

This slice adds no new Railway service and does not activate the Supervisor or
alter the existing `/scan` request path. It provides the PostgreSQL foundation
that later slices will use after material durability is in place:

- `001_scan_execution_state.sql` defines `scan_runs`, `scan_steps`, and
  append-only `scan_events` only; operational state remains separate from
  evidence and Foresight records.
- `scan_supervisor.py` is opt-in. It rejects enabled-without-DSN
  configuration, claims one lease at a time, increments a fencing token at
  every claim/reclaim, and conditions heartbeat/complete/fail writes on the
  current owner, token, and unexpired lease.
- A partial unique index permits only one `running` run; no in-memory
  dictionary becomes the source of truth for the future Supervisor.
- Repository contract tests cover disabled configuration, database requirement,
  advisory-lock claim semantics, lease expiry, fencing, and stale checkpoint
  rejection.

Still deliberately out of scope: applying the migration to Railway, FastAPI
startup/shutdown ownership, process-kill integration against a disposable
PostgreSQL database, material persistence, checkpoints, and changing
`POST /scan`. Those are required before Phase 2 can close.

Verification evidence:

- Foundation commit: [7f130ad](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/7f130ad71f4871e51021301845738bf88e73e051).
- [Reliability contracts run 33141291533](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33141291533): compilation and duplicate-definition checks passed; 127 focused contracts and 363 hermetic tests passed (61 deprecation warnings).


## Slice 2B — disposable PostgreSQL lease-reclaim proof

A dedicated GitHub Actions job now starts PostgreSQL 16 only for the Supervisor
integration contract. It applies the same migration used by the repository, then
proves all of the following against the database itself:

1. the first process claims a step with fencing token 1;
2. another process cannot claim it while that lease is live;
3. after expiry, a replacement process reclaims the same step with token 2;
4. the old owner/token cannot commit its checkpoint;
5. only the reclaimed attempt completes the run and its checkpoint; and
6. the event stream records both claims and the final completion.

The primary hermetic suite still denies all sockets and DNS. The only exception
is an explicitly configured test-only `127.0.0.1:5432` PostgreSQL endpoint in
the separate CI job; no hostname, non-loopback address, alternate port, or
production DSN is permitted.

Phase 2's database exit gate is now met.  It does not authorize production
execution: the migration must be deliberately applied and verified before the
schema-ready setting is armed, and Phase 3 must persist material/checkpoints
before an executor can claim queued work.

Verification evidence:

- Lease-reclaim commit: [6c1f92c](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/6c1f92ca175af56cc231d88544f2afcc65a6077a).
- [Reliability contracts run 33141593538](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33141593538): 127 focused contracts passed; 363 hermetic tests passed with one database integration test skipped; the disposable PostgreSQL job passed its lease-reclaim contract.

## Change rules

- One bounded architectural concern per commit or pull request.
- Shared-module changes require regression coverage for Company Summary, Foresight harvesting/cleanup, Observatory and Market Intelligence boundaries.
- No phase is marked complete from code presence alone; its documented exit gate must pass.
- No gate may be weakened to obtain a green suite.
- Deployment success is not test evidence.


## Slice 2C — disabled FastAPI lifecycle boundary

The existing FastAPI process now owns Supervisor lifecycle registration without
starting any new service, thread, or queued execution path:

- application startup reads opt-in Supervisor settings and records a precise
  lifecycle state; shutdown has no worker to drain in this phase;
- the default is `SCAN_SUPERVISOR_ENABLED=false`, which performs no database
  I/O and leaves the existing `POST /scan` implementation untouched;
- an enabled Supervisor remains blocked until
  `SCAN_SUPERVISOR_SCHEMA_READY=true`, which is a deliberate post-migration
  release assertion—not an automatic migration attempt;
- even with a schema-ready setting, Phase 2 registers no stage executor and
  never claims work.  A supplied executor is still blocked until Phase 3 adds
  durable material/checkpoint commits;
- `StageExecutor` and `StageExecutionResult` define the narrow future
  boundary: an existing cognitive stage can later return only checkpoint
  metadata, while Phase 3 persists and verifies the referenced immutable
  material.

This keeps the plan's single-Railway-service topology and the cognitive flow
unchanged.  In particular, Company Summary generation, Market Intelligence,
and Foresight harvesting remain on their current paths until the preceding
durability contracts exist.

Verification evidence:

- Lifecycle commit: [033e78a](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/033e78a77366e701819982b97c5b79b2038b7219).
- [Reliability contracts run 33142124310](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33142124310): compilation and duplicate-definition checks passed; 127 focused contracts and the full hermetic suite passed (`368 passed, 1 skipped`); the disposable PostgreSQL lease-reclaim job passed (`1 passed`).


## Slice 3A — durable material foundation

This slice creates the persistence contract for submitted scan input without
handing the live request path to PostgreSQL prematurely:

- `002_scan_materials.sql` adds an input-material table linked to a future
  `scan_runs` row.  It stores a stable material ID, origin, filename/MIME,
  byte size, raw-content SHA-256, extracted-text SHA-256, parser version,
  extraction status/warnings, allowed-use classification, and extracted text.
  It does **not** retain raw file bytes.
- Material preparation is deterministic and model-free.  A material is
  explicitly only company-provided input context; it is not Company Evidence,
  cannot satisfy a claim, and has no Foresight or publication role.
- Repository persistence is transactional and immutable: replaying the same
  material ID succeeds only when all persisted identity/hash/status fields
  agree; a conflicting reuse is rejected.
- The repository currently requires an existing run, but `POST /scan` is
  deliberately unchanged.  This prevents a partial handoff in which a
  database run is queued while the legacy in-memory thread also executes it.

Verification evidence:

- Material-foundation commit: [5d9c719](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/5d9c719a2b7e2b8190d5bfc2c21df465e7aae6f8).
- Corrective test-only commit: [5f9dbe2](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/5f9dbe2e032625ea21a5820df0787ed8df2b02c8).
- [Reliability contracts run 33142848655](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33142848655): 127 focused contracts and the full hermetic suite passed (`372 passed, 2 skipped`); two disposable PostgreSQL durability contracts passed.

The next slice is a transactional ingress adapter that creates a run and its
prepared material together.  It must remain inactive until it can replace,
rather than parallel, the legacy `/scan` thread handoff.  Only then can
material ingestion become the first resumable stage.


## Slice 3B — transactional inactive ingress

The Scanner now has a deterministic ingress contract ready for the later
`POST /scan` handoff:

- an explicit client idempotency key deterministically derives the run ID and
  the material IDs, so a retry can address the exact same durable input;
- the request hash covers the Scanner-relevant submitted input and material
  metadata, but is intentionally independent of the idempotency key;
- one PostgreSQL transaction creates the queued run, initial
  `material_ingestion` step, event, and immutable material rows;
- replaying the same request is idempotent; changing input under the same
  key is rejected; and a database-level material failure rolls the newly
  inserted run and step back completely;
- this ingress remains inactive.  It never claims a lease or starts a worker,
  and the legacy in-memory `/scan` thread is still the only live path.

This is the required precondition for switching the request path later, but
not permission to switch it yet.  The next Phase 3 slice will define durable
material retrieval and an immutable first-stage checkpoint contract; only
after that can an execution adapter replace the current handoff safely.

Verification evidence:

- Ingress commit: [69362b2](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/69362b28587379a8fec27687d55664902366a3e2).
- Database-rollback contract strengthening: [df86bd0](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/df86bd06e7233f2310f44e63697f04f8cb58ac81).
- [Reliability contracts run 33143601885](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33143601885): 127 focused contracts and the full hermetic suite passed (`376 passed, 5 skipped`); all five disposable PostgreSQL durability contracts passed.


## Slice 3C — verified material retrieval and first checkpoint

The first resumable boundary is now represented in PostgreSQL without changing
the live Scanner pipeline:

- `load_materials` reconstructs persisted material only after validating every
  extracted-text SHA-256.  A mismatch is a `MaterialIntegrityError`; it cannot
  become a best-effort input to a later stage.
- `003_scan_checkpoints.sql` stores immutable stage checkpoints containing only
  versioned material references, hashes, and gate results.  It stores neither
  raw bytes, extracted text, Company Evidence, nor Foresight content.
- Material ingestion creates a deterministic manifest checkpoint.  It passes
  only when all submitted material was extracted or deliberately empty;
  extraction failure yields an explicit blocked gate.
- A passed checkpoint is inserted and the fenced step is completed in one
  transaction.  A stale process loses both: its checkpoint insert rolls back,
  so it cannot leave durable output after a replacement lease exists.
- The success-path contract queues a stable `intent` stage name for the future
  adapter, but does not call the existing Intent function yet.

The endpoint and Supervisor are still inactive relative to this path.  The next
slice can introduce a non-registered material-ingestion stage adapter that
loads, validates, checkpoints, and queues `intent`; it must remain unable to
run until the authoritative request handoff replaces the legacy thread.

Verification evidence:

- Checkpoint contract: [8c38772](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/8c38772f679547627a18d2171f13261b5f212e9a).
- Boundary assertion correction: [12a3451](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/12a34513bc30794c1883319969099110b0f73844).
- [Reliability contracts run 33144362428](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33144362428): 127 focused contracts and the full hermetic suite passed (`378 passed, 8 skipped`); eight disposable PostgreSQL durability contracts passed.


## Slice 3D — prepare-only material-ingestion adapter

The first stage now has an adapter, while execution remains intentionally
inactive:

- `MaterialIngestionStageExecutor` reads only hash-verified durable material and
  builds the deterministic material-ingestion checkpoint.
- It returns a `StageExecutionResult` carrying that exact checkpoint and
  queues `intent` only as metadata when the material gate has passed.
- It does not write a checkpoint, complete a step, claim a lease, invoke an
  existing cognitive function, start a thread, or register with FastAPI.
  A blocked material result therefore cannot accidentally advance the flow.
- Its PostgreSQL contract proves that preparation reads the leased run but
  leaves the run at `material_ingestion`, the step leased, and the checkpoint
  table empty until a separate fenced commit explicitly accepts the result.

This isolates stage preparation from stage transition, which is necessary for
a crash-safe Supervisor.  The next dependency is durable source retrieval:
the existing Intent stage can fall back to web acquisition when submitted
material is absent, so that acquisition must become resumable before Intent
can be wrapped without changing its logic.

Verification evidence:

- Prepare-only adapter commit: [a30c73e](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/a30c73e88a9a212c24a66cfc2c675ab6c329a297).
- [Reliability contracts run 33147861608](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33147861608): 127 focused contracts and the full hermetic suite passed (`381 passed, 9 skipped`); nine disposable PostgreSQL durability contracts passed.


## Slice 3E — durable source-retrieval foundation

Source acquisition now has its own immutable, raw-body-free persistence boundary
before any existing Intent logic is wrapped:

- `004_source_retrievals.sql` adds provenance records keyed to the leased stage
  attempt: original and transport-canonical URL, retrieval time, HTTP/content
  metadata, response and extracted-text SHA-256, parser route, readability,
  outcome, warnings, and extracted text.  Raw response bytes are used only to
  calculate the response hash and are never stored.
- URL canonicalization is deliberately transport-only.  It does not establish
  publisher trust, entity identity, source eligibility, claim support, or
  equivalence between sources.
- Reload validates the extracted-text hash, and conflicting reuse of a retrieval
  ID is rejected.  The PostgreSQL contract proves replay idempotency and rejects
  tampered persisted text.
- This is retrieval provenance, not Company Evidence, a Foresight KB record, or
  a publication artifact.  It does not make a source usable for a company claim.
- No live request, retrieval client, Intent adapter, Supervisor registration,
  lease claim, or Railway setting changed in this slice.

The next safe dependency is a durable Intent input snapshot assembled only from
verified submitted material and retrieval records, followed by a prepare-only
Intent adapter.  That remains separate from any authoritative `POST /scan`
handoff, which cannot occur while the legacy in-memory thread can also execute
the same request.

Verification evidence:

- Source-retrieval foundation: [f1294095](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/f1294095309558df426c40d3649c8354f0eaf2e0).
- [Reliability contracts run 33149329773](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33149329773): compilation and duplicate-definition guard passed; 127 focused contracts, the full hermetic suite (`383 passed, 11 skipped`), and 11 disposable PostgreSQL durability contracts passed.


## Slice 3F — immutable Intent input snapshot

The next cognitive stage can now be bound to exact durable input before an Intent
adapter is allowed to use it:

- `005_intent_input_snapshots.sql` stores one immutable manifest per
  `run / intent / attempt`. It contains only material and source-retrieval
  references, hashes, parser/status metadata, and retrieval provenance.
  Extracted text and raw response bytes remain in their respective durable
  stores and are not copied into the manifest.
- The manifest has a deterministic ID and input hash. Replays are idempotent;
  a different manifest for the same attempt is rejected.
- Hydration reloads submitted material and Intent-attempt retrievals through
  their hash-verifying repository paths, then requires an exact manifest match.
  Added, omitted, stale, or tampered input cannot become a best-effort payload
  for the future adapter.
- The manifest is operational lineage only. It does not grant source trust,
  entity identity, evidence eligibility, claim support, Foresight meaning, or
  Company Summary/publication authority.
- No live acquisition client, existing Intent function, route handoff,
  Supervisor loop, lease claim, checkpoint completion, or Railway setting
  changed in this slice.

The next bounded dependency was split in
`docs/scanner-reliability-slice-backlog.md` so the adapter cannot invent its
output contract: a versioned Intent projection and checkpoint builder first,
then a prepare-only adapter that uses that builder.

Verification evidence:

- Snapshot foundation: [18e8f11e](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/18e8f11e516b235279304f8df47c8df81e086599); contract corrections: [14928c67](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/14928c674d8b845b8c6dac293830ac37e3b1e8b7), [d0da5eba](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/d0da5ebaedd514345e8f52f3a72e5ac6baff63c5), [fe9ed988](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/fe9ed988fec192378f1dd99cf033c0c1ca332c06), and [ebffbce5](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/ebffbce5712e4b4bfb7d9d35a57eaa1cdadb6cb7).
- [Reliability contracts run 33153940507](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/33153940507): compilation and duplicate-definition guard passed; 127 focused contracts, the full hermetic suite (`385 passed, 13 skipped`), and 13 disposable PostgreSQL durability contracts passed.


## Slice 3G1 — Intent input projection and output checkpoint contract

The first cognitive stage now has a versioned prepare-time contract without
being invoked or committed:

- `build_intent_input_projection` accepts only a verified Intent input snapshot
  plus the already-hydrated material and retrieval lists. Added, omitted, or
  tampered input raises an integrity error and cannot become a best-effort
  payload.
- The projection concatenates hash-verified extracted text in stable
  `material_id` then `retrieval_id` order. That string is the only payload a
  later adapter may pass to existing Intent scoring.
- `build_intent_checkpoint` maps that projection and a caller-supplied Intent
  result into a `scanner.intent/v1` checkpoint. The snapshot uses envelope
  fields (`contract`, `run_id`, `stage`, `attempt`, `input_refs`, `output_refs`,
  `gate_results`) plus Intent payload hashes. Extracted text and raw bytes are
  not copied into the checkpoint.
- A well-shaped result (`score`, `breakdown`, `metrics`) yields `passed`. A
  malformed result yields `validation_failed` and is not stored as output. Empty
  concatenated text is allowed; this slice does not add a shortness gate.
- The builders do not call `analyze_intent`, acquire content, write PostgreSQL,
  register a Supervisor executor, queue `jury_analysis`, or change `POST /scan`.

The next bounded dependency is **3G2 — prepare-only Intent adapter**. It must
read `load_verified_intent_inputs`, use these builders, invoke existing
`analyze_intent` on the projection text, and return a checkpoint proposal
without committing it.

Verification evidence:

- Projection/checkpoint contract: [53baf9a](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/53baf9a938951bfc4a0f8182bda5d34d8b10c140).
- Duplicate-definition guard passed; 127 focused trust contracts passed; full hermetic suite `391 passed, 7 skipped` (six new 3G1 contracts; PostgreSQL durability jobs were not required for this slice).


## Slice 3G2 — prepare-only Intent adapter

Intent now has a prepare-only adapter while execution remains inactive:

- `IntentStageExecutor` reads only `load_verified_intent_inputs`, builds the 3G1
  projection, and calls existing `analyze_intent` on that concatenated text.
- It returns a `StageExecutionResult` carrying the 3G1 checkpoint proposal.
  `next_stage` may name `jury_analysis` when the gate passed, but only as
  metadata. Nothing is queued or committed.
- The adapter has no claim, persist, or `complete_step` method. It does not
  scrape, import `main.py`, register with FastAPI, or change `POST /scan`.
- Its PostgreSQL contract proves preparation can read a leased Intent step and
  leave that step leased, with no Intent checkpoint row and no `jury_analysis`
  step.

The next bounded dependency was **3H — Intent fenced commit and reclaim**.

Verification evidence:

- Prepare-only adapter: [c79090f](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/c79090f222876757f1817cff037b651af70ddc3f).
- Duplicate-definition guard passed; 127 focused trust contracts passed; full hermetic suite `394 passed, 8 skipped` (three new 3G2 hermetic contracts; the disposable PostgreSQL adapter contract is wired into Reliability contracts and skipped without the test DSN).


## Slice 3H — Intent fenced commit and reclaim

Intent is now the first cognitive stage with a crash-safe durable boundary,
while live execution remains inactive:

- `commit_intent_stage` persists a passed 3G2 proposal with existing
  `complete_step_with_checkpoint` and queues only `jury_analysis`.
- A stale fencing token rolls the Intent checkpoint insert back; the
  reclaiming owner can commit. Identical replay of the same passed checkpoint
  is idempotent and does not insert a second row or a second completion event.
- A `validation_failed` or otherwise non-passed proposal cannot complete the
  step or queue Juries.
- Restart reload through `load_verified_intent_inputs` reproduces the same
  input and output hashes. Tampered persisted text cannot hydrate a new
  payload. The Intent gate remains `passed` and `jury_analysis` stays pending.
- This slice does not register a Supervisor loop, switch `POST /scan`, apply a
  Railway migration, or wrap any later cognitive stage.

Phase 3 is **not** closed. The next bounded dependency is the first Jury wrap
beat: durable Jury-stage input/retrieval lineage, specified after this evidence
and implemented as its own card. Do not start Diagnosis, Foresight, Synthesis,
Verification, or request handoff from this slice.

Verification evidence:

- Fenced Intent commit: [5a77c7f](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/5a77c7fc27787e884a03c0154dfa91ea9eb57c6b).
- Duplicate-definition guard passed; 127 focused trust contracts passed; six new hermetic 3H contracts passed; five disposable PostgreSQL 3H durability contracts passed (happy-path queue, stale reclaim, identical replay, malformed rejection, restart hash/gate). Full suite with the test DSN: `419 passed`.


## Slice 3I-J0 — Jury-stage retrieval and candidate durability

The pending `jury_analysis` step now has an immutable provenance store before
any five-Jury function is wrapped:

- `006_jury_source_candidates.sql` stores discovery candidates keyed to one
  `jury_analysis` attempt: origin, original and transport-canonical URL,
  optional cached extract text and SHA-256, parser route, warnings, and
  discovery-only metadata. Raw response bytes are never stored.
- Origins are `company_evidence_db`, `previous_scan_source_cache`, or
  `jury_live_acquisition_seed`. Metadata cannot carry claim support, entity
  match, Foresight eligibility, or publication authority.
- Reload verifies the cached-extract hash. Identical replay is idempotent; a
  different payload under the same candidate ID is rejected.
- Existing `source_retrievals` may be keyed to that pending `jury_analysis`
  step for a real Jury-stage fetch. Intent-stage retrievals remain a separate
  set. Neither record is Company Evidence.
- Candidates and Jury-stage retrievals attach without claiming the
  `jury_analysis` lease. This slice does not call five-Jury functions, scrape,
  build a Jury snapshot/adapter/commit, register a Supervisor, or switch
  `POST /scan`.

Phase 3 is **not** closed. The next bounded dependency is **3I-J1 — Jury
input snapshot from Intent checkpoint**. Do not start Diagnosis, Foresight,
Synthesis, Verification, or request handoff from this slice.

Verification evidence:

- Jury-stage candidate durability: [6e8bfc9](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/6e8bfc9197e859a813dc89bfdd500e5300c7ffa8).
- Duplicate-definition guard passed; 127 focused trust contracts passed; four new hermetic 3I-J0 contracts passed; three disposable PostgreSQL 3I-J0 durability contracts passed (pending-step attach without claim, conflicting ID rejection, tampered extract rejection). Full suite with the test DSN: `426 passed`.


## Slice 3I-J1 — Jury input snapshot from Intent checkpoint

The pending `jury_analysis` step can now be bound to exact durable input
before a Jury adapter is allowed to use it:

- `007_jury_input_snapshots.sql` stores one immutable manifest per
  `run / jury_analysis / attempt`. It contains the passed Intent checkpoint
  identity (id, ref, attempt, contract, input/output hashes) plus Jury-stage
  candidate and retrieval references. Extracted text, raw response bytes, and
  the Intent result payload are not copied into the manifest.
- The manifest has a deterministic ID and input hash. Replays are idempotent;
  a different manifest for the same attempt is rejected.
- Hydration reloads the Intent checkpoint, Jury candidates, and Jury-stage
  retrievals through their hash-verifying repository paths, then requires an
  exact manifest match. Added, omitted, stale, or tampered input cannot become
  a best-effort payload for a later adapter. Intent-stage retrievals stay
  behind the Intent checkpoint.
- The snapshot attaches without claiming the `jury_analysis` lease. This slice
  does not call five-Jury functions, scrape, build a Jury adapter or commit,
  register a Supervisor, or switch `POST /scan`.
- The manifest is operational lineage only. It does not grant source trust,
  entity identity, evidence eligibility, claim support, Foresight meaning, or
  Company Summary/publication authority.

Phase 3 is **not** closed. The next bounded dependency was **3I-J2 — prepare-only
Jury adapter**. Do not start Diagnosis, Foresight, Synthesis, Verification, or
request handoff from this slice.

Verification evidence:

- Jury input snapshot: [f68f255](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/f68f2553e58b060ddb25f7b9d4cd5e0ca4ed24b9).
- Duplicate-definition guard passed; 127 focused trust contracts passed; three new hermetic 3I-J1 contracts passed; two disposable PostgreSQL 3I-J1 durability contracts passed (pending-step attach without claim plus exact hydrate; tampered manifest and extra candidate rejected). Full suite with the test DSN: `431 passed`.


## Slice 3I-J2 — prepare-only Jury adapter

Jury now has a prepare-only adapter while execution remains inactive:

- `JuryStageExecutor` reads only `load_verified_jury_inputs`, builds a
  `scanner.jury-projection/v1` packet view from the 3I-J1 snapshot, and wraps
  existing `scanner_two._prioritize_jury_packets` and `_merge_jury_payloads`.
  It does not call `x_ray_search`, scrape, or hit a model.
- It returns a `StageExecutionResult` carrying a `scanner.jury/v1` checkpoint
  proposal. `next_stage` may name `diagnosis` when the gate passed, but only as
  metadata. Nothing is queued or committed. Malformed five-Jury output yields
  `validation_failed`.
- Packet excerpts exist only on the projection. The checkpoint stores refs and
  a compact result; it does not copy extracts, raw bytes, or the Intent payload.
- The adapter has no claim, persist, or `complete_step` method. It does not
  import `main.py`, register with FastAPI, or change `POST /scan`.
- Its PostgreSQL contract proves preparation can read a leased `jury_analysis`
  step and leave that step leased, with no Jury checkpoint row and no
  `diagnosis` step.

Phase 3 is **not** closed. The next bounded dependency was **3I-J3 — Jury
fenced commit + reclaim**. Do not start Diagnosis, Foresight, Synthesis,
Verification, or request handoff from this slice.

Verification evidence:

- Prepare-only Jury adapter: [f3662ba](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/f3662ba4a6cdd56718caab7f4f0317f96c203ee8).
- Duplicate-definition guard passed; 127 focused trust contracts passed; eleven new hermetic 3I-J2 contracts passed; one disposable PostgreSQL 3I-J2 contract passed (leased `jury_analysis` unchanged, no Jury checkpoint row, no `diagnosis` step). Full suite with the test DSN: `443 passed`.


## Slice 3I-J3 — Jury fenced commit and reclaim

Jury is now the second cognitive stage with a crash-safe durable boundary,
while live execution remains inactive:

- `commit_jury_stage` persists a passed 3I-J2 proposal with existing
  `complete_step_with_checkpoint` and queues only `diagnosis`.
- A stale fencing token rolls the Jury checkpoint insert back; the reclaiming
  owner can commit. Identical replay of the same passed checkpoint is
  idempotent and does not insert a second row or a second completion event.
- A `validation_failed` or otherwise non-passed proposal cannot complete the
  step or queue Diagnosis.
- Restart reload through `load_verified_jury_inputs` reproduces the same
  input and output hashes. Tampered persisted Jury extract cannot hydrate a
  new payload. The Jury gate remains `passed` and `diagnosis` stays pending.
- This slice does not register a Supervisor loop, switch `POST /scan`, apply a
  Railway migration, or wrap Diagnosis, Foresight, Synthesis, or Verification.

Phase 3 is **not** closed. The next bounded dependency was **3I-D1 — Diagnosis
input snapshot**. Do not start Diagnosis wrapping, Foresight, Synthesis,
Verification, or request handoff from this slice.

Verification evidence:

- Fenced Jury commit: [d157b2b](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/d157b2b742df0146f1d21ec5d06b22095a36625d).
- Duplicate-definition guard passed; 127 focused trust contracts passed; six new hermetic 3I-J3 contracts passed; five disposable PostgreSQL 3I-J3 durability contracts passed (happy-path queue, stale reclaim, identical replay, malformed rejection, restart hash/gate). Full suite with the test DSN: `454 passed`.


## Slice 3I-D1 — Diagnosis input snapshot from Jury checkpoint

The pending `diagnosis` step can now be bound to exact durable Jury output
before a Diagnosis adapter is allowed to use it:

- `008_diagnosis_input_snapshots.sql` stores one immutable manifest per
  `run / diagnosis / attempt`. It contains only the passed Jury checkpoint
  identity (id, ref, attempt, contract, input/output hashes). Extracted text,
  raw bytes, Jury findings, and Intent payloads are not copied into the
  manifest.
- The manifest has a deterministic ID and input hash. Replays are idempotent;
  a different manifest for the same attempt is rejected.
- Hydration reloads the Jury checkpoint through its hash-verifying repository
  path and requires an exact manifest match. A non-passed, wrong-run, Intent,
  or tampered Jury checkpoint cannot become a best-effort Diagnosis payload.
- The snapshot attaches without claiming the `diagnosis` lease. This slice
  does not call Diagnosis generation, scrape, build a Diagnosis adapter or
  commit, register a Supervisor, or switch `POST /scan`.
- Diagnosis has no separate retrieval/candidate store. It does not acquire
  sources; the passed Jury checkpoint is the durable input.

Phase 3 is **not** closed. The next bounded dependency was **3I-D2 — prepare-only
Diagnosis adapter**. Do not start Foresight, Synthesis, Verification, or
request handoff from this slice.

Verification evidence:

- Diagnosis input snapshot: [89b7e3c](https://github.com/jorieskolin-creator/Railway-The-Scanner/commit/89b7e3c).
- Duplicate-definition guard passed; 127 focused trust contracts passed; four new hermetic 3I-D1 contracts passed. Two disposable PostgreSQL 3I-D1 durability contracts are added to CI (pending-step attach without claim plus exact hydrate; tampered manifest and swapped Jury hash rejected). Full hermetic suite without the test DSN: `428 passed, 14 skipped`.


## Slice 3I-D2 — prepare-only Diagnosis adapter

Diagnosis now has a prepare-only adapter while execution remains inactive:

- `DiagnosisStageExecutor` reads only `load_verified_diagnosis_inputs`, builds a
  `scanner.diagnosis-projection/v1` view from the 3I-D1 snapshot, and wraps the
  compact Jury result into a Diagnosis proposal without calling a model.
- It returns a `StageExecutionResult` carrying a `scanner.diagnosis/v1`
  checkpoint proposal. `next_stage` may name `foresight` when the gate passed,
  but only as metadata. Nothing is queued or committed. Malformed Diagnosis
  output yields `validation_failed`.
- Compact Jury findings exist only on the projection. The checkpoint stores
  refs and a compact Diagnosis result (`diagnostic_summary`,
  `evidence_based_risks`, `reality_score`, `archetype`); it does not copy Jury
  quotes, extracts, raw bytes, or Intent payload.
- The adapter has no claim, persist, or `complete_step` method. It does not
  import `main.py`, register with FastAPI, or change `POST /scan`.
- Its PostgreSQL contract proves preparation can read a leased `diagnosis`
  step and leave that step leased, with no Diagnosis checkpoint row and no
  `foresight` step.

Phase 3 is **not** closed. The next bounded dependency was **3I-D3 — Diagnosis
fenced commit + reclaim**. Do not start Foresight wrapping, Synthesis,
Verification, or request handoff from that slice.

Verification evidence:

- Prepare-only Diagnosis adapter tests: ten new hermetic 3I-D2 contracts passed
  (`tests/test_scan_diagnosis_checkpoint.py`,
  `tests/test_scan_diagnosis_adapter.py`). One disposable PostgreSQL 3I-D2
  contract is added to CI (leased `diagnosis` unchanged, no Diagnosis
  checkpoint row, no `foresight` step). Duplicate-definition guard passed; 127
  focused trust contracts passed. Full hermetic suite without the test DSN:
  `438 passed, 15 skipped`.


## Slice 3I-D3 — Diagnosis fenced commit + reclaim

The prepare-only Diagnosis proposal can now be committed under the current
lease owner and fencing token:

- `commit_diagnosis_stage` persists a passed 3I-D2 checkpoint with existing
  `complete_step_with_checkpoint` and queues only `foresight`.
- Identical replay of the same passed checkpoint is idempotent. A different
  checkpoint for the same attempt is rejected. `validation_failed` proposals
  cannot complete the step or queue Foresight.
- A stale fencing token cannot insert the Diagnosis checkpoint. After lease
  expiry a replacement owner can reclaim the `diagnosis` step, prepare again,
  and commit. Restart reload keeps Diagnosis hashes and the passed gate;
  tampered Jury output cannot hydrate.
- This slice does not register a Supervisor loop, switch `POST /scan`, apply a
  Railway migration, or wrap Foresight, Synthesis, or Verification.

Phase 3 is **not** closed. The next bounded dependency is **3I-F1 — Foresight
input snapshot**. Do not start Foresight wrapping, Synthesis, Verification, or
request handoff from this slice.

Verification evidence:

- Duplicate-definition guard passed; 127 focused trust contracts passed; six
  new hermetic 3I-D3 contracts passed. Five disposable PostgreSQL 3I-D3
  durability contracts are added to CI (happy-path queue, stale reclaim,
  identical replay, malformed rejection, restart hash/gate). Full hermetic
  suite without the test DSN: `444 passed, 16 skipped`.


## CI hotfix — restore `_source_metadata` on `reliability-v2`

This is not a product slice and does not change cognitive flow, `/scan`,
Supervisor enablement, or Railway settings.

After 3I-D1 (`89b7e3c`), `_diagnosis_manifest` replaced the
`_source_metadata` definition while leaving that helper's body as unreachable
code after `return`. `load_source_retrievals` still called `_source_metadata`,
so disposable PostgreSQL contracts failed with `NameError` on every
`reliability-v2` push from 3I-D1 through 3I-D3. Hermetic tests did not hit
that load path, which is why focused-contracts stayed green and Railway
Wait-for-CI skipped.

This hotfix restores `_source_metadata` as its own function and adds two
hermetic repository contracts so focused CI fails if the helper disappears
again. It does not wrap Foresight or start 3I-F1.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Full hermetic suite without claiming GitHub CI yet: `446 passed, 16 skipped`
  (two new `_source_metadata` contracts).
- Local replay of `postgres-supervisor-contracts` (same file list as
  `.github/workflows/reliability-contracts.yml`): `38 passed`. That job was
  `28 failed, 10 passed` on `reliability-v2` HEAD `1e64fca`.


## Slice 3I-F1 — Foresight input snapshot from Diagnosis checkpoint

Foresight can now be bound to exact Diagnosis output before an adapter exists:

- `build_foresight_input_snapshot` stores only the passed Diagnosis checkpoint
  identity (id, ref, attempt, contract, input/output hashes).
- Persist/load attach to the pending `foresight` step queued by 3I-D3 without
  claiming the lease.
- Hydration reloads the Diagnosis checkpoint through the hash-verifying path
  and rejects a non-passed, wrong-run, or tampered Diagnosis checkpoint.
- The manifest does not copy Diagnosis result payload, Jury findings, extracted
  text, or Foresight KB content. This slice does not wrap Foresight generation,
  persist `kb_versions`, switch `POST /scan`, or register a Supervisor loop.

Phase 3 is **not** closed. The next bounded dependency is **3I-F2 — Prepare-only
Foresight adapter**. Do not start Foresight wrapping, Synthesis, Verification,
or request handoff from this slice.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Four new hermetic 3I-F1 contracts passed
  (`tests/test_scan_foresight_snapshot.py`). Full hermetic suite without the
  test DSN: `450 passed, 40 skipped`.
- Two disposable PostgreSQL 3I-F1 contracts passed (persist without claiming,
  exact hydrate, tampered manifest and swapped Diagnosis hash rejected). Local
  replay of `postgres-supervisor-contracts` including the new file: `40 passed`.


## Slice 3I-F2 — Prepare-only Foresight adapter

Foresight can now be prepared from the verified Diagnosis checkpoint without
moving the state machine:

- `build_foresight_input_projection` binds the 3I-F1 snapshot to the compact
  Diagnosis result. `build_foresight_checkpoint` stores refs and a compact
  Foresight result only (`dimension_forces`, `cross_impact_chain`,
  `scenarios`, `climate_exposure`).
- `ForesightStageExecutor` hydrates only `load_verified_foresight_inputs` and
  wraps Diagnosis output into an empty valid Foresight payload. It does not
  call `generate_foresight`, the harvester, or models.
- `next_stage` may name `strategic_synthesis` when the gate passed, but only as
  proposal metadata. The adapter has no persist, claim, or complete method.
- Malformed Foresight output yields `validation_failed` and cannot name
  Synthesis. Foresight KB records, Diagnosis payload text, and extracted text
  are not stored on the checkpoint.

Phase 3 is **not** closed. The next bounded dependency is **3I-F3 — Foresight
fenced commit + reclaim**. Do not start Strategic Synthesis, Verification, or
request handoff from this slice.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Ten new hermetic 3I-F2 contracts passed
  (`tests/test_scan_foresight_checkpoint.py`,
  `tests/test_scan_foresight_adapter.py`). Full hermetic suite without the
  test DSN: `460 passed, 41 skipped`.
- One disposable PostgreSQL 3I-F2 contract passed (leased `foresight`
  unchanged, no Foresight checkpoint row, no `strategic_synthesis` step).
  Local replay of `postgres-supervisor-contracts` including the new file:
  `41 passed`.


## Slice 3I-F3 — Foresight fenced commit + reclaim

The prepare-only Foresight proposal can now be committed under the current
lease owner and fencing token:

- `commit_foresight_stage` persists a passed 3I-F2 checkpoint with existing
  `complete_step_with_checkpoint` and queues only `strategic_synthesis`.
- Identical replay of the same passed checkpoint is idempotent. A different
  checkpoint for the same attempt is rejected. `validation_failed` proposals
  cannot complete the step or queue Strategic Synthesis.
- A stale fencing token cannot insert the Foresight checkpoint. After lease
  expiry a replacement owner can reclaim the `foresight` step, prepare again,
  and commit. Restart reload keeps Foresight hashes and the passed gate;
  tampered Diagnosis output cannot hydrate.
- This slice does not register a Supervisor loop, switch `POST /scan`, apply a
  Railway migration, or wrap Strategic Synthesis or Verification.

Phase 3 is **not** closed. The next bounded dependency is **3I-S1 — Strategic
Synthesis input snapshot**. Do not start a Synthesis adapter, Synthesis
commit, Verification, or request handoff from this slice.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Six new hermetic 3I-F3 contracts passed
  (`tests/test_scan_foresight_commit.py`). Full hermetic suite without the
  test DSN: `466 passed, 46 skipped`.
- Five disposable PostgreSQL 3I-F3 durability contracts passed (happy-path
  queue, stale reclaim, identical replay, malformed rejection, restart
  hash/gate). Local replay of `postgres-supervisor-contracts` including the
  new file: `46 passed`.


## Slice 3I-S1 — Strategic Synthesis input snapshot

A pending `strategic_synthesis` attempt can now be bound to the passed
Foresight checkpoint already queued by 3I-F3:

- `build_synthesis_input_snapshot` stores only Foresight checkpoint identity
  (id, ref, attempt, contract, input/output hashes) on a
  `scanner.synthesis-input/v1` manifest.
- Replay of the same manifest is idempotent. A different manifest for the
  same attempt is rejected. Hydration reloads the Foresight checkpoint
  through the hash-verifying repository path and requires an exact match.
- Persist/load do not claim the `strategic_synthesis` lease.
- This slice does not start a Synthesis adapter or commit, wrap Verification,
  register a Supervisor, switch `POST /scan`, apply a Railway migration, or
  copy Foresight KB content.

Phase 3 is **not** closed. The next bounded dependency is **3I-S2 —
Prepare-only Strategic Synthesis adapter**. Specify 3I-S2 after this
evidence; do not implement it here.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Four new hermetic 3I-S1 contracts passed
  (`tests/test_scan_synthesis_snapshot.py`). Full hermetic suite without the
  test DSN: `470 passed, 48 skipped`.
- Two disposable PostgreSQL 3I-S1 contracts passed (exact hydrate without
  claiming; tampered manifest and swapped Foresight hash rejected). Local
  replay of `postgres-supervisor-contracts` including the new file:
  `48 passed`.


## Slice 3I-S2 — Prepare-only Strategic Synthesis adapter

The 3I-S1 snapshot can now be prepared into a Synthesis checkpoint proposal
without moving the state machine:

- `build_synthesis_input_projection` reads only the hash-verified Foresight
  checkpoint. `build_synthesis_checkpoint` stores refs and a compact
  `strategic_synthesis` result. Foresight/Diagnosis/Jury/Intent payloads,
  extracts, raw bytes, and KB records stay off the checkpoint.
- `SynthesisStageExecutor.execute` hydrates `load_verified_synthesis_inputs`,
  wraps Foresight output without a model (default empty valid payload), and
  may name `verification` only as a proposal field. Malformed output is
  `validation_failed` and cannot name the next stage.
- After `execute`, the `strategic_synthesis` step remains leased, there is no
  Synthesis checkpoint row, and no `verification` step exists.
- This slice does not start a Synthesis commit, wrap Verification, register a
  Supervisor, switch `POST /scan`, apply a Railway migration, or copy
  Foresight KB content.

Phase 3 is **not** closed. The next bounded dependency is **3I-S3 —
Strategic Synthesis fenced commit + reclaim**. Specify 3I-S3 after this
evidence; do not implement it here.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Ten new hermetic 3I-S2 contracts passed
  (`tests/test_scan_synthesis_checkpoint.py`,
  `tests/test_scan_synthesis_adapter.py`). Full hermetic suite without the
  test DSN: `480 passed, 49 skipped`.
- One disposable PostgreSQL 3I-S2 contract passed (leased `strategic_synthesis`
  unchanged, no Synthesis checkpoint row, no `verification` step). Local
  replay of `postgres-supervisor-contracts` including the new file:
  `49 passed`.


## Slice 3I-S3 — Strategic Synthesis fenced commit + reclaim

The prepare-only Synthesis proposal can now be committed under the current
lease owner and fencing token:

- `commit_synthesis_stage` persists a passed 3I-S2 checkpoint with existing
  `complete_step_with_checkpoint` and queues only `verification`.
- Identical replay of the same passed checkpoint is idempotent. A different
  checkpoint for the same attempt is rejected. `validation_failed` proposals
  cannot complete the step or queue Verification.
- A stale fencing token cannot insert the Synthesis checkpoint. After lease
  expiry a replacement owner can reclaim the `strategic_synthesis` step,
  prepare again, and commit. Restart reload keeps Synthesis hashes and the
  passed gate; tampered Foresight output cannot hydrate.
- This slice does not register a Supervisor loop, switch `POST /scan`, apply a
  Railway migration, or wrap Verification.

Phase 3 is **not** closed. The next bounded dependency is **3I-V1 —
Verification input snapshot**. Specify 3I-V1 after this evidence; do not
implement it here. Do not start a Verification adapter, Verification commit,
Supervisor registration, or request handoff from this slice.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Six new hermetic 3I-S3 contracts passed
  (`tests/test_scan_synthesis_commit.py`). Full hermetic suite without the
  test DSN: `486 passed, 54 skipped`.
- Five disposable PostgreSQL 3I-S3 durability contracts passed (happy-path
  queue, stale reclaim, identical replay, malformed rejection, restart
  hash/gate). Local replay of `postgres-supervisor-contracts` including the
  new file: `54 passed`.


## Slice 3I-V1 — Verification input snapshot

A pending `verification` attempt can now be bound to the passed Strategic
Synthesis checkpoint already queued by 3I-S3:

- `build_verification_input_snapshot` stores only Synthesis checkpoint
  identity (id, ref, attempt, contract, input/output hashes) on a
  `scanner.verification-input/v1` manifest.
- Replay of the same manifest is idempotent. A different manifest for the
  same attempt is rejected. Hydration reloads the Synthesis checkpoint
  through the hash-verifying repository path and requires an exact match.
- Persist/load do not claim the `verification` lease.
- This slice does not start a Verification adapter or commit, register a
  Supervisor, switch `POST /scan`, apply a Railway migration, or copy
  Foresight KB content.

Phase 3 is **not** closed. The next bounded dependency is **3I-V2 —
Prepare-only Verification adapter**. Specify 3I-V2 after this evidence; do
not implement it here.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Four new hermetic 3I-V1 contracts passed
  (`tests/test_scan_verification_snapshot.py`). Full hermetic suite without the
  test DSN: `490 passed, 56 skipped`.
- Two disposable PostgreSQL 3I-V1 contracts passed (exact hydrate without
  claiming; tampered manifest and swapped Synthesis hash rejected). Local
  replay of `postgres-supervisor-contracts` including the new file:
  `56 passed`.


## Slice 3I-V2 — Prepare-only Verification adapter

The 3I-V1 snapshot can now be prepared into a Verification checkpoint
proposal without moving the state machine:

- `build_verification_input_projection` reads only the hash-verified Synthesis
  checkpoint. `build_verification_checkpoint` stores refs and a compact
  `verification_record`. Synthesis/Foresight/Diagnosis/Jury/Intent payloads,
  extracts, raw bytes, and KB records stay off the checkpoint.
- `VerificationStageExecutor.execute` hydrates
  `load_verified_verification_inputs` and wraps Synthesis output without a
  model (default empty valid payload). Verification is the last cognitive
  stage, so `next_stage` is `None` for passed and malformed proposals.
- After `execute`, the `verification` step remains leased, there is no
  Verification checkpoint row, and no successor step exists.
- This slice does not start a Verification commit, register a Supervisor,
  switch `POST /scan`, apply a Railway migration, or copy Foresight KB content.

Phase 3 is **not** closed. The next bounded dependency is **3I-V3 —
Verification fenced commit + reclaim**. Specify 3I-V3 after this evidence;
do not implement it here.

Verification evidence:

- Duplicate-definition guard passed; backend compileall passed.
- Focused trust contracts: `127 passed`.
- Ten new hermetic 3I-V2 contracts passed
  (`tests/test_scan_verification_checkpoint.py`,
  `tests/test_scan_verification_adapter.py`). Full hermetic suite without the
  test DSN: `500 passed, 57 skipped`.
- One disposable PostgreSQL 3I-V2 contract passed (leased `verification`
  unchanged, no Verification checkpoint row, no successor step). Local replay
  of `postgres-supervisor-contracts` including the new file: `57 passed`.
  That local replay is not GitHub CI. Pull requests targeting `reliability-v2`
  did not run Reliability contracts before merge at that time.


## Card 3G0 — reliability-contract pull-request gate

This is a CI/process-gate correction, not a product slice. No application
code, tests, Supervisor behavior, `POST /scan`, or Railway settings changed.

- `.github/workflows/reliability-contracts.yml` now runs on pull requests
  targeting `main` and `reliability-v2`. The existing push trigger for
  `reliability-v2` is unchanged. Job names, test commands, the PostgreSQL
  service, and permissions are unchanged.
- PR #30's Verification-adapter evidence was a local replay. Pull requests
  targeting `reliability-v2` did not run this workflow before merge, so that
  replay must not be described as GitHub CI.

The next product card remains **3I-V3 — Verification fenced commit + reclaim**.
Do not start it here.

Verification evidence:

- [Reliability contracts run 36117933055](https://github.com/jorieskolin-creator/Railway-The-Scanner/actions/runs/36117933055) completed successfully on pull request [#31](https://github.com/jorieskolin-creator/Railway-The-Scanner/pull/31) for commit `0554158a7477d94bdfeb6425b94a9a483a493069` (`pull_request` event, head branch `jocce-reliability-pr-gate-3g0-f5de`).
- `focused-contracts` passed.
- `postgres-supervisor-contracts` passed.


