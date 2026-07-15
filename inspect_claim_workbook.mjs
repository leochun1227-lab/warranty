import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/Leo.Li/Downloads/claim_ytd_comparison_tickets_detail_20260709_135559.xlsx";
const outDir = "po_compare_inspect";
await fs.mkdir(outDir, { recursive: true });

console.log("loading input...");
const input = await FileBlob.load(inputPath);
console.log("importing xlsx...");
const workbook = await SpreadsheetFile.importXlsx(input);
console.log("imported");

const summary = await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 12000,
  tableMaxRows: 8,
  tableMaxCols: 12,
  tableMaxCellChars: 100,
});
console.log(summary.ndjson);

const sheets = workbook.worksheets.items;
for (const sheet of sheets) {
  console.log(`--- SHEET ${sheet.name} ---`);
  const used = sheet.getUsedRange();
  console.log(`usedRange=${used?.address ?? "<none>"}`);
  if (used) {
    const region = await workbook.inspect({
      kind: "region",
      sheetId: sheet.name,
      range: used.address,
      maxChars: 10000,
      tableMaxRows: 12,
      tableMaxCols: 20,
      tableMaxCellChars: 120,
    });
    console.log(region.ndjson);
  }
  if (sheet.name === "Sheet1") {
    const preview = await workbook.render({ sheetName: sheet.name, range: "A1:G20", scale: 1, format: "png" });
    await fs.writeFile(`${outDir}/${sheet.name.replace(/[^A-Za-z0-9_-]+/g, "_")}.png`, new Uint8Array(await preview.arrayBuffer()));
  }
}
