import fs from "node:fs/promises";
import path from "node:path";

const DEFAULT_OUTPUT_DIR = path.join(process.cwd(), "outputs", `parts_classification_${new Date().toISOString().slice(0, 10)}`);
const DEFAULT_INPUT_PATH = path.join(process.cwd(), "outputs", "parts_classification_source.csv");
const DEFAULT_SEED_META_PATH = path.join(process.cwd(), "outputs", "parts_classified_meta.json");
const DETAIL_SHEET_NAME = "Classified Details";
const SUMMARY_SHEET_NAME = "Classification Summary";
const RULES_SHEET_NAME = "Rules";
const SAFETY_SHEET_NAME = "Safety Watch";
const CATEGORY_HEADER = "Part Category";
const KEYWORD_HEADER = "Matched Keyword";
const SAFETY_GROUP_HEADER = "Safety Watch Group";
const SAFETY_LEVEL_HEADER = "Safety Watch Level";
const SAFETY_REASON_HEADER = "Safety Watch Reason";
const SAFETY_ACTION_HEADER = "Safety Immediate Action";
const SAFETY_SOURCE_HEADER = "Safety Watch Match Source";
const ENABLE_SAFETY = false;
const OTHER_LABEL = "Other";
const AI_MODEL_DEFAULT = "gemini-2.0-flash";
const COMPONENT_KEYWORD_ALIASES = new Map([
  ["tail light", "Tail Light"],
  ["tail lights", "Tail Light"],
  ["taillight", "Tail Light"],
  ["taillights", "Tail Light"],
  ["combination taillight", "Tail Light"],
  ["combination taillights", "Tail Light"],
  ["marker light", "Marker Light"],
  ["marker lights", "Marker Light"],
  ["stop light", "Stop Light"],
  ["stop lights", "Stop Light"],
  ["roof hatch", "Roof Hatch"],
  ["roof hatches", "Roof Hatch"],
  ["window blind", "Window Blind"],
  ["window blinds", "Window Blind"],
  ["access door", "Access Door"],
  ["access doors", "Access Door"],
  ["main door", "Main Door"],
  ["main doors", "Main Door"],
  ["power inlet", "Power Inlet"],
  ["power inlets", "Power Inlet"],
  ["power outlet", "Power Outlet"],
  ["power outlets", "Power Outlet"],
]);
const CATEGORY_LABELS = new Map([
  ["lighting_reflectors", "Lighting / Reflectors"],
  ["windows_hatches_blinds", "Windows / Hatches / Blinds"],
  ["electrical_power_electronics", "Electrical / Power / Electronics"],
  ["doors_access_hatches", "Doors / Hatches"],
  ["chassis_wheels_towing", "Chassis / Wheels / Towing"],
  ["hardware_installation", "Hardware / Installation"],
  ["furniture_interior", "Furniture / Interior"],
  ["appliances_hvac_gas", "Appliances / HVAC / Gas"],
  ["body_exterior_trim", "Body / Exterior Trim"],
  ["water_plumbing_kitchen_bath", "Water / Plumbing / Kitchen / Bath"],
  ["awning_shade", "Awning / Shade"],
  ["storage_toolbox", "Storage / Toolbox"],
  ["other", OTHER_LABEL],
]);
const LEGACY_LABELS = new Map([
  ["\u7167\u660e/\u53cd\u5149", "Lighting / Reflectors"],
  ["\u7a97/\u5929\u7a97/\u906e\u9633", "Windows / Hatches / Blinds"],
  ["\u7535\u6c14/\u7535\u6e90/\u7535\u5b50", "Electrical / Power / Electronics"],
  ["\u95e8/\u8231\u95e8", "Doors / Hatches"],
  ["\u5e95\u76d8/\u8f6e\u7ec4/\u7275\u5f15", "Chassis / Wheels / Towing"],
  ["\u4e94\u91d1/\u5b89\u88c5\u4ef6", "Hardware / Installation"],
  ["\u5bb6\u5177/\u5185\u9970", "Furniture / Interior"],
  ["\u5bb6\u7535/\u901a\u98ce/\u71c3\u6c14", "Appliances / HVAC / Gas"],
  ["\u8f66\u8eab\u5916\u9970/\u88c5\u9970", "Body / Exterior Trim"],
  ["\u6c34\u8def/\u53a8\u623f/\u536b\u751f\u95f4", "Water / Plumbing / Kitchen / Bath"],
  ["\u906e\u9633\u68da/\u96e8\u7f6a", "Awning / Shade"],
  ["\u50a8\u7269/\u5de5\u5177\u7bb1", "Storage / Toolbox"],
  ["\u5176\u4ed6", OTHER_LABEL],
]);
const DEFAULT_SAFETY_RULES = [
  {
    key: "axle_torsion",
    group: "Axle / Torsion Axle",
    level: "Critical",
    domain: "Road Safety",
    categoryHints: ["Chassis / Wheels / Towing"],
    keywords: ["axle", "torsion axle", "torsion"],
    reason: "Axle failure can lead to wheel separation, loss of control, or roadside immobilisation.",
    immediateAction: "Check repeat VINs immediately, inspect affected stock, and escalate supplier containment.",
  },
  {
    key: "brake_system",
    group: "Brake System",
    level: "Critical",
    domain: "Road Safety",
    categoryHints: ["Chassis / Wheels / Towing"],
    keywords: ["brake", "hand brake", "brake cable", "backing plate", "drum brake"],
    reason: "Brake defects directly affect stopping distance and towing stability.",
    immediateAction: "Prioritise dealer follow-up and confirm no affected units are still operating without inspection.",
  },
  {
    key: "coupling_hitch",
    group: "Coupling / Hitch",
    level: "Critical",
    domain: "Road Safety",
    categoryHints: ["Chassis / Wheels / Towing"],
    keywords: ["coupling", "hitch", "do35", "attachment pin"],
    reason: "Coupling or hitch failure can detach the van from the tow vehicle.",
    immediateAction: "Treat as immediate towing-risk investigation and review attachment hardware batches.",
  },
  {
    key: "safety_chain",
    group: "Safety Chain",
    level: "Critical",
    domain: "Road Safety",
    categoryHints: ["Chassis / Wheels / Towing"],
    keywords: ["safety chain"],
    reason: "Safety-chain defects remove the final retention backup during a coupling event.",
    immediateAction: "Escalate immediately and confirm no open tickets are waiting on parts before inspection.",
  },
  {
    key: "suspension_bearing",
    group: "Suspension / Bearing",
    level: "High",
    domain: "Road Safety",
    categoryHints: ["Chassis / Wheels / Towing"],
    keywords: ["suspension", "bearing", "shocker", "shock absorber", "spring"],
    reason: "Suspension or bearing faults can destabilise the van at speed and accelerate secondary failures.",
    immediateAction: "Track recurrence by supplier and series, then inspect repeated units for broader running-gear damage.",
  },
  {
    key: "tyre_wheel_fastening",
    group: "Tyre / Wheel Fastening",
    level: "High",
    domain: "Road Safety",
    categoryHints: ["Chassis / Wheels / Towing"],
    keywords: ["tyre", "tire", "wheel nut", "hub", "wheel brace"],
    reason: "Tyre or wheel-fastening faults can trigger blowouts, wheel loss, or emergency roadside events.",
    immediateAction: "Review fitment quality, torque-related issues, and tyre batch concentration before the next dispatch cycle.",
  },
  {
    key: "sway_control",
    group: "Sway Control",
    level: "High",
    domain: "Road Safety",
    categoryHints: ["Chassis / Wheels / Towing"],
    keywords: ["sway command", "sway control", "sway"],
    reason: "Sway-control failures reduce trailer stability during towing and raise loss-of-control risk.",
    immediateAction: "Confirm controller version, installation consistency, and any repeat failures by series.",
  },
  {
    key: "a_frame_drawbar",
    group: "A-Frame / Drawbar",
    level: "High",
    domain: "Road Safety",
    categoryHints: ["Chassis / Wheels / Towing"],
    keywords: ["a-frame", "drawbar"],
    reason: "A-frame and drawbar issues affect the towing load path and structural safety of the van.",
    immediateAction: "Inspect mounting, weld, and supplier consistency on repeated failures before release.",
  },
];

async function loadArtifactWorkbookTools() {
  try {
    return await import("@oai/artifact-tool");
  } catch (error) {
    console.warn(
      "Optional @oai/artifact-tool package is not available; skipping formatted XLSX output. CSV and meta outputs will still be written.",
    );
    return null;
  }
}

const args = parseArgs(process.argv.slice(2));
const outputDir = path.resolve(args["output-dir"] || process.env.PARTS_OUTPUT_DIR || DEFAULT_OUTPUT_DIR);
const inputPath = path.resolve(
  args.input ||
    process.env.PARTS_INPUT_FILE ||
    DEFAULT_INPUT_PATH,
);
const seedMetaPath = path.resolve(
  args["seed-meta"] ||
    process.env.PARTS_SEED_META ||
    DEFAULT_SEED_META_PATH,
);
const outputCsvPath = path.join(outputDir, "parts_classified.csv");
const outputMetaPath = path.join(outputDir, "parts_classified_meta.json");
const outputXlsxPath = path.join(outputDir, "parts_classification_categorized.xlsx");
const aiEnabled = !flagEnabled(args["no-ai"], process.env.PARTS_NO_AI);
const aiKey = process.env.GEMINI_API_KEY || process.env.VITE_GEMINI_API_KEY || "";
const aiModel = process.env.GEMINI_MODEL || AI_MODEL_DEFAULT;
const aiBatchSize = Math.max(1, Number(args["ai-batch-size"] || process.env.PARTS_AI_BATCH_SIZE || 24));

await fs.mkdir(outputDir, { recursive: true });

const seedMeta = await readJsonIfExists(seedMetaPath, null);
if (!seedMeta || !Array.isArray(seedMeta.categories) || seedMeta.categories.length === 0) {
  throw new Error(`Missing seed meta with categories: ${seedMetaPath}`);
}

const sourceText = await fs.readFile(inputPath, "utf8");
const sourceTable = parseCsv(sourceText);
if (sourceTable.rows.length === 0) {
  throw new Error(`No data rows found in ${inputPath}`);
}

const headers = sourceTable.headers.slice();
const rows = sourceTable.rows.map((row) => row.slice());
const columnMap = buildColumnMap(headers);
const descIdx = findHeaderIndex(columnMap, [
  "Description",
  "\u63cf\u8ff0",
  "\u90e8\u4ef6\u63cf\u8ff0",
]);
if (descIdx === -1) {
  throw new Error(`Could not find a description column in ${inputPath}`);
}

const categoryIdx = ensureCanonicalColumn(headers, columnMap, CATEGORY_HEADER, ["\u90e8\u4ef6\u5206\u7c7b"]);
const keywordIdx = ensureCanonicalColumn(headers, columnMap, KEYWORD_HEADER, ["\u547d\u4e2d\u5173\u952e\u8bcd"]);
const safetyGroupIdx = ensureCanonicalColumn(headers, columnMap, SAFETY_GROUP_HEADER, []);
const safetyLevelIdx = ensureCanonicalColumn(headers, columnMap, SAFETY_LEVEL_HEADER, []);
const safetyReasonIdx = ensureCanonicalColumn(headers, columnMap, SAFETY_REASON_HEADER, []);
const safetyActionIdx = ensureCanonicalColumn(headers, columnMap, SAFETY_ACTION_HEADER, []);
const safetySourceIdx = ensureCanonicalColumn(headers, columnMap, SAFETY_SOURCE_HEADER, []);
const costIdx = findHeaderIndex(columnMap, [
  "Preferred Line Cost (AUD)",
  "Preferred Line Cost",
  "Amount Including Tax",
]);
if (costIdx === -1) {
  throw new Error(`Could not find a cost column in ${inputPath}`);
}

const categories = normalizeCategories(seedMeta.categories);
const safetyRules = ENABLE_SAFETY ? normalizeSafetyRules(seedMeta.safetyRules) : [];
const categorySet = new Set(categories.map((item) => item.label));

const history = await loadHistoryCache(seedMetaPath, inputPath, descIdx, categoryIdx, keywordIdx, categorySet);
const safetyHistory = await loadSafetyHistoryCache(
  seedMetaPath,
  inputPath,
  descIdx,
  safetyGroupIdx,
  safetyLevelIdx,
  safetyReasonIdx,
  safetyActionIdx,
);
const aiCandidates = new Map();
const updatedRows = rows.map((row) => row.slice());
let reusedHistoryCount = 0;
let keywordMatchCount = 0;
let aiMatchCount = 0;
let preservedCount = 0;
let otherCount = 0;
let reusedSafetyHistoryCount = 0;
let safetyRuleMatchCount = 0;
let safetyMonitoredCount = 0;

for (let i = 0; i < updatedRows.length; i += 1) {
  const row = updatedRows[i];
  const currentCategory = cleanCell(row[categoryIdx]);
  const currentKeyword = cleanCell(row[keywordIdx]);
  const description = cleanCell(row[descIdx]);

  if (currentCategory) {
    row[categoryIdx] = translateCategoryLabel(currentCategory);
    row[keywordIdx] = currentKeyword
      ? canonicalizeMatchedKeyword(currentKeyword, row[categoryIdx], description)
      : "";
    preservedCount += 1;
    continue;
  }

  const historyHit = history.get(normalizeForMatch(description));
  if (historyHit) {
    row[categoryIdx] = historyHit.label;
    row[keywordIdx] = canonicalizeMatchedKeyword(historyHit.keyword || "", historyHit.label, description);
    reusedHistoryCount += 1;
    continue;
  }

  const keywordHit = classifyByKeywords(description, categories);
  if (keywordHit) {
    row[categoryIdx] = keywordHit.label;
    row[keywordIdx] = canonicalizeMatchedKeyword(keywordHit.keyword, keywordHit.label, description);
    keywordMatchCount += 1;
    continue;
  }

  aiCandidates.set(normalizeForMatch(description), description);
}

if (aiEnabled && aiKey && aiCandidates.size > 0) {
  const aiResults = await classifyWithGemini({
    apiKey: aiKey,
    model: aiModel,
    categories,
    descriptions: [...aiCandidates.values()],
    batchSize: aiBatchSize,
  });

  for (const row of updatedRows) {
    if (cleanCell(row[categoryIdx])) {
      continue;
    }
    const description = cleanCell(row[descIdx]);
    const aiHit = aiResults.get(normalizeForMatch(description));
    if (!aiHit) {
      row[categoryIdx] = OTHER_LABEL;
      row[keywordIdx] = "";
      otherCount += 1;
      continue;
    }
    row[categoryIdx] = aiHit.label || OTHER_LABEL;
    row[keywordIdx] = canonicalizeMatchedKeyword(aiHit.keyword || "ai", row[categoryIdx], description);
    if (row[categoryIdx] === OTHER_LABEL) {
      otherCount += 1;
    } else {
      aiMatchCount += 1;
    }
  }
} else {
  for (const row of updatedRows) {
    if (cleanCell(row[categoryIdx])) {
      continue;
    }
    row[categoryIdx] = OTHER_LABEL;
    row[keywordIdx] = "";
    otherCount += 1;
  }
}

const cleanedTable = cleanupOutputColumns({
  headers,
  rows: updatedRows,
  categoryIdx,
  keywordIdx,
  safetyGroupIdx,
  safetyLevelIdx,
  safetyReasonIdx,
  safetyActionIdx,
  safetySourceIdx,
  legacyCategoryIdx: findHeaderIndex(columnMap, ["\u90e8\u4ef6\u5206\u7c7b"]),
  legacyKeywordIdx: findHeaderIndex(columnMap, ["\u547d\u4e2d\u5173\u952e\u8bcd"]),
});
const cleanedColumnMap = buildColumnMap(cleanedTable.headers);
const cleanedCostIdx = findHeaderIndex(cleanedColumnMap, [
  "Preferred Line Cost (AUD)",
  "Preferred Line Cost",
  "Amount Including Tax",
]);
const csvRows = [cleanedTable.headers, ...cleanedTable.rows];
const csvText = stringifyCsv(csvRows);
await fs.writeFile(outputCsvPath, csvText, "utf8");

const finalMeta = buildFinalMeta({
  seedMeta,
  sourcePath: inputPath,
  csvPath: outputCsvPath,
  rowCount: updatedRows.length,
  headers: cleanedTable.headers,
  categoryIdx: cleanedTable.categoryIdx,
  keywordIdx: cleanedTable.keywordIdx,
  ...(ENABLE_SAFETY ? {
    safetyIdx: {
      group: cleanedTable.safetyGroupIdx,
      level: cleanedTable.safetyLevelIdx,
      reason: cleanedTable.safetyReasonIdx,
      action: cleanedTable.safetyActionIdx,
      source: cleanedTable.safetySourceIdx,
    },
  } : {}),
  costIdx: cleanedCostIdx,
  descIdx,
  rows: cleanedTable.rows,
  categories,
  ...(ENABLE_SAFETY ? { safetyRules } : {}),
  aiEnabled,
  aiModel,
  counts: {
    preservedCount,
    reusedHistoryCount,
    keywordMatchCount,
    aiMatchCount,
    otherCount,
    reusedSafetyHistoryCount,
    safetyRuleMatchCount,
    safetyMonitoredCount,
  },
});

await fs.writeFile(outputMetaPath, `${JSON.stringify(finalMeta, null, 2)}\n`, "utf8");

const workbookTools = await loadArtifactWorkbookTools();
if (workbookTools) {
const { Workbook, SpreadsheetFile } = workbookTools;
const workbook = await Workbook.fromCSV(csvText, { sheetName: DETAIL_SHEET_NAME });
const detailSheet = workbook.worksheets.getItem(DETAIL_SHEET_NAME);
const lastCol = toColumnLetter(cleanedTable.headers.length - 1);
const categoryCol = toColumnLetter(cleanedTable.categoryIdx);
const keywordCol = toColumnLetter(cleanedTable.keywordIdx);
const costCol = toColumnLetter(cleanedCostIdx);

detailSheet.freezePanes.freezeRows(1);
detailSheet.showGridLines = true;
detailSheet.getRange(`A1:${lastCol}1`).format = {
  fill: "#0F172A",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
};
detailSheet.getRange(`A1:${lastCol}1`).format.rowHeightPx = 40;
detailSheet.getRange(`${descIdxToLetter(descIdx)}1:${descIdxToLetter(descIdx)}2`).format.columnWidthPx = 320;
detailSheet.getRange(`${categoryCol}1:${categoryCol}2`).format.columnWidthPx = 180;
detailSheet.getRange(`${keywordCol}1:${keywordCol}2`).format.columnWidthPx = 180;
if (ENABLE_SAFETY) {
  const safetyGroupCol = toColumnLetter(cleanedTable.safetyGroupIdx);
  const safetyLevelCol = toColumnLetter(cleanedTable.safetyLevelIdx);
  const safetyReasonCol = toColumnLetter(cleanedTable.safetyReasonIdx);
  const safetyActionCol = toColumnLetter(cleanedTable.safetyActionIdx);
  const safetySourceCol = toColumnLetter(cleanedTable.safetySourceIdx);

  detailSheet.getRange(`${safetyGroupCol}1:${safetyGroupCol}2`).format.columnWidthPx = 190;
  detailSheet.getRange(`${safetyLevelCol}1:${safetyLevelCol}2`).format.columnWidthPx = 110;
  detailSheet.getRange(`${safetyReasonCol}1:${safetyReasonCol}2`).format.columnWidthPx = 340;
  detailSheet.getRange(`${safetyActionCol}1:${safetyActionCol}2`).format.columnWidthPx = 340;
  detailSheet.getRange(`${safetySourceCol}1:${safetySourceCol}2`).format.columnWidthPx = 120;
}

if (ENABLE_SAFETY) {
const safetySummaryRows = buildSafetyWatchSummary({
  headers: cleanedTable.headers,
  rows: cleanedTable.rows,
  ticketHeader: "Ticket ID",
  descriptionHeader: "Description",
  costHeader: cleanedTable.headers[cleanedCostIdx],
});

const safetySheet = workbook.worksheets.add(SAFETY_SHEET_NAME);
safetySheet.showGridLines = false;
safetySheet.freezePanes.freezeRows(5);
safetySheet.getRange("A1:G1").merge();
safetySheet.getRange("A1").values = [["Safety Watch Summary"]];
safetySheet.getRange("A2:G2").merge();
safetySheet.getRange("A2").values = [[
  "These components are monitored separately because even low-count failures can affect towing stability, braking, or structural road safety.",
]];
safetySheet.getRange("A4:G4").values = [[
  "Safety Group",
  "Level",
  "Affected Tickets",
  "Line Items",
  "Part Cost (AUD)",
  "Why It Matters",
  "Immediate Action",
]];
if (safetySummaryRows.length > 0) {
  safetySheet.getRange(`A5:G${4 + safetySummaryRows.length}`).values = safetySummaryRows.map((item) => [
    item.group,
    item.level,
    item.tickets,
    item.lineItems,
    item.cost,
    item.reason,
    item.action,
  ]);
}
safetySheet.getRange("A1:G1").format = {
  fill: "#7F1D1D",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  wrapText: true,
};
safetySheet.getRange("A2:G2").format = {
  fill: "#FEE2E2",
  font: { color: "#7F1D1D" },
  wrapText: true,
};
safetySheet.getRange("A4:G4").format = {
  fill: "#991B1B",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
};
if (safetySummaryRows.length > 0) {
  safetySheet.getRange(`A5:G${4 + safetySummaryRows.length}`).format.borders = {
    preset: "all",
    style: "thin",
    color: "#FCA5A5",
  };
  safetySheet.getRange(`B5:B${4 + safetySummaryRows.length}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"Critical"',
    format: { fill: "#FEE2E2", font: { bold: true, color: "#991B1B" } },
  });
  safetySheet.getRange(`B5:B${4 + safetySummaryRows.length}`).conditionalFormats.add("cellIs", {
    operator: "equal",
    formula: '"High"',
    format: { fill: "#FEF3C7", font: { bold: true, color: "#92400E" } },
  });
}
safetySheet.getRange("C5:D2000").format.numberFormat = "#,##0";
safetySheet.getRange("E5:E2000").format.numberFormat = '"$"#,##0.00';
safetySheet.getRange("A1:G2").format.rowHeightPx = 34;
safetySheet.getRange("A1").format.columnWidthPx = 180;
safetySheet.getRange("B1").format.columnWidthPx = 90;
safetySheet.getRange("C1").format.columnWidthPx = 110;
safetySheet.getRange("D1").format.columnWidthPx = 95;
safetySheet.getRange("E1").format.columnWidthPx = 125;
safetySheet.getRange("F1").format.columnWidthPx = 300;
safetySheet.getRange("G1").format.columnWidthPx = 300;

}

const summary = workbook.worksheets.add(SUMMARY_SHEET_NAME);
summary.showGridLines = false;
summary.freezePanes.freezeRows(4);
summary.getRange("A1:E1").merge();
summary.getRange("A1").values = [["Parts Classification Summary"]];
summary.getRange("A2:E2").merge();
summary.getRange("A2").values = [[
  "Automatically groups parts from Description keywords, reuses historical labels first, and only falls back to AI for new unmatched items.",
]];
summary.getRange("A4:E4").values = [[
  "Part Category",
  "Detail Rows",
  "Part Cost (AUD)",
  "Row Share",
  "Cost Share",
]];

const categoryRows = categories.length;
const firstDataRow = 5;
const totalRow = firstDataRow + categoryRows;
const lastDetailRow = cleanedTable.rows.length + 1;
const categoryRange = `'${DETAIL_SHEET_NAME}'!$${categoryCol}$2:$${categoryCol}$${lastDetailRow}`;
const costRange = `'${DETAIL_SHEET_NAME}'!$${costCol}$2:$${costCol}$${lastDetailRow}`;

summary.getRange(`A${firstDataRow}:A${totalRow - 1}`).values = categories.map((item) => [item.label]);
summary.getRange(`B${firstDataRow}:B${totalRow - 1}`).formulas = categories.map((_, i) => [
  `=COUNTIF(${categoryRange},A${firstDataRow + i})`,
]);
summary.getRange(`C${firstDataRow}:C${totalRow - 1}`).formulas = categories.map((_, i) => [
  `=SUMIF(${categoryRange},A${firstDataRow + i},${costRange})`,
]);
summary.getRange(`D${firstDataRow}:D${totalRow - 1}`).formulas = categories.map((_, i) => [
  `=B${firstDataRow + i}/$B$${totalRow}`,
]);
summary.getRange(`E${firstDataRow}:E${totalRow - 1}`).formulas = categories.map((_, i) => [
  `=C${firstDataRow + i}/$C$${totalRow}`,
]);
summary.getRange(`A${totalRow}:E${totalRow}`).values = [["Total", null, null, null, null]];
summary.getRange(`B${totalRow}`).formulas = [[`=SUM(B${firstDataRow}:B${totalRow - 1})`]];
summary.getRange(`C${totalRow}`).formulas = [[`=SUM(C${firstDataRow}:C${totalRow - 1})`]];
summary.getRange(`D${totalRow}`).formulas = [[`=SUM(D${firstDataRow}:D${totalRow - 1})`]];
summary.getRange(`E${totalRow}`).formulas = [[`=SUM(E${firstDataRow}:E${totalRow - 1})`]];

summary.getRange(`B${firstDataRow}:B${totalRow}`).format.numberFormat = "#,##0";
summary.getRange(`C${firstDataRow}:C${totalRow}`).format.numberFormat = '"$"#,##0.00';
summary.getRange(`D${firstDataRow}:E${totalRow}`).format.numberFormat = "0.0%";
summary.getRange("A4:E4").format = {
  fill: "#1F2937",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
};
summary.getRange(`A${totalRow}:E${totalRow}`).format = {
  fill: "#E5E7EB",
  font: { bold: true },
};
summary.getRange(`A4:E${totalRow}`).format.borders = { preset: "all", style: "thin", color: "#D1D5DB" };
summary.getRange("A1:E1").format = {
  fill: "#0B3B5B",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  wrapText: true,
};
summary.getRange("A2:E2").format = {
  fill: "#E0F2FE",
  font: { color: "#0F172A" },
  wrapText: true,
};
summary.getRange("A1:E2").format.rowHeightPx = 34;
summary.getRange("A1:E2").format.columnWidthPx = 22;
summary.getRange("A1").format.columnWidthPx = 240;
summary.getRange("B1").format.columnWidthPx = 120;
summary.getRange("C1").format.columnWidthPx = 150;
summary.getRange("D1").format.columnWidthPx = 110;
summary.getRange("E1").format.columnWidthPx = 110;

const rulesSheet = workbook.worksheets.add(RULES_SHEET_NAME);
rulesSheet.showGridLines = false;
rulesSheet.freezePanes.freezeRows(4);
rulesSheet.getRange("A1:C1").merge();
rulesSheet.getRange("A1").values = [["Classification Rules"]];
rulesSheet.getRange("A2:C2").merge();
rulesSheet.getRange("A2").values = [[
  "Matches Description keywords in order, keeps the first matched category, and marks anything unmatched as Other.",
]];
rulesSheet.getRange("A4:C4").values = [[
  "Part Category",
  "Common Keywords",
  "Notes",
]];

const ruleRows = categories
  .filter((item) => item.label !== OTHER_LABEL)
  .map((item) => [
    item.label,
    item.keywords.join(", "),
    `${item.count.toLocaleString("en-US")} rows, total cost ${item.cost.toLocaleString("en-US")} AUD`,
  ]);
if (ruleRows.length > 0) {
  rulesSheet.getRange(`A5:C${4 + ruleRows.length}`).values = ruleRows;
  rulesSheet.getRange(`A5:C${4 + ruleRows.length}`).format.wrapText = true;
}

rulesSheet.getRange("A4:C4").format = {
  fill: "#1F2937",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
};
rulesSheet.getRange("A1:C1").format = {
  fill: "#0B3B5B",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  wrapText: true,
};
rulesSheet.getRange("A2:C2").format = {
  fill: "#E0F2FE",
  font: { color: "#0F172A" },
  wrapText: true,
};
if (ruleRows.length > 0) {
  rulesSheet.getRange(`A4:C${4 + ruleRows.length}`).format.borders = {
    preset: "all",
    style: "thin",
    color: "#D1D5DB",
  };
}
rulesSheet.getRange("A1:C2").format.rowHeightPx = 34;
rulesSheet.getRange("A1").format.columnWidthPx = 180;
rulesSheet.getRange("B1").format.columnWidthPx = 440;
rulesSheet.getRange("C1").format.columnWidthPx = 320;

summary.getRange(`A4:E${totalRow}`).format.autofitRows();
if (ruleRows.length > 0) {
  rulesSheet.getRange(`A4:C${4 + ruleRows.length}`).format.autofitRows();
}
if (ENABLE_SAFETY && safetySummaryRows.length > 0) {
  safetySheet.getRange(`A4:G${4 + safetySummaryRows.length}`).format.autofitRows();
}

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputXlsxPath);

console.log(`Saved to ${outputXlsxPath}`);
} else {
  console.log(`Saved to ${outputCsvPath}`);
  console.log(`Saved to ${outputMetaPath}`);
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) {
      continue;
    }
    const eq = token.indexOf("=");
    if (eq !== -1) {
      out[token.slice(2, eq)] = token.slice(eq + 1);
      continue;
    }
    const next = argv[i + 1];
    if (next && !next.startsWith("--")) {
      out[token.slice(2)] = next;
      i += 1;
    } else {
      out[token.slice(2)] = true;
    }
  }
  return out;
}

function flagEnabled(flagValue, envValue) {
  return [flagValue, envValue].some((value) => {
    if (value === true) return true;
    if (typeof value !== "string") return false;
    return ["1", "true", "yes", "on"].includes(value.toLowerCase());
  });
}

function cleanCell(value) {
  if (value == null) return "";
  const text = String(value).trim();
  return text;
}

function relativePathForMeta(filePath) {
  const relative = path.relative(process.cwd(), filePath);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    return filePath;
  }
  return relative.split(path.sep).join("/");
}

function normalizeForMatch(value) {
  return ` ${cleanCell(value)
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()} `;
}

function normalizeKeywordKey(value) {
  return cleanCell(value)
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function titleCaseKeyword(value) {
  const text = cleanCell(value);
  if (!text) return "";
  return text.replace(/\S+/g, (word) => {
    if (word.toUpperCase() === word || /^\d+$/.test(word)) return word;
    return word.slice(0, 1).toUpperCase() + word.slice(1).toLowerCase();
  });
}

function canonicalizeMatchedKeyword(keyword, categoryLabel = "", description = "") {
  const rawKeyword = cleanCell(keyword);
  const fallback = rawKeyword || cleanCell(description);
  if (!fallback) return "";

  const normalized = normalizeKeywordKey(fallback);
  const exact = COMPONENT_KEYWORD_ALIASES.get(normalized);
  if (exact) return exact;

  if (normalized.endsWith("s")) {
    const singular = COMPONENT_KEYWORD_ALIASES.get(normalized.slice(0, -1));
    if (singular) return singular;
  }

  if ((categoryLabel || "").includes("Lighting / Reflectors")) {
    if (/\b(?:combination\s+)?tail\s*lights?\b/.test(normalized) || /\btaillights?\b/.test(normalized)) {
      return "Tail Light";
    }
    if (/\bmarker\s+lights?\b/.test(normalized)) {
      return "Marker Light";
    }
    if (/\bstop\s+lights?\b/.test(normalized)) {
      return "Stop Light";
    }
  }

  return titleCaseKeyword(fallback);
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  let i = 0;
  const input = text.replace(/^\uFEFF/, "");

  while (i < input.length) {
    const ch = input[i];
    const next = input[i + 1];

    if (inQuotes) {
      if (ch === '"') {
        if (next === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += ch;
      i += 1;
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
      i += 1;
      continue;
    }

    if (ch === ",") {
      row.push(field);
      field = "";
      i += 1;
      continue;
    }

    if (ch === "\r" || ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      if (ch === "\r" && next === "\n") {
        i += 2;
      } else {
        i += 1;
      }
      continue;
    }

    field += ch;
    i += 1;
  }

  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }

  const headers = rows.shift() || [];
  return { headers, rows };
}

function stringifyCsv(rows) {
  return rows
    .map((row) =>
      row
        .map((value) => {
          const text = value == null ? "" : String(value);
          if (/[",\r\n]/.test(text) || /^\s|\s$/.test(text)) {
            return `"${text.replace(/"/g, '""')}"`;
          }
          return text;
        })
        .join(","),
    )
    .join("\r\n");
}

function buildColumnMap(headers) {
  const map = new Map();
  headers.forEach((header, index) => {
    map.set(normalizeHeader(header), index);
  });
  return map;
}

function normalizeHeader(value) {
  return cleanCell(value).toLowerCase();
}

function findHeaderIndex(columnMap, candidates) {
  for (const candidate of candidates) {
    const idx = columnMap.get(normalizeHeader(candidate));
    if (idx !== undefined) {
      return idx;
    }
  }
  return -1;
}

function ensureColumn(headers, columnMap, name) {
  const existing = columnMap.get(normalizeHeader(name));
  if (existing !== undefined) {
    return existing;
  }
  headers.push(name);
  const index = headers.length - 1;
  columnMap.set(normalizeHeader(name), index);
  return index;
}

function ensureCanonicalColumn(headers, columnMap, canonicalName, legacyNames = []) {
  const canonical = columnMap.get(normalizeHeader(canonicalName));
  if (canonical !== undefined) {
    return canonical;
  }
  for (const legacyName of legacyNames) {
    const legacy = columnMap.get(normalizeHeader(legacyName));
    if (legacy !== undefined) {
      headers[legacy] = canonicalName;
      columnMap.delete(normalizeHeader(legacyName));
      columnMap.set(normalizeHeader(canonicalName), legacy);
      return legacy;
    }
  }
  return ensureColumn(headers, columnMap, canonicalName);
}

function cleanupOutputColumns({
  headers,
  rows,
  categoryIdx,
  keywordIdx,
  safetyGroupIdx,
  safetyLevelIdx,
  safetyReasonIdx,
  safetyActionIdx,
  safetySourceIdx,
  legacyCategoryIdx,
  legacyKeywordIdx,
}) {
  const removeSet = new Set();
  const fillTargets = [];

  if (legacyCategoryIdx !== undefined && legacyCategoryIdx !== -1 && legacyCategoryIdx !== categoryIdx) {
    fillTargets.push({ legacyIdx: legacyCategoryIdx, targetIdx: categoryIdx });
    removeSet.add(legacyCategoryIdx);
  }
  if (legacyKeywordIdx !== undefined && legacyKeywordIdx !== -1 && legacyKeywordIdx !== keywordIdx) {
    fillTargets.push({ legacyIdx: legacyKeywordIdx, targetIdx: keywordIdx });
    removeSet.add(legacyKeywordIdx);
  }
  if (!ENABLE_SAFETY) {
    [safetyGroupIdx, safetyLevelIdx, safetyReasonIdx, safetyActionIdx, safetySourceIdx]
      .forEach((idx) => {
        if (idx !== undefined && idx !== -1) {
          removeSet.add(idx);
        }
      });
  }

  for (const row of rows) {
    for (const { legacyIdx, targetIdx } of fillTargets) {
      if (!cleanCell(row[targetIdx]) && cleanCell(row[legacyIdx])) {
        row[targetIdx] = row[legacyIdx];
      }
    }
  }

  const sortedRemovals = [...removeSet].sort((a, b) => b - a);
  const removedBefore = (index) => sortedRemovals.filter((value) => value < index).length;
  const cleanedHeaders = headers.filter((_, index) => !removeSet.has(index));
  const cleanedRows = rows.map((row) => row.filter((_, index) => !removeSet.has(index)));

  return {
    headers: cleanedHeaders,
    rows: cleanedRows,
    categoryIdx: categoryIdx - removedBefore(categoryIdx),
    keywordIdx: keywordIdx - removedBefore(keywordIdx),
    safetyGroupIdx: safetyGroupIdx - removedBefore(safetyGroupIdx),
    safetyLevelIdx: safetyLevelIdx - removedBefore(safetyLevelIdx),
    safetyReasonIdx: safetyReasonIdx - removedBefore(safetyReasonIdx),
    safetyActionIdx: safetyActionIdx - removedBefore(safetyActionIdx),
    safetySourceIdx: safetySourceIdx - removedBefore(safetySourceIdx),
  };
}

function normalizeCategories(rawCategories) {
  const categories = rawCategories.map((item) => ({
    key: item.key || item.label,
    label: translateCategoryLabel(item.label, item.key || item.label),
    keywords: Array.isArray(item.keywords) ? item.keywords.filter(Boolean) : [],
    count: Number(item.count || 0),
    cost: Number(item.cost || 0),
  }));
  if (!categories.some((item) => item.label === OTHER_LABEL)) {
    categories.push({ key: "other", label: OTHER_LABEL, keywords: [], count: 0, cost: 0 });
  }
  return categories;
}

function classifyByKeywords(description, categories) {
  const normalizedDescription = normalizeForMatch(description);
  for (const category of categories) {
    for (const keyword of category.keywords || []) {
      const normalizedKeyword = normalizeForMatch(keyword);
      if (!normalizedKeyword.trim()) {
        continue;
      }
      if (normalizedDescription.includes(normalizedKeyword)) {
        return { label: category.label, keyword };
      }
    }
  }
  return null;
}

async function loadHistoryCache(seedMetaPath, inputPath, descIdx, categoryIdx, keywordIdx, categorySet) {
  const history = new Map();
  const seedMeta = await readJsonIfExists(seedMetaPath, null);
  if (!seedMeta || !seedMeta.csvPath) {
    return history;
  }

  const candidatePaths = [seedMeta.csvPath, inputPath]
    .map((item) => path.resolve(item))
    .filter((item, index, all) => all.indexOf(item) === index)
    .filter((item) => path.basename(item).toLowerCase().endsWith(".csv"));

  for (const candidate of candidatePaths) {
    if (!(await fileExists(candidate))) {
      continue;
    }
    const parsed = parseCsv(await fs.readFile(candidate, "utf8"));
    const headers = parsed.headers;
    const headerMap = buildColumnMap(headers);
    const desc = findHeaderIndex(headerMap, ["Description", "\u63cf\u8ff0", "\u90e8\u4ef6\u63cf\u8ff0"]);
    const category = findHeaderIndex(headerMap, [CATEGORY_HEADER, "\u90e8\u4ef6\u5206\u7c7b"]);
    const keyword = findHeaderIndex(headerMap, [KEYWORD_HEADER, "\u547d\u4e2d\u5173\u952e\u8bcd"]);
    if (desc === -1 || category === -1) {
      continue;
    }
    for (const row of parsed.rows) {
      const description = cleanCell(row[desc]);
      const label = cleanCell(row[category]);
      const translatedLabel = translateCategoryLabel(label);
      if (!description || !translatedLabel || !categorySet.has(translatedLabel)) {
        continue;
      }
      const normalizedDescription = normalizeForMatch(description);
      const existing = history.get(normalizedDescription);
      const keywordValue = keyword >= 0
        ? canonicalizeMatchedKeyword(cleanCell(row[keyword]), translatedLabel, description)
        : "";
      if (!existing || (existing.label === OTHER_LABEL && translatedLabel !== OTHER_LABEL)) {
        history.set(normalizedDescription, { label: translatedLabel, keyword: keywordValue });
      }
    }
    break;
  }

  return history;
}

async function classifyWithGemini({ apiKey, model, categories, descriptions, batchSize }) {
  const results = new Map();
  const uniqueDescriptions = [...new Map(descriptions.map((desc) => [normalizeForMatch(desc), desc])).values()];
  for (let i = 0; i < uniqueDescriptions.length; i += batchSize) {
    const batch = uniqueDescriptions.slice(i, i + batchSize);
    const response = await fetchGeminiBatch({ apiKey, model, categories, batch });
    for (const item of response) {
      const description = cleanCell(item.description);
      if (!description) {
        continue;
      }
      const normalizedDescription = normalizeForMatch(description);
      const label = categoryExists(categories, item.label) ? item.label : OTHER_LABEL;
      results.set(normalizedDescription, {
        label,
        keyword: canonicalizeMatchedKeyword(cleanCell(item.keyword || item.matched_keyword || "ai"), label, description),
        confidence: Number(item.confidence || 0),
        reason: cleanCell(item.reason || item.explanation || ""),
      });
    }
  }
  return results;
}

function categoryExists(categories, label) {
  return categories.some((item) => item.label === label);
}

async function fetchGeminiBatch({ apiKey, model, categories, batch }) {
  const prompt = [
    "You classify caravan / warranty parts from the Description field.",
    "Choose exactly one label from the provided category list.",
    `If nothing fits, use "${OTHER_LABEL}".`,
    "Return JSON only, with this shape:",
    '{ "items": [ { "description": "...", "label": "...", "keyword": "...", "confidence": 0.0, "reason": "..." } ] }',
    "",
    "Categories:",
    ...categories.map(
      (category) => `- ${category.label}: ${category.keywords.length ? category.keywords.join(", ") : "(no keywords)"}`,
    ),
    "",
    "Descriptions:",
    ...batch.map((desc, index) => `${index + 1}. ${desc}`),
  ].join("\n");

  const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      contents: [{ role: "user", parts: [{ text: prompt }] }],
      generationConfig: {
        temperature: 0.1,
        responseMimeType: "application/json",
      },
    }),
  });

  if (!response.ok) {
    throw new Error(`Gemini API request failed: ${response.status} ${response.statusText}`);
  }

  const payload = await response.json();
  const text = payload?.candidates?.[0]?.content?.parts?.map((part) => part.text || "").join("") || "";
  const parsed = safeJsonParse(text);
  if (!parsed) {
    throw new Error("Gemini API returned invalid JSON.");
  }
  if (Array.isArray(parsed)) {
    return parsed;
  }
  if (Array.isArray(parsed.items)) {
    return parsed.items;
  }
  return [];
}

function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    const match = text.match(/\{[\s\S]*\}|\[[\s\S]*\]/);
    if (!match) {
      return null;
    }
    try {
      return JSON.parse(match[0]);
    } catch {
      return null;
    }
  }
}

function normalizeSafetyRules(rawRules) {
  const source = Array.isArray(rawRules) && rawRules.length > 0 ? rawRules : DEFAULT_SAFETY_RULES;
  return source.map((rule) => ({
    key: cleanCell(rule.key || rule.group),
    group: cleanCell(rule.group),
    level: cleanCell(rule.level) || "High",
    domain: cleanCell(rule.domain) || "Safety",
    categoryHints: Array.isArray(rule.categoryHints) ? rule.categoryHints.map((item) => translateCategoryLabel(item)).filter(Boolean) : [],
    keywords: Array.isArray(rule.keywords) ? rule.keywords.map((item) => cleanCell(item)).filter(Boolean) : [],
    reason: cleanCell(rule.reason),
    immediateAction: cleanCell(rule.immediateAction),
  }));
}

async function loadSafetyHistoryCache(seedMetaPath, inputPath, descIdx, groupIdx, levelIdx, reasonIdx, actionIdx) {
  const history = new Map();
  const seedMeta = await readJsonIfExists(seedMetaPath, null);
  const candidatePaths = [seedMeta?.csvPath, inputPath]
    .map((item) => (item ? path.resolve(item) : ""))
    .filter(Boolean)
    .filter((item, index, all) => all.indexOf(item) === index)
    .filter((item) => path.basename(item).toLowerCase().endsWith(".csv"));

  for (const candidate of candidatePaths) {
    if (!(await fileExists(candidate))) {
      continue;
    }
    const parsed = parseCsv(await fs.readFile(candidate, "utf8"));
    const headerMap = buildColumnMap(parsed.headers);
    const desc = findHeaderIndex(headerMap, ["Description", "\u63cf\u8ff0", "\u90e8\u4ef6\u63cf\u8ff0"]);
    const group = findHeaderIndex(headerMap, [SAFETY_GROUP_HEADER]);
    const level = findHeaderIndex(headerMap, [SAFETY_LEVEL_HEADER]);
    const reason = findHeaderIndex(headerMap, [SAFETY_REASON_HEADER]);
    const action = findHeaderIndex(headerMap, [SAFETY_ACTION_HEADER]);
    if (desc === -1 || group === -1) {
      continue;
    }
    for (const row of parsed.rows) {
      const description = cleanCell(row[desc]);
      const safetyGroup = cleanCell(row[group]);
      if (!description || !safetyGroup) {
        continue;
      }
      history.set(normalizeForMatch(description), {
        group: safetyGroup,
        level: level >= 0 ? cleanCell(row[level]) : "",
        reason: reason >= 0 ? cleanCell(row[reason]) : "",
        action: action >= 0 ? cleanCell(row[action]) : "",
      });
    }
    break;
  }
  return history;
}

function classifySafetyRow({ description, category, keyword, rules }) {
  const normalizedText = normalizeForMatch([description, keyword, category].filter(Boolean).join(" "));
  for (const rule of rules) {
    if (rule.categoryHints.length > 0 && !rule.categoryHints.includes(category)) {
      continue;
    }
    const matched = rule.keywords.some((token) => normalizedText.includes(normalizeForMatch(token)));
    if (!matched) {
      continue;
    }
    return rule;
  }
  return null;
}

function buildSafetyWatchSummary({ headers, rows, ticketHeader, descriptionHeader, costHeader }) {
  const headerMap = buildColumnMap(headers);
  const ticketIdx = findHeaderIndex(headerMap, [ticketHeader]);
  const groupIdx = findHeaderIndex(headerMap, [SAFETY_GROUP_HEADER]);
  const levelIdx = findHeaderIndex(headerMap, [SAFETY_LEVEL_HEADER]);
  const reasonIdx = findHeaderIndex(headerMap, [SAFETY_REASON_HEADER]);
  const actionIdx = findHeaderIndex(headerMap, [SAFETY_ACTION_HEADER]);
  const descIdx = findHeaderIndex(headerMap, [descriptionHeader]);
  const costIdx = findHeaderIndex(headerMap, [costHeader, "Amount Including Tax"]);
  if (groupIdx === -1) {
    return [];
  }
  const stats = new Map();
  for (const row of rows) {
    const group = cleanCell(row[groupIdx]);
    if (!group) {
      continue;
    }
    const entry = stats.get(group) || {
      group,
      level: cleanCell(row[levelIdx]),
      reason: cleanCell(row[reasonIdx]),
      action: cleanCell(row[actionIdx]),
      lineItems: 0,
      tickets: new Set(),
      cost: 0,
      samples: new Set(),
    };
    entry.lineItems += 1;
    entry.tickets.add(cleanCell(row[ticketIdx]));
    entry.cost += parseNumberLike(costIdx >= 0 ? row[costIdx] : 0);
    if (descIdx >= 0 && entry.samples.size < 3) {
      const sample = cleanCell(row[descIdx]);
      if (sample) {
        entry.samples.add(sample);
      }
    }
    stats.set(group, entry);
  }
  return [...stats.values()]
    .map((item) => ({
      ...item,
      tickets: item.tickets.size,
      cost: roundMoney(item.cost),
      sampleDescriptions: [...item.samples],
    }))
    .sort((a, b) => {
      const levelOrder = { Critical: 0, High: 1 };
      return (levelOrder[a.level] ?? 9) - (levelOrder[b.level] ?? 9)
        || b.tickets - a.tickets
        || b.cost - a.cost
        || a.group.localeCompare(b.group);
    });
}

function parseNumberLike(value) {
  const text = cleanCell(value).replace(/,/g, "");
  if (!text || text === "#") {
    return 0;
  }
  const num = Number(text);
  return Number.isFinite(num) ? num : 0;
}

function buildFinalMeta({
  seedMeta,
  sourcePath,
  csvPath,
  rowCount,
  headers,
  categoryIdx,
  keywordIdx,
  safetyIdx,
  costIdx,
  descIdx,
  rows,
  categories,
  safetyRules,
  aiEnabled,
  aiModel,
  counts,
}) {
  const labelStats = new Map(categories.map((item) => [item.label, { count: 0, cost: 0 }]));
  const otherExamples = [];

  for (const row of rows) {
    const label = cleanCell(row[categoryIdx]) || OTHER_LABEL;
    const cost = Number(cleanCell(row[costIdx]) || 0);
    const stat = labelStats.get(label) || { count: 0, cost: 0 };
    stat.count += 1;
    stat.cost += Number.isFinite(cost) ? cost : 0;
    labelStats.set(label, stat);
    if (label === OTHER_LABEL && otherExamples.length < 30) {
      otherExamples.push(cleanCell(row[descIdx]));
    }
  }

  const finalCategories = categories.map((item) => ({
    ...item,
    count: labelStats.get(item.label)?.count || 0,
    cost: roundMoney(labelStats.get(item.label)?.cost || 0),
  }));

  return {
    ...seedMeta,
    generatedAt: new Date().toISOString(),
    sourcePath: relativePathForMeta(sourcePath),
    csvPath: relativePathForMeta(csvPath),
    rowCount,
    headers,
    categoryColumn: columnName(categoryIdx),
    keywordColumn: columnName(keywordIdx),
    safetyColumns: ENABLE_SAFETY && safetyIdx ? {
      group: columnName(safetyIdx.group),
      level: columnName(safetyIdx.level),
      reason: columnName(safetyIdx.reason),
      action: columnName(safetyIdx.action),
      source: columnName(safetyIdx.source),
    } : null,
    costColumn: columnName(costIdx),
    aiEnabled,
    aiModel: aiEnabled ? aiModel : null,
    counts,
    categories: finalCategories,
    safetyRules: ENABLE_SAFETY ? safetyRules : [],
    otherExamples,
  };
}

function translateCategoryLabel(label, fallbackKey = "") {
  const text = cleanCell(label);
  if (!text) {
    return "";
  }
  if (CATEGORY_LABELS.has(fallbackKey)) {
    return CATEGORY_LABELS.get(fallbackKey);
  }
  if (LEGACY_LABELS.has(text)) {
    return LEGACY_LABELS.get(text);
  }
  return text;
}

function roundMoney(value) {
  return Math.round(Number(value || 0) * 1000) / 1000;
}

function columnName(index) {
  return toColumnLetter(index);
}

function toColumnLetter(index) {
  let n = index + 1;
  let result = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    result = String.fromCharCode(65 + rem) + result;
    n = Math.floor((n - 1) / 26);
  }
  return result;
}

function descIdxToLetter(index) {
  return toColumnLetter(index);
}

async function readJsonIfExists(filePath, fallback) {
  try {
    const text = await fs.readFile(filePath, "utf8");
    return JSON.parse(text);
  } catch {
    return fallback;
  }
}

async function fileExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}
