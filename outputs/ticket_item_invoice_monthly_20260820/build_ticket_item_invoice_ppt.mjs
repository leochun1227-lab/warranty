import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const OUT_DIR = "C:/Users/Leo.Li/Documents/GitHub/warranty/outputs/ticket_item_invoice_monthly_20260820";
const INPUT_JSON = path.join(OUT_DIR, "ticket_item_invoice_monthly.json");
const OUT_PPTX = path.join(OUT_DIR, "ticket_item_invoice_monthly_trend.pptx");

const payload = JSON.parse(await fs.readFile(INPUT_JSON, "utf8"));
const monthly = payload.monthly || [];
const totalItems = monthly.reduce((sum, row) => sum + Number(row["C4C Item Rows"] || 0), 0);
const totalTickets = monthly.reduce((sum, row) => sum + Number(row.Tickets || 0), 0);
const peak = monthly.reduce((best, row) => Number(row["C4C Item Rows"] || 0) > Number(best["C4C Item Rows"] || 0) ? row : best, monthly[0] || {});

function addText(slide, name, text, position, style) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    name,
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = style;
  return shape;
}

const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });

const slide = presentation.slides.add();
slide.background.fill = "#FFFFFF";

addText(slide, "eyebrow", "SAP invoice date basis | 2025-09-01 to 2026-08-20", { left: 72, top: 48, width: 760, height: 30 }, {
  fontSize: 16,
  bold: true,
  color: "#5B6472",
});
addText(slide, "title", "C4C item rows peaked in the latest invoice months", { left: 72, top: 92, width: 840, height: 110 }, {
  fontSize: 50,
  bold: true,
  color: "#000000",
});
addText(slide, "subtitle", "Each ticket is allocated to the month of its latest non-reversed SAP EKBE BEWTP='Q' posting date; the line tracks how many C4C item rows sit under those tickets.", { left: 72, top: 202, width: 910, height: 58 }, {
  fontSize: 20,
  color: "#303743",
});

slide.shapes.add({
  geometry: "rect",
  name: "rule",
  position: { left: 72, top: 282, width: 1136, height: 1 },
  fill: "#B8BCC4",
  line: { style: "solid", fill: "#B8BCC4", width: 0 },
});

slide.charts.add("line", {
  name: "item-trend",
  position: { left: 72, top: 318, width: 770, height: 320 },
  categories: monthly.map((row) => row.Month),
  series: [
    { name: "C4C item rows", values: monthly.map((row) => Number(row["C4C Item Rows"] || 0)), fill: "#3D8DFF" },
    { name: "Tickets", values: monthly.map((row) => Number(row.Tickets || 0)), fill: "#6DCBF4" },
  ],
  hasLegend: true,
  yAxis: {
    majorGridlines: { style: "solid", fill: "#E6E8EC", width: 1 },
    numberFormatCode: "#,##0",
  },
  xAxis: { textStyle: { fontSize: 10 } },
});

const metricTop = 330;
const metrics = [
  ["Total item rows", totalItems.toLocaleString("en-US")],
  ["Tickets invoiced", totalTickets.toLocaleString("en-US")],
  ["Peak month", `${peak.Month || ""} (${Number(peak["C4C Item Rows"] || 0).toLocaleString("en-US")})`],
];
for (let i = 0; i < metrics.length; i++) {
  const y = metricTop + i * 92;
  addText(slide, `metric-label-${i}`, metrics[i][0], { left: 900, top: y, width: 250, height: 26 }, {
    fontSize: 18,
    bold: true,
    color: "#5B6472",
  });
  addText(slide, `metric-value-${i}`, metrics[i][1], { left: 900, top: y + 28, width: 300, height: 44 }, {
    fontSize: 35,
    bold: true,
    color: "#000000",
  });
}

addText(slide, "source-note", "Source: C4C parts_classified.csv joined to SAP HANA EKBE/RBKP invoice PO-item aggregates; RBKP reversal docs excluded.", { left: 72, top: 666, width: 1030, height: 26 }, {
  fontSize: 13,
  color: "#5B6472",
});

slide.notes = `[Sources]\n- ${payload.meta.c4cPartsSource}\n- ${payload.meta.sapSource}\n- Date rule: ${payload.meta.dateRule}\n`;

const png = await presentation.export({ slide, format: "png", scale: 1 });
await fs.writeFile(path.join(OUT_DIR, "ppt_slide_preview.png"), new Uint8Array(await png.arrayBuffer()));
const layout = await slide.export({ format: "layout" });
await fs.writeFile(path.join(OUT_DIR, "ppt_slide.layout.json"), await layout.text());
const inspect = await presentation.inspect({ kind: "slide,textbox,chart,shape,notes", maxChars: 8000 });
await fs.writeFile(path.join(OUT_DIR, "ppt_inspect.ndjson"), inspect.ndjson);
console.log(inspect.ndjson);
const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(OUT_PPTX);
console.log(OUT_PPTX);
