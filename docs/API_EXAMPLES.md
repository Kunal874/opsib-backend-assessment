# API examples

Every request and response below was **captured from a real run** against the service with the supplied fixtures, by `scripts/generate_api_examples.py` (not hand-written). Long lists are shortened with `… N more`. Interactive docs: `http://localhost:8000/docs`; machine-readable: [`openapi.json`](openapi.json).

Errors always have the same shape: `{"error": {"code", "message", "details"}}`.

Write calls on tasks need `X-Actor-Id: <employee id>` — a stand-in for real authentication.

## 1. Imports

### Import a dataset (the PO file has one identical duplicate row)

The other five files are imported the same way (`/imports/<file name without .csv>`), or all at once with `python -m app.cli load-fixtures fixtures`.

```bash
curl -X POST "http://localhost:8000/imports/purchase_order_lines?source_filename=purchase_order_lines.csv" \
  -H "Content-Type: text/csv" \
  --data-binary @fixtures/purchase_order_lines.csv
```

**201**

```json
{
  "import_id": "63729222-b550-4e03-9941-d88236d06982",
  "dataset": "purchase_order_lines",
  "status": "COMPLETED",
  "source_filename": "purchase_order_lines.csv",
  "counts": {
    "total": 7,
    "accepted": 6,
    "duplicate": 1,
    "conflict": 0,
    "rejected": 0
  },
  "error": null,
  "started_at": "2026-10-16T17:59:47.691933Z",
  "finished_at": "2026-10-16T17:59:47.694782Z",
  "rows": [
    {
      "row_number": 8,
      "source_key": "ERP-PO506-L1",
      "outcome": "DUPLICATE",
      "errors": []
    }
  ]
}
```

### Read an import report

```bash
curl -X GET "http://localhost:8000/imports/63729222-b550-4e03-9941-d88236d06982"
```

**200**

```json
{
  "import_id": "63729222-b550-4e03-9941-d88236d06982",
  "status": "COMPLETED",
  "counts": {
    "total": 7,
    "accepted": 6,
    "duplicate": 1,
    "conflict": 0,
    "rejected": 0
  },
  "rows": [
    {
      "row_number": 8,
      "source_key": "ERP-PO506-L1",
      "outcome": "DUPLICATE",
      "errors": []
    }
  ]
}
```

### Import the same file again — nothing changes

```bash
curl -X POST "http://localhost:8000/imports/purchase_order_lines" \
  -H "Content-Type: text/csv" \
  --data-binary @fixtures/purchase_order_lines.csv
```

**201**

```json
{
  "status": "COMPLETED",
  "counts": {
    "total": 7,
    "accepted": 0,
    "duplicate": 7,
    "conflict": 0,
    "rejected": 0
  }
}
```

### Rows that fail validation are rejected with reasons; good rows still load

```bash
curl -X POST "http://localhost:8000/imports/purchase_order_lines" \
  -H "Content-Type: text/csv" \
  --data-binary @tests/data/po_bad_rows.csv
```

**201**

```json
{
  "status": "COMPLETED",
  "counts": {
    "total": 4,
    "accepted": 1,
    "duplicate": 0,
    "conflict": 0,
    "rejected": 3
  },
  "rows": [
    {
      "row_number": 2,
      "source_key": null,
      "outcome": "REJECTED",
      "errors": [
        "ordered_qty: Input should be greater than 0"
      ]
    },
    {
      "row_number": 3,
      "source_key": null,
      "outcome": "REJECTED",
      "errors": [
        "expected_receipt_by: Input should have timezone info"
      ]
    },
    {
      "row_number": 4,
      "source_key": null,
      "outcome": "REJECTED",
      "errors": [
        "po_number: String should have at least 1 character"
      ]
    }
  ]
}
```

### A structurally wrong file fails the whole import, visibly

```bash
curl -X POST "http://localhost:8000/imports/purchase_order_lines" \
  -H "Content-Type: text/csv" \
  --data-binary @tests/data/po_missing_column.csv
```

**422**

```json
{
  "error": {
    "code": "BAD_CSV",
    "message": "Missing required columns: expected_receipt_by"
  }
}
```

### Same source ID with different content is a conflict — the original is kept

```bash
curl -X POST "http://localhost:8000/imports/purchase_order_lines" \
  -H "Content-Type: text/csv" \
  --data-binary @tests/data/po_conflicting_duplicate.csv
```

**201**

```json
{
  "status": "COMPLETED",
  "counts": {
    "total": 1,
    "accepted": 0,
    "duplicate": 0,
    "conflict": 1,
    "rejected": 0
  },
  "rows": [
    {
      "row_number": 2,
      "source_key": "ERP-PO501-L1",
      "outcome": "CONFLICT",
      "errors": []
    }
  ]
}
```

## 2. Detection

_The examples below start again from the six supplied files only._

### Run detection (as_of defaults to the frozen 2026-10-16T15:00:00+05:30)

```bash
curl -X POST "http://localhost:8000/detection/runs"
```

**201**

```json
{
  "as_of": "2026-10-16T15:00:00+05:30",
  "rule_version": "receipt_shortfall/v1",
  "counts": {
    "SHORTFALL": 3,
    "NO_SHORTFALL": 1,
    "NOT_DUE": 1,
    "CANCELLED": 1,
    "NOT_EVALUABLE": 0
  },
  "exceptions": {
    "created": 3,
    "updated": 0,
    "unchanged": 0,
    "reopened": 0,
    "closed": 0
  },
  "tasks_created": 3,
  "lines": [
    {
      "po_number": "PO-501",
      "outcome": "SHORTFALL",
      "reason": null,
      "expected_qty": "100",
      "received_qty": "92",
      "variance_qty": "8"
    },
    {
      "po_number": "PO-502",
      "outcome": "NO_SHORTFALL",
      "reason": null,
      "expected_qty": "40",
      "received_qty": "40",
      "variance_qty": "0"
    },
    {
      "po_number": "PO-503",
      "outcome": "NOT_DUE",
      "reason": null,
      "expected_qty": "50",
      "received_qty": null,
      "variance_qty": null
    },
    {
      "po_number": "PO-504",
      "outcome": "CANCELLED",
      "reason": null,
      "expected_qty": null,
      "received_qty": null,
      "variance_qty": null
    },
    {
      "po_number": "PO-505",
      "outcome": "SHORTFALL",
      "reason": null,
      "expected_qty": "120",
      "received_qty": "96",
      "variance_qty": "24"
    },
    {
      "po_number": "PO-506",
      "outcome": "SHORTFALL",
      "reason": null,
      "expected_qty": "20",
      "received_qty": "10",
      "variance_qty": "10"
    }
  ]
}
```

### Run it again — idempotent

```bash
curl -X POST "http://localhost:8000/detection/runs"
```

**201**

```json
{
  "exceptions": {
    "created": 0,
    "updated": 0,
    "unchanged": 3,
    "reopened": 0,
    "closed": 0
  },
  "tasks_created": 0
}
```

### Override the evaluation time (`+` must be sent as `%2B`)

PO-503's deadline (17 Oct 18:00) has passed by 18 Oct, so its 30 EA gap becomes a shortfall.

```bash
curl -X POST "http://localhost:8000/detection/runs?as_of=2026-10-18T09:00:00%2B05:30"
```

**201**

```json
{
  "lines": [
    {
      "po_number": "PO-503",
      "outcome": "SHORTFALL",
      "variance_qty": "30"
    }
  ]
}
```

### Data quality (clean fixtures → nothing to report)

```bash
curl -X GET "http://localhost:8000/data-quality"
```

**200**

```json
{
  "rejected_rows": [],
  "open_conflicts": [],
  "unmapped_references": [],
  "not_evaluable_lines": [],
  "latest_detection_run": {
    "run_id": "948eb2a3-b9ea-4089-a147-7673da57ebbf",
    "as_of": "2026-10-16T09:30:00Z"
  }
}
```

## 3. Exceptions

### List exceptions

```bash
curl -X GET "http://localhost:8000/exceptions"
```

**200**

```json
[
  {
    "exception_id": "525d5277-2208-5129-9ecf-16b419da0749",
    "exception_type": "RECEIPT_SHORTFALL",
    "business_key": "RECEIPT_SHORTFALL|PO-501|1",
    "status": "OPEN",
    "rule_version": "receipt_shortfall/v1",
    "po_number": "PO-501",
    "po_line_number": 1,
    "canonical_product_id": "SKU-001",
    "canonical_location_id": "STORE-014",
    "expected_qty": "100",
    "received_qty": "92",
    "variance_qty": "8",
    "uom": "EA",
    "evaluated_as_of": "2026-10-16T09:30:00Z",
    "first_detected_at": "2026-10-16T17:59:48.622930Z",
    "last_detected_at": "2026-10-16T17:59:48.622930Z"
  },
  {
    "exception_id": "59185e06-69e5-5cc5-9c25-5a1767df3e16",
    "exception_type": "RECEIPT_SHORTFALL",
    "business_key": "RECEIPT_SHORTFALL|PO-505|1",
    "status": "OPEN",
    "rule_version": "receipt_shortfall/v1",
    "po_number": "PO-505",
    "po_line_number": 1,
    "canonical_product_id": "SKU-002",
    "canonical_location_id": "STORE-015",
    "expected_qty": "120",
    "received_qty": "96",
    "variance_qty": "24",
    "uom": "EA",
    "evaluated_as_of": "2026-10-16T09:30:00Z",
    "first_detected_at": "2026-10-16T17:59:48.622930Z",
    "last_detected_at": "2026-10-16T17:59:48.622930Z"
  },
  {
    "exception_id": "bf527dc7-9197-55f0-88fe-b566cfc64591",
    "exception_type": "RECEIPT_SHORTFALL",
    "business_key": "RECEIPT_SHORTFALL|PO-506|1",
    "status": "OPEN",
    "rule_version": "receipt_shortfall/v1",
    "po_number": "PO-506",
    "po_line_number": 1,
    "canonical_product_id": "SKU-003",
    "canonical_location_id": "STORE-014",
    "expected_qty": "20",
    "received_qty": "10",
    "variance_qty": "10",
    "uom": "EA",
    "evaluated_as_of": "2026-10-16T09:30:00Z",
    "first_detected_at": "2026-10-16T17:59:48.622930Z",
    "last_detected_at": "2026-10-16T17:59:48.622930Z"
  }
]
```

### One exception with evidence, source records (down to the CSV line), tasks and history

```bash
curl -X GET "http://localhost:8000/exceptions/59185e06-69e5-5cc5-9c25-5a1767df3e16"
```

**200**

```json
{
  "exception_id": "59185e06-69e5-5cc5-9c25-5a1767df3e16",
  "business_key": "RECEIPT_SHORTFALL|PO-505|1",
  "status": "OPEN",
  "rule_version": "receipt_shortfall/v1",
  "canonical_product_id": "SKU-002",
  "canonical_location_id": "STORE-015",
  "expected_qty": "120",
  "received_qty": "96",
  "variance_qty": "24",
  "uom": "EA",
  "evidence": {
    "mapping_refs": [
      "location_alias:ERP:MUM-015@fff13ab0-1f9b-415b-872c-ec836213a89d",
      "location_alias:WMS:WH-MH-015@fff13ab0-1f9b-415b-872c-ec836213a89d",
      "product_alias:ERP:ERP-BOX-12@f2586bfa-e60c-4826-8e5c-a5d164546079",
      "product_alias:WMS:WMS-MAT-2002@f2586bfa-e60c-4826-8e5c-a5d164546079"
    ],
    "po_source_ids": [
      "ERP-PO505-L1"
    ],
    "mapping_version": "mapping/v1",
    "ignored_receipts": [],
    "receipt_source_ids": [
      "WMS-GRN505-A-L1"
    ]
  },
  "explanation": "After the expected-receipt deadline (2026-10-15T15:00:00+05:30), posted receipts for PO-505 line 1 total 96 EA against 120 EA ordered (10 BOX \u00d7 12). Receipt sho\u2026",
  "unknowns": [
    "Supplier dispatch/ASN quantity was not provided; cannot tell whether fewer units were shipped.",
    "Carrier proof of delivery was not provided; cannot tell whether units were lost in transit.",
    "No physical recount at the receiving location; cannot rule out a receiving or counting error."
  ],
  "source_records": {
    "purchase_order_lines": [
      {
        "source_record_id": "ERP-PO505-L1",
        "import_id": "5d2adf61-a8f1-42c5-8395-0ca54b5dce94",
        "source_row_number": 6,
        "raw_row": {
          "status": "OPEN",
          "item_code": "ERP-BOX-12",
          "po_number": "PO-505",
          "line_number": "1",
          "ordered_qty": "10",
          "ordered_uom": "BOX",
          "location_code": "MUM-015",
          "supplier_code": "SUP-21",
          "source_record_id": "ERP-PO505-L1",
          "expected_receipt_by": "2026-10-15T15:00:00+05:30"
        }
      }
    ],
    "goods_receipt_lines": [
      {
        "source_record_id": "WMS-GRN505-A-L1",
        "import_id": "35092c38-2605-4bd7-93e8-743e0e048519",
        "source_row_number": 6,
        "raw_row": {
          "uom": "EA",
          "po_number": "PO-505",
          "product_ref": "WMS-MAT-2002",
          "received_at": "2026-10-15T10:00:00+05:30",
          "accepted_qty": "96",
          "receipt_line": "1",
          "location_code": "WH-MH-015",
          "po_line_number": "1",
          "posting_status": "POSTED",
          "receipt_number": "GRN-505-A",
          "source_record_id": "WMS-GRN505-A-L1"
        }
      }
    ]
  },
  "tasks": [
    {
      "task_id": "b2d74f66-9490-5f19-99fd-c04fe9c0a7ae",
      "task_type": "INVESTIGATE_RECEIPT_SHORTFALL",
      "status": "ASSIGNED",
      "assignee_employee_id": "HR-401",
      "due_at": "2026-10-17T09:30:00Z"
    }
  ],
  "history": [
    "FINDING_CREATED"
  ]
}
```

### Optional AI summary (disabled by default)

```bash
curl -X GET "http://localhost:8000/exceptions/59185e06-69e5-5cc5-9c25-5a1767df3e16/ai-summary"
```

**200**

```json
{
  "exception_id": "59185e06-69e5-5cc5-9c25-5a1767df3e16",
  "status": "UNAVAILABLE",
  "summary": null,
  "error": "provider failed: ProviderUnavailable: AI summaries are disabled"
}
```

## 4. Tasks

### Tasks for an assignee

```bash
curl -X GET "http://localhost:8000/tasks?assignee_id=HR-102"
```

**200**

```json
[
  {
    "task_id": "33900d91-af4e-5060-843d-42c101a8b1ac",
    "po_number": "PO-506",
    "status": "ASSIGNED",
    "assignee_employee_id": "HR-102",
    "canonical_location_id": "STORE-014",
    "due_at": "2026-10-17T09:30:00Z",
    "variance_qty": "10"
  },
  {
    "task_id": "84aed22c-f615-5777-a3a8-15661b620420",
    "po_number": "PO-501",
    "status": "ASSIGNED",
    "assignee_employee_id": "HR-102",
    "canonical_location_id": "STORE-014",
    "due_at": "2026-10-17T09:30:00Z",
    "variance_qty": "8"
  }
]
```

### Skipping a state is refused

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/complete" \
  -H "X-Actor-Id: HR-102" \
  -H "Content-Type: application/json" \
  -d '{"resolution_code": "SHORTFALL_CONFIRMED", "evidence_ref": "RECOUNT-0042"}'
```

**409**

```json
{
  "error": {
    "code": "INVALID_TRANSITION",
    "message": "Cannot 'complete' a task that is ASSIGNED",
    "details": {
      "status": "ASSIGNED",
      "action": "complete"
    }
  }
}
```

### Only the assignee can work the task

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/acknowledge" \
  -H "X-Actor-Id: HR-101"
```

**403**

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Only the assignee can acknowledge this task",
    "details": {}
  }
}
```

### Acknowledge

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/acknowledge" \
  -H "X-Actor-Id: HR-102"
```

**200**

```json
{
  "task": {
    "status": "ACKNOWLEDGED",
    "assignee_employee_id": "HR-102"
  },
  "replayed": false
}
```

### Start

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/start" \
  -H "X-Actor-Id: HR-102"
```

**200**

```json
{
  "task": {
    "status": "IN_PROGRESS",
    "assignee_employee_id": "HR-102"
  },
  "replayed": false
}
```

### Complete (goes straight to PENDING_VERIFICATION)

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/complete" \
  -H "X-Actor-Id: HR-102" \
  -H "Content-Type: application/json" \
  -d '{"resolution_code": "SHORTFALL_CONFIRMED", "evidence_ref": "RECOUNT-0042"}'
```

**200**

```json
{
  "task": {
    "status": "PENDING_VERIFICATION",
    "assignee_employee_id": "HR-102"
  },
  "replayed": false
}
```

### The client timed out and retries the same request → same answer, nothing written twice

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/complete" \
  -H "X-Actor-Id: HR-102" \
  -H "Content-Type: application/json" \
  -d '{"resolution_code": "SHORTFALL_CONFIRMED", "evidence_ref": "RECOUNT-0042"}'
```

**200**

```json
{
  "task": {
    "status": "PENDING_VERIFICATION",
    "assignee_employee_id": "HR-102"
  },
  "replayed": true
}
```

### A retry with different data is refused

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/complete" \
  -H "X-Actor-Id: HR-102" \
  -H "Content-Type: application/json" \
  -d '{"resolution_code": "SHORTFALL_CONFIRMED", "evidence_ref": "OTHER-1"}'
```

**409**

```json
{
  "error": {
    "code": "CONFLICTING_RETRY",
    "message": "'complete' was already applied with different data; refusing to overwrite it",
    "details": {}
  }
}
```

### The assignee cannot verify their own work

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/verify" \
  -H "X-Actor-Id: HR-102"
```

**403**

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "HR-102 does not supervise STORE-014",
    "details": {}
  }
}
```

### The store manager verifies → the exception is RESOLVED

```bash
curl -X POST "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420/verify" \
  -H "X-Actor-Id: HR-201"
```

**200**

```json
{
  "task": {
    "status": "VERIFIED",
    "exception": {
      "exception_id": "525d5277-2208-5129-9ecf-16b419da0749",
      "business_key": "RECEIPT_SHORTFALL|PO-501|1",
      "status": "RESOLVED",
      "variance_qty": "8",
      "uom": "EA"
    }
  },
  "replayed": false
}
```

### Full audit history of the task

```bash
curl -X GET "http://localhost:8000/tasks/84aed22c-f615-5777-a3a8-15661b620420"
```

**200**

```json
{
  "history": [
    {
      "event_type": "TASK_CREATED",
      "actor": "system:detection",
      "from_status": null,
      "to_status": "CREATED"
    },
    {
      "event_type": "ASSIGN",
      "actor": "system:router",
      "from_status": "CREATED",
      "to_status": "ASSIGNED"
    },
    {
      "event_type": "ACKNOWLEDGE",
      "actor": "HR-102",
      "from_status": "ASSIGNED",
      "to_status": "ACKNOWLEDGED"
    },
    {
      "event_type": "START",
      "actor": "HR-102",
      "from_status": "ACKNOWLEDGED",
      "to_status": "IN_PROGRESS"
    },
    {
      "event_type": "COMPLETE",
      "actor": "HR-102",
      "from_status": "IN_PROGRESS",
      "to_status": "COMPLETED"
    },
    {
      "event_type": "SUBMIT_FOR_VERIFICATION",
      "actor": "system:workflow",
      "from_status": "COMPLETED",
      "to_status": "PENDING_VERIFICATION"
    },
    {
      "event_type": "VERIFY",
      "actor": "HR-201",
      "from_status": "PENDING_VERIFICATION",
      "to_status": "VERIFIED"
    }
  ]
}
```

### Reassigning to an ineligible person is refused

```bash
curl -X POST "http://localhost:8000/tasks/33900d91-af4e-5060-843d-42c101a8b1ac/reassign" \
  -H "X-Actor-Id: HR-201" \
  -H "Content-Type: application/json" \
  -d '{"new_assignee_id": "HR-101", "reason": "cover for the afternoon"}'
```

**422**

```json
{
  "error": {
    "code": "INELIGIBLE_ASSIGNEE",
    "message": "HR-101 is not eligible right now",
    "details": {
      "reasons": [
        "OFF_SHIFT"
      ]
    }
  }
}
```

### Reassign with a reason

```bash
curl -X POST "http://localhost:8000/tasks/33900d91-af4e-5060-843d-42c101a8b1ac/reassign" \
  -H "X-Actor-Id: HR-201" \
  -H "Content-Type: application/json" \
  -d '{"new_assignee_id": "HR-201", "reason": "associate busy"}'
```

**200**

```json
{
  "task": {
    "status": "ASSIGNED",
    "assignee_employee_id": "HR-201"
  },
  "replayed": false
}
```
