import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = "C:/Users/Leo.Li/Desktop/Parts.xlsx";
const outputDir = "C:/Users/Leo.Li/Documents/GitHub/warranty/outputs/parts_top10_createdon_split";
const outputPath = `${outputDir}/Parts_Top10_Failure_Components_CreatedOn.xlsx`;
const previewDir = `${outputDir}/previews`;

const components = [
  { component: "Marker Light", sheet: "01 Marker Light" },
  { component: "Stop Light", sheet: "02 Stop Light" },
  { component: "Tail Light", sheet: "03 Tail Light" },
  { component: "Main Door", sheet: "04 Main Door" },
  { component: "Reflector", sheet: "05 Reflector" },
  { component: "Window Blade", sheet: "06 Window Blade" },
  { component: "Roof Hatch", sheet: "07 Roof Hatch" },
  { component: "Solar", sheet: "08 Solar" },
  { component: "Decal", sheet: "09 Decal" },
  { component: "Clip", sheet: "10 Clip" },
];

const normalize = value => String(value ?? "").trim().replace(/\s+/g, " ").toLowerCase();
const componentTokens = value => String(value ?? "")
  .split(";")
  .map(normalize)
  .filter(Boolean);

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const input = await FileBlob.load(inputPath);
const sourceWorkbook = await SpreadsheetFile.importXlsx(input);
const sourceSheet = sourceWorkbook.worksheets.getItemAt(0);
const sourceValues = sourceSheet.getRange("A1:AC4384").values;
const headers = sourceValues[0].map(value => value ?? "");
const componentCol = headers.findIndex(header => normalize(header) === "components");
if (componentCol < 0) {
  throw new Error("Components column was not found in Parts.xlsx");
}

const outputWorkbook = Workbook.create();
const counts = {};

for (const item of components) {
  const wanted = normalize(item.component);
  const rows = sourceValues.slice(1).filter(row => componentTokens(row[componentCol]).includes(wanted));
  const matrix = [headers, ...rows];
  const sheet = outputWorkbook.worksheets.add(item.sheet);
  counts[item.component] = rows.length;
  sheet.showGridLines = true;
  sheet.getRangeByIndexes(0, 0, matrix.length, headers.length).values = matrix;
  sheet.getRangeByIndexes(0, 0, 1, headers.length).format = {
    fill: "#D9EAF7",
    font: { bold: true, color: "#172033" },
  };
  sheet.getRangeByIndexes(0, 0, Math.max(matrix.length, 1), headers.length).format.autofitColumns();
  sheet.freezePanes.freezeRows(1);

  const preview = await outputWorkbook.render({
    sheetName: item.sheet,
    range: `A1:E${Math.min(matrix.length, 20)}`,
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    `${previewDir}/${item.sheet.replace(/[^A-Za-z0-9]+/g, "_")}.png`,
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const errors = await outputWorkbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

const firstSheetCheck = await outputWorkbook.inspect({
  kind: "table",
  range: "01 Marker Light!A1:E20",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 5,
});

const exported = await SpreadsheetFile.exportXlsx(outputWorkbook);
await exported.save(outputPath);

await fs.writeFile(
  `${outputDir}/verification.json`,
  JSON.stringify({ outputPath, counts, formulaErrors: errors.ndjson, firstSheetCheck: firstSheetCheck.ndjson }, null, 2),
  "utf8",
);

console.log(JSON.stringify({ outputPath, counts }, null, 2));
