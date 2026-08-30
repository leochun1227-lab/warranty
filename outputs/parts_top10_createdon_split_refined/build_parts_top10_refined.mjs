import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "C:/Users/Leo.Li/Documents/GitHub/warranty";
const oldWorkbookPath = path.join(root, "outputs/parts_top10_createdon_split/Parts_Top10_Failure_Components_CreatedOn.xlsx");
const classifiedCsvPath = path.join(root, "outputs/parts_classified.csv");
const outputDir = path.join(root, "outputs/parts_top10_createdon_split_refined");
const previewDir = path.join(outputDir, "previews");
const outputPath = path.join(outputDir, "Parts_Top10_Failure_Components_CreatedOn_refined.xlsx");

const components = [
  { component: "Marker Light", sheet: "01 Marker Light", aliases: ["marker light", "marker lights"] },
  { component: "Stop Light", sheet: "02 Stop Light", aliases: ["stop light", "stop lights"] },
  { component: "Tail Light", sheet: "03 Tail Light", aliases: ["tail light", "tail lights", "taillight", "taillights", "combination taillight", "combination taillights"] },
  { component: "Main Door", sheet: "04 Main Door", aliases: ["main door", "main doors"] },
  { component: "Reflector", sheet: "05 Reflector", aliases: ["reflector"] },
  { component: "Window Blade", sheet: "06 Window Blade", aliases: ["window blade", "window blades"] },
  { component: "Roof Hatch", sheet: "07 Roof Hatch", aliases: ["roof hatch", "roof hatches"] },
  { component: "Solar", sheet: "08 Solar", aliases: ["solar"] },
  { component: "Decal", sheet: "09 Decal", aliases: ["decal", "decals"] },
  { component: "Clip", sheet: "10 Clip", aliases: ["clip", "clips"] },
];

const outputHeaders = [
  "Ticket ID",
  "Series",
  "Claim Scope",
  "Status",
  "Claim Type",
  "Components",
  "Descriptions",
  "Created On",
  "Claim Approved On",
  "Changed On",
  "PGI Date",
  "Good Receive Date",
  "Created-PGI Days",
  "Posting Date",
  "Date of Purchase",
  "Warranty Cost AUD",
  "Chassis",
  "Serial ID",
  "Dealer",
  "Repairer",
  "Has Classified Part",
  "Classified Part Lines",
  "Part Categories",
  "Materials",
  "Purchase Orders",
  "Sales Orders",
  "Order Qty",
  "Preferred Line Cost AUD",
  "Item Rejection Status",
];

const normalize = value => String(value ?? "").trim().replace(/\s+/g, " ").toLowerCase();
const clean = value => String(value ?? "").trim();
const splitSemi = value => clean(value).split(";").map(part => part.trim()).filter(Boolean);
const numeric = value => {
  if (value === null || value === undefined || value === "") return 0;
  const parsed = Number(String(value).replace(/,/g, "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
};
const normalizeId = value => clean(value).replace(/^0+(?=\d)/, "");
const uniqueJoin = values => [...new Set(values.map(clean).filter(Boolean))].join("; ");

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (inQuotes) {
      if (ch === '"' && next === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        cell += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else if (ch !== "\r") {
      cell += ch;
    }
  }
  if (cell.length || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

function rowObject(headers, values) {
  const out = {};
  headers.forEach((header, idx) => {
    out[header] = values[idx] ?? "";
  });
  return out;
}

function getIndex(headers, name) {
  return headers.findIndex(header => normalize(header) === normalize(name));
}

function componentFromValue(value) {
  const wanted = normalize(value);
  return components.find(item => normalize(item.component) === wanted)?.component || clean(value);
}

function matchesComponentFromKeyword(row, item) {
  const keyword = normalize(row["Matched Keyword"]);
  const description = normalize(row.Description);
  return item.aliases.some(alias => keyword === normalize(alias)) ||
    item.aliases.some(alias => description.includes(normalize(alias)));
}

function fallbackFromOldRow(oldRow, oldHeaders, item) {
  const componentsIdx = getIndex(oldHeaders, "Components");
  const descIdx = getIndex(oldHeaders, "Descriptions");
  const materialIdx = getIndex(oldHeaders, "Materials");
  const categoryIdx = getIndex(oldHeaders, "Part Categories");
  const componentValues = splitSemi(oldRow[componentsIdx]).map(componentFromValue);
  const descValues = splitSemi(oldRow[descIdx]);
  const materialValues = splitSemi(oldRow[materialIdx]);
  const categoryValues = splitSemi(oldRow[categoryIdx]);
  const selectedIdxs = [];
  componentValues.forEach((component, idx) => {
    if (normalize(component) === normalize(item.component)) selectedIdxs.push(idx);
  });
  if (selectedIdxs.length === 0) return null;
  return {
    lines: selectedIdxs.length,
    descriptions: selectedIdxs.map(idx => descValues[idx]).filter(Boolean),
    materials: selectedIdxs.map(idx => materialValues[idx]).filter(Boolean),
    categories: categoryValues.length === componentValues.length
      ? selectedIdxs.map(idx => categoryValues[idx]).filter(Boolean)
      : [categoryForComponent(item.component)],
    purchaseOrders: splitSemi(oldRow[getIndex(oldHeaders, "Purchase Orders")]),
    salesOrders: splitSemi(oldRow[getIndex(oldHeaders, "Sales Orders")]),
    orderQty: oldRow[getIndex(oldHeaders, "Order Qty")] ?? "",
    cost: oldRow[getIndex(oldHeaders, "Preferred Line Cost AUD")] ?? "",
    rejectionStatus: oldRow[getIndex(oldHeaders, "Item Rejection Status")] ?? "",
  };
}

function categoryForComponent(component) {
  if (["Marker Light", "Stop Light", "Tail Light", "Reflector"].includes(component)) return "Lighting / Reflectors";
  if (["Window Blade", "Roof Hatch"].includes(component)) return "Windows / Hatches / Blinds";
  if (component === "Main Door") return "Doors / Hatches";
  if (component === "Solar") return "Electrical / Power / Electronics";
  if (component === "Decal") return "Body / Exterior Trim";
  if (component === "Clip") return "Hardware / Installation";
  return "";
}

function aggregateClassifiedRows(rows, item) {
  const relevant = rows.filter(row => matchesComponentFromKeyword(row, item));
  if (relevant.length === 0) return null;
  return {
    lines: relevant.length,
    descriptions: relevant.map(row => row.Description),
    materials: relevant.map(row => row.Material),
    categories: relevant.map(row => row["Part Category"] || categoryForComponent(item.component)),
    purchaseOrders: relevant.map(row => row["ERP Purchase Order"]),
    salesOrders: relevant.map(row => row["Sales Order"]),
    orderQty: relevant.reduce((sum, row) => sum + numeric(row["Order Qty"]), 0),
    cost: relevant.reduce((sum, row) => sum + numeric(row["Preferred Line Cost (AUD)"]), 0),
    rejectionStatus: relevant.map(row => row["Item Rejection Status"]),
  };
}

function buildOutputRow(oldRow, oldHeaders, item, detail) {
  const source = Object.fromEntries(oldHeaders.map((header, idx) => [header, oldRow[idx] ?? ""]));
  return outputHeaders.map(header => {
    if (header === "Components") return item.component;
    if (header === "Descriptions") return uniqueJoin(detail.descriptions);
    if (header === "Has Classified Part") return "Y";
    if (header === "Classified Part Lines") return detail.lines;
    if (header === "Part Categories") return uniqueJoin(detail.categories);
    if (header === "Materials") return uniqueJoin(detail.materials);
    if (header === "Purchase Orders") return uniqueJoin(detail.purchaseOrders);
    if (header === "Sales Orders") return uniqueJoin(detail.salesOrders).replace(/\b0+(?=\d)/g, "");
    if (header === "Order Qty") return typeof detail.orderQty === "number" ? detail.orderQty : detail.orderQty;
    if (header === "Preferred Line Cost AUD") return typeof detail.cost === "number" ? Math.round(detail.cost * 100) / 100 : detail.cost;
    if (header === "Item Rejection Status") return uniqueJoin(detail.rejectionStatus);
    return source[header] ?? "";
  });
}

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const oldInput = await FileBlob.load(oldWorkbookPath);
const oldWorkbook = await SpreadsheetFile.importXlsx(oldInput);
const oldSheetValues = [];
const seenOldRows = new Set();
let oldHeaders = null;
for (const item of components) {
  const sheet = oldWorkbook.worksheets.getItem(item.sheet);
  const values = sheet.getUsedRange(true).values;
  if (!oldHeaders) oldHeaders = values[0].map(value => value ?? "");
  for (const row of values.slice(1)) {
    const ticketId = row[getIndex(oldHeaders, "Ticket ID")];
    const salesOrders = row[getIndex(oldHeaders, "Sales Orders")];
    const materials = row[getIndex(oldHeaders, "Materials")];
    const key = `${ticketId}|${salesOrders}|${materials}`;
    if (seenOldRows.has(key)) continue;
    seenOldRows.add(key);
    oldSheetValues.push(row);
  }
}

const classifiedText = await fs.readFile(classifiedCsvPath, "utf8");
const parsed = parseCsv(classifiedText);
const classifiedHeaders = parsed[0];
const classifiedRows = parsed.slice(1).map(values => rowObject(classifiedHeaders, values));
const classifiedByTicket = new Map();
for (const row of classifiedRows) {
  const ticketId = normalizeId(row["Ticket ID"]);
  if (!ticketId) continue;
  if (!classifiedByTicket.has(ticketId)) classifiedByTicket.set(ticketId, []);
  classifiedByTicket.get(ticketId).push(row);
}

const outputWorkbook = Workbook.create();
const counts = {};
const fallbackCounts = {};

for (const item of components) {
  const matrix = [outputHeaders];
  let fallbackCount = 0;
  for (const oldRow of oldSheetValues) {
    const componentValues = splitSemi(oldRow[getIndex(oldHeaders, "Components")]).map(componentFromValue);
    if (!componentValues.some(component => normalize(component) === normalize(item.component))) continue;
    const ticketId = normalizeId(oldRow[getIndex(oldHeaders, "Ticket ID")]);
    const salesOrderSet = new Set(splitSemi(oldRow[getIndex(oldHeaders, "Sales Orders")]).map(normalizeId));
    const ticketRows = classifiedByTicket.get(ticketId) || [];
    const salesFiltered = ticketRows.filter(row => salesOrderSet.size === 0 || salesOrderSet.has(normalizeId(row["Sales Order"])));
    let detail = aggregateClassifiedRows(salesFiltered, item);
    if (!detail) {
      detail = aggregateClassifiedRows(ticketRows, item);
    }
    if (!detail) {
      detail = fallbackFromOldRow(oldRow, oldHeaders, item);
      fallbackCount += detail ? 1 : 0;
    }
    if (!detail) continue;
    matrix.push(buildOutputRow(oldRow, oldHeaders, item, detail));
  }

  const sheet = outputWorkbook.worksheets.add(item.sheet);
  const rowCount = matrix.length;
  const colCount = outputHeaders.length;
  sheet.showGridLines = false;
  sheet.getRangeByIndexes(0, 0, rowCount, colCount).values = matrix;
  sheet.getRangeByIndexes(0, 0, 1, colCount).format = {
    fill: "#D9EAF7",
    font: { bold: true, color: "#172033" },
  };
  sheet.getRangeByIndexes(0, 0, rowCount, colCount).format.borders = {
    insideHorizontal: { style: "thin", color: "#E5E7EB" },
    bottom: { style: "thin", color: "#CBD5E1" },
  };
  sheet.freezePanes.freezeRows(1);
  sheet.getRangeByIndexes(0, 0, rowCount, colCount).format.autofitColumns();
  sheet.getRangeByIndexes(0, 6, rowCount, 1).format.wrapText = true;
  sheet.getRangeByIndexes(0, 22, rowCount, 4).format.wrapText = true;
  sheet.getRangeByIndexes(0, 6, rowCount, 1).format.columnWidth = 42;
  sheet.getRangeByIndexes(0, 23, rowCount, 1).format.columnWidth = 24;
  sheet.getRangeByIndexes(0, 7, rowCount, 5).format.numberFormat = "yyyy-mm-dd";
  sheet.getRangeByIndexes(0, 13, rowCount, 2).format.numberFormat = "yyyy-mm-dd";
  sheet.getRangeByIndexes(0, 27, rowCount, 1).format.numberFormat = "#,##0.00";
  sheet.getRangeByIndexes(0, 26, rowCount, 1).format.numberFormat = "#,##0.0";
  sheet.getRangeByIndexes(0, 15, rowCount, 1).format.numberFormat = "#,##0.00";

  if (rowCount > 1) {
    const table = sheet.tables.add(`A1:AC${rowCount}`, true, item.sheet.replace(/[^A-Za-z0-9]/g, "") + "Table");
    table.style = "TableStyleMedium2";
    table.showFilterButton = true;
  }

  counts[item.component] = rowCount - 1;
  fallbackCounts[item.component] = fallbackCount;

  const preview = await outputWorkbook.render({
    sheetName: item.sheet,
    range: `A1:AC${Math.min(rowCount, 24)}`,
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    path.join(previewDir, `${item.sheet.replace(/[^A-Za-z0-9]+/g, "_")}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const componentChecks = {};
for (const item of components) {
  const check = await outputWorkbook.inspect({
    kind: "match",
    sheetId: item.sheet,
    searchTerm: `^(?!${item.component.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$).+`,
    range: `F2:F${Math.max(counts[item.component] + 1, 2)}`,
    options: { useRegex: true, maxResults: 20 },
    summary: `${item.component} component column check`,
  });
  componentChecks[item.component] = check.ndjson;
}

const errors = await outputWorkbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

const firstSheetCheck = await outputWorkbook.inspect({
  kind: "table",
  range: "01 Marker Light!A1:AC20",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 29,
});

const exported = await SpreadsheetFile.exportXlsx(outputWorkbook);
await exported.save(outputPath);

await fs.writeFile(
  path.join(outputDir, "verification.json"),
  JSON.stringify({ outputPath, counts, fallbackCounts, componentChecks, formulaErrors: errors.ndjson, firstSheetCheck: firstSheetCheck.ndjson }, null, 2),
  "utf8",
);

console.log(JSON.stringify({ outputPath, counts, fallbackCounts }, null, 2));
