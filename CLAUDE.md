# SIPOSTLOG v2

Logistics stock-opname app for BPBD Kabupaten Banjar.

## Commands

```bash
# In-process backend tests (extra deps: pytest, mongomock-motor)
(cd backend && python -m pytest tests -q --ignore=tests/backend_test.py --ignore-glob='tests/*_regression.py')
# Ops-script tests
python -m pytest .github/scripts/test_coolify_ops.py -q
# Frontend build (mirrors the Dockerfile)
(cd frontend && npm install && CI=false npm run build)
```

The ignored suites need a live server and database (`REACT_APP_BACKEND_URL`).

## Architecture

- `backend/server.py`: FastAPI on MongoDB (Motor); feature routers live in sibling modules, all under `/api`.
- `backend/serve.py`: production entrypoint; serves the API and the built SPA.
- `frontend/`: React 18 (CRA) + Tailwind; pages in `src/pages`, API client in `src/lib/api.js`.
- `.github/`: Coolify deploy and operations workflows, `scripts/coolify_ops.py`.
- Domain rules: `README.md` (Prinsip Proyek). Deployment: `DEPLOY.md`.

## Conventions

- Write tests in-process: `mongomock-motor` + FastAPI `TestClient`, network mocked (template: `backend/tests/test_bulk_import.py`).
- User-facing text (UI, API `detail` errors) is Indonesian, in plain words.
- The repository is public: never commit secrets or print them in workflow logs. Read sensitive workflow inputs from `$GITHUB_EVENT_PATH`, never through `${{ }}`, and mask them with `::add-mask::`.
- Every push to `main` deploys production (Coolify builds the root `Dockerfile`). Work on a branch.
- `backend/seed_data.py`: `len(INITIAL_ITEMS)` must equal `EXPECTED_ITEM_COUNT`, or `server.py` fails on import.
- MongoDB is standalone (no multi-document transactions); stock changes go through the item lock in `backend/stock_lock.py`.
