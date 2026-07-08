import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const outputDir = path.join(process.cwd(), "outputs", "parts_classification_2026-07-06");
const xlsxPath = path.join(outputDir, "parts_classification_categorized.xlsx");

const input = await FileBlob.load(xlsxPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const renders = [
  {
    sheetName: "分类汇总",
    range: "A1:E18",
    file: "summary.png",
  },
  {
    sheetName: "规则说明",
    range: "A1:C18",
    file: "rules.png",
  },
  {
    sheetName: "分类明细",
    range: "A1:AL8",
    file: "detail_head.png",
  },
];

for (const item of renders) {
  const blob = await workbook.render({
    sheetName: item.sheetName,
    range: item.range,
    scale: 1,
    format: "png",
  });
  await fs.writeFile(path.join(outputDir, item.file), new Uint8Array(await blob.arrayBuffer()));
}

console.log("Rendered preview images.");
