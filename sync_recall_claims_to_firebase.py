from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import firebase_admin
import requests
from firebase_admin import credentials, db
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry


BASE_URL = os.getenv(
    "C4C_BASE_URL",
    "https://longcui-automobile-cpi-tyrbc1k7.it-cpi010-rt.cpi.cn40.apps.platform.sapcloud.cn",
)
PATH = "/http/PC4C/Ticket/queryOdataBatch"
USERNAME = os.getenv("C4C_USERNAME", "XIEYONGDONG@newgonow.cn")
PASSWORD = os.getenv("C4C_PASSWORD", "Max@sap2022")

ROOT_DIR = Path(__file__).resolve().parent
FIREBASE_DB_URL = os.getenv(
    "FIREBASE_DB_URL",
    "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app",
)
FIREBASE_SA_PATH = os.getenv("FIREBASE_SA_PATH", str(ROOT_DIR / "firebase-service-account.json"))
RECALL_CLAIMS_TABLE_PATH = os.getenv("RECALL_CLAIMS_TABLE_PATH", "recallClaim")

RECALL_CLAIMS_TICKET_TYPE = "Z011"
DEFAULT_TOP = 20000
DEFAULT_SKIP = 0
TIMEOUT = int(os.getenv("C4C_TIMEOUT_SECONDS", "60"))
C4C_PAGE_RETRIES = max(1, int(os.getenv("C4C_PAGE_RETRIES", "4")))
C4C_PAGE_RETRY_SLEEP_SECONDS = max(0.0, float(os.getenv("C4C_PAGE_RETRY_SLEEP_SECONDS", "4")))
VERIFY_SSL = os.getenv("C4C_VERIFY_SSL", "true").strip().lower() not in {"0", "false", "no"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("recall_claims_sync")


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def build_recall_claims_url(top: int = DEFAULT_TOP, skip: int = DEFAULT_SKIP) -> str:
    typecode = quote(RECALL_CLAIMS_TICKET_TYPE, safe="")
    return BASE_URL.rstrip("/") + PATH + f"?$top={top}&$skip={skip}&$typecode={typecode}"


def norm(value: Any) -> Any:
    return None if value is None else value


def as_clean_str(value: Any) -> Optional[str]:
    value = norm(value)
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def sanitize_fb_key(value: Any) -> str:
    text = str(value or "").strip()
    for ch in [".", "$", "#", "[", "]", "/"]:
        text = text.replace(ch, "_")
    return text.strip()


def compact_lookup_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def first_clean_value(data: Dict[str, Any], candidates: Iterable[str]) -> str:
    for candidate in candidates:
        value = as_clean_str(data.get(candidate))
        if value:
            return value

    normalized = {compact_lookup_key(key): key for key in data.keys()}
    for candidate in candidates:
        key = normalized.get(compact_lookup_key(candidate))
        if key:
            value = as_clean_str(data.get(key))
            if value:
                return value
    return ""


def firebase_init() -> None:
    if getattr(firebase_admin, "_apps", None) and firebase_admin._apps:
        return
    if not Path(FIREBASE_SA_PATH).exists():
        raise SystemExit(f"FIREBASE_SA_PATH is invalid: {FIREBASE_SA_PATH}")
    if not FIREBASE_DB_URL:
        raise SystemExit("FIREBASE_DB_URL is required")

    cred = credentials.Certificate(FIREBASE_SA_PATH)
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})


def fetch_recall_claims_page(session: requests.Session, top: int, skip: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    url = build_recall_claims_url(top, skip)
    logger.info("GET %s", url)
    last_err: BaseException | None = None

    for attempt in range(1, C4C_PAGE_RETRIES + 1):
        try:
            response = session.get(
                url,
                auth=HTTPBasicAuth(USERNAME, PASSWORD),
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
                verify=VERIFY_SSL,
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"HTTP {response.status_code} for Recall Claims top={top} skip={skip}; "
                    f"body={response.text[:500]}"
                )

            payload = response.json()
            rows = list(payload.get("data", []))
            meta = {
                "pageSize": payload.get("pageSize"),
                "pageNumber": payload.get("pageNumber"),
                "count": payload.get("count"),
                "totalCount": payload.get("totalCount"),
            }
            return rows, meta
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, ValueError) as exc:
            last_err = exc
            if attempt >= C4C_PAGE_RETRIES:
                break
            wait = C4C_PAGE_RETRY_SLEEP_SECONDS * attempt
            logger.warning(
                "Recall Claims request failed (try %s/%s), wait %.1fs: %s",
                attempt,
                C4C_PAGE_RETRIES,
                wait,
                exc,
            )
            if wait:
                time.sleep(wait)

    raise RuntimeError(f"Recall Claims request failed after {C4C_PAGE_RETRIES} tries: {last_err}")


def involved_party_items(involved_parties: Any) -> List[Dict[str, Any]]:
    if isinstance(involved_parties, list):
        return [party for party in involved_parties if isinstance(party, dict)]
    if isinstance(involved_parties, dict):
        if first_clean_value(involved_parties, ["InvolvedPartyRoleID", "Involved Party Role ID", "RoleID"]):
            return [involved_parties]
        return [party for party in involved_parties.values() if isinstance(party, dict)]
    return []


def involved_parties_to_roles(involved_parties: Any) -> Dict[str, Any]:
    roles: Dict[str, Any] = {}
    for party in involved_party_items(involved_parties):
        role_id = first_clean_value(
            party,
            ["InvolvedPartyRoleID", "Involved Party Role ID", "RoleID", "Role ID"],
        )
        role_key = sanitize_fb_key(role_id)
        if role_key:
            roles[role_key] = {key: norm(value) for key, value in party.items()}
    return roles


def field_group(ticket_data: Dict[str, Any], fields: Dict[str, List[str]]) -> Dict[str, Any]:
    return {name: first_clean_value(ticket_data, candidates) for name, candidates in fields.items()}


def build_recall_claims_payload(
    rows: List[Dict[str, Any]],
    api_meta: Dict[str, Any],
    *,
    top: int = DEFAULT_TOP,
    skip: int = DEFAULT_SKIP,
) -> Dict[str, Any]:
    product_fields = {
        "product": ["Product", "ProductName", "RegisteredProduct", "Registered Product"],
        "description": ["Description", "ProductDescription", "Product Description"],
        "serialId": ["SerialID", "Serial ID", "Serial"],
        "productId": ["ProductCode", "Product ID", "RegisteredProductCode", "Registered Product Code"],
        "warranty": ["Warranty", "WarrantyDuration", "Warranty Duration"],
        "warrantyFrom": ["WarrantyFrom", "Warranty From"],
        "warrantyTo": ["WarrantyTo", "Warranty To"],
        "dateOfPurchase": ["DateOfPurchase", "Date of Purchase", "PurchaseDate"],
        "chassisNumber": ["ChassisNumber", "Chassis Number", "VIN"],
        "brand": ["Brand"],
        "dealershipPurchasedFrom": ["Dealership Purchased from", "DealershipPurchasedFrom", "DealerName", "Dealer Name"],
        "modelSalesforce": ["Model (SalesForce)", "ModelSalesForce", "Model"],
    }
    customer_fields = {
        "customer": ["Customer", "CustomerName", "AccountName", "Account", "ServiceRequesterName", "TicketName"],
        "address": ["Address", "CustomerAddress", "ServiceRequesterAddress"],
        "mobile": ["Mobile", "ServiceRequesterMobile", "Service Requester Mobile"],
        "phone": ["Phone", "ServiceRequesterPhone", "Service Requester Phone"],
        "email": ["E-Mail", "Email", "ServiceRequesterEmail", "Service Requester Email"],
        "serviceRequesterFirstName": ["Service Requester First Name", "ServiceRequesterFirstName"],
        "serviceRequesterLastName": ["Service Requester Last Name", "ServiceRequesterLastName"],
        "serviceRequesterPhone": ["Service Requester Phone", "ServiceRequesterPhone"],
        "serviceRequesterPostalCode": ["Service Requester Postal Code", "ServiceRequesterPostalCode", "PostalCode"],
    }
    pricing_fields = {
        "claimTotalAmount": ["ClaimTotalAmount", "Claim Total Amount", "AmountIncludingTax"],
        "amountIncludingTax": ["AmountIncludingTax", "Amount Including Tax"],
        "currency": ["Currency", "TransactionCurrency", "CurrencyCode"],
    }

    tickets: Dict[str, Any] = {}
    skipped_other_types = 0
    for row in rows:
        ticket_type = first_clean_value(row, ["TicketType", "Ticket Type", "ProcessType", "TypeCode"])
        if ticket_type.upper() != RECALL_CLAIMS_TICKET_TYPE:
            skipped_other_types += 1
            continue

        ticket_id = first_clean_value(row, ["TicketID", "Ticket ID"])
        ticket_key = sanitize_fb_key(ticket_id)
        if not ticket_key:
            continue

        tickets[ticket_key] = {
            "ticketId": ticket_id,
            "ticketType": ticket_type,
            "ticketTypeText": first_clean_value(row, ["TicketTypeText", "Ticket Type Text", "Ticket Type"]),
            "statusCode": first_clean_value(row, ["TicketStatus", "StatusCode", "Status"]),
            "statusText": first_clean_value(row, ["TicketStatusText", "Status Text", "Status"]),
            "createdOn": first_clean_value(row, ["CreatedOn", "Created On"]),
            "changedOn": first_clean_value(row, ["ChangeOnDateTime", "ChangedOn", "Changed On"]),
            "product": field_group(row, product_fields),
            "customer": field_group(row, customer_fields),
            "pricingData": field_group(row, pricing_fields),
            "ticket": row,
            "roles": involved_parties_to_roles(row.get("InvolvedParties")),
            "syncedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    raw_row_count = api_meta.get("totalCount") or api_meta.get("count") or len(rows)
    return {
        "meta": {
            "ticketType": RECALL_CLAIMS_TICKET_TYPE,
            "tableName": "Recall Claims Tickets",
            "source": "C4C Ticket queryOdataBatch with typecode",
            "requestUrl": build_recall_claims_url(top, skip),
            "apiCount": raw_row_count,
            "rawRowCount": raw_row_count,
            "returnedTicketRows": len(rows),
            "uniqueTicketCount": len(tickets),
            "count": len(tickets),
            "skippedOtherTypes": skipped_other_types,
            "syncedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "tickets": tickets,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Recall Claims Z011 from C4C to Firebase.")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="SAP raw flattened row limit. Default: 20000.")
    parser.add_argument("--skip", type=int, default=DEFAULT_SKIP, help="SAP raw flattened row offset. Default: 0.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and build payload, but do not write Firebase.")
    parser.add_argument("--print-url", action="store_true", help="Print the exact Recall Claims request URL and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    url = build_recall_claims_url(args.top, args.skip)
    if args.print_url:
        print(url)
        return

    if not USERNAME or not PASSWORD:
        raise SystemExit("Please set C4C_USERNAME / C4C_PASSWORD")

    started = time.time()
    logger.info("Recall Claims standalone sync started.")
    logger.info("This program does not send any CCSRQ_DPY_ROLE_CD $filter.")

    session = build_session()
    try:
        rows, api_meta = fetch_recall_claims_page(session, args.top, args.skip)
        payload = build_recall_claims_payload(rows, api_meta, top=args.top, skip=args.skip)
        meta = payload["meta"]
        logger.info(
            "Recall Claims counts: api/raw=%s returned=%s uniqueTickets=%s skippedOtherTypes=%s",
            meta["rawRowCount"],
            meta["returnedTicketRows"],
            meta["uniqueTicketCount"],
            meta["skippedOtherTypes"],
        )

        if args.dry_run:
            print(json.dumps(meta, ensure_ascii=False, indent=2))
            return

        firebase_init()
        db.reference(RECALL_CLAIMS_TABLE_PATH).set(payload)
        logger.info("Wrote Recall Claims payload to Firebase path %s", RECALL_CLAIMS_TABLE_PATH)
    finally:
        session.close()

    logger.info("Recall Claims standalone sync done. Total elapsed: %.1fs", time.time() - started)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Recall Claims standalone sync failed: %s", exc)
        sys.exit(1)
