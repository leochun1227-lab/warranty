from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

try:
    import pyodbc
except ImportError:  # pragma: no cover
    pyodbc = None

try:
    import firebase_admin
    from firebase_admin import credentials, db
except ImportError:
    firebase_admin = None
    credentials = None
    db = None


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
ANALYSIS_TICKET_FAILURE_TIMING_CSV_PATH = OUTPUT_DIR / "analysis_ticket_failure_timing.csv"
ANALYSIS_TICKET_FAILURE_TIMING_JS_PATH = OUTPUT_DIR / "analysis_ticket_failure_timing.js"
DEFAULT_LATEST_WORKBOOK_PATH = OUTPUT_DIR / "vehicle_failure_timing_2025_2026_latest.xlsx"
VEHICLE_FAILURE_TIMING_SUMMARY_JSON_PATH = OUTPUT_DIR / "vehicle_failure_timing_2025_2026_latest_summary.json"
VEHICLE_FAILURE_TIMING_SUMMARY_JS_PATH = OUTPUT_DIR / "vehicle_failure_timing_2025_2026_latest_summary.js"

DEFAULT_DSN = os.getenv(
    "SAP_HANA_DSN",
    "DRIVER={HDBODBC};SERVERNODE=10.11.2.25:30241;UID=BAOJIANFENG;PWD=Xja@2025ABC;",
)
DEFAULT_SAP_CLIENT = os.getenv("SAP_CLIENT", "800")
DEFAULT_SALES_ORG = os.getenv("SALES_ORG", "3110")
DEFAULT_FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
DEFAULT_FIREBASE_SA_PATH = os.getenv(
    "FIREBASE_SA_PATH",
    str(ROOT / "firebase-service-account.json"),
)
DEFAULT_FIREBASE_ROOT = os.getenv("FIREBASE_ROOT", "c4cTickets_test")
DEFAULT_LOCAL_TICKET_CSV = str(
    ROOT / "SAPAnalyticsReport_ZF8C06456D7698BCB54F44D_.csv"
    if (ROOT / "SAPAnalyticsReport_ZF8C06456D7698BCB54F44D_.csv").exists()
    else ROOT / "SAPAnalyticsReport(ZF8C06456D7698BCB54F44D).csv"
)

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y%m%d",
    "%d.%m.%Y",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M:%S AUSACT",
    "%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("vehicle_failure_timing_export")

TICKET_COLUMNS = [
    "Ticket ID",
    "Created On",
    "Date of Purchase",
    "Posting Date",
    "Sales Order",
    "Vehicle Dispatch Date",
    "Vehicle Dispatch Source",
    "Vehicle Dispatch Sales Order",
    "Vehicle Dispatch Serial",
    "Serial ID",
    "Chassis Number",
    "Dealer Name",
    "Repair Shop",
    "Ticket Type",
    "Claim Scope",
    "Status",
    "Registered Product",
    "Product",
    "Amount Including Tax",
]


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def sql_quote(value: Any) -> str:
    return clean(value).replace("'", "''")


def normalize_vehicle_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", clean(value)).upper()


def first_non_blank(*values: Any) -> str:
    for value in values:
        text = clean(value)
        if text:
            return text
    return ""


def classify_claim_scope(*values: Any) -> str:
    text = " ".join(clean(value).lower() for value in values if clean(value))
    if not text:
        return ""
    if any(token in text for token in ("pre delivery", "pre-delivery", "predelivery", "pdi")):
        return "Pre Delivery"
    if any(token in text for token in ("in field", "in-field", "infield", "field warranty")):
        return "In Field"
    return "Other"


def parse_any_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = clean(value)
    if not text or text == "00000000":
        return None

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def iso_date(value: Any) -> str:
    parsed = parse_any_date(value)
    return parsed.isoformat() if parsed else ""


def diff_days(end_value: Any, start_value: Any) -> Optional[int]:
    end_date = parse_any_date(end_value)
    start_date = parse_any_date(start_value)
    if not end_date or not start_date:
        return None
    return (end_date - start_date).days


def firebase_node_to_dict(node: Any) -> Dict[str, Any]:
    if isinstance(node, dict):
        return node
    if isinstance(node, list):
        return {str(i): value for i, value in enumerate(node) if value is not None}
    return {}


def firebase_init(firebase_db_url: str, firebase_sa_path: str) -> None:
    if firebase_admin is None or credentials is None or db is None:
        raise RuntimeError("firebase_admin is not installed.")
    if getattr(firebase_admin, "_apps", None):
        return
    if not Path(firebase_sa_path).exists():
        raise FileNotFoundError(f"Firebase service account file not found: {firebase_sa_path}")
    cred = credentials.Certificate(firebase_sa_path)
    firebase_admin.initialize_app(cred, {"databaseURL": firebase_db_url})


def load_tickets_from_firebase(firebase_root: str) -> pd.DataFrame:
    raw = db.reference(f"{firebase_root}/tickets").get()
    tickets = firebase_node_to_dict(raw)
    rows: List[Dict[str, Any]] = []

    for ticket_id, node in tickets.items():
        ticket = (node or {}).get("ticket", {}) if isinstance(node, dict) else {}
        if not isinstance(ticket, dict):
            continue
        rows.append(
            {
                "Ticket ID": clean(ticket_id),
                "Created On": first_non_blank(ticket.get("Created On"), ticket.get("CreatedOn"), ticket.get("createdOn")),
                "Date of Purchase": first_non_blank(ticket.get("Date of Purchase"), ticket.get("DateOfPurchase")),
                "Posting Date": first_non_blank(ticket.get("Posting Date"), ticket.get("PostingDate")),
                "Sales Order": first_non_blank(ticket.get("Sales Order"), ticket.get("SalesOrder"), ticket.get("LookupSalesOrder")),
                "Vehicle Dispatch Date": first_non_blank(ticket.get("Vehicle Dispatch Date"), ticket.get("vehicleDispatchDate")),
                "Vehicle Dispatch Source": first_non_blank(ticket.get("Vehicle Dispatch Source"), ticket.get("vehicleDispatchSource")),
                "Vehicle Dispatch Sales Order": first_non_blank(ticket.get("Vehicle Dispatch Sales Order"), ticket.get("vehicleDispatchSalesOrder")),
                "Vehicle Dispatch Serial": first_non_blank(ticket.get("Vehicle Dispatch Serial"), ticket.get("vehicleDispatchSerial")),
                "Serial ID": first_non_blank(ticket.get("Serial ID"), ticket.get("SerialID")),
                "Chassis Number": first_non_blank(ticket.get("Chassis Number"), ticket.get("ChassisNumber")),
                "Dealer Name": first_non_blank(ticket.get("Dealer Name"), ticket.get("DealerName")),
                "Repair Shop": first_non_blank(ticket.get("Repair Shop"), ticket.get("Repair Shop Name"), ticket.get("RepairshopID")),
                "Ticket Type": first_non_blank(
                    ticket.get("Ticket Type"),
                    ticket.get("Claim Type"),
                    ticket.get("Ticket Type Text"),
                    ticket.get("Warranty Claim Type"),
                ),
                "Claim Scope": classify_claim_scope(
                    ticket.get("Ticket Type"),
                    ticket.get("Claim Type"),
                    ticket.get("Ticket Type Text"),
                    ticket.get("Warranty Claim Type"),
                ),
                "Status": first_non_blank(ticket.get("Status"), ticket.get("TicketStatusText"), ticket.get("TicketStatus")),
                "Registered Product": clean(ticket.get("Registered Product")),
                "Product": clean(ticket.get("Product")),
                "Amount Including Tax": clean(ticket.get("Amount Including Tax") or ticket.get("AmountIncludingTax")),
            }
        )

    return normalize_ticket_df(pd.DataFrame(rows))


def load_tickets_from_csv(csv_path: str) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Local ticket CSV not found: {csv_path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        clean_header = [clean(name) for name in header]
        name_to_indexes: Dict[str, List[int]] = {}
        for idx, name in enumerate(clean_header):
            name_to_indexes.setdefault(name, []).append(idx)

        def pick(row: List[str], *names: str) -> str:
            for name in names:
                for idx in name_to_indexes.get(name, []):
                    if idx < len(row):
                        value = clean(row[idx])
                        if value:
                            return value
            return ""

        def pick_numeric_ticket_id(row: List[str]) -> str:
            for idx, raw_value in enumerate(row):
                value = clean(raw_value)
                if not value or not re.fullmatch(r"\d+", value):
                    continue
                current = clean_header[idx] if idx < len(clean_header) else ""
                prev_name = clean_header[idx - 1] if idx > 0 else ""
                next_name = clean_header[idx + 1] if idx + 1 < len(clean_header) else ""
                if "Ticket ID" in (current, prev_name, next_name) or (current == "Ticket" and value):
                    return value
            fallback = pick(row, "Ticket ID")
            return fallback if re.fullmatch(r"\d+", fallback) else ""

        rows: List[Dict[str, Any]] = []
        for row in reader:
            ticket_id = pick_numeric_ticket_id(row)
            if not ticket_id:
                continue
            rows.append(
                {
                    "Ticket ID": ticket_id,
                    "Created On": pick(row, "Created On"),
                    "Date of Purchase": pick(row, "Date of Purchase"),
                    "Posting Date": pick(row, "Posting Date"),
                    "Sales Order": pick(row, "Sales Order", "ERP Free Order ID"),
                    "Vehicle Dispatch Date": "",
                    "Vehicle Dispatch Source": "",
                    "Vehicle Dispatch Sales Order": "",
                    "Vehicle Dispatch Serial": "",
                    "Serial ID": pick(row, "Serial ID"),
                    "Chassis Number": pick(row, "Chassis Number"),
                    "Dealer Name": pick(row, "Dealer Name", "Dealer"),
                    "Repair Shop": pick(row, "Service Technician"),
                    "Ticket Type": pick(row, "Ticket Type"),
                    "Claim Scope": classify_claim_scope(pick(row, "Ticket Type"), pick(row, "Claim Type")),
                    "Status": pick(row, "Status"),
                    "Registered Product": pick(row, "Registered Product"),
                    "Product": pick(row, "Product"),
                    "Amount Including Tax": pick(row, "ClaimTotalAmount"),
                }
            )

    return normalize_ticket_df(pd.DataFrame(rows))


def normalize_ticket_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=TICKET_COLUMNS)

    out = df.copy()
    for col in TICKET_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    for col in out.columns:
        out[col] = out[col].fillna("").astype(str).str.strip()
    if "Claim Scope" in out.columns:
        out["Claim Scope"] = [
            classify_claim_scope(scope, ticket_type)
            for scope, ticket_type in zip(out["Claim Scope"].tolist(), out["Ticket Type"].tolist())
        ]
    return out[TICKET_COLUMNS]


def merge_ticket_sources(primary_df: pd.DataFrame, secondary_df: pd.DataFrame) -> pd.DataFrame:
    primary = normalize_ticket_df(primary_df)
    secondary = normalize_ticket_df(secondary_df)
    if primary.empty:
        return secondary
    if secondary.empty:
        return primary

    secondary_map = {
        clean(row.get("Ticket ID")): row
        for row in secondary.to_dict("records")
        if clean(row.get("Ticket ID"))
    }

    merged_rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in primary.to_dict("records"):
        ticket_id = clean(row.get("Ticket ID"))
        extra = secondary_map.get(ticket_id, {})
        merged = {
            col: first_non_blank(row.get(col), extra.get(col))
            for col in TICKET_COLUMNS
        }
        merged["Claim Scope"] = classify_claim_scope(merged.get("Claim Scope"), merged.get("Ticket Type"))
        merged_rows.append(merged)
        if ticket_id:
            seen.add(ticket_id)

    for row in secondary.to_dict("records"):
        ticket_id = clean(row.get("Ticket ID"))
        if not ticket_id or ticket_id in seen:
            continue
        merged = {col: clean(row.get(col)) for col in TICKET_COLUMNS}
        merged["Claim Scope"] = classify_claim_scope(merged.get("Claim Scope"), merged.get("Ticket Type"))
        merged_rows.append(merged)

    return normalize_ticket_df(pd.DataFrame(merged_rows))


def build_pgi_audit_sql(start_yyyymmdd: str, end_yyyymmdd: str, sap_client: str, sales_org: str) -> str:
    return f"""
WITH obj AS (
    SELECT DISTINCT
        vbak."MANDT"                    AS "MANDT",
        vbak."VBELN"                    AS "Sales Order",
        vbak."ERDAT"                    AS "SO Created Date",
        vbap."MATNR"                    AS "Material",
        vbap."ARKTX"                    AS "Description",
        objk."SERNR"                    AS "Serial",
        z."SERNR2"                      AS "VIN"
    FROM "SAPHANADB"."VBAK" vbak
    INNER JOIN "SAPHANADB"."VBAP" vbap
        ON vbap."MANDT" = vbak."MANDT"
       AND vbap."VBELN" = vbak."VBELN"
       AND LPAD(TO_VARCHAR(vbap."POSNR"), 6, '0') = '000010'
       AND vbap."MATNR" LIKE 'Z%'
    INNER JOIN "SAPHANADB"."SER02" ser02
        ON ser02."MANDT" = vbak."MANDT"
       AND ser02."SDAUFNR" = vbak."VBELN"
       AND LPAD(TO_VARCHAR(ser02."POSNR"), 6, '0') = '000010'
    INNER JOIN "SAPHANADB"."OBJK" objk
        ON objk."MANDT" = ser02."MANDT"
       AND objk."OBKNR" = ser02."OBKNR"
    LEFT JOIN "SAPHANADB"."ZTSD002" z
        ON z."MANDT" = objk."MANDT"
       AND z."WERKS" = '3091'
       AND z."SERNR" = objk."SERNR"
    WHERE vbak."MANDT" = '{sql_quote(sap_client)}'
      AND vbak."VKORG" = '{sql_quote(sales_org)}'
),
gi AS (
    SELECT DISTINCT
        obj."MANDT"                     AS "MANDT",
        obj."Serial"                    AS "Serial",
        obj."VIN"                       AS "VIN",
        obj."Sales Order"               AS "Sales Order",
        obj."SO Created Date"           AS "SO Created Date",
        obj."Material"                  AS "Material",
        obj."Description"               AS "Description",
        gi."MBLNR"                      AS "PGI Material Doc",
        gi."ZEILE"                      AS "PGI Item Raw",
        LPAD(TO_VARCHAR(gi."ZEILE"), 4, '0') AS "PGI Item",
        gi."BUDAT_MKPF"                 AS "PGI Date",
        gi."BWART"                      AS "PGI Movement Type"
    FROM obj
    INNER JOIN "SAPHANADB"."NSDM_V_MSEG" gi
        ON gi."MANDT" = obj."MANDT"
       AND gi."KDAUF" = obj."Sales Order"
       AND LPAD(TO_VARCHAR(gi."KDPOS"), 6, '0') = '000010'
       AND gi."WERKS" = '3111'
       AND gi."BWART" = '601'
       AND gi."BUDAT_MKPF" >= '{sql_quote(start_yyyymmdd)}'
       AND gi."BUDAT_MKPF" <= '{sql_quote(end_yyyymmdd)}'
)
SELECT
    gi."Serial"                        AS "Serial",
    gi."VIN"                           AS "Chassis",
    gi."Sales Order"                   AS "Sales Order",
    gi."SO Created Date"               AS "SO Created Date",
    gi."Material"                      AS "Material",
    gi."Description"                   AS "Description",
    gi."PGI Material Doc"              AS "PGI Material Doc",
    gi."PGI Item"                      AS "PGI Item",
    gi."PGI Date"                      AS "PGI Date",
    gi."PGI Movement Type"             AS "PGI Movement Type",
    rev."MBLNR"                        AS "Reversal Material Doc",
    LPAD(TO_VARCHAR(rev."ZEILE"), 4, '0') AS "Reversal Item",
    rev."BUDAT_MKPF"                   AS "Reversal Date",
    rev."BWART"                        AS "Reversal Movement Type",
    CASE
        WHEN rev."MBLNR" IS NOT NULL THEN 'Reversed'
        ELSE 'Valid PGI'
    END                                AS "PGI Status"
FROM gi
LEFT JOIN "SAPHANADB"."NSDM_V_MSEG" rev
    ON rev."MANDT" = gi."MANDT"
   AND rev."SMBLN" = gi."PGI Material Doc"
   AND rev."SMBLP" = gi."PGI Item Raw"
   AND rev."BWART" = '602'
ORDER BY gi."PGI Date" DESC, gi."PGI Material Doc" DESC
"""


def fetch_pgi_audit_df(
    conn: Any,
    start_yyyymmdd: str,
    end_yyyymmdd: str,
    sap_client: str,
    sales_org: str,
) -> pd.DataFrame:
    sql = build_pgi_audit_sql(start_yyyymmdd, end_yyyymmdd, sap_client, sales_org)
    df = pd.read_sql(sql, conn)
    if df.empty:
        return df

    for col in df.columns:
        df[col] = df[col].fillna("").astype(str).str.strip()

    for col in ("PGI Date", "Reversal Date", "SO Created Date"):
        if col in df.columns:
            df[f"{col} ISO"] = df[col].map(iso_date)

    return df


def build_sold_vehicle_df(pgi_audit_df: pd.DataFrame) -> pd.DataFrame:
    if pgi_audit_df.empty:
        return pd.DataFrame(
            columns=[
                "Vehicle Key",
                "Serial",
                "Chassis",
                "Primary Sales Order",
                "Sales Orders",
                "Material",
                "Description",
                "SO Created Date",
                "First Valid PGI Date",
                "Latest Valid PGI Date",
                "Valid PGI Count",
                "Reversed PGI Count",
                "First Valid PGI Doc",
                "Latest Valid PGI Doc",
            ]
        )

    work = pgi_audit_df.copy()
    work["Vehicle Key"] = work.apply(
        lambda row: normalize_vehicle_id(row.get("Chassis")) or normalize_vehicle_id(row.get("Serial")),
        axis=1,
    )
    work = work[work["Vehicle Key"] != ""].copy()
    if work.empty:
        return pd.DataFrame()

    valid = work[work["PGI Status"] == "Valid PGI"].copy()
    if valid.empty:
        return pd.DataFrame()

    valid["PGI Date Parsed"] = valid["PGI Date"].map(parse_any_date)
    valid = valid.sort_values(
        ["Vehicle Key", "PGI Date Parsed", "PGI Material Doc", "Sales Order"],
        na_position="last",
    )

    reversed_counts = (
        work.assign(IsReversed=work["PGI Status"].eq("Reversed").astype(int))
        .groupby("Vehicle Key", as_index=False)["IsReversed"]
        .sum()
        .rename(columns={"IsReversed": "Reversed PGI Count"})
    )

    rows: List[Dict[str, Any]] = []
    for vehicle_key, group in valid.groupby("Vehicle Key", sort=False):
        first_row = group.iloc[0]
        last_row = group.iloc[-1]
        rows.append(
            {
                "Vehicle Key": vehicle_key,
                "Serial": first_non_blank(first_row.get("Serial"), last_row.get("Serial")),
                "Chassis": first_non_blank(first_row.get("Chassis"), last_row.get("Chassis")),
                "Primary Sales Order": clean(first_row.get("Sales Order")),
                "Sales Orders": " | ".join(sorted({clean(v) for v in group["Sales Order"].tolist() if clean(v)})),
                "Material": first_non_blank(first_row.get("Material"), last_row.get("Material")),
                "Description": first_non_blank(first_row.get("Description"), last_row.get("Description")),
                "SO Created Date": iso_date(first_row.get("SO Created Date")),
                "First Valid PGI Date": iso_date(first_row.get("PGI Date")),
                "Latest Valid PGI Date": iso_date(last_row.get("PGI Date")),
                "Valid PGI Count": int(len(group)),
                "First Valid PGI Doc": clean(first_row.get("PGI Material Doc")),
                "Latest Valid PGI Doc": clean(last_row.get("PGI Material Doc")),
            }
        )

    out = pd.DataFrame(rows)
    out = out.merge(reversed_counts, how="left", on="Vehicle Key")
    out["Reversed PGI Count"] = out["Reversed PGI Count"].fillna(0).astype(int)
    return out.sort_values(["First Valid PGI Date", "Serial", "Chassis"], na_position="last").reset_index(drop=True)


def build_vehicle_lookup_maps(sold_vehicle_df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    serial_map: Dict[str, Dict[str, Any]] = {}
    chassis_map: Dict[str, Dict[str, Any]] = {}
    so_map: Dict[str, Dict[str, Any]] = {}

    if sold_vehicle_df.empty:
        return {"serial": serial_map, "chassis": chassis_map, "sales_order": so_map}

    for _, row in sold_vehicle_df.iterrows():
        payload = row.to_dict()
        serial_key = normalize_vehicle_id(row.get("Serial"))
        chassis_key = normalize_vehicle_id(row.get("Chassis"))
        if serial_key and serial_key not in serial_map:
            serial_map[serial_key] = payload
        if chassis_key and chassis_key not in chassis_map:
            chassis_map[chassis_key] = payload

        sales_orders = [clean(row.get("Primary Sales Order"))]
        sales_orders.extend(clean(part) for part in clean(row.get("Sales Orders")).split("|"))
        for sales_order in sales_orders:
            sales_order = clean(sales_order)
            if sales_order and sales_order not in so_map:
                so_map[sales_order] = payload

    return {"serial": serial_map, "chassis": chassis_map, "sales_order": so_map}


def build_ticket_failure_timing_df(ticket_df: pd.DataFrame, sold_vehicle_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Ticket ID",
        "Match Source",
        "Matched Vehicle Key",
        "Matched Serial",
        "Matched Chassis",
        "Matched Sales Order",
        "Ticket Sales Order",
        "Ticket Serial ID",
        "Ticket Chassis Number",
        "Created On",
        "Created On ISO",
        "PGI Date",
        "Vehicle Delivery Date",
        "Date of Purchase",
        "Date of Purchase ISO",
        "Timing Date Source",
        "Failure Days",
        "Status",
        "Dealer Name",
        "Repair Shop",
        "Ticket Type",
        "Claim Scope",
        "Vehicle Dispatch Date",
        "Vehicle Dispatch Source",
        "Registered Product",
        "Product",
        "Amount Including Tax",
    ]
    if ticket_df.empty or sold_vehicle_df.empty:
        return pd.DataFrame(columns=columns)

    lookups = build_vehicle_lookup_maps(sold_vehicle_df)
    rows: List[Dict[str, Any]] = []

    for _, row in ticket_df.iterrows():
        ticket_serial = clean(row.get("Serial ID"))
        ticket_chassis = clean(row.get("Chassis Number"))
        ticket_sales_order = first_non_blank(row.get("Sales Order"), row.get("Vehicle Dispatch Sales Order"))

        matched: Optional[Dict[str, Any]] = None
        match_source = ""

        serial_key = normalize_vehicle_id(ticket_serial)
        chassis_key = normalize_vehicle_id(ticket_chassis)

        if serial_key and serial_key in lookups["serial"]:
            matched = lookups["serial"][serial_key]
            match_source = "serial"
        elif chassis_key and chassis_key in lookups["chassis"]:
            matched = lookups["chassis"][chassis_key]
            match_source = "chassis"
        elif ticket_sales_order and ticket_sales_order in lookups["sales_order"]:
            matched = lookups["sales_order"][ticket_sales_order]
            match_source = "sales_order"

        delivery_date = ""
        pgi_date = ""
        timing_source = "missing"
        matched_vehicle_key = ""
        matched_serial = ""
        matched_chassis = ""
        matched_sales_order = ""

        if matched is not None:
            pgi_date = clean(matched.get("First Valid PGI Date"))
            delivery_date = pgi_date
            timing_source = f"valid_pgi_{match_source}"
            matched_vehicle_key = clean(matched.get("Vehicle Key"))
            matched_serial = clean(matched.get("Serial"))
            matched_chassis = clean(matched.get("Chassis"))
            matched_sales_order = clean(matched.get("Primary Sales Order"))
        else:
            purchase_date = iso_date(row.get("Date of Purchase"))
            if purchase_date:
                delivery_date = purchase_date
                timing_source = "purchase_fallback"

        created_iso = iso_date(row.get("Created On"))
        purchase_iso = iso_date(row.get("Date of Purchase"))
        failure_days = diff_days(created_iso, delivery_date)

        rows.append(
            {
                "Ticket ID": clean(row.get("Ticket ID")),
                "Match Source": match_source,
                "Matched Vehicle Key": matched_vehicle_key,
                "Matched Serial": matched_serial,
                "Matched Chassis": matched_chassis,
                "Matched Sales Order": matched_sales_order,
                "Ticket Sales Order": ticket_sales_order,
                "Ticket Serial ID": ticket_serial,
                "Ticket Chassis Number": ticket_chassis,
                "Created On": clean(row.get("Created On")),
                "Created On ISO": created_iso,
                "PGI Date": pgi_date,
                "Vehicle Delivery Date": delivery_date,
                "Date of Purchase": clean(row.get("Date of Purchase")),
                "Date of Purchase ISO": purchase_iso,
                "Timing Date Source": timing_source,
                "Failure Days": failure_days if failure_days is not None and failure_days >= 0 else "",
                "Status": clean(row.get("Status")),
                "Dealer Name": clean(row.get("Dealer Name")),
                "Repair Shop": clean(row.get("Repair Shop")),
                "Ticket Type": clean(row.get("Ticket Type")),
                "Claim Scope": classify_claim_scope(row.get("Claim Scope"), row.get("Ticket Type")),
                "Vehicle Dispatch Date": clean(row.get("Vehicle Dispatch Date")),
                "Vehicle Dispatch Source": clean(row.get("Vehicle Dispatch Source")),
                "Registered Product": clean(row.get("Registered Product")),
                "Product": clean(row.get("Product")),
                "Amount Including Tax": clean(row.get("Amount Including Tax")),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=columns)

    return out.sort_values(
        ["Timing Date Source", "Vehicle Delivery Date", "Created On ISO", "Ticket ID"],
        na_position="last",
    ).reset_index(drop=True)


def build_vehicle_ticket_summary_df(sold_vehicle_df: pd.DataFrame, ticket_failure_df: pd.DataFrame) -> pd.DataFrame:
    if sold_vehicle_df.empty:
        return sold_vehicle_df

    summary = sold_vehicle_df.copy()
    summary["Ticket Count"] = 0
    summary["First Ticket Created On"] = ""
    summary["Latest Ticket Created On"] = ""
    summary["First Failure Days"] = ""

    if ticket_failure_df.empty:
        return summary

    matched = ticket_failure_df[ticket_failure_df["Matched Vehicle Key"] != ""].copy()
    if matched.empty:
        return summary

    rows: List[Dict[str, Any]] = []
    for vehicle_key, group in matched.groupby("Matched Vehicle Key", sort=False):
        created_dates = [d for d in group["Created On ISO"].tolist() if clean(d)]
        failure_days = [int(v) for v in group["Failure Days"].tolist() if clean(v) != ""]
        rows.append(
            {
                "Vehicle Key": vehicle_key,
                "Ticket Count": int(len(group)),
                "First Ticket Created On": min(created_dates) if created_dates else "",
                "Latest Ticket Created On": max(created_dates) if created_dates else "",
                "First Failure Days": min(failure_days) if failure_days else "",
            }
        )

    extra = pd.DataFrame(rows)
    if extra.empty:
        return summary

    summary = summary.merge(extra, how="left", on="Vehicle Key", suffixes=("", "_new"))
    for col in ("Ticket Count", "First Ticket Created On", "Latest Ticket Created On", "First Failure Days"):
        if f"{col}_new" in summary.columns:
            summary[col] = summary[f"{col}_new"].where(summary[f"{col}_new"].notna(), summary[col])
            summary = summary.drop(columns=[f"{col}_new"])

    summary["Ticket Count"] = summary["Ticket Count"].fillna(0).astype(int)
    return summary


def build_overview_df(
    pgi_audit_df: pd.DataFrame,
    sold_vehicle_df: pd.DataFrame,
    ticket_df: pd.DataFrame,
    ticket_failure_df: pd.DataFrame,
    ticket_source_label: str,
    start_yyyymmdd: str,
    end_yyyymmdd: str,
    sales_org: str,
    sap_client: str,
) -> pd.DataFrame:
    matched_ticket_count = 0
    purchase_fallback_count = 0
    missing_count = 0
    if not ticket_failure_df.empty:
        matched_ticket_count = int((ticket_failure_df["Matched Vehicle Key"] != "").sum())
        purchase_fallback_count = int((ticket_failure_df["Timing Date Source"] == "purchase_fallback").sum())
        missing_count = int((ticket_failure_df["Timing Date Source"] == "missing").sum())

    rows = [
        {"Metric": "Run At", "Value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        {"Metric": "Date Range Start", "Value": start_yyyymmdd},
        {"Metric": "Date Range End", "Value": end_yyyymmdd},
        {"Metric": "SAP Client", "Value": sap_client},
        {"Metric": "Sales Org", "Value": sales_org},
        {"Metric": "PGI Events", "Value": int(len(pgi_audit_df))},
        {"Metric": "Valid PGI Events", "Value": int((pgi_audit_df.get("PGI Status", pd.Series(dtype=str)) == "Valid PGI").sum())},
        {"Metric": "Reversed PGI Events", "Value": int((pgi_audit_df.get("PGI Status", pd.Series(dtype=str)) == "Reversed").sum())},
        {"Metric": "Unique Sold Vehicles", "Value": int(len(sold_vehicle_df))},
        {"Metric": "Ticket Source", "Value": ticket_source_label},
        {"Metric": "Tickets Loaded", "Value": int(len(ticket_df))},
        {"Metric": "Matched Tickets", "Value": matched_ticket_count},
        {"Metric": "Purchase Fallback Tickets", "Value": purchase_fallback_count},
        {"Metric": "Missing Timing Source Tickets", "Value": missing_count},
    ]
    return pd.DataFrame(rows)


def autosize_sheet(writer: pd.ExcelWriter, sheet_name: str, df: pd.DataFrame) -> None:
    worksheet = writer.sheets.get(sheet_name)
    if worksheet is None:
        return
    for idx, col in enumerate(df.columns, start=1):
        max_len = max([len(clean(col))] + [len(clean(v)) for v in df[col].head(5000).tolist()])
        worksheet.column_dimensions[chr(64 + idx) if idx <= 26 else worksheet.cell(row=1, column=idx).column_letter].width = min(max_len + 2, 48)


def write_js_global(path: Path, global_name: str, payload: Any, *, is_text: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value_text = json.dumps(payload, ensure_ascii=False) if is_text else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(f"globalThis.{global_name} = {value_text};\n", encoding="utf-8")


def build_sold_vehicle_summary_payload(sold_vehicle_df: pd.DataFrame, workbook_path: Path) -> Dict[str, Any]:
    total_sold_vehicles = int(len(sold_vehicle_df))
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourceWorkbook": workbook_path.name,
        "sourceSheet": "sold_vehicles",
        "totalSoldVehicles": total_sold_vehicles,
        "totalVehicles": total_sold_vehicles,
    }


def export_sold_vehicle_summary(summary_payload: Dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VEHICLE_FAILURE_TIMING_SUMMARY_JSON_PATH.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_js_global(
        VEHICLE_FAILURE_TIMING_SUMMARY_JS_PATH,
        "ANALYSIS_VEHICLE_FAILURE_TIMING_SUMMARY",
        summary_payload,
    )


def export_analysis_ticket_failure_assets(ticket_failure_df: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ticket_failure_df.to_csv(ANALYSIS_TICKET_FAILURE_TIMING_CSV_PATH, index=False, encoding="utf-8-sig")
    csv_text = ANALYSIS_TICKET_FAILURE_TIMING_CSV_PATH.read_text(encoding="utf-8-sig")
    write_js_global(
        ANALYSIS_TICKET_FAILURE_TIMING_JS_PATH,
        "ANALYSIS_TICKET_FAILURE_TIMING_CSV_TEXT",
        csv_text,
        is_text=True,
    )


def export_workbook(
    output_path: Path,
    overview_df: pd.DataFrame,
    sold_vehicle_df: pd.DataFrame,
    ticket_failure_df: pd.DataFrame,
    unmatched_ticket_df: pd.DataFrame,
    pgi_audit_df: pd.DataFrame,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        overview_df.to_excel(writer, index=False, sheet_name="overview")
        sold_vehicle_df.to_excel(writer, index=False, sheet_name="sold_vehicles")
        ticket_failure_df.to_excel(writer, index=False, sheet_name="ticket_failure_timing")
        unmatched_ticket_df.to_excel(writer, index=False, sheet_name="unmatched_tickets")
        pgi_audit_df.to_excel(writer, index=False, sheet_name="pgi_audit")

        autosize_sheet(writer, "overview", overview_df)
        autosize_sheet(writer, "sold_vehicles", sold_vehicle_df)
        autosize_sheet(writer, "ticket_failure_timing", ticket_failure_df)
        autosize_sheet(writer, "unmatched_tickets", unmatched_ticket_df)
        autosize_sheet(writer, "pgi_audit", pgi_audit_df)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export 2025/2026 sold vehicles plus ticket failure timing to Excel.",
    )
    parser.add_argument("--start-date", default="20250101", help="SAP date filter start in YYYYMMDD format.")
    parser.add_argument("--end-date", default="20261231", help="SAP date filter end in YYYYMMDD format.")
    parser.add_argument("--sap-client", default=DEFAULT_SAP_CLIENT)
    parser.add_argument("--sales-org", default=DEFAULT_SALES_ORG)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--firebase-db-url", default=DEFAULT_FIREBASE_DB_URL)
    parser.add_argument("--firebase-sa-path", default=DEFAULT_FIREBASE_SA_PATH)
    parser.add_argument("--firebase-root", default=DEFAULT_FIREBASE_ROOT)
    parser.add_argument("--local-ticket-csv", default=DEFAULT_LOCAL_TICKET_CSV)
    parser.add_argument("--skip-firebase", action="store_true", help="Skip loading tickets from Firebase.")
    parser.add_argument("--output", default="", help="Optional full output path for the workbook.")
    parser.add_argument(
        "--latest-output",
        default=str(DEFAULT_LATEST_WORKBOOK_PATH),
        help="Stable workbook path updated after each run. Use empty string to disable.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info(
        "Export start: start=%s end=%s sales_org=%s sap_client=%s",
        args.start_date,
        args.end_date,
        args.sales_org,
        args.sap_client,
    )

    if pyodbc is None:
        raise SystemExit("pyodbc is required to query SAP HANA.")

    with pyodbc.connect(args.dsn, autocommit=True) as conn:
        pgi_audit_df = fetch_pgi_audit_df(
            conn=conn,
            start_yyyymmdd=args.start_date,
            end_yyyymmdd=args.end_date,
            sap_client=args.sap_client,
            sales_org=args.sales_org,
        )

    logger.info("PGI audit rows: %s", len(pgi_audit_df))
    sold_vehicle_df = build_sold_vehicle_df(pgi_audit_df)
    logger.info("Sold vehicles: %s", len(sold_vehicle_df))

    ticket_df = pd.DataFrame()
    ticket_source_label = "none"
    if not args.skip_firebase:
        try:
            firebase_init(args.firebase_db_url, args.firebase_sa_path)
            ticket_df = load_tickets_from_firebase(args.firebase_root)
            ticket_source_label = "firebase"
            logger.info("Tickets loaded from Firebase: %s", len(ticket_df))
        except Exception as exc:
            logger.warning("Firebase load failed; falling back to local CSV: %s", exc)

    local_ticket_df = pd.DataFrame()
    if args.local_ticket_csv:
        try:
            local_ticket_df = load_tickets_from_csv(args.local_ticket_csv)
            logger.info("Tickets loaded from local CSV: %s", len(local_ticket_df))
            if ticket_df.empty:
                ticket_df = local_ticket_df
                ticket_source_label = f"local_csv:{Path(args.local_ticket_csv).name}"
            else:
                before_rows = len(ticket_df)
                ticket_df = merge_ticket_sources(ticket_df, local_ticket_df)
                ticket_source_label = f"{ticket_source_label}+local_csv_enriched:{Path(args.local_ticket_csv).name}"
                logger.info("Tickets enriched from local CSV: %s -> %s", before_rows, len(ticket_df))
        except Exception as exc:
            logger.warning("Local CSV load failed; continuing without local enrichment: %s", exc)

    ticket_failure_df = build_ticket_failure_timing_df(ticket_df, sold_vehicle_df)
    unmatched_ticket_df = ticket_failure_df[ticket_failure_df["Matched Vehicle Key"] == ""].copy() if not ticket_failure_df.empty else pd.DataFrame()
    sold_vehicle_df = build_vehicle_ticket_summary_df(sold_vehicle_df, ticket_failure_df)
    overview_df = build_overview_df(
        pgi_audit_df=pgi_audit_df,
        sold_vehicle_df=sold_vehicle_df,
        ticket_df=ticket_df,
        ticket_failure_df=ticket_failure_df,
        ticket_source_label=ticket_source_label,
        start_yyyymmdd=args.start_date,
        end_yyyymmdd=args.end_date,
        sales_org=args.sales_org,
        sap_client=args.sap_client,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output) if args.output else OUTPUT_DIR / f"vehicle_failure_timing_2025_2026_{timestamp}.xlsx"
    export_workbook(
        output_path=output_path,
        overview_df=overview_df,
        sold_vehicle_df=sold_vehicle_df,
        ticket_failure_df=ticket_failure_df,
        unmatched_ticket_df=unmatched_ticket_df,
        pgi_audit_df=pgi_audit_df,
    )
    export_analysis_ticket_failure_assets(ticket_failure_df)

    latest_output = Path(args.latest_output) if clean(args.latest_output) else None
    if latest_output:
        latest_output.parent.mkdir(parents=True, exist_ok=True)
        if output_path.resolve() != latest_output.resolve():
            shutil.copy2(output_path, latest_output)
        logger.info("Latest workbook updated: %s", latest_output)

    sold_vehicle_summary = build_sold_vehicle_summary_payload(
        sold_vehicle_df=sold_vehicle_df,
        workbook_path=latest_output if latest_output else output_path,
    )
    export_sold_vehicle_summary(sold_vehicle_summary)

    logger.info("Workbook written: %s", output_path)
    logger.info("Analysis ticket timing assets written: %s, %s", ANALYSIS_TICKET_FAILURE_TIMING_CSV_PATH, ANALYSIS_TICKET_FAILURE_TIMING_JS_PATH)
    logger.info("Sold vehicle summary written: %s", VEHICLE_FAILURE_TIMING_SUMMARY_JSON_PATH)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
