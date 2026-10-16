# How we built it — the OPSiB receipt-shortfall service, start to finish

This is the story of the project: what problem it solves, how it was planned, how it was built task by
task (including what went wrong and how it was fixed), how one CSV row travels all the way to a task on
an employee's list, how to change it, and the questions a reviewer is likely to ask — with answers.

If you only have five minutes: read §1, §5 and §7.

| | |
|---|---|
| Size | ~2,000 lines of application code (26 modules) · ~1,440 lines of tests · **151 tests** |
| Stack | Python 3.14 · FastAPI · psycopg 3 (no ORM) · PostgreSQL 16 in Docker · pytest |
| History | one commit per task, Tasks 0–11, plus one fix in Task 0 — `git log --oneline` |

---

## 1. The problem, in plain words

A retailer runs four systems that don't agree with each other:

- the **ERP** creates purchase orders ("send 10 boxes of SKU-002 to the Mumbai store by 15 Oct"),
- the **WMS** (warehouse) records what physically arrived ("96 units received"),
- the **HRMS** knows who works where, in what role, and whether they're still employed,
- the **workforce tool** knows who is on shift right now.

Each system uses **its own IDs** for the same product (`ERP-BOX-12` vs `WMS-MAT-2002`) and the same store
(`MUM-015` vs `WH-MH-015` vs `ST-015`), and sometimes **its own units** (boxes vs units). The exports
contain traps on purpose: a row sent twice, a receipt split across two deliveries, a cancelled order, an
order whose deadline hasn't passed, a box-to-unit conversion, an inactive employee who appears on shift,
and an active employee who isn't.

OPSiB has to:

1. load those exports **safely** (bad rows visible, repeats harmless, nothing silently overwritten),
2. translate everything into **one consistent model** (one product ID, one store ID, one unit),
3. find order lines where **fewer units were received than ordered** after the deadline — without
   claiming *why* (there's no shipping data, so it could be the supplier, the carrier or the warehouse),
4. send each problem to the **right, eligible person** as a task, and
5. run that task through a **server-enforced workflow** with a full audit trail.

The required answers for the six purchase orders in the fixtures:

| PO | What happened | Expected result | Our result |
|---|---|---|---|
| PO-501 | 100 ordered; 60 + 32 received in two deliveries | shortfall 8 | ✅ shortfall 8 → task for HR-102 |
| PO-502 | 40 ordered; 40 received in time | none | ✅ |
| PO-503 | 50 ordered; 20 so far; deadline is tomorrow | not yet actionable | ✅ NOT_DUE |
| PO-504 | cancelled | none | ✅ CANCELLED |
| PO-505 | 10 **boxes** (×12) = 120 ordered; 96 received | shortfall 24 | ✅ shortfall 24 → task for HR-401 |
| PO-506 | 20 ordered; the same 10-unit receipt sent twice | shortfall 10 (not 0) | ✅ shortfall 10 → task for HR-102 |

---

## 2. The approach we chose (and why)

Before writing code we wrote three notes: an explainer of the brief, a step-by-step build plan, and a
decision log (100+ decisions by the end). Four ideas shaped everything:

**① Four layers, each rebuildable from the one below.**

```
raw CSV rows  →  source facts (keyed by each system's own ID)  →  findings (keyed by the business problem)  →  tasks + audit
```

Raw input is never edited. If an alias turns out to be wrong, fix the alias and re-run detection — the
evidence is still there.

**② The database is the referee for "did this already happen?".** Every "must not happen twice" rule is
a unique key or check constraint in [`migrations/001_init.sql`](../migrations/001_init.sql). Code never
does "check first, then insert" (two workers can both check, both see nothing, both insert).

**③ Pure decisions, thin I/O.** The three places where business judgment lives — the shortfall rule,
"who is eligible", and the task state machine — are plain functions: data in, decision out, no database,
no clock. They're easy to read, easy to test, and they never see a CSV column name, so another retailer's
export doesn't touch them.

**④ "Now" is a parameter.** All business logic uses `as_of` (frozen at `2026-10-16T15:00:00+05:30` by
default). The real clock is only used for audit timestamps. This made deadlines testable to the second —
and later let us "travel back in time" in tests.

Every task followed the same loop: **write the failing tests first → implement the minimum → run
everything → try it live → record decisions → commit.**

---

## 3. A tour of the repository

```
app/
  config.py, db.py, errors.py, log.py, audit.py, main.py, cli.py   plumbing
  ingestion/  rows.py · datasets.py · importer.py                  CSV → validated, idempotent facts
  mapping/    resolver.py                                          source IDs/units → canonical
  detection/  shortfall.py · findings.py · service.py · read.py    the rule → findings
  routing/    eligibility.py · service.py                          who may get the task
  workflows/  state_machine.py · tasks.py                          task lifecycle
  ai/         summary.py                                           optional, validated explanation
  api/        imports.py · detection.py · exceptions.py · tasks.py HTTP only
  quality.py                                                       GET /data-quality
migrations/001_init.sql                                            the schema = the invariants
fixtures/                                                          supplied CSVs, byte-identical
tests/unit (no DB) · tests/integration (real Postgres) · tests/data (our synthetic rows)
docs/  ARCHITECTURE · API_EXAMPLES (generated) · openapi.json · TEST_RESULTS
scripts/generate_api_examples.py
```

---

## 4. The build, task by task

### Task 0 — Scaffold (`4de86e2`, `30111b9`)
**Built:** git repo, Postgres 16 in Docker with a separate test database, settings with the frozen time
(a time without a UTC offset is refused — guessing a timezone would silently move every deadline),
connection helper + SQL migration runner, JSON logs, one error format, the app factory, the test harness
(schema rebuilt once per run, tables emptied before each test). **5 tests.**

**What went wrong:**
- Tests failed with *"password authentication failed"* although the password was right. Investigating the
  port showed a native **PostgreSQL 17 Windows service** already on 5432, and two other projects' Docker
  databases on 5433 and 5434. Fix: publish ours on **55432** — without touching the user's services.
- Starlette 1.8 warned that its test client should use `httpx2`; switched, warning gone.
- After the first commit, git warned about line endings. Checking showed the supplied CSVs use Windows
  line endings and git had silently stored them differently — a fresh clone would **not** have been
  byte-identical to what OPSiB sent. Fix: `.gitattributes` (`fixtures/** -text`), verified by comparing
  checksums of the committed files with the originals.

### Task 1 — The schema (`a77ae15`)
**Built:** 13 tables in four groups (raw input, source facts, findings, work). **9 tests** that prove the
*database itself* refuses bad data: the same source ID twice (per tenant), a zero quantity, a second
exception for the same business key, a second task for the same exception, an assigned task with no
assignee, an unknown task status.

**Key decision:** the PO table is keyed by the ERP's record ID (storage idempotency), but an exception is
keyed by *PO number + line* (the business problem) — so an ERP re-export under a new record ID can't
create a second case.

### Task 2 — Row models and hashing (`f3cd44f`)
**Built:** one typed Pydantic model per CSV, a registry saying which fields identify each record, and
`content_hash()`. **16 tests.**

**Key decisions:**
- The hash compares *meaning*, not text: `100` = `100.00`, `+05:30` = `Z` for the same instant.
  Otherwise a harmless formatting change in tomorrow's export would look like a conflict.
- Status words (`OPEN`, `POSTED`, `ACTIVE`) are stored as text; the *rules* interpret them with
  allow-lists. An unknown status then surfaces as a visible "can't evaluate" line instead of vanishing as
  a rejected row.
- `NaN` quantities are rejected — a `NaN` makes every comparison false, i.e. "no shortfall", silently.

### Task 3 — The idempotent importer (`39f3ad3`)
**Built:** `POST /imports/{dataset}`, `GET /imports/{id}`, the CLI loader, our synthetic test files.
Each row ends up `ACCEPTED`, `DUPLICATE`, `CONFLICT` or `REJECTED` (with reasons), and its raw text is
kept. **10 tests**, including the three that matter most:
- **re-import** → all duplicates, no new facts;
- **two imports at the same moment** → exactly 6 accepted + 8 duplicates however the race goes;
- **crash on row 3** → no facts, no raw rows, and the import visibly marked `FAILED` (the import record
  is committed *before* the rows; the rows commit together or not at all).

**Live check:** posted the conflicting file over HTTP — the conflict was recorded and the original kept.
That left an open conflict on PO-501 in the dev database, which would have hidden its shortfall later,
so the dev database was reset to the clean fixtures.

### Task 4 — The mapping resolver (`66f2c06`)
**Built:** IDs → canonical IDs, quantities → base units, explicit issues (`UNMAPPED_PRODUCT`,
`UNMAPPED_LOCATION`, `UOM_MISMATCH`, `CONFLICTED_MAPPING`). **13 tests**, including "all 12 supplied
PO and receipt lines map cleanly".

**Key decisions:** aliases are looked up by *(system, ID)* — a WMS code on an ERP line stays unmapped even
though the string exists in the alias file. Mapping happens at evaluation time, so files can arrive in
any order. A conflicting alias (is a box 10 or 12?) blocks evaluation rather than picking one.

### Task 5 — The shortfall rule (`4284617`)
**Built:** `evaluate_shortfall()` — about 40 lines, checked in a fixed order: blocked? → status →
mapping → deadline → which receipts count → compare with tolerance. **24 tests**, including the deadline
at −1 s / exactly / +1 s.

**Key decisions:** the deadline is strict (`as_of > deadline`) but a receipt at exactly `as_of` counts —
deliberately asymmetric. "Would this receipt count?" is checked before "is it mapped?", so a draft
receipt with a broken alias doesn't block the whole line. Running the rule on the real data at this
point already produced all six required outcomes — before any service existed.

### Task 6 — Findings, detection service, data-quality view (`b46cb16`)
**Built:** findings with a deterministic ID, evidence back to every source record and alias row, neutral
wording and a list of unknowns; `POST /detection/runs`; `GET /data-quality`. Re-running changes
nothing; a late receipt **closes** a finding; a shortfall that reappears **reopens** the same one.
**19 tests.**

**The best bug of the project:** the test that runs three detections at once failed with
`UniqueViolation` on `exceptions_pkey`. The `exceptions` table has *two* unique keys for one identity (the
ID is derived from the business key), and `ON CONFLICT (business key) DO NOTHING` only absorbs a clash on
the key it names — the losing insert could hit the other one first. A stress test (25 rounds × 6
simultaneous runs) showed **1 crash in 150** with the old clause and **0 in 150** with the fix (no
conflict target). Even when it crashed, the data was right — the whole run rolled back — so the symptom
was a spurious error, never a duplicate finding.

**Neat trick:** the reopen test runs detection "at 12:00", before the late receipt arrived — possible
only because "now" is a parameter.

### Task 7 — Routing (`a5cf6b1`)
**Built:** policy `routing/v1` — receiving associate, then store manager; same mapped store; `ACTIVE` in
HRMS; latest availability sample at or before `as_of` says `ON_SHIFT` and is at most 30 minutes old;
lowest employee ID breaks ties; nobody → the task waits for a human. Every decision is stored with each
candidate and why they were rejected. The pure task state machine was built here (planned for Task 8)
so even system steps — assign, cancel, reopen — go through it. **30 tests**; stress test again 0 / 150.

**Key observations:** at 12:00 the 14:50 shift samples "don't exist yet", so routing refuses to trust
them — the frozen-time rule applied to people. Task creation is a harmless no-op when the task already
exists, so the three findings created in Task 6 (before routing existed) got their tasks automatically.

### Task 8 — The task API (`d287039`)
**Built:** list/detail and one route per action, `X-Actor-Id` header (stand-in for authentication).
Only the assignee works a task; only a supervisor who is *not* the assignee verifies, rejects or
reassigns; a reassignment runs the router's eligibility check; completing needs a resolution code and
evidence. **14 tests**, including two simultaneous completions → one 200, one 409, one `COMPLETE` event.

**The retry design** (the brief's Task C question): lock the row; store a hash of action + actor + payload
on the audit event; an exact repeat of the last action → 200 `replayed: true`, nothing written; same
action with different data → 409 `CONFLICTING_RETRY`.

### Task 9 — Exception detail and the AI boundary (`1e9db5d`)
**Built:** `GET /exceptions/{id}` shows the source records down to the **CSV line number** and raw text;
`GET /exceptions/{id}/ai-summary` runs an optional provider. **11 tests.**

**Key decisions:** the provider sees only case facts and evidence IDs (no employee data); its output must
pass a schema that **forbids unknown fields** (it can't add `"verified": true` or a new quantity); every
record it cites must be in the evidence — one test shows the same AI answer accepted for PO-501 but
rejected for PO-505 because it cites a PO-501 receipt. Timeouts and errors degrade to `UNAVAILABLE`.
Disabled by default: no key, no cost.

### Task 10 — Documentation and proof (`45ed98b`)
**Built:** README, architecture note, OpenAPI export, test results, and API examples **captured from real
calls** by `scripts/generate_api_examples.py`. **Verified from scratch:** a fresh `git clone` with a new
virtualenv → 151 passed; the Docker image → 151 passed inside the container; `docker compose up` → API
served the findings.

### Task 11 — this document.

---

## 5. Follow one row: PO-505, end to end

Everything below is real data from a run of the service on the supplied fixtures.

**1. Import** — `POST /imports/purchase_order_lines` reads line 6 of `purchase_order_lines.csv`:

```
ERP-PO505-L1,PO-505,1,SUP-21,MUM-015,ERP-BOX-12,10,BOX,OPEN,2026-10-15T15:00:00+05:30
```

`importer.import_csv()` → `PurchaseOrderLineRow` validates it (quantity > 0, timestamp has an offset) →
`_store()` inserts into `purchase_order_lines` with `ON CONFLICT DO NOTHING` → **ACCEPTED**; the raw line
is kept in `import_rows` (row 6). The receipt `WMS-GRN505-A-L1` (96 EA, `POSTED`) arrives the same way
from line 6 of `goods_receipt_lines.csv`.

**2. Map** — `resolver.resolve_po_line()` at detection time:

| Source value | Alias row | Canonical |
|---|---|---|
| `MUM-015` (ERP) | `location_aliases ERP MUM-015` | `STORE-015`, `REGION-MH` |
| `ERP-BOX-12` (ERP), `BOX` | `product_aliases ERP ERP-BOX-12 BOX→EA ×12` | `SKU-002`, **120 EA** |
| `WH-MH-015` (WMS) | `location_aliases WMS WH-MH-015` | `STORE-015` |
| `WMS-MAT-2002` (WMS), `EA` | `product_aliases WMS WMS-MAT-2002 EA→EA ×1` | `SKU-002`, **96 EA** |

**3. Evaluate** — `shortfall.evaluate_shortfall()`: not blocked · `OPEN` · mapped · `as_of` 16 Oct 15:00
is after the 15 Oct 15:00 deadline · one posted receipt, same product and store · 120 − 96 = **24 > 0 →
SHORTFALL**.

**4. Record the finding** — `findings.build_finding()` + `upsert_finding()`:

```
exception_id  59185e06-69e5-5cc5-9c25-5a1767df3e16   (= uuid5(NAMESPACE_URL, "opsib:demo-retailer|RECEIPT_SHORTFALL|PO-505|1"),
business_key  RECEIPT_SHORTFALL|PO-505|1               so it is the same on every machine and every run)
explanation   "After the expected-receipt deadline (2026-10-15T15:00:00+05:30), posted receipts for PO-505
               line 1 total 96 EA against 120 EA ordered (10 BOX × 12). Receipt shortfall: 24 EA.
               The available data does not establish the cause."
evidence      po_source_ids [ERP-PO505-L1], receipt_source_ids [WMS-GRN505-A-L1],
              mapping_refs [4 alias rows, each with the import that delivered it], mapping/v1
unknowns      ASN quantity · carrier proof of delivery · physical recount
audit         FINDING_CREATED by system:detection
```

**5. Route** — `routing.load_candidates()` maps HRMS `ST-015` → `STORE-015`;
`eligibility.choose_assignee()`: no `receiving_associate` at STORE-015 → fall back to `store_manager` →
HR-401 is `ACTIVE`, `ON_SHIFT`, sample 10 minutes old → **chosen**.

**6. Create the task** — `tasks.create_task_for_exception()`:

```
task_id   b2d74f66-9490-5f19-99fd-c04fe9c0a7ae   (= uuid5(exception_id, "INVESTIGATE_RECEIPT_SHORTFALL"))
status    ASSIGNED → HR-401        due 2026-10-17 15:00 IST (as_of + 24 h)
audit     TASK_CREATED by system:detection · ASSIGN by system:router (with every candidate's check)
```

**7. Work it** — HR-401 acknowledges, starts, completes with a resolution code and evidence → the system
moves it to `PENDING_VERIFICATION`. HR-401 can't verify their own work (403); HR-501 (regional operations,
same region) verifies → task `VERIFIED`, exception `RESOLVED` with a resolution record naming who did the
work and who verified it.

**8. Inspect it** — `GET /exceptions/59185e06-…` returns all of the above, with `ERP-PO505-L1` and
`WMS-GRN505-A-L1` each traced to **CSV line 6** and their raw text.

---

## 6. How to change it (likely review exercises)

Always finish a change with `pytest -q`, and if behaviour changed, `python scripts/generate_api_examples.py`.

| Change | Where | Notes |
|---|---|---|
| Trust availability samples for 60 minutes instead of 30 | env `WORKFORCE_MAX_SAMPLE_AGE_MINUTES=60`, or the default in `app/config.py:32` | `test_eligibility.py` pins the boundary at 30 — update it |
| Allow a 2-unit tolerance | env `SHORTFALL_TOLERANCE=2` | applied in `app/detection/shortfall.py:79`; PO-501 (8) still raises |
| Add a resolution code | `RESOLUTION_CODES` in `app/workflows/tasks.py:25` | keep it neutral — no code should assert fault |
| Change who gets tasks first | `role_chain` in `app/routing/eligibility.py:17` | roles must have a store; regional roles need region matching first |
| Make the deadline inclusive | `app/detection/shortfall.py:56` (`<=` → `<`) | then fix the boundary test — and say why the brief suggests strict |
| A new retailer's PO export | a row model in `app/ingestion/rows.py` + an entry in `DATASETS` (`app/ingestion/datasets.py:27`) + alias rows for its `source_system` | the rule doesn't change: it only sees canonical data |
| A new exception rule (e.g. over-receipt) | a new pure evaluator next to `shortfall.py` with its own `exception_type` | findings, tasks, routing and audit are reused |
| Bump a version | `RULE_VERSION` (`shortfall.py:24`), `POLICY_VERSION` (`eligibility.py:12`) | stored on every finding / routing decision |

---

## 7. Review preparation — questions and answers

**The brief's design questions**

**Q: How do you stop a second import or a concurrent worker from producing a different result?**
Source identity is the primary key of each source table; inserts use `ON CONFLICT DO NOTHING` inside one
transaction per file, so Postgres serialises competing inserts on the same key. Then a content-hash
comparison decides duplicate (no effect) vs conflict (original kept, recorded once). Proven by the
concurrent-import test: 6 accepted + 8 duplicates, whatever the timing.

**Q: Completion was committed but the client timed out — what happens on retry?**
The row is locked; the exact same request is recognised by its hash on the last audit event → 200
`replayed: true`, nothing written. Different data → 409 `CONFLICTING_RETRY`. Two simultaneous completions →
one wins, one gets 409, one `COMPLETE` event.

**The brief's "be ready to discuss" list**

**Q: Why this canonical model, and how does it handle source IDs that change or collide?**
Facts keep their source IDs and are never rewritten; canonical IDs come from alias tables at evaluation
time. A *changed* ID for the same PO line shows up as two source records for one line → `AMBIGUOUS_PO_LINE`
(not double-counted). A *collision* (same ID, new content) → a recorded conflict; the line isn't evaluated
until it's resolved.

**Q: What if a receipt arrives late, a record is repeated, an import partially fails, or a mapping is ambiguous?**
Late receipt → next run closes the finding and cancels its open task (reopen if it comes back). Repeat →
duplicate, no effect. Partial failure → bad rows rejected with reasons, good rows load; a broken file fails
whole and visibly; a crash rolls back the rows and leaves a `FAILED` record. Ambiguous mapping →
`NOT_EVALUABLE` in `/data-quality`, never a guess.

**Q: How does your code stop two workers creating two tasks for the same issue?**
`UNIQUE (exception_id, task_type)` plus a deterministic task ID; only the transaction that actually
inserted the task routes it; the insert has no conflict target because two keys guard one identity — the
lesson from the Task 6 race.

**Q: Why a "receipt shortfall" rather than a proven supplier fault?**
We have the order and the receipt, nothing in between. The gap could be the supplier, the carrier or
receiving. The finding states the facts and lists the evidence needed to establish cause.

**Q: How would you add another retailer export without changing the rule engine?**
New row model + registry entry + its alias rows. The rule consumes canonical, base-unit lines only.

**Q: What must change before accepting production data from multiple tenants?**
Tenant and identity from an authenticated token (not a setting and a header); row-level security as a
backstop; per-tenant alias and policy config; secrets management; migrations in CI; monitoring of failed
imports, conflicts, not-evaluable rates and stale workforce data; retention for raw rows and personal data.

**Questions asked along the way (with model answers)**

**Q: Why rebuild the test schema once per session but empty the tables before each test?**
Rebuilding proves tests run on exactly the migration's DDL; emptying is cheap isolation. Rebuilding every
test is slow; emptying alone could hide an old schema.

**Q: What happens with `EVALUATION_TIME=2026-10-16T15:00:00` (no offset)?**
The service refuses to start. Assuming a zone would shift every deadline by hours (15:00 UTC is 20:30 IST).

**Q: Which single constraint stops two tasks for one exception?**
`UNIQUE (exception_id, task_type)` on `tasks`.

**Q: Why is the PO keyed by `source_record_id` but the exception by PO + line?**
Storage idempotency vs business identity — a re-keyed ERP export mustn't create a second case.

**Q: `100` vs `100.00` — duplicate or conflict?** Duplicate: the hash compares meaning, not text.

**Q: Why are status fields free text?** So an unknown value reaches the rule and becomes a visible
"can't evaluate" line instead of disappearing as a rejected row.

**Q: Why is HR-101 not assigned although their scheduled shift covers 15:00?** The *observed* status says
`OFF_SHIFT`; observations beat schedules.

**Q: Why is HR-103 not assigned although the shift export says `ON_SHIFT`?** HRMS says `INACTIVE`;
employment status beats the shift export.

**Q: If the product alias file arrives a day after the PO file, what happens to PO-505?** Nothing breaks:
the line is `NOT_EVALUABLE / UNMAPPED_PRODUCT` until the alias arrives, then the next run evaluates it.

**Q: Why is "unmapped" `NOT_EVALUABLE`, not "no shortfall"?** "No shortfall" would silently hide a real
shortage behind a data problem.

**Known limits to mention before you're asked:** a changed value for an existing ID is a conflict, not an
update (master-data versioning is the production fix); only the latest action can be replayed (an
idempotency-key table is the fix); raw rows don't keep column order or file bytes (keep uploads in object
storage); imports run in the request; no connection pool; no pagination.

---

## 8. What we would do next

In order of value: authentication + tenant from token; a conflict-resolution endpoint (accept / keep, with
audit) so master-data updates flow; idempotency keys; background imports with `202 Accepted`; monitoring
and alerts on the data-quality signals; pagination; a real AI provider behind the existing validation.

---

## 9. How AI was used

The project was built with **Claude Code** (Claude Opus 5.5) working with the candidate task by task:
Claude proposed the plan and decisions, wrote code, tests and documentation, ran the tests, and debugged
failures (the Postgres port clash, the line-ending problem, the `ON CONFLICT` race). Each task was
reviewed and committed only with the candidate's go-ahead. The candidate's own account of what they
reviewed and changed belongs in the README's *AI tooling disclosure* section.
