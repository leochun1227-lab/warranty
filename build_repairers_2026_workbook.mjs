import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = path.join(ROOT, "outputs", "repairers_2026");
const INPUT_JSON = path.join(OUTPUT_DIR, "repairers_2026_data.json");
const OUT_XLSX = path.join(OUTPUT_DIR, "repairers_2026_analysis_state.xlsx");

function money(v) {
  return Number(v || 0).toLocaleString(undefined, {
    style: "currency",
    currency: "AUD",
    maximumFractionDigits: 2,
  });
}

function num(v) {
  return Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function setTitle(sheet, text, subtitle) {
  sheet.getRange("A1:I1").merge();
  sheet.getRange("A1").values = [[text]];
  sheet.getRange("A2:I2").merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange("A1:I2").format = {
    fill: "#0F172A",
    font: { color: "#FFFFFF", bold: true },
  };
  sheet.getRange("A1").format = {
    font: { size: 18, bold: true, color: "#FFFFFF" },
  };
  sheet.getRange("A2").format = {
    font: { size: 11, color: "#D1D5DB" },
  };
}

function styleTable(sheet, headerRange, bodyRange) {
  sheet.getRange(headerRange).format = {
    fill: "#E8F1FF",
    font: { bold: true, color: "#0F172A" },
    borders: { preset: "all", style: "thin", color: "#D7E2EE" },
  };
  sheet.getRange(bodyRange).format = {
    borders: { preset: "all", style: "thin", color: "#E7EDF5" },
  };
}

async function main() {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
  const raw = JSON.parse(await fs.readFile(INPUT_JSON, "utf8"));

  const workbook = Workbook.create();
  const summary = workbook.worksheets.add("Summary");
  const repairers = workbook.worksheets.add("Repairers");
  const addresses = workbook.worksheets.add("Addresses");
  const states = workbook.worksheets.add("States");
  const variants = workbook.worksheets.add("Name Map");
  const details = workbook.worksheets.add("2026 Detail");

  // Summary sheet
  setTitle(
    summary,
    "2026 Repairer Analysis",
    "Source year filter uses Created On = 2026. Customer-like repairer names are excluded. State grouping is inferred from dealer/location text first, then dealer code fallback."
  );

  const summaryRows = [
    ["Metric", "Value"],
    ["Tickets kept", num(raw.summary.total_tickets)],
    ["Tickets excluded (customer-like repairer)", num(raw.summary.excluded_customer_like_repairer_rows)],
    ["Raw repairer names", num(raw.summary.unique_repairers_raw)],
    ["Normalized repairers", num(raw.summary.unique_repairers_normalized)],
    ["States", num(raw.summary.unique_states)],
    ["Address groups", num(raw.summary.unique_addresses)],
    ["Total warranty cost", money(raw.summary.total_warranty_cost)],
    ["Average warranty cost / ticket", money(raw.summary.avg_warranty_cost)],
  ];
  summary.getRange("A4:B12").values = summaryRows;
  styleTable(summary, "A4:B4", "A5:B12");

  summary.getRange("D4:I4").values = [[
    "Top 20 Repairers",
    "Tickets",
    "Total Warranty Cost",
    "Avg / Ticket",
    "Top State",
    "Top Address Group",
  ]];
  const top20 = raw.summary.top_repairers.slice(0, 20);
  summary.getRange(`D5:I${4 + top20.length}`).values = top20.map((r) => [
    r.repairer_name,
    Number(r.ticket_count || 0),
    Number(r.total_warranty_cost || 0),
    Number(r.avg_warranty_cost || 0),
    r.top_state || "",
    r.top_address_group || "",
  ]);
  summary.getRange(`E5:E${4 + top20.length}`).format.numberFormat = "#,##0";
  summary.getRange(`F5:G${4 + top20.length}`).format.numberFormat = "$#,##0.00";
  styleTable(summary, "D4:I4", `D5:I${4 + top20.length}`);

  summary.getRange("A14:I14").merge();
  summary.getRange("A14").values = [[
    "This workbook keeps both raw repairer names and a normalized repairer key so you can see how many naming variants exist in the source."
  ]];
  summary.getRange("A14").format = {
    font: { italic: true, color: "#475569" },
    fill: "#F8FAFC",
    borders: { preset: "all", style: "thin", color: "#E2E8F0" },
  };

  // Repairers sheet
  repairers.getRange("A1:O1").values = [[
    "Repairer Name",
    "Normalized Key",
    "Ticket Count",
    "Total Warranty Cost",
    "Avg Warranty Cost",
    "Unique Address Groups",
    "Unique States",
    "Top Address Group",
    "Top State",
    "Top Dealer Name",
    "Raw Variants",
    "Variants Detail",
    "First Created On",
    "Last Created On",
    "Notes",
  ]];
  const repairerStart = 2;
  const repairerEnd = repairerStart + raw.repairers.length - 1;
  repairers.getRange(`A${repairerStart}:O${repairerEnd}`).values = raw.repairers.map((r) => [
    r.repairer_name,
    r.normalized_key,
    Number(r.ticket_count || 0),
    Number(r.total_warranty_cost || 0),
    Number(r.avg_warranty_cost || 0),
    Number(r.unique_address_groups || 0),
    Number(r.unique_states || 0),
    r.top_address_group || "",
    r.top_state || "",
    r.top_dealer_name || "",
    Number(r.raw_name_variants || 0),
    r.raw_name_variants_text || "",
    r.first_created_on || "",
    r.last_created_on || "",
    "",
  ]);
  repairers.getRange(`C${repairerStart}:G${repairerEnd}`).format.numberFormat = "#,##0";
  repairers.getRange(`D${repairerStart}:E${repairerEnd}`).format.numberFormat = "$#,##0.00";
  styleTable(repairers, "A1:O1", `A${repairerStart}:O${repairerEnd}`);

  // Addresses sheet
  addresses.getRange("A1:H1").values = [[
    "Address Group",
    "Tickets",
    "Total Cost",
    "Avg Cost",
    "Repairers",
    "Top State",
    "Top Repairer",
    "Dealer",
  ]];
  const addressEnd = 2 + raw.addresses.length - 1;
  addresses.getRange(`A2:H${addressEnd}`).values = raw.addresses.map((r) => [
    r.address_group,
    Number(r.ticket_count || 0),
    Number(r.total_warranty_cost || 0),
    Number(r.avg_warranty_cost || 0),
    Number(r.unique_repairers || 0),
    r.top_state || "",
    r.top_repairer || "",
    r.top_dealer_name || "",
  ]);
  addresses.getRange(`B2:E${addressEnd}`).format.numberFormat = "#,##0";
  addresses.getRange(`C2:D${addressEnd}`).format.numberFormat = "$#,##0.00";
  styleTable(addresses, "A1:H1", `A2:H${addressEnd}`);

  // States sheet
  states.getRange("A1:H1").values = [[
    "State",
    "Tickets",
    "Total Cost",
    "Avg Cost",
    "Repairers",
    "Top Repairer",
    "Top Dealer",
    "Notes",
  ]];
  const stateEnd = 2 + raw.states.length - 1;
  states.getRange(`A2:H${stateEnd}`).values = raw.states.map((r) => [
    r.state,
    Number(r.ticket_count || 0),
    Number(r.total_warranty_cost || 0),
    Number(r.avg_warranty_cost || 0),
    Number(r.unique_repairers || 0),
    r.top_repairer || "",
    r.top_dealer_name || "",
    "",
  ]);
  states.getRange(`B2:E${stateEnd}`).format.numberFormat = "#,##0";
  states.getRange(`C2:D${stateEnd}`).format.numberFormat = "$#,##0.00";
  styleTable(states, "A1:H1", `A2:H${stateEnd}`);

  // Name Map sheet
  variants.getRange("A1:M1").values = [[
    "Raw Repairer Name",
    "Normalized Key",
    "State",
    "State Source",
    "Address Group",
    "Dealer Name",
    "Dealer Code",
    "Country/Region",
    "Postal Code",
    "Ticket ID",
    "Created On",
    "Status",
    "Claim Total Amount",
  ]];
  const variantEnd = 2 + raw.variants.length - 1;
  variants.getRange(`A2:M${variantEnd}`).values = raw.variants.map((r) => [
    r.raw_repairer_name,
    r.normalized_key,
    r.state || "",
    r.state_source || "",
    r.address_group,
    r.dealer_name,
    r.dealer_code,
    r.country_region,
    r.postal_code,
    r.ticket_id,
    r.created_on,
    r.status,
    Number(r.claim_total_amount || 0),
  ]);
  variants.getRange(`M2:M${variantEnd}`).format.numberFormat = "$#,##0.00";
  styleTable(variants, "A1:M1", `A2:M${variantEnd}`);

  // Detail sheet
  details.getRange("A1:P1").values = [[
    "Created On",
    "Posting Date",
    "Changed On",
    "Ticket ID",
    "Ticket",
    "Ticket Type",
    "Status",
    "Service Technician",
    "Normalized Key",
    "State",
    "Address Group",
    "Dealer Name",
    "Country/Region",
    "Postal Code",
    "Claim Total Amount",
    "Repairer Parts Claim Total Amount",
  ]];
  const detailRows = raw.details;
  const variantByTicket = new Map(raw.variants.map((r) => [r.ticket_id, r]));
  const detailEnd = 2 + detailRows.length - 1;
  details.getRange(`A2:P${detailEnd}`).values = detailRows.map((r) => [
    r["Created On"],
    r["Posting Date"],
    r["Changed On"],
    r["Ticket ID"],
    r["Ticket"],
    r["Ticket Type"],
    r["Status"],
    r["Service Technician"],
    variantByTicket.get(r["Ticket ID"])?.normalized_key || "",
    variantByTicket.get(r["Ticket ID"])?.state || "",
    variantByTicket.get(r["Ticket ID"])?.address_group || "",
    r["Dealer Name"],
    r["Country/Region"],
    r["Service Requester Postal Code"],
    Number(r["ClaimTotalAmount"] || 0),
    Number(r["Repairer Parts Claim Total Amount"] || 0),
  ]);
  details.getRange(`O2:P${detailEnd}`).format.numberFormat = "$#,##0.00";
  styleTable(details, "A1:P1", `A2:P${detailEnd}`);

  // Basic widths and freeze panes.
  for (const sheet of [summary, repairers, addresses, variants, details]) {
    sheet.freezePanes.freezeRows(1);
    sheet.showGridLines = false;
  }
  summary.getRange("A:A").format.columnWidthPx = 220;
  summary.getRange("B:B").format.columnWidthPx = 140;
  summary.getRange("D:D").format.columnWidthPx = 240;
  summary.getRange("E:E").format.columnWidthPx = 110;
  summary.getRange("F:F").format.columnWidthPx = 130;
  summary.getRange("G:G").format.columnWidthPx = 130;
  summary.getRange("H:H").format.columnWidthPx = 160;
  summary.getRange("I:I").format.columnWidthPx = 180;

  repairers.getRange("A:A").format.columnWidthPx = 280;
  repairers.getRange("B:B").format.columnWidthPx = 180;
  repairers.getRange("D:E").format.columnWidthPx = 120;
  repairers.getRange("F:F").format.columnWidthPx = 120;
  repairers.getRange("G:G").format.columnWidthPx = 100;
  repairers.getRange("H:H").format.columnWidthPx = 220;
  repairers.getRange("I:I").format.columnWidthPx = 100;
  repairers.getRange("J:J").format.columnWidthPx = 220;
  repairers.getRange("K:K").format.columnWidthPx = 90;
  repairers.getRange("L:L").format.columnWidthPx = 300;
  repairers.getRange("M:N").format.columnWidthPx = 110;
  repairers.getRange("O:O").format.columnWidthPx = 140;

  addresses.getRange("A:A").format.columnWidthPx = 280;
  addresses.getRange("B:B").format.columnWidthPx = 92;
  addresses.getRange("C:C").format.columnWidthPx = 120;
  addresses.getRange("D:D").format.columnWidthPx = 110;
  addresses.getRange("E:E").format.columnWidthPx = 96;
  addresses.getRange("F:F").format.columnWidthPx = 100;
  addresses.getRange("G:G").format.columnWidthPx = 240;
  addresses.getRange("H:H").format.columnWidthPx = 220;

  states.getRange("A:A").format.columnWidthPx = 90;
  states.getRange("B:B").format.columnWidthPx = 92;
  states.getRange("C:C").format.columnWidthPx = 120;
  states.getRange("D:D").format.columnWidthPx = 110;
  states.getRange("E:E").format.columnWidthPx = 96;
  states.getRange("F:F").format.columnWidthPx = 220;
  states.getRange("G:G").format.columnWidthPx = 220;
  states.getRange("H:H").format.columnWidthPx = 90;

  variants.getRange("A:A").format.columnWidthPx = 260;
  variants.getRange("B:B").format.columnWidthPx = 180;
  variants.getRange("C:C").format.columnWidthPx = 90;
  variants.getRange("D:D").format.columnWidthPx = 140;
  variants.getRange("E:E").format.columnWidthPx = 240;
  variants.getRange("F:F").format.columnWidthPx = 220;
  variants.getRange("G:G").format.columnWidthPx = 110;
  variants.getRange("H:H").format.columnWidthPx = 120;
  variants.getRange("I:I").format.columnWidthPx = 100;
  variants.getRange("J:J").format.columnWidthPx = 110;
  variants.getRange("K:K").format.columnWidthPx = 110;
  variants.getRange("L:L").format.columnWidthPx = 160;
  variants.getRange("M:M").format.columnWidthPx = 120;

  details.getRange("A:A").format.columnWidthPx = 110;
  details.getRange("B:C").format.columnWidthPx = 110;
  details.getRange("D:D").format.columnWidthPx = 90;
  details.getRange("E:E").format.columnWidthPx = 220;
  details.getRange("F:F").format.columnWidthPx = 180;
  details.getRange("G:G").format.columnWidthPx = 160;
  details.getRange("H:H").format.columnWidthPx = 250;
  details.getRange("I:I").format.columnWidthPx = 180;
  details.getRange("J:J").format.columnWidthPx = 90;
  details.getRange("K:K").format.columnWidthPx = 220;
  details.getRange("L:L").format.columnWidthPx = 220;
  details.getRange("M:M").format.columnWidthPx = 120;
  details.getRange("N:N").format.columnWidthPx = 120;
  details.getRange("O:P").format.columnWidthPx = 120;

  // Date formats
  details.getRange(`A2:C${detailEnd}`).format.numberFormat = "yyyy-mm-dd";
  repairers.getRange(`M2:N${repairerEnd}`).format.numberFormat = "yyyy-mm-dd";

  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(OUT_XLSX);
  console.log(`Saved ${OUT_XLSX}`);
}

await main();
