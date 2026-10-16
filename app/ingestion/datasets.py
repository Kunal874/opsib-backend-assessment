"""Registry of importable datasets: the row model that validates it, and its source identity.

The dataset name doubles as the URL segment and the table name. Adding another retailer's export
means adding an entry here (plus its row model) — the detector never sees CSV columns.
"""
from dataclasses import dataclass

from pydantic import BaseModel

from app.ingestion import rows


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    row_model: type[BaseModel]
    key_fields: tuple[str, ...]  # source identity, without tenant_id — the table's primary key (D-14)

    @property
    def required_columns(self) -> tuple[str, ...]:
        return tuple(self.row_model.model_fields)

    def source_key(self, row: BaseModel) -> str:
        return "|".join(str(rows.canonical_value(getattr(row, field))) for field in self.key_fields)


DATASETS: dict[str, DatasetSpec] = {
    spec.name: spec
    for spec in (
        DatasetSpec("purchase_order_lines", rows.PurchaseOrderLineRow, ("source_record_id",)),
        DatasetSpec("goods_receipt_lines", rows.GoodsReceiptLineRow, ("source_record_id",)),
        DatasetSpec("product_aliases", rows.ProductAliasRow, ("source_system", "external_item_id")),
        DatasetSpec("location_aliases", rows.LocationAliasRow, ("source_system", "external_location_id")),
        DatasetSpec("employees", rows.EmployeeRow, ("source_employee_id",)),
        DatasetSpec("workforce_status", rows.WorkforceStatusRow, ("source_employee_id", "observed_at")),
    )
}
