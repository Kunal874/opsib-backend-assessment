# OPSiB — receipt-shortfall service

A small backend (Python · FastAPI · PostgreSQL) that turns inconsistent retail exports into
**traceable findings** and **tasks a real, eligible employee can act on**:

```
6 CSV exports → validated, idempotent source facts → canonical product / store / base units
             → deterministic receipt-shortfall rule → exceptions with evidence
             → routed tasks → server-enforced, audited workflow
```

With the supplied fixtures and the frozen evaluation time `2026-10-16T15:00:00+05:30`:

| PO | Interpretation (from the brief) | Result | Routed to |
|---|---|---|---|
| PO-501 | split receipts 60 + 32 of 100 EA | **shortfall 8 EA** | HR-102 (HR-101 off shift, HR-103 inactive) |
| PO-502 | 40 of 40 before the deadline | no shortfall | — |
| PO-503 | deadline 17 Oct still in the future | not due | — |
| PO-504 | cancelled | no shortfall | — |
| PO-505 | 10 BOX × 12 = 120 EA vs 96 EA | **shortfall 24 EA** | HR-401 (no receiving associate at STORE-015 → store manager) |
| PO-506 | the same 10 EA receipt sent twice | **shortfall 10 EA** (counted once) | HR-102 |

Every finding says only what the data shows ("receipt shortfall") and lists what is unknown (no
dispatch/ASN data, no carrier proof of delivery, no recount) — it never asserts supplier fault.

**More detail:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (design, data model, flows) ·
[`docs/API_EXAMPLES.md`](docs/API_EXAMPLES.md) (real requests/responses) ·
[`docs/openapi.json`](docs/openapi.json) · [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md) ·
[`tests/data/README.md`](tests/data/README.md) (synthetic test data we added).

---

## 1. Run it

Requirements: **Docker** (for PostgreSQL) and **Python 3.14** (developed and tested on 3.14; the Docker
image uses `python:3.14-slim`).

### Option A — local Python, Postgres in Docker

```bash
docker compose up -d db                       # Postgres 16 on localhost:55432 (+ an opsib_test database)
python -m venv .venv
source .venv/Scripts/activate                 # Windows Git Bash;  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.cli migrate                     # apply migrations/*.sql
python -m app.cli load-fixtures fixtures      # import the six supplied CSVs
python -m app.cli run-detection               # evaluate every PO line, create findings + tasks
uvicorn --factory app.main:create_app         # API on http://localhost:8000  (docs at /docs)
```

### Option B — everything in Docker

```bash
docker compose up --build                     # db + api (migrations run on start) → http://localhost:8000/docs
docker compose run --rm api python -m app.cli load-fixtures fixtures
docker compose run --rm api python -m app.cli run-detection
docker compose run --rm api pytest -q         # the full test suite inside the container
```

> Postgres is published on host port **55432**, not 5432, because 5432–5434 are commonly taken by a
> local PostgreSQL install or other projects. Inside the compose network the API uses `db:5432`.

### Environment variables

All have working defaults (see [`.env.example`](.env.example)); set one only to change it.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql://opsib:opsib@localhost:55432/opsib` | service database |
| `TEST_DATABASE_URL` | `postgresql://opsib:opsib@localhost:55432/opsib_test` | used only by the tests (rebuilt each run) |
| `TENANT_ID` | `demo-retailer` | tenant written on every row (see §6) |
| `EVALUATION_TIME` | `2026-10-16T15:00:00+05:30` | frozen business "now"; must include a UTC offset |
| `SHORTFALL_TOLERANCE` | `0` | shortfall only if expected − received > tolerance (base units) |
| `WORKFORCE_MAX_SAMPLE_AGE_MINUTES` | `30` | older availability samples are not trusted for routing |
| `TASK_SLA_HOURS` | `24` | task due time = evaluation time + SLA |
| `LOG_LEVEL` | `INFO` | JSON logs to stderr (IDs and counts only, never names) |

The credentials in `docker-compose.yml` are local-development values, not secrets.

### Migrations

Plain numbered SQL files in [`migrations/`](migrations/) applied in order by `python -m app.cli migrate`,
each in its own transaction, recorded in `schema_migrations` (running it again applies nothing).
`001_init.sql` is the whole schema; every business invariant in it is a constraint.

### API at a glance

| | |
|---|---|
| `POST /imports/{dataset}` (`text/csv` body) · `GET /imports/{id}` | import with row-level report |
| `GET /data-quality` | rejected rows, open conflicts, unmapped references, not-evaluable lines |
| `POST /detection/runs?as_of=` | run the rule (default: frozen time) |
| `GET /exceptions` · `GET /exceptions/{id}` · `GET /exceptions/{id}/ai-summary` | findings with lineage |
| `GET /tasks?assignee_id=&location_id=&status=` · `GET /tasks/{id}` | work lists and history |
| `POST /tasks/{id}/acknowledge · start · block · unblock · complete · verify · reject · reassign` | lifecycle (`X-Actor-Id` header) |

`dataset` is one of `purchase_order_lines`, `goods_receipt_lines`, `product_aliases`, `location_aliases`,
`employees`, `workforce_status`. Errors always look like `{"error": {"code", "message", "details"}}`.

---

## 2. Tests

```bash
docker compose up -d db
pytest -q                                     # 151 tests, ~16 s; uses TEST_DATABASE_URL
```

Unit tests need no database; integration tests run against **real PostgreSQL** (the constraints,
locks and conflict handling we rely on don't exist in SQLite). The schema is rebuilt from the
migrations once per session and every table is emptied before each test.

| File | Tests | What it proves |
|---|---|---|
| `unit/test_rows.py` | 16 | typed validation, naive timestamps rejected, hash ignores formatting noise |
| `unit/test_resolver.py` | 9 | BOX → EA, per-system identity, unmapped / unit-mismatch / conflicted → issue |
| `unit/test_shortfall_rule.py` | 24 | **UOM, partial receipts, deadline boundary (−1 s / = / +1 s), cancelled lines**, duplicates, tolerance |
| `unit/test_findings.py` | 5 | stable IDs, evidence + mapping refs, neutral wording |
| `unit/test_eligibility.py` | 12 | **HR-101 off shift, HR-103 inactive, HR-401 fallback**, stale samples, supervisors |
| `unit/test_state_machine.py` | 14 | every valid / invalid transition |
| `unit/test_ai_summary.py` | 6 | invented IDs, schema violations, smuggled fields, timeouts |
| `integration/test_smoke.py` | 5 | config, health, migrations, error shape |
| `integration/test_schema.py` | 9 | the constraints themselves |
| `integration/test_imports.py` | 10 | counts, rejects, **conflicting duplicate**, **re-import**, **concurrent import**, crash rollback |
| `integration/test_mapping.py` | 4 | every supplied line maps; tenant scoping |
| `integration/test_scenarios.py` | 10 | **the six required outcomes from the fixture pack**, lineage, late receipt, reopen, routing |
| `integration/test_idempotency.py` | 2 | **import + detect twice = identical result**; concurrent detection |
| `integration/test_data_quality.py` | 6 | **unknown product**, conflicts, ambiguous PO line, unmapped employee store |
| `integration/test_exceptions_api.py` | 5 | exception detail down to the CSV line; AI boundary over HTTP |
| `integration/test_task_workflow.py` | 14 | **invalid transitions, repeated completion, audit history**, permissions, concurrent completion |

The brief's minimum tests map to the **bold** items. Latest run: [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md).

---

## 3. Design questions

**How does a second import or a concurrent worker avoid creating a different result? (Task A)**
Each source table's primary key is the record's source identity (`tenant_id, source_record_id`, or the
alias / employee / sample key). Rows are inserted with `INSERT … ON CONFLICT DO NOTHING` inside one
transaction per file, so Postgres itself serialises competing inserts on the same key: the second waits
for the first to commit, then sees the existing row. We then compare a hash of the *validated* values —
identical → `DUPLICATE` (no effect), different → `CONFLICT` (original kept, clash recorded once, line not
evaluated until resolved). There is no check-then-insert in Python anywhere. Exceptions and tasks are
protected the same way (`UNIQUE` business key; `UNIQUE (exception_id, task_type)`).

**The API committed task completion but the client timed out; what happens on retry? (Task C)**
`complete` locks the task row (`SELECT … FOR UPDATE`) and stores a hash of *action + actor + payload* on
the audit event. On retry the transition is no longer allowed (the task is `PENDING_VERIFICATION`), so the
service checks the latest human event: same action with the same hash → **200 with `"replayed": true`**
and nothing written; same action with different data → **409 `CONFLICTING_RETRY`**; anything else →
409 `INVALID_TRANSITION`. Two simultaneous completions → one 200, one 409, exactly one `COMPLETE` event.

---

## 4. Assumptions and trade-offs

The important decisions are below; [`project_walkthrough/`](project_walkthrough/) tells how each was
reached, task by task:

- **Mapping happens at evaluation time**, not import time — files can arrive in any order; a late alias
  makes a line evaluable on the next run.
- **"Passed" means strictly after the deadline**; a receipt at exactly `as_of` counts. Late receipts
  (after the deadline, before `as_of`) count — the goods arrived.
- **Only `POSTED` receipts count**; only `OPEN` PO lines are evaluated (`CANCELLED` skipped, any other
  status → not evaluable). Status words are stored as text and interpreted by the rule with allow-lists,
  so an unknown value fails safe *and* stays visible.
- **Zero tolerance by default**, configurable. Over-receipt is not a shortfall (no separate rule).
- **Anything we can't evaluate is a data-quality line, never a guessed exception**: unmapped product or
  store, unit mismatch, open source conflict, two source records for one PO line.
- **Two clocks:** business logic uses `as_of` (frozen by default, overridable per run); audit timestamps use
  the real clock.
- **Routing policy `routing/v1`:** receiving associate, then store manager; HRMS status beats the shift
  export; the observed status beats the scheduled shift; samples older than 30 minutes or from after
  `as_of` are not trusted; ties go to the lowest employee ID. Regional roles never get store work
  automatically but can verify and reassign within their region.
- **Four-eyes verification:** the assignee can't verify their own work; only a verified task resolves the
  exception. Manual reassignment runs the same eligibility check as automatic routing.
- **A late receipt closes the finding and cancels its open task; a shortfall that reappears reopens both.**

**Known limits (deliberate, with the upgrade path):**

- **Stale evidence for detection is not detected.** The explicit "can't evaluate" path exists: a line is
  `NOT_EVALUABLE` with a reason and is listed in `GET /data-quality`. It covers missing, unmapped,
  conflicting and ambiguous evidence, and availability samples are refused when stale. What it can't
  judge is whether the **ERP/WMS feeds themselves** are out of date, because the supplied exports carry no
  extraction timestamp. Production: record each feed's extract time on import, and mark a line
  `NOT_EVALUABLE / STALE_RECEIPT_FEED` when the latest WMS extract is older than the PO line's deadline.
  Not having seen a receipt is not the same as the receipt not existing.

- A changed value for an existing source ID (e.g. HRMS later sends HR-102 as `INACTIVE`, or a PO line
  becomes `CANCELLED`) is a **conflict for review**, not an update. Production: versioned master data /
  snapshot sequencing, plus a conflict-resolution endpoint (not built).
- Only the **latest** human action can be replayed; a stale retry of an older action gets 409 (safe, less
  friendly). Production: an idempotency-key table.
- Raw rows keep every value but not the original column order or file bytes (only the file's sha256).
  Production: keep uploads in object storage keyed by that hash.
- Imports run inside the request (files here are tiny). The import is already idempotent and records its
  own status, so moving it to a background worker is mechanical.
- One database connection per request; no pool.
- Lists (`/data-quality`, `/tasks`, `/exceptions`) are not paginated.

**Intentionally omitted:** frontend, background workers / queues, a live AI provider, over-receipt and
supplier-performance rules, financial impact, conflict-resolution UI.

---

## 5. What changes for production (documented here, **not implemented**)

- **Authentication and tenant-level authorisation:** identity and tenant from a verified token (OIDC/JWT)
  instead of the `X-Actor-Id` header and the `TENANT_ID` setting; Postgres row-level security as a backstop.
- **Secrets:** database and integration credentials from a secret manager, never in files.
- **Migrations:** run by CI/CD with review and a lock; down-migrations / expand-contract for changes.
- **Monitoring and alerting:** failed imports, open-conflict count, `NOT_EVALUABLE` rate per run, stale
  workforce data, tasks waiting for manual assignment, overdue tasks; structured logs shipped centrally.
- **Retention:** policy for raw rows and employee data (`display_name` is the only personal field; it is
  stored but never logged).
- **Integration credentials and ingestion:** pull from the retailer's systems via scoped credentials;
  large files via a worker (`202 Accepted` + poll `GET /imports/{id}`); connection pooling; pagination.

---

## 6. AI tooling disclosure

**Tools used:** Claude Code (model: Claude Opus 5.5), used throughout, inside the desktop app.

**What the AI did:** proposed the plan and the decision log, then wrote the application code, SQL
migration, tests, test data and documentation task by task (test first, then implementation), ran the
tests, and debugged failures — including the concurrency race in finding creation, which was reproduced
with a stress test before and after the fix (see `docs/ARCHITECTURE.md` §6).

**My part:**

I used Claude Code as a pair programmer for most of the implementation. I didn't write the
majority of the code by hand, and I don't want to pretend otherwise. How I actually worked:

- Before any code was written, I had it walk me through the brief and the fixture data, so I
  understood the traps (the duplicate rows, the BOX→EA conversion, HR-101 vs HR-103) and could check
  the outputs against what I expected.
- I set the structure: a written build plan and decision log first, then one task at a time
  (schema → import → mapping → rule → findings → routing → workflow → docs), each with tests written
  before the implementation.
- I reviewed each task's results and test runs before approving its commit, and I decided when to
  move on, what to push and where.
- The six scenario outcomes were worked out by hand from the fixture CSVs before any code existed,
  and the tests check exactly those numbers. I'm practising explaining the design (idempotency, the
  retry behaviour, the routing rules) in a mock review without AI help.

Where I'd be weakest without help is writing some of the SQL and concurrency handling from scratch.
I understand what it does and why, and that's what I'll walk through in the review.

**How the result was verified:** the full suite (151 tests) against real PostgreSQL; repeated runs of the
concurrency tests; the six scenarios traced by hand from the fixture CSVs; the API exercised live over
HTTP (captured in `docs/API_EXAMPLES.md`); and a fresh clone of the repository built and tested from
scratch (`docs/TEST_RESULTS.md`).
