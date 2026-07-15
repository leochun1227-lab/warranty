import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const source = JSON.parse(await fs.readFile("po_compare_source.json", "utf8"));
const outputDir = "po_compare_output";
await fs.mkdir(outputDir, { recursive: true });

function toDate(value) {
  if (!value || typeof value !== "string") return null;
  const normalized = value.includes(" ") ? value.replace(" ", "T") : value;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("PO价格对比");
sheet.showGridLines = false;

const headers = [
  "Ticket",
  "PO价格",
  "AmountIncludingTax价格",
  "差异百分比",
  "价格对比",
  "StatusGroup",
  "StatusText",
  "ClaimScope",
  "ApproveDate",
  "PODate",
  "DealerName",
  "TicketType",
  "TicketTypeText",
  "CreatedDate",
  "ResolvedDate",
  "AmountValue",
  "ClaimApprovedOn",
];

sheet.getRange("A1:Q1").values = [headers];

const body = source.map((row) => [
  row.ticket,
  row.po_price,
  row.amount_including_tax,
  null,
  null,
  row.status_group ?? null,
  row.status_text ?? null,
  row.claim_scope ?? null,
  toDate(row.approved_date),
  toDate(row.po_date),
  row.dealer_name ?? null,
  row.ticket_type ?? null,
  row.ticket_type_text ?? null,
  toDate(row.created_date),
  toDate(row.resolved_date),
  row.amount_value ?? null,
  toDate(row.claim_approved_on),
]);

const lastRow = body.length + 1;
sheet.getRange(`A2:Q${lastRow}`).values = body;

sheet.getRange("D2").formulas = [[`=IF(OR(ISBLANK(B2),ISBLANK(C2),B2=0),"",(C2-B2)/B2)`]];
sheet.getRange(`D2:D${lastRow}`).fillDown();
sheet.getRange("E2").formulas = [[`=IF(ISBLANK(C2),"AmountIncludingTax缺失",IF(ABS(B2-C2)<0.005,"一致","不一致"))`]];
sheet.getRange(`E2:E${lastRow}`).fillDown();

sheet.getRange("A1:Q1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "outside", style: "medium", color: "#1F4E78" },
};
sheet.getRange("A1:Q1").format.rowHeight = 30;

sheet.getRange(`A2:Q${lastRow}`).format = {
  verticalAlignment: "center",
  borders: { insideHorizontal: { style: "thin", color: "#E5E7EB" } },
};
sheet.getRange(`A2:A${lastRow}`).format.horizontalAlignment = "center";
sheet.getRange(`B2:D${lastRow}`).format.horizontalAlignment = "right";
sheet.getRange(`E2:E${lastRow}`).format.horizontalAlignment = "center";
sheet.getRange(`B2:C${lastRow}`).format.numberFormat = "#,##0.00";
sheet.getRange(`D2:D${lastRow}`).format.numberFormat = "0.0%;[Red]-0.0%";
sheet.getRange(`P2:P${lastRow}`).format.numberFormat = "#,##0.00";
for (const dateColumn of ["I", "J", "N", "O", "Q"]) {
  sheet.getRange(`${dateColumn}2:${dateColumn}${lastRow}`).format.numberFormat = "yyyy-mm-dd";
}

const widths = {
  A: 12,
  B: 13,
  C: 21,
  D: 13,
  E: 20,
  F: 15,
  G: 24,
  H: 15,
  I: 14,
  J: 14,
  K: 30,
  L: 12,
  M: 30,
  N: 14,
  O: 14,
  P: 14,
  Q: 17,
};
for (const [column, width] of Object.entries(widths)) {
  sheet.getRange(`${column}:${column}`).format.columnWidth = width;
}

const dataRange = sheet.getRange(`A2:Q${lastRow}`);
dataRange.conditionalFormats.addCustom(
  `=AND($C2<>"",ABS($B2-$C2)>0.005)`,
  { fill: "#FDECEC", font: { color: "#9B1C1C" } },
);
dataRange.conditionalFormats.addCustom(
  `=AND($B2<>"",ISBLANK($C2))`,
  { fill: "#FFF4CC", font: { color: "#92400E" } },
);

sheet.freezePanes.freezeRows(1);
sheet.freezePanes.freezeColumns(4);

const table = sheet.tables.add(`A1:Q${lastRow}`, true, "POPriceComparison");
table.showFilterButton = true;

const check = await workbook.inspect({
  kind: "table",
  range: `PO价格对比!A1:Q8`,
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 17,
  maxChars: 12000,
});
console.log(check.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const preview = await workbook.render({
  sheetName: "PO价格对比",
  range: "A1:Q20",
  scale: 1,
  format: "png",
});
await fs.writeFile(`${outputDir}/po_price_comparison_preview.png`, new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(`${outputDir}/po_price_comparison.xlsx`);
console.log(JSON.stringify({ output: `${outputDir}/po_price_comparison.xlsx`, rows: source.length }));
