Patch: add SAP material prices and preferred parts cost to the daily fetch
===========================================================================

Goal
----
Populate 12 new fields on every `Sales Order Details.{item}` entry in Firebase
so the web export can render 3090 SO / 3110 PO / 3091 PO prices per material,
and write a daily preferred-parts cost report that uses:

1. `3110 PO` first
2. `3090 SO` as fallback when `3110 PO` is blank

`AmountIncludingTax` stays in the payload for reference only. It must not be
used as the parts cost metric.

Prerequisites
-------------
- Put `sap_material_prices.py` in the same directory as the fetch script.
- No new pip installs; the module reuses `pyodbc` and `pandas` already used by
  the existing HANA queries.

Edit 1 of 3: import the module near the top of the fetch script
---------------------------------------------------------------
Add this near the other imports (roughly line 20-30, wherever the existing
imports live):

    from sap_material_prices import fetch_material_price_map, enrich_detail_rows

Edit 2 of 3: populate prices inside build_ticket_fields_payload
---------------------------------------------------------------
Find the function that builds the Firebase payload for each ticket.

Right after the DataFrame is grouped and before the per-ticket loop begins,
add this once-per-run price lookup:

    # Fetch material prices from SAP once for the whole snapshot.
    all_materials = set()
    if "Material" in final_df.columns:
        all_materials.update(
            m for m in final_df["Material"].astype(str).str.strip() if m
        )
    logger.info("Fetching SAP prices for %s unique materials", len(all_materials))
    try:
        material_price_map = fetch_material_price_map(sorted(all_materials))
    except Exception as exc:
        logger.warning("Material price fetch failed, exports will show blanks: %s", exc)
        material_price_map = {}

Then, after `details` is fully built for a ticket and before the
`payload[f"{base}/Sales Order Details"] = details` line, enrich the rows:

    enrich_detail_rows(details, material_price_map)
    payload[f"{base}/Sales Order Details"] = details

Edit 3 of 3: keep the daily preferred-parts cost report
------------------------------------------------------
The daily fetch also writes the cost report used by `delivery_flow.html`.
That report should already follow the same preferred rule:

- use `3110 PO Net Price` first
- if blank, fall back to `3090 SO Net Price`
- multiply by `Order Qty`
- convert CNY to AUD with the configured fixed rate

The webpage should read the Firebase report directly. The Excel export can
still self-fetch on demand, but it should use the same preferred-cost rule as
the daily Firebase report.

Verify
------
Run the fetch script normally. Then in Firebase console open one ticket's
`Sales Order Details` node - each item should now have keys like
`"3090 SO Net Price"`, `"3090 SO Currency"`, `"3090 SO Date"`, `"3090 SO Doc"`,
`"3110 PO Net Price"`, etc. Empty strings are fine (means SAP had no history
for that material on that org).

Open `delivery_flow.html` in the browser, hit the Open Parts (Not Issued)
export button, and confirm the Item Detail sheet has the 12 SAP price columns
plus the preferred-cost column that uses `3110 PO` first and `3090 SO`
fallback. The price columns are AUD-normalized (`CNY -> AUD` at rate
`CNY_TO_AUD_RATE = 5`, edit in one place if the rate changes).

Rollback
--------
Removing the edits and re-running the fetch will leave the old fields stale in
Firebase but harmless. The HTML will just render blanks if a field is missing.
