import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const OUT_DIR = "C:/Users/Leo.Li/Documents/GitHub/warranty/outputs/sap_po_history_pure_ekbe_20260820";
const INPUT_JSON = path.join(OUT_DIR, "sap_po_history_pure.json");
const OUT_XLSX = path.join(OUT_DIR, "sap_po_history_pure_ekbe.xlsx");
const FALLBACK_XLSX = path.join(OUT_DIR, "sap_po_history_pure_ekbe_alt.xlsx");
const PREVIEW_PNG = path.join(OUT_DIR, "sap_po_history_pure_preview.png");

function colLetter(n) {
  let s = "";
  while (n > 0) {
    const m = (n - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

function rowsToMatrix(rows, headers) {
  return [headers, ...rows.map((row) => headers.map((h) => row?.[h] ?? ""))];
}

function styleTable(sheet, rowCount, colCount) {
  const last = `${colLetter(colCount)}${Math.max(rowCount, 1)}`;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.getRange(`A1:${colLetter(colCount)}1`).format = {
    fill: "#16324F",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
  };
  sheet.getRange(`A1:${last}`).format.borders = {
    insideHorizontal: { style: "thin", color: "#E3E8EF" },
    bottom: { style: "thin", color: "#CDD6E3" },
  };
  sheet.getRange(`A1:${last}`).format.autofitRows();
  sheet.getRange(`A1:${last}`).format.autofitColumns();
}

const payload = JSON.parse(await fs.readFile(INPUT_JSON, "utf8"));
const workbook = Workbook.create();

const readme = workbook.worksheets.add("Read Me");
readme.showGridLines = false;
readme.getRange("A1:E1").merge();
readme.getRange("A1").values = [["Pure SAP PO History From User SQL"]];
readme.getRange("A1").format = {
  fill: "#EAF3F8",
  font: { bold: true, color: "#16324F", size: 14 },
};
readme.getRange("A3:B9").values = [
  ["Metric", "Value"],
  ["Source", payload.meta.source],
  ["Created at", payload.meta.createdAt],
  ["SAP grouped row count", payload.meta.rowCount],
  ["Rows exported to workbook", payload.meta.exportedRows],
  ["Full export", payload.meta.isFullExport ? "Yes" : "No, preview capped"],
  ["Max rows setting", payload.meta.maxRows],
];
styleTable(readme, 9, 5);
readme.getRange("B3:B9").format.columnWidth = 46;

const headers = [
  "PO Number",
  "PO Item",
  "Latest Valid GR",
  "Latest Valid GR Posting Date",
  "GR Amt",
  "Invoice Amt",
  "Down Payment Amt",
  "Down Payment Clearing",
];
const data = workbook.worksheets.add("PO History");
const matrix = rowsToMatrix(payload.rows || [], headers);
data.getRangeByIndexes(0, 0, Math.max(matrix.length, 1), headers.length).values = matrix.length ? matrix : [headers];
styleTable(data, Math.max(matrix.length, 1), headers.length);
data.getRange(`D2:D${Math.max(matrix.length, 2)}`).format.numberFormat = "yyyy-mm-dd";
data.getRange(`E2:H${Math.max(matrix.length, 2)}`).format.numberFormat = "#,##0.00";
data.getRange("A:A").format.columnWidth = 18;
data.getRange("B:D").format.columnWidth = 18;
data.getRange("E:H").format.columnWidth = 20;

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  maxChars: 2000,
});
console.log(errors.ndjson);

const previewRows = Math.min((payload.rows || []).length + 1, 35);
const preview = await workbook.render({
  sheetName: "PO History",
  range: `A1:H${Math.max(previewRows, 2)}`,
  scale: 1,
  format: "png",
});
await fs.writeFile(PREVIEW_PNG, new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
try {
  await output.save(OUT_XLSX);
  console.log(OUT_XLSX);
} catch (error) {
  if (error?.code !== "EBUSY") throw error;
  await output.save(FALLBACK_XLSX);
  console.log(FALLBACK_XLSX);
}
