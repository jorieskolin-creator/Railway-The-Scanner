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
| 2 — PostgreSQL orchestration primitives | Not started on v2 | Process-kill reclaim and stale-attempt rejection proven |
| 3 — Material durability and checkpoints | Not started | Restart at every stage preserves immutable lineage |
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

## Change rules

- One bounded architectural concern per commit or pull request.
- Shared-module changes require regression coverage for Company Summary, Foresight harvesting/cleanup, Observatory and Market Intelligence boundaries.
- No phase is marked complete from code presence alone; its documented exit gate must pass.
- No gate may be weakened to obtain a green suite.
- Deployment success is not test evidence.
