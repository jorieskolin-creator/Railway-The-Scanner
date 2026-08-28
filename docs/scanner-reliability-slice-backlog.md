# Scanner Reliability v2 — Operational Slice Backlog

> This register does **not** replace `docs/scanner-reliability-plan.md` or the
> stakeholder status report. Architecture, trust boundaries, cognitive flow,
> deployment topology, no-go controls, and phase exit gates stay exactly as
> written there.
>
> Purpose: split remaining *implementation tasks* into one-agent, one-concern
> cards so a slice cannot grow large enough to lose context.

Authority:

- Canonical ideas: `docs/scanner-reliability-plan.md`
- Completed evidence: `docs/scanner-reliability-status.md`
- Implementation branch: `reliability-v2`
- This file: next executable cards only

## Why the stakeholder roadmap needs this split

Phases 0–2 and slices 3A–3F are clear and proven. The remaining stakeholder
labels are still the right *ideas*, but they are not safe implementation units:

| Stakeholder label | Risk if implemented as one task |
|---|---|
| 3G Prepare-only Intent adapter | Mixes input projection, output-contract shape, cognitive invocation, and adapter wiring. 3D stayed small only because 3C had already defined the checkpoint builder. |
| 3H Intent durable commit | Safe if it is *only* fenced persist + reclaim. Unsafe if it also invents Jury wrapping, Supervisor registration, or `/scan` handoff. |
| 3I Sequential stage wrapping | This is the context-loss danger. Five Juries, Diagnosis, Foresight, Strategic Synthesis, and Verification are five (or more) independent wrap cycles, and Juries still have an acquisition path that Intent already required 3E/3F to tame. |
| 3J Authoritative request handoff | Cutover must be the last, smallest code change. Recovery proof, dual-path exclusion, and Railway enablement are separate decisions. |
| Phases 4–7 | Do not pre-expand into a full work queue now. Write those cards only after the Intent wrap is closed and the stage-wrap template below has been used once. |

Empirical size that already worked on this branch:

- 3D prepare-only adapter: ~237 lines, hermetic + one PostgreSQL proof, no state write
- 3A/3C/3E/3F foundations: ~470–670 lines, then a docs status commit
- 3F needed four follow-up contract fixes — that is the current upper bound before context is already being spent on repair instead of the original concern

Rule of thumb for every remaining card: one architectural concern, one new
module or one adapter, hermetic tests, PostgreSQL proof only when the slice
touches the database, then a status-register commit. Prefer the 3D size. Reject
any card that would require reading or editing `backend/main.py` scan
orchestration, Railway settings, or more than one cognitive stage.

## Hard rules inherited from the plan

These are restated here so a slice agent does not need the whole PDF:

1. Cognitive flow stays `Intent → five Juries → Diagnosis → Foresight → Strategic Synthesis → Verification`.
2. Do not register a Supervisor execution loop or claim queued work.
3. Do not switch `POST /scan` while the legacy in-memory thread can execute the same request.
4. Do not apply Railway migrations or enable `SCAN_SUPERVISOR_*` without an explicit release decision.
5. Do not scrape, acquire, or otherwise invent inputs that are not in the verified snapshot.
6. Retrieval provenance and Foresight KB are not Company Evidence.
7. A checkpoint is operational lineage, not a claim, eligibility verdict, or publication authority.
8. Keep new stage code out of `backend/scan_supervisor.py` (already ~1475 lines). Follow 3D/3F: new module + tests + a thin repository call if needed.
9. One slice per agent run. Do not start the next card in the same run.
10. Mark a slice complete only after CI evidence is recorded in `docs/scanner-reliability-status.md`.

## Envelope alignment without pulling Phase 4 forward

Phase 4 still starts only after Phase 3's stage-wrap exit gate. To avoid a later
rewrite, 3G's versioned Intent projection and checkpoint **must use the field
set already specified** in the plan's typed envelope:

`contract`, `run_id`, `stage`, `attempt`, `input_refs` / hashes, `output_refs` /
hashes, `gate_results`, plus Intent-specific payload hashes.

Do not implement generic envelope validation for every stage. Do not persist
Phase 4 gate-result tables. Shape Intent's contract so Phase 4 can adopt it
instead of replacing it.

---

## Card 3G1 — Intent input projection and output checkpoint contract

**Parent stakeholder step:** 3G
**Status:** Complete
**Mirrors:** 3C checkpoint builder, without the fenced commit that 3C also included

### In scope

- New module `backend/scan_intent_checkpoint.py`.
- Pure functions:
  - `build_intent_input_projection(...)` from a verified `IntentInputSnapshot`
    plus the already-hydrated `ScanMaterial` and `SourceRetrieval` lists.
  - `build_intent_checkpoint(...)` from that projection plus a caller-supplied
    Intent result mapping (the `analyze_intent` return shape).
- Deterministic concatenated text: materials by `material_id`, then retrievals
  by `retrieval_id`, using only hash-verified `extracted_text`. This is the
  exact string a later adapter may pass to `analyze_intent`.
- Checkpoint snapshot stores refs, hashes, gate result, and the compact Intent
  result (`score`, `breakdown`, `metrics`). It does not copy raw bytes or
  extracted text.
- Gate: `passed` when projection and result shape validate; `validation_failed`
  when the supplied result is malformed. Do not invent a new “text too short”
  block — that would change existing Intent behavior.

### Deliberate exclusions

- Do not call `analyze_intent`.
- Do not import acquisition/scrape clients.
- Do not write to PostgreSQL or add a migration.
- Do not add an executor, FastAPI registration, lease claim, or `/scan` change.
- Do not queue `jury_analysis` here.

### Tests

- `tests/test_scan_intent_checkpoint.py` (hermetic):
  - projection is deterministic and uses only snapshot-bound inputs
  - added/omitted/tampered material or retrieval cannot build a projection
    (integrity error)
  - checkpoint hashes are stable for the same projection + result
  - malformed Intent result cannot produce `status=passed`
  - checkpoint snapshot has no extracted text / raw bytes
- No PostgreSQL job change required.

### Exit evidence

Projection and checkpoint builders are deterministic, evidence-neutral, and
unused by any live path. Status register records 3G1 and names 3G2 as next.

### Likely files

`backend/scan_intent_checkpoint.py`, `tests/test_scan_intent_checkpoint.py`,
`docs/scanner-reliability-status.md`

---

## Card 3G2 — Prepare-only Intent adapter

**Parent stakeholder step:** 3G
**Status:** Complete
**Mirrors:** 3D `MaterialIngestionStageExecutor`

### In scope

- New `IntentStageExecutor` (same file as 3D or `backend/scan_intent_adapter.py`
  if that keeps `scan_stage_executor.py` from mixing stages).
- `execute(lease)`:
  1. reject any stage other than `intent`
  2. call only `load_verified_intent_inputs`
  3. build the 3G1 projection
  4. call existing `analyze_intent(projection_text)` — no rewrite of scoring
  5. return `StageExecutionResult` with the 3G1 checkpoint proposal
- `next_stage` metadata may name `jury_analysis` when the gate passed, but only
  as a proposal field. Nothing is queued.
- Adapter has no `complete_step` / persist / claim method.

### Deliberate exclusions

- No scrape, Brave, Firecrawl, Coresignal, or `scrape_strategic_content`.
- No parallel legacy path and no call into `main.py` scan orchestration.
- No database transition: after `execute`, the intent step remains leased and
  `scan_checkpoints` has no new Intent row.
- No Supervisor registration, no `/scan` change, no Railway setting.

### Tests

- Hermetic: snapshot-only inputs; `analyze_intent` result matches calling it
  directly on the same concatenated text; wrong stage rejected; no persist
  method on the class.
- PostgreSQL: persist 3F snapshot (+ materials/retrievals as needed), claim an
  `intent` lease (via completing material ingestion with the existing fenced
  helper, or inserting the intent step the same way current tests do), run the
  adapter, assert run/step unchanged and checkpoint count for `intent` is 0.
- Add the new PostgreSQL test path to
  `.github/workflows/reliability-contracts.yml` disposable job.

### Exit evidence

Same class of proof as 3D: preparation reads durable input and proposes output
without moving the state machine. Stakeholder checkpoint after 3G is satisfied
here, not after 3H.

---

## Card 3H — Intent fenced commit and reclaim

**Parent stakeholder step:** 3H
**Status:** Complete

### In scope

- Persist the 3G2 proposal with existing `complete_step_with_checkpoint`.
- Queue only the already-defined next stage name `jury_analysis`.
- Stale fencing token rolls back the Intent checkpoint insert and does not
  advance the run.
- Process-kill / lease-reclaim proof for the Intent step, reusing the Phase 2
  disposable-PostgreSQL pattern.
- Restart from the Intent checkpoint must not re-hash different inputs or skip
  the Intent gate.

### Deliberate exclusions

- No Jury adapter, Jury snapshot, or Jury cognitive call.
- No Supervisor loop registration.
- No `POST /scan` switch.
- No Railway migration/enablement.
- No Phase 4 envelope validator beyond what 3G1 already stored.

### Tests

- PostgreSQL: happy-path commit queues `jury_analysis`; stale owner cannot
  commit; reclaiming owner can; replay of the same passed checkpoint is
  idempotent; malformed/blocked proposal cannot complete the step.
- Hermetic regression: `analyze_intent` scoring fixtures unchanged.

### Exit evidence

Intent is the first cognitive stage with a crash-safe durable boundary.
Phase 3 is **not** closed. Next work is the first 3I template card for Juries
(input snapshot only), written after 3H evidence exists — not before.

---

## 3I template — one cognitive stage, three beats, never a bundle

Do not implement “wrap the remaining stages” as a task. After 3H, each later
stage gets its own mini-cycle, copied from Intent:

| Beat | Intent analogue | Allowed content |
|---|---|---|
| 1. Exact input snapshot | 3F / 3G1 | Hash-verified upstream checkpoint + that stage's durable inputs. No cognitive call. |
| 2. Prepare-only adapter | 3G2 | Invoke the *existing* stage function on hydrated inputs. Return a proposal. No commit. |
| 3. Fenced commit + reclaim | 3H | Atomic checkpoint + next stage name. Process-kill proof. |

Additional Jury-only beat, because Juries can acquire sources the same way
Intent could scrape:

| Beat | Intent analogue | Allowed content |
|---|---|---|
| 0. Retrieval/evidence input durability | 3E | Immutable provenance for Jury-stage fetches/candidates. Not Company Evidence. Not Foresight eligibility. |

**One agent run implements one beat of one stage.** A Jury run that also
touches Diagnosis, or a Diagnosis run that also “quickly wires Foresight,” is
out of bounds.

Do not write detailed 3I-Diagnosis/Foresight/Synthesis/Verification cards now.
Write the next card only when the previous beat's status evidence is recorded.
That is how this program already avoided losing the thread through 3A–3F.

Suggested names when 3H closes (cards not specified until then):

1. `3I-J0` Jury-stage retrieval/candidate durability
2. `3I-J1` Jury input snapshot from Intent checkpoint
3. `3I-J2` Prepare-only Jury adapter
4. `3I-J3` Jury fenced commit + reclaim
5. Then the same 1–3 beats for Diagnosis, Foresight, Strategic Synthesis,
   Verification

Phase 3 exit gate stays the plan's gate: restart after every major stage
resumes without re-upload, lost lineage, skipped gate, or mutated snapshot.

---

## 3J split — proof first, cutover last and small

| Card | Outcome | Not this card |
|---|---|---|
| 3J-loop | Test-only Supervisor loop can drive the completed durable chain. Still not bound to `POST /scan`. | Railway enablement, live traffic |
| 3J-exclusive | Contract proving only one execution path can own a request (durable path cannot start while legacy thread can, and the reverse). | The actual route swap |
| 3J-enablement | Written operational checklist: migration, `SCAN_SUPERVISOR_*`, verification queries, rollback. Review artifact, not a silent settings change. | Applying it to Railway |
| 3J-cutover | Replace, not parallelize, the in-memory `/scan` handoff. Target size is a small route change after the proofs exist. | Inventing missing stage wraps |

If 3J-cutover is not small, earlier cards leaked scope. Stop and split; do not
“just finish the handoff.”

---

## Phases 4–7

Leave them as phase-level intent until Phase 3's wrap template has been used
for Intent and at least the Jury input snapshot. Then add cards with the same
size cap. In particular:

- Phase 4A should become “envelope schema helpers + adopt on already-wrapped
  stages,” not “type every future stage in one pass.”
- Phase 7 stays last and behaviour-preserving. Do not extract `main.py` while
  checkpoints are still incomplete.
- Company Summary (Phase 5) stays blocked on durable typed evidence/gate
  lineage. Do not start it from an Intent adapter run.

---

## Recommended CI planning fix (optional micro-card before 3G2)

`.github/workflows/reliability-contracts.yml` currently runs PostgreSQL
contracts on `push` to `reliability-v2` and pull requests to `main` only.
Slice PRs that target `reliability-v2` therefore may not run the disposable
PostgreSQL job until merge.

Optional card **3G0**: also trigger the workflow on pull requests to
`reliability-v2`. No product behavior. Do this as its own tiny commit if
touched.

---

## One-slice agent prompt

Use this as the entire implementation prompt for the next run. Do not paste
the stakeholder PDF or this whole file as the task.

```text
Work only on reliability-v2.

Specify then implement exactly slice 3I-J0 from docs/scanner-reliability-slice-backlog.md
(Jury-stage retrieval/candidate durability). Do not start a Jury adapter, Jury
commit, Diagnosis, Foresight, Supervisor registration, POST /scan, or Railway.

Deliver: the specified card plus its module/tests/status evidence. Stop when
3I-J0 exit evidence is recorded.
```

After 3I-J0 merges, replace `3I-J0` with `3I-J1` in that prompt. Do not accumulate
open cards in one run.
