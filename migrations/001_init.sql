-- OPSiB assessment schema.
-- Business invariants live here as constraints, so no code path can bypass them (D-14, D-29, D-34).
-- Every table carries tenant_id and every unique key includes it (D-44).

-- ---------------------------------------------------------------- raw input layer

CREATE TABLE import_runs (
    import_id        UUID PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    dataset          TEXT NOT NULL,
    source_filename  TEXT,
    file_sha256      TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('PROCESSING', 'COMPLETED', 'FAILED')),
    total_rows       INT  NOT NULL DEFAULT 0,
    accepted_rows    INT  NOT NULL DEFAULT 0,
    duplicate_rows   INT  NOT NULL DEFAULT 0,
    conflict_rows    INT  NOT NULL DEFAULT 0,
    rejected_rows    INT  NOT NULL DEFAULT 0,
    error            TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ
);

-- Every CSV line exactly as received, with its outcome (D-11). Never updated.
CREATE TABLE import_rows (
    import_id   UUID NOT NULL REFERENCES import_runs (import_id),
    row_number  INT  NOT NULL,                 -- line number in the file; the header is line 1
    source_key  TEXT,                          -- NULL when the row could not be validated
    outcome     TEXT NOT NULL CHECK (outcome IN ('ACCEPTED', 'DUPLICATE', 'CONFLICT', 'REJECTED')),
    errors      JSONB NOT NULL DEFAULT '[]',
    raw         JSONB NOT NULL,
    PRIMARY KEY (import_id, row_number)
);
CREATE INDEX import_rows_by_source_key ON import_rows (source_key);

-- ---------------------------------------------------------------- source facts (PK = source identity)

CREATE TABLE product_aliases (
    tenant_id            TEXT NOT NULL,
    source_system        TEXT NOT NULL,
    external_item_id     TEXT NOT NULL,
    canonical_product_id TEXT NOT NULL,
    source_uom           TEXT NOT NULL,
    base_uom             TEXT NOT NULL,
    to_base_factor       NUMERIC NOT NULL CHECK (to_base_factor > 0),
    content_hash         TEXT NOT NULL,
    import_id            UUID NOT NULL REFERENCES import_runs (import_id),
    PRIMARY KEY (tenant_id, source_system, external_item_id)
);

CREATE TABLE location_aliases (
    tenant_id             TEXT NOT NULL,
    source_system         TEXT NOT NULL,
    external_location_id  TEXT NOT NULL,
    canonical_location_id TEXT NOT NULL,
    region_id             TEXT NOT NULL,
    content_hash          TEXT NOT NULL,
    import_id             UUID NOT NULL REFERENCES import_runs (import_id),
    PRIMARY KEY (tenant_id, source_system, external_location_id)
);

CREATE TABLE purchase_order_lines (
    tenant_id           TEXT NOT NULL,
    source_system       TEXT NOT NULL DEFAULT 'ERP',
    source_record_id    TEXT NOT NULL,
    po_number           TEXT NOT NULL,
    line_number         INT  NOT NULL,
    supplier_code       TEXT NOT NULL,
    location_code       TEXT NOT NULL,
    item_code           TEXT NOT NULL,
    ordered_qty         NUMERIC NOT NULL CHECK (ordered_qty > 0),
    ordered_uom         TEXT NOT NULL,
    status              TEXT NOT NULL,
    expected_receipt_by TIMESTAMPTZ NOT NULL,
    content_hash        TEXT NOT NULL,
    import_id           UUID NOT NULL REFERENCES import_runs (import_id),
    PRIMARY KEY (tenant_id, source_record_id)
);
CREATE INDEX purchase_order_lines_business_key ON purchase_order_lines (tenant_id, po_number, line_number);

CREATE TABLE goods_receipt_lines (
    tenant_id        TEXT NOT NULL,
    source_system    TEXT NOT NULL DEFAULT 'WMS',
    source_record_id TEXT NOT NULL,
    receipt_number   TEXT NOT NULL,
    receipt_line     INT  NOT NULL,
    po_number        TEXT NOT NULL,
    po_line_number   INT  NOT NULL,
    location_code    TEXT NOT NULL,
    product_ref      TEXT NOT NULL,
    accepted_qty     NUMERIC NOT NULL CHECK (accepted_qty >= 0),
    uom              TEXT NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL,
    posting_status   TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    import_id        UUID NOT NULL REFERENCES import_runs (import_id),
    PRIMARY KEY (tenant_id, source_record_id)
);
CREATE INDEX goods_receipt_lines_po_link ON goods_receipt_lines (tenant_id, po_number, po_line_number);

CREATE TABLE employees (
    tenant_id           TEXT NOT NULL,
    source_system       TEXT NOT NULL DEFAULT 'HRMS',
    source_employee_id  TEXT NOT NULL,
    display_name        TEXT NOT NULL,         -- personal data: stored, never logged (D-46)
    employment_status   TEXT NOT NULL,
    role                TEXT NOT NULL,
    location_code       TEXT,                  -- NULL for region-scoped roles (D-37)
    manager_employee_id TEXT,
    region_id           TEXT,
    content_hash        TEXT NOT NULL,
    import_id           UUID NOT NULL REFERENCES import_runs (import_id),
    PRIMARY KEY (tenant_id, source_employee_id)
);

-- Availability is a time series of observations, not one row per employee (D-17).
CREATE TABLE workforce_status (
    tenant_id           TEXT NOT NULL,
    source_system       TEXT NOT NULL DEFAULT 'WFM',
    source_employee_id  TEXT NOT NULL,
    availability_status TEXT NOT NULL,
    shift_start         TIMESTAMPTZ,
    shift_end           TIMESTAMPTZ,
    observed_at         TIMESTAMPTZ NOT NULL,
    content_hash        TEXT NOT NULL,
    import_id           UUID NOT NULL REFERENCES import_runs (import_id),
    PRIMARY KEY (tenant_id, source_employee_id, observed_at)
);

-- Same source key, different content: the original is kept and the clash recorded for review (D-16).
CREATE TABLE source_conflicts (
    conflict_id    BIGSERIAL PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    dataset        TEXT NOT NULL,
    source_key     TEXT NOT NULL,
    existing_hash  TEXT NOT NULL,
    incoming_hash  TEXT NOT NULL,
    incoming_row   JSONB NOT NULL,
    import_id      UUID NOT NULL REFERENCES import_runs (import_id),
    row_number     INT  NOT NULL,
    status         TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, dataset, source_key, incoming_hash)   -- re-importing the same clash records it once
);

-- ---------------------------------------------------------------- findings

-- Every run keeps the outcome of every PO line, including NOT_EVALUABLE ones (D-31).
CREATE TABLE detection_runs (
    run_id        UUID PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    rule_version  TEXT NOT NULL,
    as_of         TIMESTAMPTZ NOT NULL,
    summary       JSONB NOT NULL,
    line_outcomes JSONB NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE exceptions (
    exception_id          UUID PRIMARY KEY,    -- uuid5(tenant | business_key): stable across re-runs (D-29)
    tenant_id             TEXT NOT NULL,
    exception_type        TEXT NOT NULL,
    business_key          TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED', 'RESOLVED')),
    rule_version          TEXT NOT NULL,
    po_number             TEXT NOT NULL,
    po_line_number        INT  NOT NULL,
    canonical_product_id  TEXT NOT NULL,
    canonical_location_id TEXT NOT NULL,
    expected_qty          NUMERIC NOT NULL,
    received_qty          NUMERIC NOT NULL,
    variance_qty          NUMERIC NOT NULL,
    uom                   TEXT NOT NULL,
    evidence              JSONB NOT NULL,
    explanation           TEXT NOT NULL,
    unknowns              JSONB NOT NULL,
    evaluated_as_of       TIMESTAMPTZ NOT NULL,  -- business time the rule used
    first_detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- wall clock, audit only
    last_detected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolution            JSONB,
    UNIQUE (tenant_id, exception_type, business_key)         -- one case per business problem
);

-- ---------------------------------------------------------------- work

CREATE TABLE tasks (
    task_id               UUID PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    exception_id          UUID NOT NULL REFERENCES exceptions (exception_id),
    task_type             TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('CREATED', 'ASSIGNED', 'ACKNOWLEDGED', 'IN_PROGRESS',
                                                         'BLOCKED', 'COMPLETED', 'PENDING_VERIFICATION', 'VERIFIED',
                                                         'CANCELLED')),
    canonical_location_id TEXT NOT NULL,
    region_id             TEXT,
    assignee_employee_id  TEXT,
    due_at                TIMESTAMPTZ NOT NULL,
    resolution_code       TEXT,
    evidence_ref          TEXT,
    notes                 TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (exception_id, task_type),                                              -- no duplicate work
    CHECK (status IN ('CREATED', 'CANCELLED') OR assignee_employee_id IS NOT NULL)  -- nobody works an unassigned task
);
CREATE INDEX tasks_by_assignee ON tasks (tenant_id, assignee_employee_id, status);
CREATE INDEX tasks_by_location ON tasks (tenant_id, canonical_location_id, status);

-- Append-only history for exceptions, tasks (incl. every assignment) and conflicts (D-43).
CREATE TABLE audit_events (
    event_id     BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    entity_type  TEXT NOT NULL CHECK (entity_type IN ('exception', 'task', 'conflict')),
    entity_id    TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    actor        TEXT NOT NULL,                -- employee ID or 'system:<component>'
    from_status  TEXT,
    to_status    TEXT,
    data         JSONB NOT NULL DEFAULT '{}',
    request_hash TEXT,                         -- lets a retried request be recognised (D-41)
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_by_entity ON audit_events (tenant_id, entity_type, entity_id, event_id);
