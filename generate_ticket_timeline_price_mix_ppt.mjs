import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const artifactToolPath =
  process.env.ARTIFACT_TOOL_MODULE ||
  "C:/Users/Leo.Li/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";
const { Presentation, PresentationFile } = await import(pathToFileURL(artifactToolPath).href);

const summaryPath = path.join(__dirname, "generated_exports", "ticket_timeline_summary.json");
const outPath = path.join(__dirname, "generated_exports", "ticket_timeline_price_mix_2026.pptx");
const previewDir = path.join(__dirname, "generated_exports", "ticket_timeline_price_mix_2026_preview");

const durationColors = {
  "0-7": "#0f8f8c",
  "8-14": "#4f8f42",
  "15-21": "#2474a6",
  "22-30": "#d88a18",
  "31-60": "#ef9f42",
  "60+": "#c9513f",
};

const stageNames = {
  approval: "Warranty Approval",
  parts: "Parts Preparation",
  repair: "Repairer Time",
};

const stageTakeaways = {
  approval: "Approval speed by claim value band",
  parts: "Parts preparation timing by claim value band",
  repair: "Repairer timing by claim value band",
};

function fmt(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString("en-US") : "-";
}

function days(value) {
  const num = Number(value);
  return Number.isFinite(num) ? `${num.toFixed(1)} days` : "No completed tickets";
}

function pct(value) {
  const num = Number(value);
  return Number.isFinite(num) ? `${num.toFixed(1)}%` : "0.0%";
}

function addText(slide, text, position, style = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontSize: 18,
    color: "#1d2a3d",
    fontFace: "Aptos",
    ...style,
  };
  return shape;
}

function addCard(slide, position, fill = "#ffffff", line = "#d8e4f1") {
  return slide.shapes.add({
    geometry: "roundRect",
    position,
    fill,
    line: { style: "solid", fill: line, width: 1 },
    borderRadius: "rounded-xl",
    shadow: "shadow-sm",
  });
}

function topDurationRows(bucket) {
  return [...(bucket.distribution || [])]
    .filter((row) => Number(row[3]) > 0)
    .sort((a, b) => Number(b[3]) - Number(a[3]))
    .slice(0, 3);
}

function addDistributionRows(slide, rows, x, y, width) {
  const rowHeight = 20;
  rows.forEach((row, index) => {
    const top = y + index * rowHeight;
    slide.shapes.add({
      geometry: "ellipse",
      position: { left: x, top: top + 4, width: 9, height: 9 },
      fill: row[2] || durationColors[row[0]] || "#2474a6",
      line: { style: "solid", fill: "none", width: 0 },
    });
    addText(slide, `${row[0]} days`, { left: x + 16, top, width: 92, height: 19 }, { fontSize: 13, color: "#526176" });
    addText(slide, `${fmt(row[3])} tickets`, { left: x + 110, top, width: 94, height: 19 }, { fontSize: 13, color: "#1d2a3d", alignment: "right" });
    addText(slide, pct(row[1]), { left: x + width - 56, top, width: 56, height: 19 }, { fontSize: 13, color: "#1d2a3d", alignment: "right" });
  });
}

function addPriceBucket(slide, bucket, position) {
  addCard(slide, position, "#ffffff", "#d8e4f1");
  const pad = 18;
  const chartSize = 150;
  const x = position.left + pad;
  const y = position.top + 58;
  addText(slide, bucket.label, { left: position.left + pad, top: position.top + 16, width: 150, height: 28 }, { fontSize: 22, bold: true, color: "#172033" });
  addText(
    slide,
    `${fmt(bucket.count)} tickets  /  avg ${days(bucket.average)}`,
    { left: position.left + pad, top: position.top + 43, width: position.width - pad * 2, height: 22 },
    { fontSize: 13, color: "#647287" },
  );

  const distribution = bucket.distribution || [];
  slide.charts.add("doughnut", {
    position: { left: x, top: y, width: chartSize, height: chartSize },
    categories: distribution.map((row) => `${row[0]} days`),
    series: [
      {
        name: "Tickets",
        values: distribution.map((row) => Number(row[3]) || 0),
        points: distribution.map((row, idx) => ({
          idx,
          fill: row[2] || durationColors[row[0]] || "#2474a6",
          line: { style: "solid", fill: "#ffffff", width: 1 },
        })),
      },
    ],
    hasLegend: false,
    doughnutOptions: { holeSize: 58, firstSliceAngle: 270 },
    chartFill: "none",
    plotAreaFill: "none",
  });

  addDistributionRows(slide, distribution, x + chartSize + 20, y + 3, position.width - chartSize - 40);
}

function addHeader(slide, title, subtitle) {
  addText(slide, "2026 TICKET TIMELINE", { left: 64, top: 38, width: 280, height: 22 }, { fontSize: 12, bold: true, color: "#0f8f8c", charSpacing: 1.5 });
  addText(slide, title, { left: 64, top: 68, width: 860, height: 54 }, { fontSize: 36, bold: true, color: "#172033" });
  addText(slide, subtitle, { left: 66, top: 120, width: 760, height: 30 }, { fontSize: 16, color: "#647287" });
}

function addFooter(slide, pageNumber) {
  slide.shapes.add({
    geometry: "rect",
    position: { left: 64, top: 664, width: 1152, height: 1 },
    fill: "#d8e4f1",
    line: { style: "solid", fill: "none", width: 0 },
  });
  addText(slide, `Generated from ticket_timeline_summary.json`, { left: 64, top: 676, width: 420, height: 20 }, { fontSize: 11, color: "#7a8796" });
  addText(slide, String(pageNumber), { left: 1168, top: 676, width: 48, height: 20 }, { fontSize: 11, color: "#7a8796", alignment: "right" });
}

function createCover(presentation, stages) {
  const slide = presentation.slides.add();
  slide.background.fill = "#f5f8fb";
  addHeader(slide, "Price mix shows how ticket value relates to handling duration", "Each stage groups completed tickets by claim value, then shows the percentage of those tickets completed in each duration bucket.");

  const cardW = 360;
  stages.forEach((stage, index) => {
    const left = 64 + index * (cardW + 26);
    const evidence = stage.evidence;
    addCard(slide, { left, top: 232, width: cardW, height: 210 }, "#ffffff", "#d8e4f1");
    addText(slide, stageNames[stage.key] || stage.short, { left: left + 22, top: 254, width: cardW - 44, height: 28 }, { fontSize: 23, bold: true, color: "#172033" });
    addText(slide, `${fmt(evidence.priceDurationMix?.pricedTickets || 0)} priced tickets`, { left: left + 22, top: 300, width: cardW - 44, height: 28 }, { fontSize: 26, color: "#2474a6" });
    addText(slide, `Stage average: ${days(evidence.average)}`, { left: left + 22, top: 344, width: cardW - 44, height: 24 }, { fontSize: 16, color: "#526176" });
    addText(slide, `Current standard: ${fmt(evidence.standard)} days`, { left: left + 22, top: 374, width: cardW - 44, height: 24 }, { fontSize: 16, color: "#526176" });
  });
  addFooter(slide, 1);
}

function createStageSlide(presentation, stage, pageNumber) {
  const slide = presentation.slides.add();
  slide.background.fill = "#f7fafc";
  const evidence = stage.evidence;
  const buckets = evidence.priceDurationMix?.buckets || [];
  addHeader(
    slide,
    `${stageNames[stage.key] || stage.short}: duration mix by ticket value`,
    `${fmt(evidence.priceDurationMix?.pricedTickets || 0)} priced tickets. Percentages are ticket-count share within each price band, not claim amount share.`,
  );

  const positions = [
    { left: 64, top: 178, width: 548, height: 214 },
    { left: 668, top: 178, width: 548, height: 214 },
    { left: 64, top: 426, width: 548, height: 214 },
    { left: 668, top: 426, width: 548, height: 214 },
  ];
  buckets.forEach((bucket, index) => addPriceBucket(slide, bucket, positions[index]));
  addText(slide, stageTakeaways[stage.key] || "Price mix by duration", { left: 940, top: 42, width: 276, height: 32 }, { fontSize: 15, color: "#0d6d6b", alignment: "right" });
  addFooter(slide, pageNumber);
}

async function main() {
  const summary = JSON.parse(await fs.readFile(summaryPath, "utf8"));
  const stages = (summary.stages || []).filter((stage) => ["approval", "parts", "repair"].includes(stage.key));
  if (!stages.length) throw new Error("No ticket timeline stages found in summary JSON.");

  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
  createCover(presentation, stages);
  stages.forEach((stage, index) => createStageSlide(presentation, stage, index + 2));

  await fs.mkdir(path.dirname(outPath), { recursive: true });
  await fs.mkdir(previewDir, { recursive: true });
  for (const [index, slide] of presentation.slides.items.entries()) {
    const png = await presentation.export({ slide, format: "png", scale: 1 });
    await fs.writeFile(path.join(previewDir, `slide-${String(index + 1).padStart(2, "0")}.png`), new Uint8Array(await png.arrayBuffer()));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(path.join(previewDir, `slide-${String(index + 1).padStart(2, "0")}.layout.json`), await layout.text());
  }
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(outPath);
  console.log(`PPT written: ${outPath}`);
  console.log(`Preview written: ${previewDir}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
