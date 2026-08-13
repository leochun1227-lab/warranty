import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const INPUT_JSON = path.join(ROOT, "outputs", "repairers_2026", "sap_authoritative_repair_payments.json");
const OUT_XLSX = path.join(ROOT, "outputs", "repairers_2026", "sap_authoritative_repair_payments.xlsx");
const FALLBACK_OUT_XLSX = path.join(ROOT, "outputs", "repairers_2026", "sap_authoritative_repair_payments_with_cancelled.xlsx");
const PREVIEW_PNG = path.join(ROOT, "outputs", "repairers_2026", "sap_authoritative_repair_payments_summary.png");

function colName(index) {
  let n = index + 1;
  let out = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

function normalizeCell(value) {
  if (value == null) return "";
  if (typeof value === "number" || typeof value === "boolean") return value;
  return String(value);
}

function matrixFromRows(rows, preferredHeaders = []) {
  const seen = new Set();
  const headers = [];
  for (const header of preferredHeaders) {
    if (!seen.has(header)) {
      headers.push(header);
      seen.add(header);
    }
  }
  for (const row of rows) {
    for (const key of Object.keys(row || {})) {
      if (!seen.has(key)) {
        headers.push(key);
        seen.add(key);
      }
    }
  }
  return {
    headers,
    matrix: [headers, ...rows.map((row) => headers.map((header) => normalizeCell(row?.[header])))],
  };
}

function formatSheet(sheet, headers, rowCount) {
  const lastCol = colName(Math.max(headers.length - 1, 0));
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastCol}1`).format = {
    fill: "#14213D",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#C8D3E6" },
  };
  if (rowCount > 1) {
    sheet.getRange(`A2:${lastCol}${rowCount}`).format = {
      borders: { preset: "all", style: "thin", color: "#E4EAF4" },
    };
  }
  sheet.freezePanes.freezeRows(1);
  for (let i = 0; i < headers.length; i += 1) {
    const header = headers[i];
    const col = colName(i);
    if (/Short Text|Rule|Repairer|Invoice Docs|POs|Compare|Match/i.test(header)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 230;
      sheet.getRange(`${col}:${col}`).format.wrapText = true;
    } else if (/Ticket ID|Vendor|Date|Currency|PO$|PO Item|Invoice Doc/i.test(header)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 130;
    } else if (/Amount|Cost|Value|Price/i.test(header)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 130;
      sheet.getRange(`${col}2:${col}${rowCount}`).format.numberFormat = "$#,##0.00";
    } else if (/Count|Qty|Line/i.test(header)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 100;
      sheet.getRange(`${col}2:${col}${rowCount}`).format.numberFormat = "#,##0";
    } else {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 150;
    }
  }
}

function writeSheet(workbook, name, rows, preferredHeaders = []) {
  const sheet = workbook.worksheets.add(name);
  const safeRows = rows.length ? rows : [{}];
  const { headers, matrix } = matrixFromRows(safeRows, preferredHeaders);
  const rowCount = matrix.length;
  const colCount = Math.max(headers.length, 1);
  sheet.getRangeByIndexes(0, 0, rowCount, colCount).values = matrix;
  formatSheet(sheet, headers.length ? headers : ["No Data"], rowCount);
}

function writeMeta(workbook, metaRows) {
  const sheet = workbook.worksheets.add("Read Me");
  sheet.showGridLines = false;
  sheet.getRange("A1:D1").merge();
  sheet.getRange("A1").values = [["SAP Authoritative Repair PO Costs"]];
  sheet.getRange("A2:D2").merge();
  sheet.getRange("A2").values = [["C4C is used only to decide repairer-analysis eligibility and remove customer/self repairs. SAP is authoritative for PO date, ticket ID parsed from PO short text, PO net value, cancellation status, and repairer/vendor. Invoice fields are status/completion info only."]];
  sheet.getRange("A1:D2").format = {
    fill: "#EAF3FF",
    font: { color: "#14213D" },
    borders: { preset: "all", style: "thin", color: "#C8D3E6" },
    wrapText: true,
  };
  sheet.getRange("A1").format = { font: { bold: true, size: 16, color: "#14213D" } };
  const matrix = [["Metric", "Value"], ...metaRows.map((row) => [row.Metric || "", row.Value ?? ""])];
  sheet.getRangeByIndexes(3, 0, matrix.length, 2).values = matrix;
  sheet.getRange("A4:B4").format = {
    fill: "#14213D",
    font: { bold: true, color: "#FFFFFF" },
    borders: { preset: "all", style: "thin", color: "#C8D3E6" },
  };
  sheet.getRange(`A5:B${3 + matrix.length}`).format = {
    borders: { preset: "all", style: "thin", color: "#E4EAF4" },
    wrapText: true,
  };
  sheet.getRange("A:A").format.columnWidthPx = 260;
  sheet.getRange("B:B").format.columnWidthPx = 760;
}

async function main() {
  const payload = JSON.parse(await fs.readFile(INPUT_JSON, "utf8"));
  const workbook = Workbook.create();
  writeMeta(workbook, payload.meta || []);
  writeSheet(workbook, "SAP Ticket Summary", payload.summary || [], [
    "SAP Ticket ID",
    "SAP First PO Date",
    "SAP Last PO Date",
    "SAP Last Invoice Date",
    "SAP Repairer Name",
    "SAP Repairer Vendor ID",
    "SAP PO Net Value",
    "SAP Cancelled PO Net Value",
    "SAP PO Cancelled",
    "SAP Invoice Status",
    "SAP Signed Invoice Amount",
    "SAP Currency",
    "SAP Line Count",
    "SAP Invoice Row Count",
    "SAP Invoice Docs",
    "SAP POs",
    "C4C Eligible For Repairer Analysis",
    "C4C Eligibility Match Source",
    "C4C Eligibility Reason",
    "C4C Service Technician",
    "C4C Status",
    "C4C PO",
    "C4C Chassis Number",
    "C4C Repair Detail Exists",
    "C4C Repair Detail Repairer",
    "C4C Repair Detail PO",
    "C4C Repair Detail Approved Cost",
  ]);
  writeSheet(workbook, "SAP PO Lines", payload.po_lines || payload.invoice_lines || [], [
    "SAP Ticket ID",
    "SAP PO Date",
    "SAP Last Invoice Date",
    "SAP Last Invoice Doc",
    "SAP PO",
    "SAP PO Item",
    "SAP Repairer Name",
    "SAP Repairer Vendor ID",
    "SAP PO Net Value",
    "SAP Active PO Net Value",
    "SAP Cancelled PO Net Value",
    "SAP PO Cancelled",
    "SAP PO Header Deletion Indicator",
    "SAP PO Item Deletion Indicator",
    "SAP PO Currency",
    "SAP Invoice Status",
    "SAP Signed Invoice Amount Doc",
    "SAP Short Text",
    "C4C Eligible For Repairer Analysis",
    "C4C Eligibility Match Source",
    "C4C Eligibility Reason",
    "C4C Service Technician",
    "C4C Status",
    "C4C PO",
    "C4C Chassis Number",
    "C4C Repair Detail Exists",
    "C4C Repair Detail Repairer",
    "C4C Repair Detail PO",
    "C4C Repair Detail Approved Cost",
  ]);
  writeSheet(workbook, "C4C Ineligible Excluded", payload.c4c_ineligible || [], [
    "SAP Ticket ID",
    "SAP PO Date",
    "SAP PO",
    "SAP PO Item",
    "SAP Repairer Name",
    "SAP Repairer Vendor ID",
    "SAP PO Net Value",
    "SAP PO Cancelled",
    "SAP Short Text",
    "C4C Eligibility Match Source",
    "C4C Eligibility Reason",
    "C4C Service Technician",
    "C4C Status",
    "C4C PO",
    "C4C Chassis Number",
  ]);
  writeSheet(workbook, "Short Text Unreadable", payload.short_text_unreadable || [], [
    "SAP PO",
    "SAP PO Item",
    "SAP PO Date",
    "SAP Last Invoice Doc",
    "SAP Last Invoice Date",
    "SAP Repairer Name",
    "SAP PO Net Value",
    "SAP PO Cancelled",
    "SAP PO Currency",
    "SAP Short Text",
    "Short Text Parse Note",
  ]);
  writeSheet(workbook, "C4C Compare", payload.c4c_compare || [], [
    "SAP Ticket ID",
    "SAP Last PO Date",
    "SAP Last Invoice Date",
    "SAP Repairer Name",
    "C4C Repair Detail Repairer",
    "Repairer Match",
    "SAP POs",
    "C4C Repair Detail PO",
    "Any SAP PO Equals C4C PO",
    "SAP PO Net Value",
    "C4C Repair Detail Approved Cost",
    "SAP Minus C4C Approved Cost",
    "C4C Repair Detail Exists",
  ]);

  const preview = await workbook.render({ sheetName: "Read Me", autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(PREVIEW_PNG, new Uint8Array(await preview.arrayBuffer()));
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  let savedPath = OUT_XLSX;
  try {
    await xlsx.save(OUT_XLSX);
  } catch (error) {
    if (error && error.code === "EBUSY") {
      savedPath = FALLBACK_OUT_XLSX;
      await xlsx.save(savedPath);
    } else {
      throw error;
    }
  }

  const inspect = await workbook.inspect({
    kind: "sheet,table",
    tableMaxRows: 6,
    tableMaxCols: 8,
    maxChars: 5000,
  });
  console.log(inspect.ndjson);
  console.log(JSON.stringify({
    output: savedPath,
    preview: PREVIEW_PNG,
    summary: (payload.summary || []).length,
    po_lines: (payload.po_lines || payload.invoice_lines || []).length,
    c4c_ineligible: (payload.c4c_ineligible || []).length,
    unreadable: (payload.short_text_unreadable || []).length,
    compare: (payload.c4c_compare || []).length,
  }, null, 2));
}

await main();
