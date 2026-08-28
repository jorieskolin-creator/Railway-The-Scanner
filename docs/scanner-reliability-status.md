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
