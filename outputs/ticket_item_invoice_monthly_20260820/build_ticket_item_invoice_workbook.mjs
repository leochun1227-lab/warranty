import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const OUT_DIR = "C:/Users/Leo.Li/Documents/GitHub/warranty/outputs/ticket_item_invoice_monthly_20260820";
const INPUT_JSON = path.join(OUT_DIR, "ticket_item_invoice_monthly.json");
const OUT_XLSX = path.join(OUT_DIR, "ticket_item_invoice_monthly_detail.xlsx");
const FALLBACK_XLSX = path.join(OUT_DIR, "ticket_item_invoice_monthly_detail_rigorous.xlsx");

function rowsToMatrix(rows, preferredHeaders = []) {
  const seen = new Set();
  const headers = [];
  for (const h of preferredHeaders) {
    if (!seen.has(h)) {
      seen.add(h);
      headers.push(h);
    }
  }
  for (const row of rows) {
    for (const h of Object.keys(row || {})) {
      if (!seen.has(h)) {
        seen.add(h);
        headers.push(h);
      }
    }
  }
  return {
    headers,
    matrix: [headers, ...rows.map((row) => headers.map((h) => row?.[h] ?? ""))],
  };
}

function colLetter(n) {
  let s = "";
  while (n > 0) {
    const m = (n - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

function styleTable(sheet, rowCount, colCount) {
  const last = `${colLetter(colCount)}${Math.max(rowCount, 1)}`;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.getRange(`A1:${colLetter(colCount)}1`).format = {
    fill: "#14213D",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
  };
  sheet.getRange(`A1:${last}`).format.borders = {
    insideHorizontal: { style: "thin", color: "#E4EAF4" },
    top: { style: "thin", color: "#C8D3E6" },
    bottom: { style: "thin", color: "#C8D3E6" },
  };
  sheet.getRange(`A1:${last}`).format.autofitColumns();
  sheet.getRange(`A1:${last}`).format.autofitRows();
}

function writeSheet(workbook, name, rows, preferredHeaders) {
  const sheet = workbook.worksheets.add(name);
  const safeRows = rows.length ? rows : [{}];
  const { headers, matrix } = rowsToMatrix(safeRows, preferredHeaders);
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  styleTable(sheet, matrix.length, headers.length);
  for (let i = 0; i < headers.length; i++) {
    const h = headers[i].toLowerCase();
    const col = colLetter(i + 1);
    if (h.includes("date")) sheet.getRange(`${col}2:${col}${matrix.length}`).format.numberFormat = "yyyy-mm-dd";
    if (h.includes("amount") || h.includes("cost") || h.includes("value")) sheet.getRange(`${col}2:${col}${matrix.length}`).format.numberFormat = "$#,##0.00";
    if (h.includes("rows") || h.includes("count") || h.includes("tickets")) sheet.getRange(`${col}2:${col}${matrix.length}`).format.numberFormat = "#,##0";
    if (h.includes("avg")) sheet.getRange(`${col}2:${col}${matrix.length}`).format.numberFormat = "#,##0.00";
  }
  const widthCap = Math.min(headers.length, 20);
  for (let i = 1; i <= widthCap; i++) {
    sheet.getRange(`${colLetter(i)}:${colLetter(i)}`).format.columnWidth = i <= 3 ? 18 : 24;
  }
  return sheet;
}

const payload = JSON.parse(await fs.readFile(INPUT_JSON, "utf8"));
const workbook = Workbook.create();

const readme = workbook.worksheets.add("Read Me");
readme.showGridLines = false;
readme.getRange("A1:F1").merge();
readme.getRange("A1").values = [["Ticket item rows by invoice month"]];
readme.getRange("A1").format = { font: { bold: true, color: "#14213D" }, fill: "#EAF3FF" };
readme.getRange("A2:F2").merge();
readme.getRange("A2").values = [[payload.meta.dateRule]];
readme.getRange("A4:B15").values = [
  ["Metric", "Value"],
  ["Date from", payload.meta.dateFrom],
  ["Date to", payload.meta.dateTo],
  ["SAP invoice rows", payload.meta.sapRows],
  ["SAP row grain", payload.meta.sapRowGrain || ""],
  ["Parsed SAP ticket rows", payload.meta.parsedTicketRows],
  ["Recovered SAP rows", payload.meta.recoveredSapRows || 0],
  ["Unreadable SAP short text rows", payload.meta.unreadableSapRows],
  ["Ticket count", payload.meta.ticketCount],
  ["C4C item detail rows", payload.meta.itemDetailRows],
  ["C4C source", payload.meta.c4cPartsSource],
  ["Recovery rule", payload.meta.recoveryRule || ""],
];
styleTable(readme, 15, 6);
readme.getRange("B4:B15").format.columnWidth = 96;

writeSheet(workbook, "Monthly Summary", payload.monthly || [], [
  "Month", "Tickets", "C4C Item Rows", "C4C Item Rows Not Rejected", "Avg C4C Item Rows per Ticket",
  "C4C Item Qty", "SAP Invoice Row Count", "SAP Signed Invoice Amount",
]);

writeSheet(workbook, "Ticket Summary", payload.ticket_summary || [], [
  "Invoice Month", "SAP Ticket ID", "SAP First Invoice Date", "SAP Last Invoice Date", "SAP Invoice Docs", "SAP POs",
  "C4C Item Rows", "C4C Item Rows Not Rejected", "C4C Item Qty", "SAP Invoice Row Count", "SAP Signed Invoice Amount",
  "SAP Currency", "SAP Repairer Name", "C4C Ticket Status", "C4C Dealer Name", "C4C Sales Order", "C4C SO Created Date",
]);

writeSheet(workbook, "C4C Item Detail", payload.item_detail || [], [
  "Invoice Month", "SAP Last Invoice Date", "Ticket ID", "Sales Order", "Sales Order Item", "Material", "Description",
  "Order Qty", "Sales Unit", "Item Rejection Status", "Part Category", "Matched Keyword", "Preferred Line Cost (AUD)",
  "Dealer Name", "Ticket Status Text", "SAP Invoice Docs", "SAP POs",
]);

writeSheet(workbook, "SAP Invoice Rows", payload.sap_invoice_rows || [], [
  "SAP Invoice Date", "SAP First Invoice Date", "SAP Ticket ID", "SAP PO", "SAP PO Item", "SAP Invoice Doc", "SAP First Invoice Doc",
  "SAP Invoice Rows", "SAP Signed Invoice Amount Doc", "SAP GR Amt", "SAP Down Payment Amt", "SAP Down Payment Clearing",
  "SAP Latest Valid GR", "SAP Latest Valid GR Posting Date", "SAP Currency", "SAP Short Text", "SAP Short Text Parse Note",
  "C4C Recovery Method", "C4C Recovery Candidates", "SAP Repairer Name", "SAP Repairer Vendor ID", "SAP PO Date", "SAP Material",
]);

writeSheet(workbook, "Recovered SAP Rows", payload.sap_recovered_rows || [], [
  "SAP Invoice Date", "SAP First Invoice Date", "SAP Ticket ID", "SAP PO", "SAP PO Item", "SAP Invoice Doc", "SAP First Invoice Doc",
  "SAP Invoice Rows", "SAP Signed Invoice Amount Doc", "SAP GR Amt", "SAP Latest Valid GR", "SAP Latest Valid GR Posting Date",
  "SAP Currency", "SAP Short Text", "SAP Short Text Parse Note", "C4C Recovery Method", "C4C Recovery Candidates",
  "SAP Repairer Name", "SAP Repairer Vendor ID", "SAP PO Date",
]);

writeSheet(workbook, "Unreadable SAP Rows", payload.sap_unreadable_short_text || [], [
  "SAP Invoice Date", "SAP PO", "SAP PO Item", "SAP Invoice Doc", "SAP Short Text", "SAP Short Text Parse Note",
  "C4C Recovery Method", "C4C Recovery Candidates",
]);

const summary = workbook.worksheets.getItem("Monthly Summary");
const mRows = (payload.monthly || []).length + 1;
const chart = summary.charts.add("line", summary.getRange(`A1:C${mRows}`));
chart.title = "C4C item rows by SAP invoice month";
chart.hasLegend = true;
chart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 9 } };
chart.yAxis = { numberFormatCode: "#,##0" };
chart.setPosition("J2", "Q20");

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  maxChars: 2000,
});
console.log(errors.ndjson);

const preview = await workbook.render({ sheetName: "Monthly Summary", range: "A1:Q20", scale: 1, format: "png" });
await fs.writeFile(path.join(OUT_DIR, "workbook_monthly_preview.png"), new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
try {
  await output.save(OUT_XLSX);
  console.log(OUT_XLSX);
} catch (error) {
  if (error?.code !== "EBUSY") throw error;
  await output.save(FALLBACK_XLSX);
  console.log(FALLBACK_XLSX);
}
