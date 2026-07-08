import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "C:/Users/Leo.Li/Documents/GitHub/warranty/outputs/employee_removed_approved_reconciliation";
const data = JSON.parse(await fs.readFile(`${outputDir}/reconciliation.json`, "utf8"));

const workbook = Workbook.create();

function colName(index) {
  let n = index + 1;
  let out = "";
  while (n > 0) {
    const m = (n - 1) % 26;
    out = String.fromCharCode(65 + m) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

function cleanCell(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "number") return value;
  return String(value);
}

function writeRows(sheetName, rows, tableName) {
  const sheet = workbook.worksheets.add(sheetName);
  sheet.showGridLines = false;
  const headers = rows.length ? Object.keys(rows[0]) : ["Result"];
  const values = [
    headers,
    ...(rows.length ? rows.map((row) => headers.map((header) => cleanCell(row[header]))) : [["No rows"]]),
  ];
  const range = sheet.getRangeByIndexes(0, 0, values.length, headers.length);
  range.values = values;
  sheet.getRangeByIndexes(0, 0, 1, headers.length).format = {
    fill: "#0F3B63",
    font: { bold: true, color: "#FFFFFF" },
  };
  range.format.borders = { preset: "inside", style: "thin", color: "#E2E8F0" };
  sheet.freezePanes.freezeRows(1);
  const address = `A1:${colName(headers.length - 1)}${values.length}`;
  try {
    const table = sheet.tables.add(address, true, tableName);
    table.showFilterButton = true;
    table.style = "TableStyleMedium2";
  } catch {}
  range.format.autofitColumns();
  range.format.autofitRows();
  return sheet;
}

const summarySheet = writeRows("Summary", data.summary, "SummaryTable");
summarySheet.getRange("A1:C1").format = {
  fill: "#123D5A",
  font: { bold: true, color: "#FFFFFF" },
};
summarySheet.getRange("A:C").format.wrapText = true;

writeRows("Approved_in_Removed", data.approved_in_removed, "ApprovedInRemoved");
writeRows("Approved_not_in_Removed", data.approved_not_in_removed, "ApprovedNotInRemoved");
writeRows("Removed_in_Approved", data.removed_in_approved, "RemovedInApproved");
writeRows("Removed_not_in_Approved", data.removed_not_in_approved, "RemovedNotInApproved");
writeRows("Missing_Raw_Fields", data.missing_raw_fields, "MissingRawFields");
writeRows("Method", data.method, "MethodTable");

const sourceRows = [
  { Source: "Approved/Unapproved export", Path: data.source_files.approved },
  { Source: "Employee workload export", Path: data.source_files.workload },
];
writeRows("Source_Files", sourceRows, "SourceFilesTable");

const preview = await workbook.render({
  sheetName: "Summary",
  autoCrop: "all",
  scale: 1,
  format: "png",
});
await fs.writeFile(`${outputDir}/summary-preview.png`, new Uint8Array(await preview.arrayBuffer()));

const inspect = await workbook.inspect({
  kind: "table",
  sheetId: "Summary",
  range: "A1:C20",
  tableMaxRows: 20,
  tableMaxCols: 3,
  maxChars: 3000,
});
console.log(inspect.ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(`${outputDir}/employee_removed_approved_reconciliation.xlsx`);
console.log(`${outputDir}/employee_removed_approved_reconciliation.xlsx`);
