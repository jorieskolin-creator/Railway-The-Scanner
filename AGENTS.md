# AGENTS.md — Railway-The-Scanner

Repository-specific instructions for **The Scanner** (`jorieskolin-creator/Railway-The-Scanner`).
Organization-wide engineering, PR, and Railway/Vercel safety rules also apply automatically.

## 1. What this repository is
- **Frontend:** React 19 + TypeScript + Vite SPA at the repo root (`App.tsx`, `components/`, `services/`, `types.ts`), routed via `vercel.json`.
- **Backend:** Python FastAPI service under `backend/` (`backend/main.py`, `backend/requirements.txt`), packaged for Railway via `railway.json` and `backend/worker.dockerfile` (`uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}`).
- **Storage:** Vercel Blob via `backend/storage.py` (`BLOB_READ_WRITE_TOKEN`, `VITE_BLOB_URL` / `BLOB_URL`).

## 2. Verification commands
Run all checks relevant to your change before pushing or opening a PR:

- **Backend tests:** `pytest tests` (with `PYTHONPATH=backend:.` if needed)
- **Frontend type-check:** `npx tsc --noEmit`
- **Frontend production build:** `npm run build`
- **Remote safety check (verifies `origin` + `main` before release pushes):** `npm run verify:remote`

## 3. Data, secrets, and test isolation
- Keep all tests offline using mocks/fixtures (`monkeypatch`); never call live LLM providers or read/write live Vercel Blob storage during tests or verification.
- Never read, print, or commit `.env` or secret values (`OPENAI_API_KEY`, `GPT_API_KEY`, `ANTHROPIC_API_KEY`, `BLOB_READ_WRITE_TOKEN`, or Railway tokens).
- Do not modify `railway.json`, `backend/worker.dockerfile`, or `vercel.json` deployment routing unless explicitly requested.

## 4. Architecture & change boundaries
- Treat `backend/evidence_contracts.py`, `backend/model_policy.py`, `backend/stakeholder_policy.py`, and `types.ts` as shared contracts—check downstream callers and tests before changing fields or policy rules.
- `App.tsx`, `backend/main.py`, `backend/buying_potential.py`, and `backend/scanner_two.py` are large core modules: keep edits narrowly scoped and avoid unrelated refactors.
- `docs/` contains architecture references and visual maps; do not regenerate or overwrite them unless asked.
