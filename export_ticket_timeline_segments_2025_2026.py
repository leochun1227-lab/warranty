from __future__ import annotations

import argparse
import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


ROOT = Path(__file__).resolve().parent
DEFAULT_ANALYSIS_TICKET_JS = ROOT / "outputs" / "analysis_ticket_csv.js"
DEFAULT_FAILURE_TIMING_CSV = ROOT / "outputs" / "analysis_ticket_failure_timing.csv"
DEFAULT_PARTS_CLASSIFIED_CSV = ROOT / "outputs" / "parts_classified.csv"
DEFAULT_REPAIRERS_JSON = ROOT / "outputs" / "repairers_2026" / "repairers_2026_data.json"
DEFAULT_OUTPUT = ROOT / "generated_exports" / "ticket_timeline_segments_2025_2026.xlsx"

APPROVED_STATUS_TEXTS = {
    "sales order approved",
    "dispatch parts",
    "partially picked",
    "repair in progress",
    "repairer invoiced received",
    "repairer invoiced processed",
    "approved claims closed",
    "approved claims closed (closed)",
}
UNAPPROVED_STATUS_TEXTS = {
    "unapproved claims closed",
    "unapproved claims closed (closed)",
}


@dataclass(frozen=True)
class Paths:
    analysis_ticket_js: Path
    failure_timing_csv: Path
    parts_classified_csv: Path
    repairers_json: Path
    output: Path


def clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", clean(value)).strip()


def normalize_po(value: Any) -> str:
    text = clean(value)
    if not text or text == "#":
        return ""
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return text.split(".", 1)[0]
    return text


def normalize_vehicle_key(value: Any) -> str:
    text = clean(value)
    if not text or text == "#":
        return ""
    return text.upper()


def parse_amount(value: Any) -> float | None:
    text = clean(value)
    if not text or text == "#":
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    text = clean(value)
    if not text or text == "#":
        return None
    text = text.replace(" AUSACT", "")
    for fmt in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return parse_date(value)


def iso_or_blank(value: Any) -> str:
    normalized = to_date(value)
    if normalized is not None:
        return normalized.isoformat()
    return ""


def day_diff(later: Any, earlier: Any) -> int | None:
    later_date = to_date(later)
    earlier_date = to_date(earlier)
    if later_date is None or earlier_date is None:
        return None
    return (later_date - earlier_date).days


def mean_or_blank(series: Iterable[float | int | None]) -> float | None:
    values = [float(value) for value in series if value is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def median_or_blank(series: Iterable[float | int | None]) -> float | None:
    values = sorted(float(value) for value in series if value is not None)
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return round(values[mid], 2)
    return round((values[mid - 1] + values[mid]) / 2, 2)


def read_embedded_csv_text(js_path: Path) -> str:
    text = js_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'globalThis\.ANALYSIS_TICKET_CSV_TEXT\s*=\s*"(.*)";\s*$', text, re.S)
    if not match:
        raise ValueError(f"Could not find embedded CSV text in {js_path}")
    return match.group(1).encode("utf-8").decode("unicode_escape")


def ticket_claim_scope(ticket_type: str) -> str:
    text = normalize_spaces(ticket_type).lower()
    if "pre delivery" in text:
        return "Pre Delivery"
    if "in field" in text:
        return "In Field"
    return "Other"


def is_unapproved_status(status_text: str) -> bool:
    text = normalize_spaces(status_text).lower()
    return text in UNAPPROVED_STATUS_TEXTS or "unapproved" in text


def is_approved_like_status(status_text: str) -> bool:
    text = normalize_spaces(status_text).lower()
    return text in APPROVED_STATUS_TEXTS or "approved" in text or "repairer invoiced" in text or "repair in progress" in text


def load_analysis_ticket_base(js_path: Path) -> pd.DataFrame:
    raw_csv = read_embedded_csv_text(js_path)
    reader = csv.reader(io.StringIO(raw_csv))
    next(reader, None)  # header row is position-based and intentionally skipped

    rows: list[dict[str, Any]] = []
    for raw_row in reader:
        row = list(raw_row) + [""] * max(0, 34 - len(raw_row))
        created_date = parse_date(row[14])
        approved_date = parse_date(row[19])
        purchase_date = parse_date(row[18])
        changed_date = parse_date(row[32])
        posting_date = parse_date(row[33])
        status_text = clean(row[25])
        ticket_number = clean(row[1] or row[3])
        ticket_key = clean(row[0] or row[2])
        po = normalize_po(row[12])

        rows.append(
            {
                "ticket_number": ticket_number,
                "ticket_key": ticket_key,
                "ticket_id_text": clean(row[2]),
                "ticket_type": clean(row[4]),
                "claim_scope": ticket_claim_scope(row[4]),
                "agent_name": clean(row[5]),
                "agent_id": clean(row[6]),
                "serial_id": clean(row[7]),
                "chassis_number": clean(row[8]),
                "account_name": clean(row[9]),
                "account_id": clean(row[10]),
                "erp_free_order_id": clean(row[11]),
                "po": po,
                "erp_service_order_id": clean(row[13]),
                "created_on": clean(row[14]),
                "created_date": created_date,
                "dealer_code": clean(row[15]),
                "dealer_name": clean(row[16]),
                "country_region": clean(row[17]),
                "date_of_purchase": clean(row[18]),
                "purchase_date": purchase_date,
                "claim_approved_on": clean(row[19]),
                "approved_date": approved_date,
                "postal_code": clean(row[20]),
                "registered_product": clean(row[21]),
                "registered_product_code": clean(row[22]),
                "product": clean(row[23]),
                "product_code": clean(row[24]),
                "status_text": status_text,
                "service_technician": clean(row[26]),
                "service_technician_id": clean(row[27]),
                "claim_total_amount": parse_amount(row[28]),
                "factory_parts_claim_total_amount": parse_amount(row[29]),
                "labour_hours_total_amount": parse_amount(row[30]),
                "repairer_parts_claim_total_amount": parse_amount(row[31]),
                "changed_on": clean(row[32]),
                "changed_date": changed_date,
                "posting_date": clean(row[33]),
                "posting_date_value": posting_date,
                "is_unapproved": is_unapproved_status(status_text),
                "is_approved_like_status": is_approved_like_status(status_text),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["created_year"] = out["created_date"].map(lambda value: value.year if value else None)
    return out


def load_failure_timing_rows(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "ticket_number",
                "failure_ticket_created_on",
                "failure_ticket_created_date",
                "failure_pgi_date",
                "failure_vehicle_delivery_date",
                "failure_delivery_date",
                "failure_timing_source",
                "failure_days",
                "failure_matched_vehicle_key",
                "failure_matched_serial",
                "failure_matched_chassis",
                "failure_matched_sales_order",
                "failure_ticket_sales_order",
                "failure_ticket_serial_id",
                "failure_ticket_chassis_number",
            ]
        )

    work = df.copy()
    work["ticket_number"] = work["Ticket ID"].map(clean)
    work["failure_ticket_created_on"] = work["Created On"].map(clean)
    work["failure_ticket_created_date"] = work.apply(
        lambda row: parse_date(row.get("Created On ISO")) or parse_date(row.get("Created On")),
        axis=1,
    )
    work["failure_pgi_date"] = work["PGI Date"].map(clean)
    work["failure_vehicle_delivery_date"] = work["Vehicle Delivery Date"].map(clean)
    work["failure_delivery_date"] = work["failure_vehicle_delivery_date"].map(parse_date)
    work["failure_timing_source"] = work["Timing Date Source"].map(clean)
    work["failure_days"] = pd.to_numeric(work["Failure Days"].map(clean), errors="coerce")
    work["failure_days"] = work.apply(
        lambda row: (
            row["failure_days"]
            if pd.notna(row["failure_days"])
            else day_diff(row["failure_ticket_created_date"], row["failure_delivery_date"])
        ),
        axis=1,
    )
    work["failure_matched_vehicle_key"] = work["Matched Vehicle Key"].map(clean)
    work["failure_matched_serial"] = work["Matched Serial"].map(clean)
    work["failure_matched_chassis"] = work["Matched Chassis"].map(clean)
    work["failure_matched_sales_order"] = work["Matched Sales Order"].map(clean)
    work["failure_ticket_sales_order"] = work["Ticket Sales Order"].map(clean)
    work["failure_ticket_serial_id"] = work["Ticket Serial ID"].map(clean)
    work["failure_ticket_chassis_number"] = work["Ticket Chassis Number"].map(clean)
    work = work.sort_values(["ticket_number", "failure_ticket_created_on"], na_position="last")
    return work.drop_duplicates(subset=["ticket_number"], keep="first").reset_index(drop=True)


def load_parts_ticket_summary(csv_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    if df.empty:
        empty = pd.DataFrame(
            columns=[
                "ticket_number",
                "parts_po",
                "parts_sales_order",
                "so_created_date",
                "first_issue_date",
                "parts_item_rows",
                "issued_item_rows",
            ]
        )
        return empty, empty.rename(columns={"ticket_number": "po"})

    work = df.copy()
    work["ticket_number"] = work["Ticket ID"].map(clean)
    work["parts_po"] = work["ERP Purchase Order"].map(normalize_po)
    work["parts_sales_order"] = work["Sales Order"].map(clean)
    work["so_created_date"] = work["SO Created Date"].map(parse_date)
    work["first_issue_date"] = work["First Issue Date"].map(parse_date)
    work["is_issued_item"] = work["first_issue_date"].notna()

    by_ticket = (
        work.groupby("ticket_number", dropna=False)
        .agg(
            parts_po=("parts_po", lambda values: next((value for value in values if clean(value)), "")),
            parts_sales_order=("parts_sales_order", lambda values: next((value for value in values if clean(value)), "")),
            so_created_date=("so_created_date", "min"),
            first_issue_date=("first_issue_date", "min"),
            parts_item_rows=("ticket_number", "size"),
            issued_item_rows=("is_issued_item", "sum"),
        )
        .reset_index()
    )

    by_po = (
        work[work["parts_po"] != ""]
        .groupby("parts_po", dropna=False)
        .agg(
            parts_sales_order=("parts_sales_order", lambda values: next((value for value in values if clean(value)), "")),
            so_created_date=("so_created_date", "min"),
            first_issue_date=("first_issue_date", "min"),
            parts_item_rows=("parts_po", "size"),
            issued_item_rows=("is_issued_item", "sum"),
        )
        .reset_index()
        .rename(columns={"parts_po": "po"})
    )

    return by_ticket, by_po


def load_repairer_invoice_rows(json_path: Path) -> pd.DataFrame:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rows = payload.get("details", []) if isinstance(payload, dict) else []

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        po = normalize_po(row.get("ERP Purchase Order ID"))
        if not po:
            continue
        invoice_date = parse_date(row.get("invoice_date"))
        normalized_rows.append(
            {
                "po": po,
                "invoice_status": clean(row.get("invoice_status")),
                "invoice_number": clean(row.get("invoice_number")),
                "invoice_date": invoice_date,
                "invoice_date_text": clean(row.get("invoice_date")),
                "repairer_status_text": clean(row.get("Status")),
                "repairer_ticket_key": clean(row.get("Ticket ID") or row.get("TicketID")),
                "repairer_name_from_repairs": clean(row.get("repairer_name") or row.get("Service Technician")),
            }
        )

    if not normalized_rows:
        return pd.DataFrame(
            columns=[
                "po",
                "invoice_status",
                "invoice_number",
                "invoice_date",
                "invoice_date_text",
                "repairer_status_text",
                "repairer_ticket_key",
                "repairer_name_from_repairs",
            ]
        )

    df = pd.DataFrame(normalized_rows)
    df = df.sort_values(["po", "invoice_date"], na_position="last")
    return df.drop_duplicates(subset=["po"], keep="last").reset_index(drop=True)


def build_qualifying_universe(base_df: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    work = base_df.copy()
    year_mask = work["created_year"].between(start_year, end_year, inclusive="both")
    qualify_mask = year_mask & work["po"].ne("") & work["approved_date"].notna() & ~work["is_unapproved"]
    out = work[qualify_mask].copy()
    out["approval_days"] = out.apply(lambda row: day_diff(row["approved_date"], row["created_date"]), axis=1)
    out["approval_days_valid"] = out["approval_days"].map(lambda value: value is not None and value >= 0)
    return out.sort_values(["created_date", "ticket_number", "ticket_key"], na_position="last").reset_index(drop=True)


def build_failure_segment(
    qualifying_df: pd.DataFrame,
    failure_df: pd.DataFrame,
    anomalies: list[dict[str, Any]],
) -> pd.DataFrame:
    merged = qualifying_df.merge(failure_df, on="ticket_number", how="left")
    merged["failure_vehicle_key"] = merged.apply(
        lambda row: next(
            (
                candidate
                for candidate in [
                    clean(row.get("failure_matched_vehicle_key")),
                    clean(row.get("failure_matched_chassis")),
                    clean(row.get("failure_matched_serial")),
                    clean(row.get("failure_matched_sales_order")),
                    clean(row.get("chassis_number")),
                    clean(row.get("serial_id")),
                    clean(row.get("failure_ticket_chassis_number")),
                    clean(row.get("failure_ticket_serial_id")),
                    clean(row.get("failure_ticket_sales_order")),
                ]
                if candidate
            ),
            "",
        ),
        axis=1,
    )
    merged["failure_days_valid"] = merged["failure_days"].notna() & merged["failure_days"].map(lambda value: value >= 0)
    merged["has_failure_timing_source"] = merged["failure_timing_source"].map(clean).ne("")
    merged["has_failure_delivery_basis"] = merged["has_failure_timing_source"] & merged["failure_timing_source"].ne("missing")

    for _, row in merged[merged["failure_timing_source"].eq("")].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Failure Timing",
                reason="missing failure timing row",
                row=row,
            )
        )

    for _, row in merged[merged["failure_timing_source"].eq("missing")].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Failure Timing",
                reason="failure timing source is missing",
                row=row,
            )
        )

    for _, row in merged[merged["has_failure_delivery_basis"] & merged["failure_vehicle_key"].map(clean).eq("")].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Failure Timing",
                reason="missing matched vehicle key",
                row=row,
            )
        )

    for _, row in merged[merged["has_failure_delivery_basis"] & ~merged["failure_days_valid"]].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Failure Timing",
                reason="missing or negative failure days",
                row=row,
            )
        )

    valid = merged[
        merged["has_failure_delivery_basis"] & merged["failure_days_valid"] & merged["failure_vehicle_key"].map(clean).ne("")
    ].copy()
    if valid.empty:
        return valid

    valid = valid.sort_values(["failure_vehicle_key", "created_date", "ticket_number", "ticket_key"], na_position="last")
    valid["failure_rank_in_vehicle"] = valid.groupby("failure_vehicle_key").cumcount() + 1

    for _, row in valid[valid["failure_rank_in_vehicle"] > 1].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Failure Timing",
                reason="later qualifying ticket on the same vehicle",
                row=row,
            )
        )

    first_only = valid[valid["failure_rank_in_vehicle"] == 1].copy()
    first_only["failure_days"] = first_only["failure_days"].round(2)
    return first_only


def merge_parts_data(
    qualifying_df: pd.DataFrame,
    parts_by_ticket_df: pd.DataFrame,
    parts_by_po_df: pd.DataFrame,
    anomalies: list[dict[str, Any]],
) -> pd.DataFrame:
    merged = qualifying_df.merge(parts_by_ticket_df, on="ticket_number", how="left", suffixes=("", "_by_ticket"))
    merged = merged.merge(parts_by_po_df, on="po", how="left", suffixes=("", "_by_po"))

    def choose_parts_value(row: pd.Series, ticket_col: str, po_col: str) -> Any:
        ticket_value = row.get(ticket_col)
        if pd.notna(ticket_value):
            return ticket_value
        return row.get(po_col)

    result = merged.copy()
    result["parts_join_source"] = ""
    result["parts_po_joined"] = ""
    result["parts_sales_order_joined"] = ""
    result["so_created_date_joined"] = pd.NaT
    result["first_issue_date_joined"] = pd.NaT
    result["parts_item_rows_joined"] = pd.NA
    result["issued_item_rows_joined"] = pd.NA

    ticket_has_match = result["parts_po"].fillna("").astype(str).str.strip().ne("")
    po_has_match = result["parts_sales_order_by_po"].fillna("").astype(str).str.strip().ne("") | result["so_created_date_by_po"].notna() | result["first_issue_date_by_po"].notna()

    result.loc[ticket_has_match, "parts_join_source"] = "ticket_number"
    result.loc[~ticket_has_match & po_has_match, "parts_join_source"] = "po_fallback"

    result["parts_po_joined"] = result.apply(
        lambda row: clean(row["parts_po"]) if clean(row["parts_join_source"]) == "ticket_number" else clean(row.get("po")),
        axis=1,
    )
    result["parts_sales_order_joined"] = result.apply(
        lambda row: clean(row["parts_sales_order"]) if clean(row["parts_join_source"]) == "ticket_number" else clean(row.get("parts_sales_order_by_po")),
        axis=1,
    )
    result["so_created_date_joined"] = result.apply(
        lambda row: row["so_created_date"] if clean(row["parts_join_source"]) == "ticket_number" else row.get("so_created_date_by_po"),
        axis=1,
    )
    result["first_issue_date_joined"] = result.apply(
        lambda row: row["first_issue_date"] if clean(row["parts_join_source"]) == "ticket_number" else row.get("first_issue_date_by_po"),
        axis=1,
    )
    result["parts_item_rows_joined"] = result.apply(
        lambda row: row["parts_item_rows"] if clean(row["parts_join_source"]) == "ticket_number" else row.get("parts_item_rows_by_po"),
        axis=1,
    )
    result["issued_item_rows_joined"] = result.apply(
        lambda row: row["issued_item_rows"] if clean(row["parts_join_source"]) == "ticket_number" else row.get("issued_item_rows_by_po"),
        axis=1,
    )

    both_match_mask = ticket_has_match & po_has_match
    mismatch_mask = both_match_mask & (
        (result["parts_po"].fillna("").astype(str).str.strip() != result["po"].fillna("").astype(str).str.strip())
        | (result["so_created_date"].fillna(pd.Timestamp.min) != result["so_created_date_by_po"].fillna(pd.Timestamp.min))
        | (result["first_issue_date"].fillna(pd.Timestamp.min) != result["first_issue_date_by_po"].fillna(pd.Timestamp.min))
    )
    for _, row in result[mismatch_mask].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Parts Issuing",
                reason="ticket-number join and PO join disagree",
                row=row,
                detail=f"ticket_parts_po={clean(row.get('parts_po'))}, po_join={clean(row.get('po'))}",
            )
        )

    for _, row in result[result["parts_join_source"].eq("")].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Parts Issuing",
                reason="missing parts summary match",
                row=row,
            )
        )

    return result


def build_parts_segment(parts_joined_df: pd.DataFrame, anomalies: list[dict[str, Any]]) -> pd.DataFrame:
    work = parts_joined_df.copy()
    work["parts_issuing_days"] = work.apply(
        lambda row: day_diff(row["first_issue_date_joined"], row["so_created_date_joined"]),
        axis=1,
    )
    work["parts_issuing_days_valid"] = work["parts_issuing_days"].map(lambda value: value is not None and value >= 0)

    for _, row in work[work["parts_join_source"].ne("") & work["so_created_date_joined"].isna()].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Parts Issuing",
                reason="missing SO created date",
                row=row,
            )
        )

    for _, row in work[work["parts_join_source"].ne("") & work["first_issue_date_joined"].isna()].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Parts Issuing",
                reason="missing first issue date",
                row=row,
            )
        )

    for _, row in work[work["parts_issuing_days"].map(lambda value: value is not None and value < 0)].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Parts Issuing",
                reason="negative parts issuing days",
                row=row,
            )
        )

    return work[work["parts_issuing_days_valid"]].copy()


def build_repairer_segment(
    parts_joined_df: pd.DataFrame,
    invoice_df: pd.DataFrame,
    anomalies: list[dict[str, Any]],
) -> pd.DataFrame:
    merged = parts_joined_df.merge(invoice_df, on="po", how="left")
    merged["po_last_invoice_date"] = merged["invoice_date"]
    merged["chassis_invoice_key"] = merged["chassis_number"].map(normalize_vehicle_key)

    chassis_invoice_lookup = (
        merged.loc[merged["chassis_invoice_key"].ne("") & merged["po_last_invoice_date"].notna(), ["chassis_invoice_key", "po_last_invoice_date"]]
        .sort_values(["chassis_invoice_key", "po_last_invoice_date"], na_position="last")
        .drop_duplicates(subset=["chassis_invoice_key"], keep="last")
        .set_index("chassis_invoice_key")["po_last_invoice_date"]
        .to_dict()
    )
    merged["chassis_last_invoice_date"] = merged["chassis_invoice_key"].map(chassis_invoice_lookup)

    merged["created_to_invoice_days"] = merged.apply(
        lambda row: day_diff(row["invoice_date"], row["created_date"]),
        axis=1,
    )
    merged["created_to_chassis_invoice_days"] = merged.apply(
        lambda row: day_diff(row["chassis_last_invoice_date"], row["created_date"]),
        axis=1,
    )
    merged["invoice_minus_first_issue_days"] = merged.apply(
        lambda row: day_diff(row["invoice_date"], row["first_issue_date_joined"]),
        axis=1,
    )
    merged["chassis_invoice_minus_first_issue_days"] = merged.apply(
        lambda row: day_diff(row["chassis_last_invoice_date"], row["first_issue_date_joined"]),
        axis=1,
    )
    merged["repairer_repair_days"] = merged.apply(
        lambda row: (
            None
            if row["created_to_invoice_days"] is None
            or row["approval_days"] is None
            or row["approval_days"] < 0
            or row["parts_issuing_days"] is None
            else row["created_to_invoice_days"] - row["approval_days"] - row["parts_issuing_days"]
        ),
        axis=1,
    )
    merged["repairer_repair_days_by_chassis_invoice"] = merged.apply(
        lambda row: (
            None
            if row["created_to_chassis_invoice_days"] is None
            or row["approval_days"] is None
            or row["approval_days"] < 0
            or row["parts_issuing_days"] is None
            else row["created_to_chassis_invoice_days"] - row["approval_days"] - row["parts_issuing_days"]
        ),
        axis=1,
    )
    merged["po_vs_chassis_invoice_gap_days"] = merged.apply(
        lambda row: day_diff(row["chassis_last_invoice_date"], row["po_last_invoice_date"]),
        axis=1,
    )
    merged["repairer_repair_days_valid"] = merged["repairer_repair_days"].map(lambda value: value is not None and value >= 0)

    for _, row in merged[merged["invoice_date"].isna()].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Repairer Repair Time",
                reason="missing invoice date",
                row=row,
            )
        )

    for _, row in merged[merged["created_to_invoice_days"].map(lambda value: value is not None and value < 0)].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Repairer Repair Time",
                reason="invoice date is earlier than created date",
                row=row,
            )
        )

    for _, row in merged[merged["repairer_repair_days"].map(lambda value: value is not None and value < 0)].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Repairer Repair Time",
                reason="negative repairer repair days after subtracting approval and parts time",
                row=row,
            )
        )

    return merged[merged["repairer_repair_days_valid"]].copy()


def anomaly_row(stage: str, reason: str, row: pd.Series, detail: str = "") -> dict[str, Any]:
    return {
        "stage": stage,
        "reason": reason,
        "ticket_number": clean(row.get("ticket_number")),
        "ticket_key": clean(row.get("ticket_key")),
        "claim_scope": clean(row.get("claim_scope")),
        "ticket_type": clean(row.get("ticket_type")),
        "status_text": clean(row.get("status_text")),
        "dealer_name": clean(row.get("dealer_name")),
        "repair_shop": clean(row.get("service_technician")),
        "po": clean(row.get("po")),
        "created_on": clean(row.get("created_on")),
        "claim_approved_on": clean(row.get("claim_approved_on")),
        "failure_pgi_date": clean(row.get("failure_pgi_date")),
        "so_created_date": iso_or_blank(row.get("so_created_date_joined")),
        "first_issue_date": iso_or_blank(row.get("first_issue_date_joined")),
        "invoice_date": iso_or_blank(row.get("invoice_date")),
        "approval_days": row.get("approval_days"),
        "parts_issuing_days": row.get("parts_issuing_days"),
        "failure_days": row.get("failure_days"),
        "created_to_invoice_days": row.get("created_to_invoice_days"),
        "repairer_repair_days": row.get("repairer_repair_days"),
        "detail": detail,
    }


def build_summary_sheet(
    qualifying_df: pd.DataFrame,
    failure_segment_df: pd.DataFrame,
    approval_segment_df: pd.DataFrame,
    parts_segment_df: pd.DataFrame,
    repairer_segment_df: pd.DataFrame,
    anomalies_df: pd.DataFrame,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    rows = [
        {"Metric": "Run At", "Value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        {"Metric": "Created On Filter", "Value": f"{start_year}-{end_year}"},
        {"Metric": "Qualification Filter", "Value": "Created On in scope + matched PO + Claim Approved On present + not unapproved"},
        {"Metric": "Failure Timing Basis", "Value": "First qualifying ticket per vehicle, using Model Series delivery-date hierarchy, Failure Days = Created On - Delivery Date"},
        {"Metric": "Warranty Approval Basis", "Value": "Claim Approved On - Created On"},
        {"Metric": "Parts Issuing Basis", "Value": "First Issue Date - SO Created Date"},
        {"Metric": "Repairer Basis", "Value": "Invoice Date - Created On - Approval Days - Parts Issuing Days"},
        {"Metric": "Qualifying Tickets", "Value": int(len(qualifying_df))},
        {"Metric": "Failure Timing Rows", "Value": int(len(failure_segment_df))},
        {"Metric": "Failure Timing Avg Days", "Value": mean_or_blank(failure_segment_df.get("failure_days", []))},
        {"Metric": "Failure Timing Median Days", "Value": median_or_blank(failure_segment_df.get("failure_days", []))},
        {"Metric": "Warranty Approval Rows", "Value": int(len(approval_segment_df))},
        {"Metric": "Warranty Approval Avg Days", "Value": mean_or_blank(approval_segment_df.get("approval_days", []))},
        {"Metric": "Warranty Approval Median Days", "Value": median_or_blank(approval_segment_df.get("approval_days", []))},
        {"Metric": "Parts Issuing Rows", "Value": int(len(parts_segment_df))},
        {"Metric": "Parts Issuing Avg Days", "Value": mean_or_blank(parts_segment_df.get("parts_issuing_days", []))},
        {"Metric": "Parts Issuing Median Days", "Value": median_or_blank(parts_segment_df.get("parts_issuing_days", []))},
        {"Metric": "Repairer Rows", "Value": int(len(repairer_segment_df))},
        {"Metric": "Repairer Avg Days", "Value": mean_or_blank(repairer_segment_df.get("repairer_repair_days", []))},
        {"Metric": "Repairer Median Days", "Value": median_or_blank(repairer_segment_df.get("repairer_repair_days", []))},
        {"Metric": "Anomaly Rows", "Value": int(len(anomalies_df))},
    ]
    return pd.DataFrame(rows)


def build_qualifying_sheet(
    qualifying_df: pd.DataFrame,
    failure_segment_df: pd.DataFrame,
    approval_segment_df: pd.DataFrame,
    parts_segment_df: pd.DataFrame,
    repairer_segment_df: pd.DataFrame,
) -> pd.DataFrame:
    out = qualifying_df.copy()
    out["used_in_failure_timing"] = out["ticket_number"].isin(set(failure_segment_df["ticket_number"]))
    out["used_in_warranty_approval"] = out["ticket_number"].isin(set(approval_segment_df["ticket_number"]))
    out["used_in_parts_issuing"] = out["ticket_number"].isin(set(parts_segment_df["ticket_number"]))
    out["used_in_repairer_time"] = out["ticket_number"].isin(set(repairer_segment_df["ticket_number"]))

    failure_days_map = failure_segment_df.set_index("ticket_number")["failure_days"].to_dict() if not failure_segment_df.empty else {}
    parts_days_map = parts_segment_df.set_index("ticket_number")["parts_issuing_days"].to_dict() if not parts_segment_df.empty else {}
    repairer_days_map = repairer_segment_df.set_index("ticket_number")["repairer_repair_days"].to_dict() if not repairer_segment_df.empty else {}

    out["failure_days"] = out["ticket_number"].map(failure_days_map)
    out["parts_issuing_days"] = out["ticket_number"].map(parts_days_map)
    out["repairer_repair_days"] = out["ticket_number"].map(repairer_days_map)

    return out[
        [
            "ticket_number",
            "ticket_key",
            "ticket_id_text",
            "ticket_type",
            "claim_scope",
            "status_text",
            "agent_name",
            "agent_id",
            "dealer_code",
            "dealer_name",
            "account_name",
            "account_id",
            "country_region",
            "service_technician",
            "service_technician_id",
            "po",
            "erp_service_order_id",
            "erp_free_order_id",
            "created_on",
            "claim_approved_on",
            "changed_on",
            "posting_date",
            "date_of_purchase",
            "serial_id",
            "chassis_number",
            "registered_product",
            "registered_product_code",
            "product",
            "product_code",
            "claim_total_amount",
            "factory_parts_claim_total_amount",
            "labour_hours_total_amount",
            "repairer_parts_claim_total_amount",
            "approval_days",
            "used_in_failure_timing",
            "failure_days",
            "used_in_warranty_approval",
            "used_in_parts_issuing",
            "parts_issuing_days",
            "used_in_repairer_time",
            "repairer_repair_days",
        ]
    ].copy()


def prepare_failure_sheet(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        [
            "failure_vehicle_key",
            "ticket_number",
            "ticket_key",
            "ticket_id_text",
            "ticket_type",
            "claim_scope",
            "status_text",
            "agent_name",
            "dealer_code",
            "dealer_name",
            "account_name",
            "country_region",
            "service_technician",
            "service_technician_id",
            "po",
            "erp_service_order_id",
            "created_on",
            "claim_approved_on",
            "changed_on",
            "posting_date",
            "date_of_purchase",
            "failure_pgi_date",
            "failure_vehicle_delivery_date",
            "failure_days",
            "failure_timing_source",
            "failure_matched_vehicle_key",
            "failure_matched_serial",
            "failure_matched_chassis",
            "failure_matched_sales_order",
            "failure_ticket_sales_order",
            "failure_ticket_serial_id",
            "failure_ticket_chassis_number",
            "serial_id",
            "chassis_number",
            "registered_product",
            "registered_product_code",
            "product",
            "product_code",
            "claim_total_amount",
            "factory_parts_claim_total_amount",
            "labour_hours_total_amount",
            "repairer_parts_claim_total_amount",
        ]
    ].copy()


def prepare_approval_sheet(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        [
            "ticket_number",
            "ticket_key",
            "ticket_id_text",
            "ticket_type",
            "claim_scope",
            "status_text",
            "agent_name",
            "dealer_code",
            "dealer_name",
            "account_name",
            "country_region",
            "service_technician",
            "service_technician_id",
            "po",
            "erp_service_order_id",
            "created_on",
            "claim_approved_on",
            "approval_days",
            "changed_on",
            "posting_date",
            "date_of_purchase",
            "serial_id",
            "chassis_number",
            "registered_product",
            "registered_product_code",
            "product",
            "product_code",
            "claim_total_amount",
            "factory_parts_claim_total_amount",
            "labour_hours_total_amount",
            "repairer_parts_claim_total_amount",
        ]
    ].copy()


def prepare_parts_sheet(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["so_created_date"] = out["so_created_date_joined"].map(iso_or_blank)
    out["first_issue_date"] = out["first_issue_date_joined"].map(iso_or_blank)
    return out[
        [
            "ticket_number",
            "ticket_key",
            "ticket_id_text",
            "ticket_type",
            "claim_scope",
            "status_text",
            "agent_name",
            "dealer_code",
            "dealer_name",
            "account_name",
            "country_region",
            "service_technician",
            "service_technician_id",
            "po",
            "erp_service_order_id",
            "parts_join_source",
            "parts_sales_order_joined",
            "so_created_date",
            "first_issue_date",
            "parts_issuing_days",
            "parts_item_rows_joined",
            "issued_item_rows_joined",
            "created_on",
            "claim_approved_on",
            "changed_on",
            "posting_date",
            "date_of_purchase",
            "serial_id",
            "chassis_number",
            "registered_product",
            "registered_product_code",
            "product",
            "product_code",
            "claim_total_amount",
            "factory_parts_claim_total_amount",
            "labour_hours_total_amount",
            "repairer_parts_claim_total_amount",
        ]
    ].copy()


def prepare_repairer_sheet(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["so_created_date"] = out["so_created_date_joined"].map(iso_or_blank)
    out["first_issue_date"] = out["first_issue_date_joined"].map(iso_or_blank)
    out["po_last_invoice_date"] = out["po_last_invoice_date"].map(iso_or_blank)
    out["chassis_last_invoice_date"] = out["chassis_last_invoice_date"].map(iso_or_blank)
    out["repairer_formula_text_po_invoice"] = out.apply(
        lambda row: (
            ""
            if pd.isna(row.get("created_to_invoice_days"))
            or pd.isna(row.get("approval_days"))
            or pd.isna(row.get("parts_issuing_days"))
            or pd.isna(row.get("repairer_repair_days"))
            else (
                f"{int(row['created_to_invoice_days'])}"
                f" - {int(row['approval_days'])}"
                f" - {int(row['parts_issuing_days'])}"
                f" = {int(row['repairer_repair_days'])}"
            )
        ),
        axis=1,
    )
    out["repairer_formula_text_chassis_invoice"] = out.apply(
        lambda row: (
            ""
            if pd.isna(row.get("created_to_chassis_invoice_days"))
            or pd.isna(row.get("approval_days"))
            or pd.isna(row.get("parts_issuing_days"))
            or pd.isna(row.get("repairer_repair_days_by_chassis_invoice"))
            else (
                f"{int(row['created_to_chassis_invoice_days'])}"
                f" - {int(row['approval_days'])}"
                f" - {int(row['parts_issuing_days'])}"
                f" = {int(row['repairer_repair_days_by_chassis_invoice'])}"
            )
        ),
        axis=1,
    )
    out = out[
        [
            "ticket_number",
            "ticket_key",
            "ticket_id_text",
            "ticket_type",
            "claim_scope",
            "status_text",
            "agent_name",
            "dealer_code",
            "dealer_name",
            "account_name",
            "country_region",
            "service_technician",
            "service_technician_id",
            "po",
            "erp_service_order_id",
            "created_on",
            "claim_approved_on",
            "changed_on",
            "posting_date",
            "date_of_purchase",
            "so_created_date",
            "first_issue_date",
            "po_last_invoice_date",
            "chassis_last_invoice_date",
            "approval_days",
            "parts_issuing_days",
            "created_to_invoice_days",
            "created_to_chassis_invoice_days",
            "repairer_repair_days",
            "repairer_repair_days_by_chassis_invoice",
            "invoice_minus_first_issue_days",
            "chassis_invoice_minus_first_issue_days",
            "po_vs_chassis_invoice_gap_days",
            "repairer_formula_text_po_invoice",
            "repairer_formula_text_chassis_invoice",
            "invoice_status",
            "invoice_number",
            "serial_id",
            "chassis_number",
            "registered_product",
            "registered_product_code",
            "product",
            "product_code",
            "claim_total_amount",
            "factory_parts_claim_total_amount",
            "labour_hours_total_amount",
            "repairer_parts_claim_total_amount",
        ]
    ].copy()
    return out.rename(
        columns={
            "created_on": "ticket_created_on",
            "claim_approved_on": "claim_approved_on_ticket",
            "changed_on": "ticket_changed_on",
            "posting_date": "ticket_posting_date",
            "so_created_date": "parts_so_created_date",
            "first_issue_date": "parts_first_issue_date",
            "approval_days": "warranty_approval_days",
            "parts_issuing_days": "parts_preparation_days",
            "created_to_invoice_days": "total_cycle_days_created_to_po_invoice",
            "created_to_chassis_invoice_days": "total_cycle_days_created_to_chassis_invoice",
            "repairer_repair_days": "repairer_net_days_po_invoice",
            "repairer_repair_days_by_chassis_invoice": "repairer_net_days_chassis_invoice",
            "invoice_minus_first_issue_days": "po_invoice_minus_first_issue_days_audit",
            "chassis_invoice_minus_first_issue_days": "chassis_invoice_minus_first_issue_days_audit",
        }
    )


def prepare_formula_breakdown_sheet(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ticket_created_on"] = out["created_on"]
    out["claim_approved_on_ticket"] = out["claim_approved_on"]
    out["parts_so_created_date"] = out["so_created_date_joined"].map(iso_or_blank)
    out["parts_first_issue_date"] = out["first_issue_date_joined"].map(iso_or_blank)
    out["po_last_invoice_date"] = out["po_last_invoice_date"].map(iso_or_blank)
    out["chassis_last_invoice_date"] = out["chassis_last_invoice_date"].map(iso_or_blank)
    out["warranty_approval_days"] = out["approval_days"]
    out["parts_preparation_days"] = out["parts_issuing_days"]
    out["total_cycle_days_created_to_po_invoice"] = out["created_to_invoice_days"]
    out["total_cycle_days_created_to_chassis_invoice"] = out["created_to_chassis_invoice_days"]
    out["repairer_net_days_po_invoice"] = out["repairer_repair_days"]
    out["repairer_net_days_chassis_invoice"] = out["repairer_repair_days_by_chassis_invoice"]
    out["po_invoice_minus_first_issue_days_audit"] = out["invoice_minus_first_issue_days"]
    out["chassis_invoice_minus_first_issue_days_audit"] = out["chassis_invoice_minus_first_issue_days"]
    out["approved_to_so_created_days_audit"] = out.apply(
        lambda row: day_diff(row.get("so_created_date_joined"), row.get("approved_date")),
        axis=1,
    )
    out["repairer_formula_text_po_invoice"] = out.apply(
        lambda row: (
            ""
            if pd.isna(row.get("created_to_invoice_days"))
            or pd.isna(row.get("approval_days"))
            or pd.isna(row.get("parts_issuing_days"))
            or pd.isna(row.get("repairer_repair_days"))
            else (
                f"({int(row['created_to_invoice_days'])}) total cycle"
                f" - ({int(row['approval_days'])}) warranty approval"
                f" - ({int(row['parts_issuing_days'])}) parts prep"
                f" = ({int(row['repairer_repair_days'])}) repairer"
            )
        ),
        axis=1,
    )
    out["repairer_formula_text_chassis_invoice"] = out.apply(
        lambda row: (
            ""
            if pd.isna(row.get("created_to_chassis_invoice_days"))
            or pd.isna(row.get("approval_days"))
            or pd.isna(row.get("parts_issuing_days"))
            or pd.isna(row.get("repairer_repair_days_by_chassis_invoice"))
            else (
                f"({int(row['created_to_chassis_invoice_days'])}) total cycle"
                f" - ({int(row['approval_days'])}) warranty approval"
                f" - ({int(row['parts_issuing_days'])}) parts prep"
                f" = ({int(row['repairer_repair_days_by_chassis_invoice'])}) repairer"
            )
        ),
        axis=1,
    )
    out["formula_balance_check_po_invoice"] = out.apply(
        lambda row: (
            None
            if pd.isna(row.get("created_to_invoice_days"))
            or pd.isna(row.get("approval_days"))
            or pd.isna(row.get("parts_issuing_days"))
            or pd.isna(row.get("repairer_repair_days"))
            else int(row["created_to_invoice_days"]) - int(row["approval_days"]) - int(row["parts_issuing_days"]) - int(row["repairer_repair_days"])
        ),
        axis=1,
    )
    out["formula_balance_check_chassis_invoice"] = out.apply(
        lambda row: (
            None
            if pd.isna(row.get("created_to_chassis_invoice_days"))
            or pd.isna(row.get("approval_days"))
            or pd.isna(row.get("parts_issuing_days"))
            or pd.isna(row.get("repairer_repair_days_by_chassis_invoice"))
            else int(row["created_to_chassis_invoice_days"]) - int(row["approval_days"]) - int(row["parts_issuing_days"]) - int(row["repairer_repair_days_by_chassis_invoice"])
        ),
        axis=1,
    )
    return out[
        [
            "ticket_number",
            "ticket_key",
            "ticket_id_text",
            "ticket_type",
            "claim_scope",
            "status_text",
            "dealer_name",
            "service_technician",
            "po",
            "erp_service_order_id",
            "ticket_created_on",
            "claim_approved_on_ticket",
            "parts_so_created_date",
            "parts_first_issue_date",
            "po_last_invoice_date",
            "chassis_last_invoice_date",
            "total_cycle_days_created_to_po_invoice",
            "total_cycle_days_created_to_chassis_invoice",
            "warranty_approval_days",
            "parts_preparation_days",
            "repairer_net_days_po_invoice",
            "repairer_net_days_chassis_invoice",
            "po_invoice_minus_first_issue_days_audit",
            "chassis_invoice_minus_first_issue_days_audit",
            "approved_to_so_created_days_audit",
            "po_vs_chassis_invoice_gap_days",
            "formula_balance_check_po_invoice",
            "formula_balance_check_chassis_invoice",
            "repairer_formula_text_po_invoice",
            "repairer_formula_text_chassis_invoice",
            "serial_id",
            "chassis_number",
            "registered_product",
            "product",
            "claim_total_amount",
            "factory_parts_claim_total_amount",
            "labour_hours_total_amount",
            "repairer_parts_claim_total_amount",
        ]
    ].copy()


def autosize_workbook(output_path: Path) -> None:
    workbook = load_workbook(output_path)
    for sheet in workbook.worksheets:
        if sheet.max_row >= 1 and sheet.max_column >= 1:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
        for column_cells in sheet.columns:
            letter = get_column_letter(column_cells[0].column)
            max_length = 0
            for cell in column_cells:
                value = "" if cell.value is None else str(cell.value)
                if len(value) > max_length:
                    max_length = len(value)
            sheet.column_dimensions[letter].width = min(max(12, max_length + 2), 48)
    workbook.save(output_path)


def write_workbook_file(output_path: Path, sheets: list[tuple[str, pd.DataFrame]]) -> None:
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, df in sheets:
            export_df = df.copy()
            for column in export_df.columns:
                if pd.api.types.is_datetime64_any_dtype(export_df[column]):
                    export_df[column] = export_df[column].dt.strftime("%Y-%m-%d")
            export_df.to_excel(writer, sheet_name=sheet_name, index=False)
    autosize_workbook(output_path)


def fallback_output_path(output_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = output_path.with_name(f"{output_path.stem}_{timestamp}{output_path.suffix}")
    index = 1
    while candidate.exists():
        index += 1
        candidate = output_path.with_name(f"{output_path.stem}_{timestamp}_{index}{output_path.suffix}")
    return candidate


def export_workbook(output_path: Path, sheets: list[tuple[str, pd.DataFrame]]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_workbook_file(output_path, sheets)
        return output_path
    except PermissionError:
        generated_exports_dir = ROOT / "generated_exports"
        generated_exports_dir.mkdir(parents=True, exist_ok=True)
        fallback_seed = generated_exports_dir / output_path.name
        fallback_path = fallback_output_path(fallback_seed)
        write_workbook_file(fallback_path, sheets)
        return fallback_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Ticket Timeline segment ticket sheets for 2025/2026 approved PO-backed tickets."
    )
    parser.add_argument("--analysis-ticket-js", default=str(DEFAULT_ANALYSIS_TICKET_JS))
    parser.add_argument("--failure-timing-csv", default=str(DEFAULT_FAILURE_TIMING_CSV))
    parser.add_argument("--parts-classified-csv", default=str(DEFAULT_PARTS_CLASSIFIED_CSV))
    parser.add_argument("--repairers-json", default=str(DEFAULT_REPAIRERS_JSON))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2026)
    return parser.parse_args()


def validate_paths(paths: Paths) -> None:
    missing = [path for path in [paths.analysis_ticket_js, paths.failure_timing_csv, paths.parts_classified_csv, paths.repairers_json] if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required source files:\n" + "\n".join(str(path) for path in missing))


def main() -> int:
    args = parse_args()
    paths = Paths(
        analysis_ticket_js=Path(args.analysis_ticket_js),
        failure_timing_csv=Path(args.failure_timing_csv),
        parts_classified_csv=Path(args.parts_classified_csv),
        repairers_json=Path(args.repairers_json),
        output=Path(args.output),
    )
    validate_paths(paths)

    base_df = load_analysis_ticket_base(paths.analysis_ticket_js)
    qualifying_df = build_qualifying_universe(base_df, start_year=args.start_year, end_year=args.end_year)

    failure_df = load_failure_timing_rows(paths.failure_timing_csv)
    parts_by_ticket_df, parts_by_po_df = load_parts_ticket_summary(paths.parts_classified_csv)
    invoice_df = load_repairer_invoice_rows(paths.repairers_json)

    anomalies: list[dict[str, Any]] = []

    failure_segment_df = build_failure_segment(qualifying_df, failure_df, anomalies)
    approval_segment_df = qualifying_df[qualifying_df["approval_days_valid"]].copy()

    for _, row in qualifying_df[~qualifying_df["approval_days_valid"]].iterrows():
        anomalies.append(
            anomaly_row(
                stage="Warranty Approval",
                reason="missing or negative approval days",
                row=row,
            )
        )

    parts_joined_df = merge_parts_data(qualifying_df, parts_by_ticket_df, parts_by_po_df, anomalies)
    parts_segment_df = build_parts_segment(parts_joined_df, anomalies)
    repairer_segment_df = build_repairer_segment(parts_segment_df, invoice_df, anomalies)
    anomalies_df = pd.DataFrame(anomalies).sort_values(["stage", "reason", "ticket_number", "ticket_key"], na_position="last").reset_index(drop=True)

    summary_df = build_summary_sheet(
        qualifying_df=qualifying_df,
        failure_segment_df=failure_segment_df,
        approval_segment_df=approval_segment_df,
        parts_segment_df=parts_segment_df,
        repairer_segment_df=repairer_segment_df,
        anomalies_df=anomalies_df,
        start_year=args.start_year,
        end_year=args.end_year,
    )
    qualifying_sheet_df = build_qualifying_sheet(
        qualifying_df=qualifying_df,
        failure_segment_df=failure_segment_df,
        approval_segment_df=approval_segment_df,
        parts_segment_df=parts_segment_df,
        repairer_segment_df=repairer_segment_df,
    )

    sheets = [
        ("summary", summary_df),
        ("qualifying_tickets", qualifying_sheet_df),
        ("failure_timing", prepare_failure_sheet(failure_segment_df)),
        ("warranty_approval", prepare_approval_sheet(approval_segment_df)),
        ("parts_issuing", prepare_parts_sheet(parts_segment_df)),
        ("repairer_time", prepare_repairer_sheet(repairer_segment_df)),
        ("formula_breakdown", prepare_formula_breakdown_sheet(repairer_segment_df)),
        ("anomalies", anomalies_df),
    ]
    written_path = export_workbook(paths.output, sheets)

    print(f"Workbook written: {written_path}")
    print(f"Qualifying tickets: {len(qualifying_df)}")
    print(f"Failure timing rows: {len(failure_segment_df)}")
    print(f"Warranty approval rows: {len(approval_segment_df)}")
    print(f"Parts issuing rows: {len(parts_segment_df)}")
    print(f"Repairer rows: {len(repairer_segment_df)}")
    print(f"Anomaly rows: {len(anomalies_df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
