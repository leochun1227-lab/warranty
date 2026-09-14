# Recall model refresh

Both `sync_recall_claims_to_firebase.py` and
`fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py` call
`recall_models.enrich_recall_models` before writing `recallClaim`.

Each run reads SAP HANA client **800**, sales organization **3110**. Chassis/serial
values join `OBJK -> SER02 -> VBAK`; VINs first map through `ZTSD002` plant **3091**.
The model is the `VBAP.ARKTX` description from sales order item **000010** (displayed
as 0010), for example `2026 SRC22S`. `VBAP.MATNR` is used internally to detect
conflicting results but is not written to Firebase.

Firebase fields under `recallClaim/tickets/{ticketId}`:

- `model`: description from the most recent matching order, for example `2026 SRC22S`.
- `modelLookup`: status, lookup time, input vehicle identifiers, sales orders,
  and descriptive candidates for review. These contain no material codes.

The former duplicate `modelDescription` field is removed during refresh. A blank
description produces `missing_model_description`; it never falls back to a code.

Several orders with the same material count as one model. Different material
codes, including disagreements between VIN and serial, produce `conflict` and
leave `model` unset. Unmatched or invalid identifiers also leave it unset. A
successful lookup replaces any previous model, so stale results cannot hide a
new conflict. A HANA failure aborts before the recall Firebase write.

The existing Firebase transaction still merges saved postcodes from the latest
database state using `preserve_recall_postcodes`. Model refresh never recalculates
or replaces postcodes. Standalone `--dry-run` includes HANA lookup but writes no
Firebase data.

Validation: `python -m unittest discover -s tests -p "test_recall*.py"`.
