import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

async function resolveOutputDir() {
  const root = process.cwd();
  const stableMetaPath = path.join(root, "outputs", "parts_classified_meta.json");
  try {
    const meta = JSON.parse(await fs.readFile(stableMetaPath, "utf8"));
    const csvPath = String(meta?.csvPath || "").trim();
    if (csvPath) {
      return path.dirname(path.resolve(root, csvPath));
    }
  } catch {}

  const outputsDir = path.join(root, "outputs");
  const entries = await fs.readdir(outputsDir, { withFileTypes: true }).catch(() => []);
  const dirs = entries
    .filter((entry) => entry.isDirectory() && entry.name.startsWith("parts_classification_"))
    .map((entry) => entry.name)
    .sort()
    .reverse();
  if (dirs.length) {
    return path.join(outputsDir, dirs[0]);
  }
  return path.join(outputsDir, `parts_classification_${new Date().toISOString().slice(0, 10)}`);
}

const outputDir = await resolveOutputDir();
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
