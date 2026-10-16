# Derived test data

The supplied fixtures in `fixtures/` are never modified. Every file here is **synthetic data we
added** to exercise paths the fixtures don't cover. Each is used only by tests.

| File | What it contains | What it proves |
|---|---|---|
| `po_bad_rows.csv` | 4 PO rows: `ordered_qty = 0` (line 2), timestamp without UTC offset (line 3), blank `po_number` (line 4), one valid row (line 5) | Bad rows are rejected **with readable reasons**, the raw text is kept, and good rows in the same file still load (partial success). |
| `po_missing_column.csv` | PO file without the `expected_receipt_by` column | A structurally wrong file fails the whole import visibly (`FAILED`, HTTP 422 `BAD_CSV`) and applies nothing. |
| `po_conflicting_duplicate.csv` | `ERP-PO501-L1` again, but ordered **90** instead of 100 | Same source ID with different content is a `CONFLICT`: the original is kept, a conflict is recorded once, never silently overwritten. |
| `po_unknown_product.csv` | New line PO-507 for `ERP-SKU-999`, which has no product alias; deadline passed | An unmapped product is a **data-quality issue** (`NOT_EVALUABLE / UNMAPPED_PRODUCT`, listed in `GET /data-quality`), never a business exception and never matched by name. |
| `workforce_hr401_off.csv` | A *later* availability sample for HR-401 (14:55, `OFF_SHIFT`) | Availability is a time series: the latest sample at or before `as_of` wins, so the STORE-015 fallback has nobody eligible and the PO-505 task waits for manual assignment instead of going to an off-shift manager. |

Some tests also build tiny one-row CSVs inline (as bytes in the test file, so the data sits next to the assertion):
a late receipt `WMS-GRN501-C-L1` (8 EA for PO-501) that closes a finding, a re-keyed PO line `ERP-PO502-L1-V2`
(ambiguous identity), a conflicting product alias (`ERP-BOX-12` ×10) and a new employee at an unmapped store `ST-999`.
