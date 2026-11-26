# AGENTS.md — Railway-The-Scanner (`reliability-v2`)

Repository-specific instructions for **The Scanner** (`jorieskolin-creator/Railway-The-Scanner`).
Organization-wide engineering, PR, and Railway/Vercel safety rules also apply automatically.

## Branch roles & authoritative plans
- **`main`:** Active production branch currently in use. Do not push directly to `main` or merge incomplete reliability slices into `main`.
- **`reliability-v2`:** Active development branch for the Reliability v2 program (replaces `main` only after all phase exit gates pass). Never merge the historical `staging` branch.
- **Authoritative `reliability-v2` docs:** Follow `docs/scanner-reliability-plan.md`, `docs/scanner-reliability-status.md`, and `docs/scanner-reliability-slice-backlog.md` (one bounded card = one branch = one PR targeting `reliability-v2`).

## 1. What this repository is
- **Frontend:** React 19 + TypeScript + Vite SPA at the repo root (`App.tsx`, `components/`, `services/`, `types.ts`), routed via `vercel.json`.
- **Backend:** Python FastAPI service under `backend/` (`backend/main.py`, `backend/requirements.txt`), packaged for Railway via `railway.json` and `backend/worker.dockerfile` (`uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}`).
- **Domain Storage (Vercel Blob):** `backend/storage.py` (`BLOB_READ_WRITE_TOKEN`, `VITE_BLOB_URL` / `BLOB_URL`, `VITE_BLOB2_URL` / `BLOB2_URL`) is authoritative for shared domain artifacts (`companies/{slug}.json`, `index/summary_list.json`, reports, Foresight KB, and exports).
- **Durable Orchestration (PostgreSQL on `reliability-v2`):** `backend/migrations/`, `backend/scan_supervisor.py`, and `backend/scan_*.py` store operational scan state, leases, fencing tokens, and hash-verified stage checkpoints only (`SCANNER_DATABASE_URL` / `DATABASE_URL`). The in-process Supervisor defaults to inert unless `SCAN_SUPERVISOR_ENABLED=true` and `SCAN_SUPERVISOR_SCHEMA_READY=true`.

## 2. Verification commands
Run all checks relevant to your change before pushing or opening a PR:

- **Backend compile & duplicate-definition guard:** `python -m compileall -q backend && python scripts/check_duplicate_top_level_defs.py`
- **Hermetic backend tests (offline; `tests/conftest.py` blocks external sockets/DNS):** `PYTHONPATH=backend:. pytest -q`
- **Disposable PostgreSQL durability contracts (when changing `backend/scan_*.py` or `backend/migrations/`, and enforced in `.github/workflows/reliability-contracts.yml`):** run `pytest` on `tests/test_scan_*_postgres_integration.py` with `SCANNER_ALLOW_LOOPBACK_TEST_DATABASE=true` and `SCANNER_TEST_DATABASE_URL=postgresql://scanner:scanner@127.0.0.1:5432/scanner_test`
- **Frontend type-check:** `npx tsc --noEmit`
- **Frontend production build:** `npm run build`
- **Release remote safety check (`main` release branch only — intentionally fails on `reliability-v2` and feature branches):** `npm run verify:remote`

## 3. Data, secrets, and test isolation
- Keep all tests offline/hermetic using mocks and local fixtures (`monkeypatch`). Clear ambient provider/storage keys (`OPENAI_API_KEY`, `GPT_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `CORESIGNAL_API_KEY`, `FIRECRAWL_API_KEY`, `BRAVE_API_KEY`, `BLOB_READ_WRITE_TOKEN`) when running tests locally so runs never call live LLM/search providers or touch live Vercel Blob / Railway databases.
- Never read, print, or commit `.env` or secret values (`OPENAI_API_KEY`, `GPT_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `BLOB_READ_WRITE_TOKEN`, `SCANNER_DATABASE_URL` / `DATABASE_URL`, or Railway tokens).
- Do not modify `railway.json`, `backend/worker.dockerfile`, or `vercel.json` deployment routing unless explicitly requested.

## 4. Architecture & change boundaries
- Treat `backend/evidence_contracts.py`, `backend/model_policy.py`, `backend/stakeholder_policy.py`, `types.ts`, and shared Vercel Blob schemas (`companies/{slug}.json`, `index/summary_list.json`) as shared contracts—check downstream callers and compatibility tests (`tests/test_company_and_mi_compatibility_contracts.py`, `tests/test_foresight_buying_compatibility_contracts.py`) before changing fields or policy rules.
- Applied SQL migrations under `backend/migrations/` are immutable—never edit applied migration files; add a new numbered migration instead.
- Preserve `reliability-v2` slice boundaries: keep cognitive stage order (`Intent → five Juries → Diagnosis → Foresight → Strategic Synthesis → Verification`) intact, fail closed on provider unavailability or hash/gate mismatches (never convert to `passed`), and do not modify live `POST /scan`, enable the production Supervisor loop, or change Blob publication paths outside their designated cutover cards.
- `App.tsx`, `backend/main.py`, `backend/buying_potential.py`, and `backend/scanner_two.py` are large core modules: keep edits narrowly scoped and avoid unrelated refactors.
- `docs/` contains architecture references and visual maps—do not regenerate or overwrite them unless asked, except recording slice completion and CI evidence in `docs/scanner-reliability-status.md` and `docs/scanner-reliability-slice-backlog.md`.
