# Scanner Reliability Implementation Plan v2

> **Status:** Canonical implementation contract for `reliability-v2`.
> This plan supersedes the abandoned `staging` design that used a separately deployed Scanner worker. The target is one Railway FastAPI service with a PostgreSQL-backed in-process Supervisor.

## Purpose and success definition

The purpose of this program is to make the existing cognitive pipeline
**verifiable, contract-bound, and more reliable than the old browser-fragile
execution path**. Success means that the pipeline can be executed through
explicit stage contracts, durable orchestration state, resumable checkpoints,
and deterministic failure states while preserving the current cognitive model.

This program is **not** a promise that the Scanner becomes fully production
ready when the plan is complete. The current authorization model is sufficient
for this phase. User management, broader production hardening, public launch
readiness, and operational scaling are separate later decisions.

The immediate outcomes to prove are:

- the cognitive model still works end to end;
- every stage boundary has a clear typed contract;
- durable orchestration can recover from process loss;
- malformed, stale, missing, or contradictory inputs fail explicitly;
- final domain artifacts remain compatible with the existing shared Vercel
  Blob stores.

Prepare-only adapters and empty default payloads are contract scaffolding.
They prove snapshot, hash, state-transition, and fencing behavior, but they do
not by themselves prove that the cognitive model produced a valid result.
Cognitive validation requires representative execution through the existing
stage functions and final artifact contract.

## Decision and constraints

The Scanner will retain its cognitive flow unchanged:

`Intent → five Juries → Diagnosis → Foresight → Strategic Synthesis → Verification`

Reliability work changes execution, persistence, and trust enforcement around
those stages. It must not merge stages, let orchestration metadata become
evidence, or let the Foresight Knowledge Base prove company action.

The deployment design is intentionally small:

- keep the existing Railway FastAPI service;
- use the PostgreSQL database already attached to Railway for orchestration;
- run a PostgreSQL-backed **Scan Supervisor inside that API process**;
- do not require a Railway staging environment or a second worker service;
- keep the existing Vercel Blob stores as the authoritative shared domain-data
  stores;
- do not create another Vercel Blob store or write intermediate Blob objects;
- retain existing Blob writes, schemas, and current pointers for final
  immutable reports, exports, dashboard data, company summaries, and shared
  Foresight artifacts;
- initially permit one active scan at a time.

“Supervisor” means an execution loop in the existing application, not a new
deployed service. It claims a queued database row, invokes the existing stage
functions, heartbeats its lease, and records checkpoints. If Railway restarts,
the same application can reclaim an expired lease and resume from the last
valid checkpoint.

## Non-goals for this reliability program

The following are deliberately out of scope unless a later plan explicitly
adds them:

- replacing the current authorization model;
- adding user accounts, teams, roles, or multi-tenant administration;
- moving the source of truth for dashboard data, company reports, company
  summaries, Foresight artifacts, evidence packages, publications, or exports
  from Vercel Blob to PostgreSQL;
- creating a second authoritative domain-data store;
- introducing a new worker service or another Vercel Blob store;
- making the service generally production ready for a broader launch;
- changing the cognitive stages or their analytical intent as part of a
  reliability slice.

## Storage authority

Vercel Blob and PostgreSQL have separate authority. Neither may silently take
over the other's role.

| Store | Authoritative for | Must not become |
|---|---|---|
| Vercel Blob | Shared domain artifacts: dashboard summaries, company reports, company summaries, Foresight KB artifacts, evidence packages, publications, and exports | A scan-execution lock, lease store, retry log, or process-recovery mechanism |
| PostgreSQL | Scan-orchestration state: runs, steps, leases, heartbeats, retries, idempotency records, checkpoints, failure state, and references to Blob artifacts | A duplicate source of truth for shared domain artifacts |
| Browser memory | Presentation and user interaction only | Durable scan state or recovery authority |

PostgreSQL artifact rows should store Blob object identity, URL or key,
content hash, schema version, validation status, and lineage. They should not
duplicate full shared artifacts unless a later explicit decision creates a
rebuildable derived projection.

Shared Blob schemas are external contracts because other solutions consume the
same stores. Changes must be versioned and compatibility-tested. Existing
shared objects must not be silently mutated into a new shape.

## Trust boundaries

The system must keep these stores and meanings separate:

| Boundary | Contains | Must not imply |
|---|---|---|
| Scan material | uploaded extracts, fetched pages, hashes, parser metadata | that a claim is true |
| Company evidence | company-scoped sources, passages, propositions, support decisions | macro foresight eligibility |
| Foresight KB | company-neutral external forces and context | company action, deployment, or current capability |
| Operational state | run, step, lease, retry, event, checkpoint metadata | evidence or analytical support |
| Published artifacts | Vercel Blob output that passed its publication contract | that withheld/internal drafts passed |

The evidence lineage remains:

`SRC/EVD → PRS → PASS → PROP → PKG → FND → CLM/SYN`

Every transition must preserve stable IDs and source references. URL liveness,
readability, entity match, source-context eligibility, claim support, and
publication eligibility are independent verdicts. A live, readable,
entity-scoped source may remain bounded context after a claim relation fails,
but cannot support that failed claim.

## Target execution model

```text
POST /scan
    │ validate request and persist material first
    ▼
PostgreSQL scan_runs: queued
    │
    ▼
FastAPI Scan Supervisor (same Railway service)
    │ claim lease + fencing token; heartbeat while active
    ▼
Existing cognitive stage function
    │
    ├─ write immutable input/output snapshot + hash
    ├─ write deterministic gate result
    └─ atomically commit checkpoint and next queued step
    ▼
Final publication gate
    ├─ passed  → existing immutable Blob report/export and Blob current pointer
    │            plus PostgreSQL artifact reference/reconciliation state
    └─ failed  → internal draft/checkpoint only; no public Blob publication
```

The process may disappear at any instruction. Correctness therefore cannot
depend on a Python dictionary, daemon thread, in-memory lock, or `finally`
block. A stage commit is accepted only when its lease owner and fencing token
still match the current database row. A stale process may finish a model call,
but it cannot commit after another attempt has reclaimed the step.

The browser is not authoritative. Closing the browser must not interrupt an
active backend scan once the durable orchestration path is enabled.

## PostgreSQL records

Use relational columns for identity, state, ordering, timestamps, hashes, and
foreign keys. Use JSONB only for versioned payloads that have a validated schema.

### Initial tables

- `scan_runs`: run ID, company identity, cognitive-contract version, status,
  current stage, request idempotency key, timestamps, final artifact reference.
- `scan_steps`: stable stage name, attempt, status, lease owner, lease expiry,
  heartbeat, fencing token, input/output hash, checkpoint reference, gate result.
- `scan_events`: append-only state transitions and diagnostics; no evidence role.
- `scan_materials`: material ID, run ID, origin, MIME, byte size, SHA-256,
  parser/version, extraction status, warnings, allowed-use classification, and
  extracted text or a reference to database-backed chunk rows.
- `source_retrievals`: canonical URL, retrieval time, HTTP/content metadata,
  content hash, parser route, readability and retrieval outcome.
- `evidence_records`: stable `SRC/EVD/PRS/PASS/PROP/PKG/FND` identity, contract
  version, immutable payload/hash, run/stage origin, supersession relationship.
- `claim_support`: claim/source/proposition IDs and deterministic relation verdict.
- `gate_results`: gate name/version, inputs, status, blocking reasons, warnings,
  checked timestamp; statuses are explicit (`passed`, `failed`, `blocked`,
  `insufficient_evidence`, `validation_failed`, `approval_required`).
- `artifacts`: final or internal artifact identity, content hash, publication
  status, Blob reference where applicable, and gate-result reference. This is
  an orchestration/reference record; it does not replace Blob as the domain
  artifact store.
- `kb_versions`: Foresight KB version/hash used by a run; KB content remains a
  distinct contract and cannot be copied into company-evidence rows.
- `foresight_signals`: stable signal IDs and the company-neutral source contract.

Raw PDF bytes are not required for resumability. Before acknowledging the scan,
persist extracted text plus the original SHA-256, MIME, byte size, parser
version, extraction warnings, and allowed-use classification. Retaining raw
bytes requires a separate explicit retention policy.

## State machine and retry contract

Run states:

`queued → running → completed | failed | blocked | cancelled`

Step states:

`pending → leased → running → completed | retryable_failed | terminal_failed | blocked`

Rules:

1. The API commits the run and its material records in one transaction before
   returning a run ID.
2. The Supervisor claims work with a row lock/atomic update and receives a new
   fencing token.
3. A heartbeat only extends the lease for the current owner and token.
4. A checkpoint transaction writes the immutable snapshot, its hash, gate
   result, step completion, event, and next step together.
5. Retry reuses a completed checkpoint only after schema, hash, stage version,
   model-policy version, KB version, and required upstream gates validate.
6. Model/provider timeout is not evidence failure. It is an explicit unavailable
   or retryable operational result and cannot be converted to `passed`.
7. Non-idempotent final Blob publication and current-pointer updates use an
   idempotency key and uniqueness/reconciliation controls so retries cannot
   publish duplicate “latest” reports.

## Cross-store publication protocol

Vercel Blob and PostgreSQL cannot commit atomically together. Final
publication therefore needs an explicit, retryable sequence:

1. write the new immutable final artifact to the existing Vercel Blob store
   under a versioned object key;
2. verify readability and content hash;
3. record the artifact reference, hash, gate result, and publication attempt
   in PostgreSQL;
4. update the Blob current pointer or index using the existing shared schema;
5. mark publication reconciled only after the Blob pointer and PostgreSQL
   reference agree.

A failure between steps must remain visible and retryable. It must not be
converted into fabricated success. Existing dashboard and company reads remain
Blob-backed even when PostgreSQL orchestration is disabled or unavailable.

## Typed stage envelope

Each stage will eventually accept and return one versioned envelope while its
existing cognitive function remains intact:

```json
{
  "contract": "scanner.stage/v1",
  "run_id": "RUN-…",
  "stage": "jury_analysis",
  "attempt": 1,
  "input_refs": [{"id": "PKG-…", "sha256": "…"}],
  "output_refs": [{"id": "FND-…", "sha256": "…"}],
  "gate_results": [{"gate": "jury_finding/v1", "status": "passed"}],
  "model_policy_version": "…",
  "kb_version": "…",
  "started_at": "…",
  "completed_at": "…"
}
```

The envelope carries references and verdicts, not implicit trust. JSON schema or
typed-model validation runs at both ingress and egress. Invalid output is
`validation_failed`; later stages do not receive a best-effort malformed object.

## Company Summary publication contract

Company Summary should become a separately gated artifact, not merely generated
text with a warning label.

Required changes:

1. Build its generation payload only from eligible company claims with stable
   claim, evidence, proposition, and passage IDs. URL-less Jury findings cannot
   enter factual prose.
2. Require every factual sentence to declare a claim type and its supporting
   IDs. Deterministically reject missing, unknown, cross-company, Foresight-only,
   or non-generation-eligible references.
3. Preserve current same-payload generation/verification and independent
   verifier separation where available.
4. Store failed or inconclusive output as an internal draft with its gate result;
   do not place it in the public/latest Summary slot in Blob.
5. Publish only after schema, evidence lineage, entity, claim-support, citation,
   contradiction, language, and verifier gates pass.

## Tag and identity hardening

Tagging is classification metadata, not evidence. To reduce fragile large-call
behavior:

- assign stable `signal_id`, `source_id`, `proposition_id`, `finding_id`, and
  `claim_id` before model calls;
- make models return IDs from the supplied set rather than repeat titles/URLs;
- replace free-form canonical tags with versioned enums and explicit `unknown`;
- reject invalid enum values instead of converting them to an empty string;
- never use title-substring matching when a stable ID is expected;
- split discovery, validation/extraction, classification, Jury synthesis, and
  final synthesis into bounded calls with payload-size ceilings;
- record tag contract version and deterministic validation results per stage.

## Delivery order and acceptance gates

### Phase 1 — repair and freeze trust contracts

- Classify current test failures as implementation regression, stale expectation,
  test-isolation/network leak, or environment/provider issue.
- Fix only genuine regressions; update stale tests only when the implementation
  is intentionally stricter than the old expectation.
- Freeze lifecycle, Company Summary, Jury, Foresight, and sanitizer contracts in
  deterministic tests. Do not weaken a gate to obtain a green suite.

Exit gate: trust-boundary suites are deterministic and green with no external
network/model calls; remaining full-suite failures are explicitly classified.

### Phase 2 — PostgreSQL orchestration primitives

- Add migrations, configuration, repository transactions, state enums,
  idempotency constraints, lease/heartbeat/fencing operations, and repository
  contract tests.
- Add the internal Supervisor to FastAPI startup/shutdown, disabled until the
  schema is ready. Keep one active scan globally using PostgreSQL locking.

Exit gate: a process-kill integration test demonstrates lease expiry and safe
reclaim; a stale attempt cannot commit.

### Phase 3 — material durability and checkpoints

- Persist uploads/extracts before queueing.
- Wrap existing major stages one at a time, starting at material ingestion and
  Jury inputs; add immutable hashes and resume validation.
- Do not rewrite the cognitive stage implementation while checkpointing it.

Exit gate: restart after each major stage resumes without re-uploading material,
losing lineage, skipping a gate, or changing a completed immutable snapshot.

Remaining Phase 3 *tasks* (not ideas) are split into one-concern cards in
`docs/scanner-reliability-slice-backlog.md` so implementation runs stay inside
the 3A–3F size that already worked.

### Phase 4 — typed envelopes and deterministic gates

- Introduce versioned stage envelopes at each boundary.
- Persist gate results and block downstream execution on invalid/insufficient
  inputs according to the contract.

Exit gate: malformed, missing, contradictory, stale, and hash-mismatched inputs
produce explicit blocked states in contract tests.

### Phase 5 — Summary and tagging hardening

- Implement structured sentence support for Company Summary and enforce strict
  publication withholding.
- Introduce stable-ID/canonical-enum tagging in bounded calls.

Exit gate: mutation tests removing or swapping evidence IDs cause publication
rejection; tag order/title wording does not alter stable associations.

### Phase 6 — Blob-first publication reconciliation

- Keep latest-company/report-index state in the existing Vercel Blob store.
- Keep final immutable artifacts and exports in the existing Vercel Blob store.
- Record artifact references, hashes, gate results, and publication attempts in
  PostgreSQL for orchestration and reconciliation.
- Reconcile Blob publication and PostgreSQL references through idempotent
  artifact records, not a process-local read/modify/write lock.
- Do not create a PostgreSQL authoritative read model. If a later performance
  need justifies one, it must be a rebuildable derived projection of Blob
  artifacts, not the source of truth.

Exit gate: concurrent or retried publication produces one Blob current pointer,
no duplicate final artifact record, and a visible reconciliation path for any
partial cross-store failure.

### Phase 7 — orchestrator decomposition

- Split the large orchestrator by existing stage ownership only after stage
  inputs/outputs are checkpointed and contract-tested.
- Treat this as behavior-preserving refactoring; change cognitive behavior in
  separate reviewed work.

Exit gate: checkpoint replay and golden contract tests are unchanged before and
after each extraction.

## Verification strategy

The reliability program requires more than ordinary happy-path tests:

- schema and state-transition unit tests;
- repository tests against real PostgreSQL transactions;
- lease race, stale fencing token, duplicate request, and concurrent claim tests;
- process termination at every checkpoint boundary;
- provider timeout and malformed-output fault injection;
- hash tampering and checkpoint-version mismatch tests;
- evidence/KB cross-contamination tests;
- Summary evidence-ID mutation and publication-withholding tests;
- shared Blob schema compatibility tests for artifacts consumed by other
  solutions;
- no-network deterministic contract suites;
- one representative end-to-end scan using the existing cognitive stage
  functions, with restart, resume, verification, and final Blob publication
  tested in the existing Railway-shaped single-process topology.

Repository process gates:

- pull requests targeting `reliability-v2` must run the reliability contracts
  before merge, not only after the branch is updated;
- local command replay may be recorded as evidence, but it must be labelled as
  local replay and must not be described as GitHub CI;
- deployment success is not test evidence;
- a green test run does not authorize changing Railway settings, enabling the
  Supervisor, or switching `POST /scan`.

Operational metrics should include queue age, run/stage duration, lease age,
heartbeat lag, retry count/reason, checkpoint age, gate status counts, material
extraction failures, evidence attrition by gate, Summary publication withholding,
Blob publication reconciliation failures, and final artifact reference
mismatches. Logs and events must include run and stage IDs but must not print
uploaded content or secrets.

## First implementation slice

The first code slice is deliberately before PostgreSQL:

1. correct contradictory trust-state accounting;
2. prevent exact entity-verified company material from being misclassified as
   Foresight context;
3. preserve current source-passage verification through Jury normalization;
4. reject dead/unreadable Foresight records even when another status still says
   eligible;
5. make sanitization idempotent;
6. make focused trust suites hermetic and freeze the corrected contracts.

Only after that slice is green should migrations and Supervisor code be added.
