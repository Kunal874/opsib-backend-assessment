# Derived test data

The supplied fixtures in `fixtures/` are never modified. Every file here is **synthetic data we
added** to exercise paths the fixtures don't cover. Each is used only by tests.

| File | What it contains | What it proves |
|---|---|---|
| `po_bad_rows.csv` | 4 PO rows: `ordered_qty = 0` (line 2), timestamp without UTC offset (line 3), blank `po_number` (line 4), one valid row (line 5) | Bad rows are rejected **with readable reasons**, the raw text is kept, and good rows in the same file still load (partial success). |
| `po_missing_column.csv` | PO file without the `expected_receipt_by` column | A structurally wrong file fails the whole import visibly (`FAILED`, HTTP 422 `BAD_CSV`) and applies nothing. |
| `po_conflicting_duplicate.csv` | `ERP-PO501-L1` again, but ordered **90** instead of 100 | Same source ID with different content is a `CONFLICT`: the original is kept, a conflict is recorded once, never silently overwritten. |
