import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const INPUT_JSON = path.join(ROOT, "outputs", "repairers_2026", "c4c_sap_po_repairer_compare.json");
const OUT_XLSX = path.join(ROOT, "outputs", "repairers_2026", "c4c_sap_po_repairer_compare.xlsx");
const PREVIEW_PNG = path.join(ROOT, "outputs", "repairers_2026", "c4c_sap_po_repairer_compare_summary.png");

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

function value(v) {
  if (v == null) return "";
  if (typeof v === "number" || typeof v === "boolean") return v;
  return String(v);
}

function matrixFromRows(rows, preferredHeaders = []) {
  const headers = [];
  const seen = new Set();
  for (const h of preferredHeaders) {
    if (!seen.has(h)) {
      headers.push(h);
      seen.add(h);
    }
  }
  for (const row of rows) {
    for (const h of Object.keys(row || {})) {
      if (!seen.has(h)) {
        headers.push(h);
        seen.add(h);
      }
    }
  }
  return {
    headers,
    matrix: [headers, ...rows.map((row) => headers.map((h) => value(row?.[h])))],
  };
}

function formatTable(sheet, headers, rowCount) {
  const lastCol = colName(Math.max(headers.length - 1, 0));
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastCol}1`).format = {
    fill: "#18324A",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#CAD7EA" },
  };
  if (rowCount > 1) {
    sheet.getRange(`A2:${lastCol}${rowCount}`).format = {
      borders: { preset: "all", style: "thin", color: "#E3EAF5" },
      wrapText: true,
    };
  }
  sheet.freezePanes.freezeRows(1);
  for (let i = 0; i < headers.length; i += 1) {
    const h = headers[i];
    const col = colName(i);
    if (/Service Technician|Repairer|Tickets|Status|Evidence|Rule|Source/i.test(h)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 260;
    } else if (/PO|Date|Vendor|Rows|Count/i.test(h)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 130;
    } else if (/Amount|Value|Cost/i.test(h)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 130;
      sheet.getRange(`${col}2:${col}${rowCount}`).format.numberFormat = "$#,##0.00";
    } else {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 160;
    }
  }
}

function writeSheet(workbook, name, rows, preferredHeaders = []) {
  const sheet = workbook.worksheets.add(name);
  const safeRows = rows.length ? rows : [{}];
  const { headers, matrix } = matrixFromRows(safeRows, preferredHeaders);
  sheet.getRangeByIndexes(0, 0, matrix.length, Math.max(headers.length, 1)).values = matrix;
  formatTable(sheet, headers.length ? headers : ["No Data"], matrix.length);
}

function writeSummary(workbook, payload) {
  const sheet = workbook.worksheets.add("Summary");
  sheet.showGridLines = false;
  sheet.getRange("A1:D1").merge();
  sheet.getRange("A1").values = [["C4C vs SAP PO Repairer Compare"]];
  sheet.getRange("A2:D2").merge();
  sheet.getRange("A2").values = [[
    "Compares C4C ERP Purchase Order ID + Service Technician against SAP active PO + SAP Repairer Name. SAP cancelled PO lines are excluded.",
  ]];
  sheet.getRange("A1:D2").format = {
    fill: "#EAF3FF",
    font: { color: "#14213D" },
    borders: { preset: "all", style: "thin", color: "#C8D3E6" },
    wrapText: true,
  };
  sheet.getRange("A1").format = { font: { bold: true, size: 16, color: "#14213D" } };
  const rows = payload.summary || [];
  const matrix = [["Metric", "Value"], ...rows.map((r) => [r.Metric || "", r.Value ?? ""])];
  sheet.getRangeByIndexes(3, 0, matrix.length, 2).values = matrix;
  sheet.getRange("A4:B4").format = {
    fill: "#18324A",
    font: { bold: true, color: "#FFFFFF" },
    borders: { preset: "all", style: "thin", color: "#CAD7EA" },
  };
  sheet.getRange(`A5:B${3 + matrix.length}`).format = {
    borders: { preset: "all", style: "thin", color: "#E3EAF5" },
    wrapText: true,
  };
  sheet.getRange("A:A").format.columnWidthPx = 320;
  sheet.getRange("B:B").format.columnWidthPx = 560;
}

async function main() {
  const payload = JSON.parse(await fs.readFile(INPUT_JSON, "utf8"));
  const workbook = Workbook.create();
  writeSummary(workbook, payload);
  writeSheet(workbook, "Common PO Compare", payload.common_po_compare || [], [
    "PO",
    "Repairer Match Status",
    "Match Evidence",
    "C4C Service Technician",
    "SAP Repairer Name",
    "C4C Normalized Repairer",
    "SAP Normalized Repairer",
    "C4C Rows",
    "SAP Active Rows",
    "SAP Active PO Net Value",
    "C4C Claim Amount",
    "SAP Ticket IDs",
    "C4C Tickets",
    "C4C Status",
    "SAP Invoice Status",
    "SAP PO Date",
  ]);
  writeSheet(workbook, "Repairer Mismatch", payload.repairer_mismatch || [], [
    "PO",
    "Repairer Match Status",
    "C4C Service Technician",
    "SAP Repairer Name",
    "C4C Normalized Repairer",
    "SAP Normalized Repairer",
    "C4C Rows",
    "SAP Active Rows",
    "SAP Active PO Net Value",
    "C4C Claim Amount",
    "SAP Ticket IDs",
    "C4C Tickets",
  ]);
  writeSheet(workbook, "SAP Only Active PO", payload.sap_only_active_po || [], [
    "PO",
    "SAP Repairer Name",
    "SAP Normalized Repairer",
    "SAP Active PO Net Value",
    "SAP Ticket IDs",
    "SAP Active Rows",
    "SAP PO Date",
    "SAP Invoice Status",
    "SAP Vendor IDs",
  ]);
  writeSheet(workbook, "C4C Only PO", payload.c4c_only_po || [], [
    "PO",
    "C4C Service Technician",
    "C4C Normalized Repairer",
    "C4C Claim Amount",
    "C4C Tickets",
    "C4C Rows",
    "C4C Status",
    "C4C Created On",
    "C4C Posting Date",
  ]);
  writeSheet(workbook, "Sources", [
    { Key: "C4C CSV", Value: payload.source?.c4c_csv || "" },
    { Key: "SAP Workbook", Value: payload.source?.sap_xlsx || "" },
    { Key: "SAP Cancelled Rule", Value: payload.source?.sap_cancelled_rule || "" },
  ], ["Key", "Value"]);

  const preview = await workbook.render({ sheetName: "Summary", autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(PREVIEW_PNG, new Uint8Array(await preview.arrayBuffer()));
  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(OUT_XLSX);
  const inspect = await workbook.inspect({
    kind: "sheet,table",
    tableMaxRows: 6,
    tableMaxCols: 8,
    maxChars: 5000,
  });
  console.log(inspect.ndjson);
  console.log(JSON.stringify({
    output: OUT_XLSX,
    preview: PREVIEW_PNG,
    common_po: (payload.common_po_compare || []).length,
    repairer_mismatch: (payload.repairer_mismatch || []).length,
    sap_only_active_po: (payload.sap_only_active_po || []).length,
    c4c_only_po: (payload.c4c_only_po || []).length,
  }, null, 2));
}

await main();
