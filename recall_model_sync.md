# Recall model refresh

Both `sync_recall_claims_to_firebase.py` and
`fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py` call
`recall_models.enrich_recall_models` before writing `recallClaim`.

Each run reads SAP HANA client **800**, sales organization **3110**. Chassis/serial
values join `OBJK -> SER02 -> VBAK`; VINs first map through `ZTSD002` plant **3091**.
The model is the exact `VBAP.MATNR` from sales order item **000010** (displayed as
0010). It is not the vehicle's serial, VIN, or shortened marketing name.

Firebase fields under `recallClaim/tickets/{ticketId}`:

- `model`: unique material code, for example `Z12112414`.
- `modelDescription`: description from the most recent matching order.
- `modelLookup`: status, lookup time, input vehicle identifiers, sales orders,
  and material candidates for review.

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
