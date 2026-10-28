# AI Vacancy Research Agent — Backend

FastAPI + PostgreSQL + SQLAlchemy 2 + Alembic + Celery/Redis. It manages companies, researches job vacancies on
official career pages (and permitted portals), extracts and matches them with an LLM, verifies whether they are
really open, and serves the React dashboard.

## Quick start (no credentials needed: research uses mock data)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                      # set SECRET_KEY: openssl rand -hex 32
docker compose up -d db redis             # or point DATABASE_URL at your own PostgreSQL
alembic upgrade head                      # create the tables
python -m app.scripts.create_admin you@example.org "Your Name"
uvicorn app.main:app --reload             # Swagger UI: http://localhost:8000/docs
pytest                                    # 34 tests, no network or Postgres required
```

Everything in Docker (API + worker + beat + Postgres + Redis): `docker compose up --build`.
Migrations: `alembic upgrade head` · new change: `alembic revision --autogenerate -m "what changed"` · undo: `alembic downgrade -1`.

Sign in via Swagger's **Authorize** button (email as username) or `POST /api/v1/auth/login` (form fields
`username`, `password`), then send `Authorization: Bearer <token>`.

## Roles

| Role | Can do |
|---|---|
| viewer | read everything, export |
| advisor | plus create/edit/archive companies and vacancies, import files, start/retry/cancel searches, recheck, review and add notes |
| admin | plus permanent deletes, user roles, organisation-wide research defaults |

Self-registration creates advisors. Create admins with `create_admin`; set `ALLOW_REGISTRATION=false` when your team has accounts.

## API (all under `/api/v1`, interactive docs at `/docs`)

- `/auth`: `register`, `login`, `me`, `users` (admin), `users/{id}/role` (admin)
- `/companies`: CRUD, `archive`/`unarchive`, `POST /import` (CSV/XLSX; `?dry_run=true` returns a preview, row errors and duplicates)
- `/searches`: `POST` start (202), `GET` history, `GET /{id}` progress (`status`, `processed_companies`, `progress_percent`), `/{id}/results`, `/{id}/retry`, `/{id}/cancel`
- `/vacancies`: list/filter/sort/paginate, CRUD, `archive`, `POST /{id}/recheck`
- `/reports`: `dashboard`, `summary`, `companies` (same filters as vacancies)
- `/exports`: `vacancies`, `company-report` as `?format=csv|xlsx` (same filters)
- `/settings`: `preferences`, `search-configurations` (saved presets), `research` (admin-only defaults)

Vacancy filters: `company_id, q, location, stack, min_experience, max_experience, status (repeatable), min_score,
max_score, date_from, date_to, reviewed, include_archived`, plus `sort`, `order`, `page`, `page_size` (max 100).

Conventions: lists return `{items,total,page,page_size,pages}`. Errors always return
`{"error":{"code","message","details"}}` (401/403/404/409/422/429/500). Internal errors are logged, never returned.

## Statuses and what they mean

`Active — Verified`, `Found — Unverified`, `Closed — Verified` (per vacancy); `No Matching Vacancy Found` and
`Search Incomplete` (per company in a search run).

- **Active — Verified** needs all of: the page loads (HTTP 200), no closed notice, not expired, the job title appears on the page, and at
  least two of: hosted on the company's domain, an apply link, valid `JobPosting` data. Anything less stays **Found — Unverified**.
- **Closed — Verified**: closed notice on the page, expired `validThrough`, or (on recheck) 404/410.
- **No Matching Vacancy Found** is recorded only if at least one source was read successfully with no problems. A timeout, robots block,
  missing careers page or search-API failure gives **Search Incomplete** instead. Retry re-runs only the incomplete companies.
- The LLM never decides status. Manual vacancies are always created as Found — Unverified; the API has no way to set status.
- A failed recheck (timeout, blocked) never changes status; it only adds a note.

Note: the frontend prototype labels the fourth status "No Match Found"; this API uses "No Matching Vacancy Found" from your spec. Use `status_label` from the API.

## How a search works

`POST /searches` stores an immutable snapshot of the criteria and queues the run (Celery if `CELERY_ENABLED=true`, otherwise a FastAPI
background task). Per company: careers page → posting links (plus search API hits on permitted domains) → fetch → extract
(JSON-LD first, LLM second, heuristics offline) → match score → verify → upsert with sources and evidence.
Duplicates are matched on canonical URL (no `www`, fragment or tracking params) and on a company+title+location hash.

Match score (0–100) is rule-based over the criteria you specified (technologies 45, role 30, location 15, experience 10,
reweighted to what was requested). With an LLM configured it is blended with the model's judgement, which can move it by at most 20 points.

## Real research and AI

- `SEARCH_PROVIDER=http` with `SEARCH_API_URL`/`SEARCH_API_KEY`: a generic JSON search client (`app/research/providers.py`; adapt `_parse` to your vendor).
  Results are used only if they are on the company's domain, or on a domain in `PERMITTED_PORTALS` and the run enables `job_portals`.
- `LLM_PROVIDER=anthropic` with `LLM_API_KEY` (model via `LLM_MODEL`).
- `USE_PLAYWRIGHT=true` (plus `pip install playwright && playwright install chromium`): re-renders JavaScript-only career pages; robots, SSRF checks and throttling still apply.
- Scheduled rechecks: Celery beat runs hourly and rechecks vacancies not verified within `RECHECK_INTERVAL_HOURS`. Without Celery, use cron: `python -m app.scripts.recheck`.

## Security notes

- Secrets come only from the environment; production refuses to start with a weak `SECRET_KEY`. API keys are never logged or returned.
- bcrypt password hashes, JWT with expiry, generic login errors, in-memory login rate limit (use a gateway or Redis limiter across several replicas).
- Web content is untrusted: wrapped in delimiters, instruction-like text is flagged in the evidence trail, model output is schema-validated,
  sanitised and score-limited, and status comes from code, not the model.
- Fetcher: robots.txt (a 5xx on robots.txt means don't fetch), per-domain delay, timeouts, bounded retries, size cap, http(s) only, and
  private/loopback/link-local addresses are blocked on every redirect hop (DNS rebinding is not fully excluded; add network egress rules in production).
- Exports neutralise spreadsheet formula injection. Text searches escape LIKE wildcards.

## Layout

`app/api/v1` routes (thin) · `app/services` business logic · `app/research` fetching, parsing, verification, mock web ·
`app/ai` extraction, matching, prompts, sanitising · `app/tasks` Celery · `app/models`, `app/schemas`, `app/db`, `app/core` · `migrations/` · `tests/`.

## Known limits

- Tested on SQLite. The PostgreSQL DDL was generated offline from the migration but not run against a live server.
- The HTTP search provider, the Anthropic client and the Playwright fallback are written but not exercised against live services.
- Rate limiting is per process. Hard-deleting a company removes its vacancies (prefer archiving).
- One vacancy per company + title + location: genuinely separate openings with identical title and location are merged.
