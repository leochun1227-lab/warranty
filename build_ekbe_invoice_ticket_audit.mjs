import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const INPUT_JSON = path.join(ROOT, "outputs", "repairers_2026", "ekbe_invoice_ticket_audit_data.json");
const OUT_XLSX = path.join(ROOT, "outputs", "repairers_2026", "ekbe_invoice_ticket_audit.xlsx");
const PREVIEW_PNG = path.join(ROOT, "outputs", "repairers_2026", "ekbe_invoice_ticket_audit_summary.png");

function clean(value) {
  return value == null ? "" : String(value).trim();
}

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

function tableMatrix(rows, preferredHeaders = []) {
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
  const matrix = [headers];
  for (const row of rows) {
    matrix.push(headers.map((header) => normalizeCell(row?.[header])));
  }
  return { headers, matrix };
}

function writeSheet(workbook, name, rows, preferredHeaders = []) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const { headers, matrix } = tableMatrix(rows.length ? rows : [{}], preferredHeaders);
  const rowCount = Math.max(matrix.length, 1);
  const colCount = Math.max(headers.length, 1);
  sheet.getRangeByIndexes(0, 0, rowCount, colCount).values = matrix;
  const lastCol = colName(colCount - 1);
  sheet.getRange(`A1:${lastCol}1`).format = {
    fill: "#1F2A44",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#CAD7EA" },
  };
  if (rowCount > 1) {
    sheet.getRange(`A2:${lastCol}${rowCount}`).format = {
      borders: { preset: "all", style: "thin", color: "#E3EAF5" },
    };
  }
  sheet.freezePanes.freezeRows(1);
  sheet.getRange(`A:${lastCol}`).format.autofitColumns();
  for (let i = 0; i < colCount; i += 1) {
    const header = headers[i] || "";
    const col = colName(i);
    if (/Short Text|Status|Candidate|Reason|Rule|DSN|source/i.test(header)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 240;
      sheet.getRange(`${col}:${col}`).format.wrapText = true;
    } else if (/Ticket ID|Invoice|PO|Date|Currency|State/i.test(header)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 130;
    } else if (/Amount|Cost|Value|Qty|Count/i.test(header)) {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 120;
    } else {
      sheet.getRange(`${col}:${col}`).format.columnWidthPx = 160;
    }
  }
  for (let i = 0; i < headers.length; i += 1) {
    if (/Amount|Cost|Value/i.test(headers[i])) {
      const col = colName(i);
      sheet.getRange(`${col}2:${col}${rowCount}`).format.numberFormat = "$#,##0.00";
    } else if (/Count|Qty/i.test(headers[i])) {
      const col = colName(i);
      sheet.getRange(`${col}2:${col}${rowCount}`).format.numberFormat = "#,##0";
    }
  }
  return sheet;
}

function writeSummary(workbook, summaryRows) {
  const sheet = workbook.worksheets.add("Summary");
  sheet.showGridLines = false;
  sheet.getRange("A1:D1").merge();
  sheet.getRange("A1").values = [["EKBE Invoice Ticket Audit"]];
  sheet.getRange("A2:D2").merge();
  sheet.getRange("A2").values = [["Invoice rows are from SAP EKBE where VGABE = 2, joined to EKPO.TXZ01 short text by PO/item. The parsed short-text ticket ID is treated as the source of truth; PO mismatch is shown as a warning."]];
  sheet.getRange("A1:D2").format = {
    fill: "#EEF4FF",
    font: { color: "#14213D" },
    borders: { preset: "all", style: "thin", color: "#CAD7EA" },
    wrapText: true,
  };
  sheet.getRange("A1").format = { font: { bold: true, size: 16, color: "#14213D" } };

  const matrix = [["Metric", "Value"], ...summaryRows.map((row) => [row.Metric || "", row.Value ?? ""])];
  sheet.getRangeByIndexes(3, 0, matrix.length, 2).values = matrix;
  sheet.getRange(`A4:B4`).format = {
    fill: "#1F2A44",
    font: { bold: true, color: "#FFFFFF" },
    borders: { preset: "all", style: "thin", color: "#CAD7EA" },
  };
  sheet.getRange(`A5:B${3 + matrix.length}`).format = {
    borders: { preset: "all", style: "thin", color: "#E3EAF5" },
    wrapText: true,
  };
  sheet.getRange("A:A").format.columnWidthPx = 260;
  sheet.getRange("B:B").format.columnWidthPx = 760;
  sheet.freezePanes.freezeRows(4);
  return sheet;
}

async function main() {
  const payload = JSON.parse(await fs.readFile(INPUT_JSON, "utf8"));
  const workbook = Workbook.create();

  writeSummary(workbook, payload.summary || []);
  writeSheet(workbook, "Unified by Short Text", payload.unified_by_short_text || [], [
    "PO", "PO Item", "Invoice Doc", "Posting Date", "Amount Doc", "Currency", "Short Text",
    "Parsed Ticket ID", "Repair Detail C4C Ticket ID", "Repair Detail Ticket ID",
    "Repair Shop", "Repair State", "Repair Detail PO", "Repair Detail Invoice Number",
    "Repair Detail Approved Cost", "Repair Detail PO Equals SAP PO", "Parsed Ticket Is One Of PO Candidate Tickets",
    "Match Status",
  ]);
  writeSheet(workbook, "Short Text Unreadable", payload.short_text_unreadable || [], [
    "PO", "PO Item", "Invoice Doc", "Posting Date", "Amount Doc", "Currency", "Short Text",
    "Parsed Ticket ID", "Parse Note", "Match Status", "PO Candidate Ticket IDs", "PO Candidate Count",
  ]);
  writeSheet(workbook, "Parsed Not In Repair Detail", payload.parsed_ticket_not_in_repair_detail || [], [
    "PO", "PO Item", "Invoice Doc", "Posting Date", "Amount Doc", "Currency", "Short Text",
    "Parsed Ticket ID", "Parse Note", "Match Status", "PO Candidate Ticket IDs", "PO Candidate Count",
  ]);
  writeSheet(workbook, "Raw EKBE Invoice Rows", payload.raw || [], [
    "PO", "PO Item", "Invoice Doc", "Fiscal Year", "History Item", "Posting Date", "Amount Local",
    "Amount Doc", "Currency", "Short Text", "Parsed Ticket ID", "Parse Note", "Match Status",
    "Short Text Ticket Found In Repair Detail", "Repair Detail PO Equals SAP PO",
    "PO Candidate Ticket IDs", "Raw History Type", "Raw History Category", "Raw PO Item Net Value",
  ]);

  const summaryPreview = await workbook.render({ sheetName: "Summary", autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(PREVIEW_PNG, new Uint8Array(await summaryPreview.arrayBuffer()));

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
    unified_by_short_text: (payload.unified_by_short_text || []).length,
    short_text_unreadable: (payload.short_text_unreadable || []).length,
    parsed_ticket_not_in_repair_detail: (payload.parsed_ticket_not_in_repair_detail || []).length,
    raw: (payload.raw || []).length,
  }, null, 2));
}

await main();
