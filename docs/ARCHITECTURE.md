# Architecture

A small FastAPI + PostgreSQL service. One idea runs through everything: **business invariants live in the
database as constraints, decisions live in pure functions, and everything in between is thin, explicit I/O.**

## 1. Layers

```
 CSV ──► ① RAW INPUT ───────────► ② SOURCE FACTS ──────────► ③ FINDINGS ───────────► ④ WORK
         import_runs                purchase_order_lines       detection_runs            tasks
         import_rows (raw JSON,     goods_receipt_lines        exceptions                audit_events
          outcome, reasons)         product_aliases               │
                                    location_aliases              └──► optional AI summary
                                    employees, workforce_status         (explains, never decides)
                                    source_conflicts
                 │                          │
                 └──── rejected rows, conflicts, unmapped refs, not-evaluable lines ──► GET /data-quality
```

Each layer can be rebuilt from the one before it. Raw rows are never modified; source facts are keyed
by their **source identity** and never overwritten; findings are keyed by **business identity**; work
(tasks) is separate from the business case (exceptions).

## 2. Module map

| Package | Responsibility | Pure? |
|---|---|---|
| `app/ingestion/rows.py` | One typed Pydantic model per CSV; `content_hash()` compares meaning, not spelling | yes |
| `app/ingestion/datasets.py` | Registry: dataset → row model + source-identity fields | yes |
| `app/ingestion/importer.py` | Validate every row, store raw input, idempotent insert, duplicate vs conflict, report | I/O |
| `app/mapping/resolver.py` | Source IDs → canonical product/location; quantities → base unit; explicit issues | yes (+ one loader) |
| `app/detection/shortfall.py` | **The rule**: resolved PO line + receipts + `as_of` → `Evaluation` | yes |
| `app/detection/findings.py` | Evaluation → finding (stable ID, evidence, neutral text); upsert/close | builder pure |
| `app/detection/service.py` | One detection run in one transaction; routes tasks for new/reopened findings | I/O |
| `app/routing/eligibility.py` | **Who may be assigned**: role chain, store, ACTIVE, fresh ON_SHIFT sample; supervisors | yes |
| `app/routing/service.py` | Roster + latest availability sample ≤ `as_of` | I/O |
| `app/workflows/state_machine.py` | **The only table of allowed task transitions** | yes |
| `app/workflows/tasks.py` | Create / cancel / reopen tasks; `perform_action()` for people (locks, permissions, retries) | I/O |
| `app/ai/summary.py` | Evidence package → provider → strictly validated summary | yes |
| `app/quality.py`, `app/audit.py`, `app/detection/read.py` | Data-quality view, audit trail, exception reads | I/O |
| `app/api/*` | HTTP only: parse input, call the service, return JSON | — |

The three pure cores (rule, eligibility, state machine) are unit-tested without a database or a clock.

## 3. Data model (keys are the point)

| Table | Primary key / unique key | Why |
|---|---|---|
| `import_runs` | `import_id` | every attempt, even a re-import, is visible |
| `import_rows` | `(import_id, row_number)` | raw line + outcome + reasons; `row_number` = file line |
| `purchase_order_lines` | `(tenant_id, source_record_id)` | idempotent storage of ERP facts |
| `goods_receipt_lines` | `(tenant_id, source_record_id)` | a repeated receipt can't be counted twice |
| `product_aliases` | `(tenant_id, source_system, external_item_id)` | identity is per system |
| `location_aliases` | `(tenant_id, source_system, external_location_id)` | ERP / WMS / HRMS codes → one store |
| `employees` | `(tenant_id, source_employee_id)` | HRMS roster; store optional for regional roles |
| `workforce_status` | `(tenant_id, source_employee_id, observed_at)` | availability is a time series |
| `source_conflicts` | `UNIQUE (tenant_id, dataset, source_key, incoming_hash)` | a clash is recorded once |
| `detection_runs` | `run_id` | every line's outcome per run, incl. NOT_EVALUABLE |
| `exceptions` | PK `exception_id = uuid5(tenant ∣ business_key)`, `UNIQUE (tenant_id, exception_type, business_key)` | one case per business problem, same ID forever |
| `tasks` | PK `task_id = uuid5(exception_id, task_type)`, `UNIQUE (exception_id, task_type)`, `CHECK (status IN ('CREATED','CANCELLED') OR assignee IS NOT NULL)` | no duplicate work; nobody works an unassigned task |
| `audit_events` | `event_id` (append-only) | who did what, when, from → to, with `request_hash` |

Full DDL: [`migrations/001_init.sql`](../migrations/001_init.sql).

### Record grain — what one row means

Getting the grain wrong is how shortfalls get double-counted or hidden, so every table has exactly one
meaning per row, and the rule never counts CSV rows or matches on product names.

| Table | One row is… | Not… | Consequence in the fixtures |
|---|---|---|---|
| `import_rows` | one physical line of one uploaded file | a business fact | re-importing a file adds new raw rows but no new facts |
| `purchase_order_lines` | one ERP PO-line **source record** (`source_record_id`) | one CSV row | the repeated `ERP-PO506-L1` line is stored once |
| `goods_receipt_lines` | one WMS receipt-line **source record** | one CSV row, one GRN, or one PO line | PO-501 has two (`GRN-501-A`, `GRN-501-B`) and they are summed; the repeated `WMS-GRN506-A-L1` is one fact, so PO-506 is short 10, not 0 |
| `product_aliases` / `location_aliases` | one mapping for one `(source_system, external_id)` | one product or one store | `ERP-SKU-001` and `WMS-BRC-001` are two rows for one `SKU-001` |
| `employees` | one person, by HRMS ID | one person per store | HR-501 has a region and no store |
| `workforce_status` | one availability **observation** `(employee, observed_at)` | one row per employee | a later sample (e.g. HR-401 `OFF_SHIFT` at 14:55) supersedes an earlier one for routing |
| `exceptions` | one business case per `(tenant, rule type, PO number + line)` | one detection run | re-runs update or close the same case |
| `tasks` | one unit of work per `(exception, task type)` | one assignment | reassigning changes the assignee, not the task |
| `audit_events` | one change, appended | current state | the history is never rewritten |

How detection keeps that grain: PO facts are grouped by business line `(po_number, line_number)`. If two
different source records claim one business line, the line is `NOT_EVALUABLE / AMBIGUOUS_PO_LINE` rather
than summed. Receipt facts are linked by `(po_number, po_line_number)`, de-duplicated by
`source_record_id`, converted to the base unit through their own alias, and only then summed. Tests:
`test_partial_receipts_are_summed`, `test_repeated_receipt_counts_once`, `test_six_required_scenarios`
(PO-506 = 10), and `test_two_source_records_for_one_po_line_are_ambiguous`.

## 4. Flows

**Ingestion** (`POST /imports/{dataset}` or `python -m app.cli load-fixtures`)
1. `import_runs` row inserted and **committed first** (status `PROCESSING`).
2. Header check (missing columns → `FAILED`, HTTP 422, nothing applied).
3. In **one transaction**: for each row → save raw → validate → `INSERT … ON CONFLICT DO NOTHING` →
   if nothing inserted, compare content hashes → `DUPLICATE` or `CONFLICT` (original kept, clash recorded).
4. Counts + `COMPLETED`. Any crash → whole row transaction rolls back, run marked `FAILED`.

**Mapping** happens at evaluation time, not import time, so files can arrive in any order.
`(source_system, external_id)` → alias → canonical ID; quantity × the alias's factor, and only if the
row's unit equals the alias's source unit. Missing / conflicted alias or unit mismatch → an *issue*.

**Detection** (`POST /detection/runs?as_of=`) — one transaction, PO lines in business-key order:

```
blocked? (open conflict / two records for one PO line) → NOT_EVALUABLE
status CANCELLED → CANCELLED;  status ≠ OPEN → NOT_EVALUABLE
mapping issue → NOT_EVALUABLE
as_of ≤ deadline → NOT_DUE
distinct receipts: unposted / after as_of → ignored (listed); unmapped or elsewhere → NOT_EVALUABLE
variance = expected − Σ accepted;  > tolerance → SHORTFALL else NO_SHORTFALL
```

SHORTFALL → upsert finding (CREATED / UPDATED / UNCHANGED / REOPENED) → ensure a task exists and route it
(reopened → task reopened and re-routed). NO_SHORTFALL on an open finding → finding CLOSED, open task
CANCELLED. Nothing changed → nothing written.

**Routing** (policy `routing/v1`): for the finding's store, walk `receiving_associate → store_manager`;
eligible = mapped home store matches, HRMS `ACTIVE`, latest sample ≤ `as_of` is `ON_SHIFT` and ≤ 30 min old.
Lowest employee ID wins ties. Nobody → task stays `CREATED` (`ROUTING_NEEDS_MANUAL_ASSIGNMENT`). Every
decision is stored with all candidates and their reasons.

**Workflow** (`POST /tasks/{id}/<action>`, `X-Actor-Id`):

```
CREATED ─assign─► ASSIGNED ─acknowledge─► ACKNOWLEDGED ─start─► IN_PROGRESS ⇄ BLOCKED (block/unblock)
IN_PROGRESS ─complete─► COMPLETED ─(system)─► PENDING_VERIFICATION ─verify─► VERIFIED  (exception RESOLVED)
PENDING_VERIFICATION ─reject─► IN_PROGRESS          any open state ─reassign─► ASSIGNED
any open state ─cancel (system)─► CANCELLED ─reopen (system)─► CREATED
```

Assignee-only: acknowledge, start, block, unblock, complete. Supervisor (store manager of that store or
regional operations of its region) **who is not the assignee**: verify, reject, reassign. Reassignment
runs the same eligibility check as automatic routing.

## 5. Authority — which system wins

| Field | Authority |
|---|---|
| Ordered quantity, UOM, PO status, deadline, supplier | ERP (`purchase_order_lines`) |
| Accepted quantity, receipt time, posting status | WMS (`goods_receipt_lines`) |
| Product identity and unit conversion | `product_aliases` (never names / text similarity) |
| Store identity and region | `location_aliases` |
| Employment status, role, home store | HRMS (`employees`) — beats the shift export |
| On / off shift now | latest fresh `workforce_status` sample — beats the scheduled shift |

## 6. Idempotency, transactions and concurrency

| Risk | What stops it | Evidence |
|---|---|---|
| Same file imported twice | PK on source identity + `ON CONFLICT DO NOTHING` | `test_reimport_is_all_duplicates…` |
| Two imports at once | Postgres serialises inserts on the same key | `test_concurrent_imports…` (6 accepted + 8 duplicates, any interleaving) |
| Crash mid-import | run committed first; row work in one transaction | `test_crash_mid_import…` |
| Two detection runs at once | unique business key + PK; **untargeted** `ON CONFLICT` (both keys guard one identity); same lock order | `test_concurrent_detection…`; stress 25 × 6 runs → 0 failures |
| Two tasks for one case | `UNIQUE (exception_id, task_type)`; only the inserting transaction routes | same tests: exactly 3 tasks, 3 `ASSIGN` events |
| Client retries `complete` after a timeout | `SELECT … FOR UPDATE`; `request_hash` on the audit event: same request → 200 `replayed`, different → 409 | `test_full_lifecycle…`, `test_two_simultaneous_completions…` |
| Re-running detection rewrites history | compare before write; audit only real changes | `test_importing_and_detecting_twice_changes_nothing` |

Connections are autocommit; every write path is an explicit `with conn.transaction():` block, so each
boundary is visible in the code it protects.

## 7. Extension points

- **Another retailer's export:** add a row model + `DatasetSpec` (and alias rows for its `source_system`).
  The rule only sees canonical, base-unit data, so `shortfall.py` doesn't change.
- **Another exception rule:** a new pure evaluator + its own `exception_type` / business key; the finding,
  task and audit machinery is shared.
- **Another routing policy:** a new `RoutingPolicy` (role chain, freshness) and version string.
- **A real AI provider:** implement `SummaryProvider.summarize(package)`; validation stays in front of it.

## 8. Multi-tenancy (what exists vs what's needed)

Exists: `tenant_id` on every table and inside every unique key; every query filters by it; the tenant is
`TENANT_ID` from config. Needed before real multi-tenant data: tenant taken from an authenticated token,
Postgres row-level security as a backstop, per-tenant alias/policy configuration.
