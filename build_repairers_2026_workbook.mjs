import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = path.join(ROOT, "outputs", "repairers_2026");
const FAST_JSON = path.join(OUTPUT_DIR, "repairers_2026_fast.json");
const LIGHT_JSON = path.join(OUTPUT_DIR, "repairers_2026_light.json");
const DATA_JSON = path.join(OUTPUT_DIR, "repairers_2026_data.json");
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

function clean(v) {
  return v == null ? "" : String(v).trim();
}

function amountFromDetail(row) {
  return Number(row?.confirmed_cost_aud || row?.sap_po_cost_aud || row?.sap_po_net_value || 0) || 0;
}

function dateKey(row) {
  return clean(row?.["C4C Claim Approved On"] || row?.c4c_claim_approved_on || row?.["Posting Date"] || row?.["Created On"]);
}

function mostCommon(rows, getter) {
  const counts = new Map();
  for (const row of rows) {
    const value = clean(getter(row));
    if (!value) continue;
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] || "";
}

function repairerBaseDisplayName(value) {
  return clean(value).replace(/\s\([A-Z]{2,3}\)$/i, "");
}

function deriveAddressRows(details) {
  const groups = new Map();
  for (const row of details) {
    const cost = amountFromDetail(row);
    if (cost <= 0) continue;
    const address = clean(row.top_address_group || row.top_dealer_name || row.repairer_name || row["Dealer Name"] || "Unknown");
    const rec = groups.get(address) || { rows: [], ticket_count: 0, total_warranty_cost: 0, repairers: new Set() };
    rec.rows.push(row);
    rec.ticket_count += 1;
    rec.total_warranty_cost += cost;
    rec.repairers.add(clean(row.repairer_name || row.RepairerName));
    groups.set(address, rec);
  }
  return Array.from(groups.entries()).map(([address_group, rec]) => ({
    address_group,
    ticket_count: rec.ticket_count,
    total_warranty_cost: rec.total_warranty_cost,
    avg_warranty_cost: rec.ticket_count ? rec.total_warranty_cost / rec.ticket_count : 0,
    unique_repairers: rec.repairers.size,
    top_state: mostCommon(rec.rows, row => row.state || row.State),
    top_repairer: mostCommon(rec.rows, row => row.repairer_name || row.RepairerName),
    top_dealer_name: mostCommon(rec.rows, row => row.top_dealer_name || row["Dealer Name"] || row.DealerName),
  })).sort((a, b) => b.ticket_count - a.ticket_count || b.total_warranty_cost - a.total_warranty_cost);
}

function deriveVariantRows(details) {
  return details
    .filter(row => amountFromDetail(row) > 0)
    .map(row => ({
      raw_repairer_name: clean(row.repairer_name_before_rule_mapping || row.raw_repairer_name || row.c4c_compare_repairer || row["Service Technician"] || row.repairer_name),
      normalized_key: clean(row.normalized_key || row.repairer_base_name || row.repairer_name).toUpperCase(),
      state: clean(row.state || row.State),
      state_source: clean(row.repairer_name_rule_source) ? "repairer_mapping" : clean(row.state_source || ""),
      address_group: clean(row.top_address_group || row.top_dealer_name || row.repairer_name),
      dealer_name: clean(row.top_dealer_name || row["Dealer Name"] || row.DealerName),
      dealer_code: clean(row.repairshop_id || row.RepairerBusinessNameID || row.Dealer),
      country_region: clean(row["Country/Region"]),
      postal_code: clean(row["Service Requester Postal Code"]),
      ticket_id: clean(row["Ticket ID"] || row.TicketID),
      created_on: clean(row["Created On"]),
      status: clean(row.Status || row.status),
      claim_total_amount: Number(row.ClaimTotalAmount || row.current_claim_amount_aud || 0) || 0,
    }));
}

function applyFastRepairerSource(raw, fast, light) {
  if (!fast || !Array.isArray(fast.repairers) || !fast.repairers.length) return raw;
  const details = Array.isArray(light.details) ? light.details : [];
  const repairers = fast.repairers
    .slice()
    .sort((a, b) => Number(b.ticketCount || 0) - Number(a.ticketCount || 0) || Number(b.totalCost || 0) - Number(a.totalCost || 0));
  raw.details = details;
  raw.summary = {
    ...(raw.summary || {}),
    total_tickets: Number(fast.summary?.total_tickets || 0),
    excluded_customer_like_repairer_rows: Number(fast.summary?.excluded_customer_like_repairer_rows || 0),
    unique_repairers_raw: repairers.length,
    unique_repairers_normalized: repairers.length,
    unique_states: Array.isArray(fast.states) ? fast.states.length : 0,
    unique_addresses: new Set(details.map(row => clean(row.top_address_group || row.top_dealer_name)).filter(Boolean)).size,
    total_warranty_cost: Number(fast.summary?.confirmed_cost || 0),
    avg_warranty_cost: Number(fast.summary?.avg_confirmed_cost || 0),
    top_repairers: repairers.slice(0, 20).map(r => ({
      repairer_name: r.repairName,
      ticket_count: Number(r.ticketCount || 0),
      total_warranty_cost: Number(r.totalCost || 0),
      avg_warranty_cost: Number(r.avgCost || 0),
      top_state: r.state || "",
      top_address_group: r.top_address_group || r.top_dealer_name || "",
    })),
  };
  raw.repairers = repairers.map(r => ({
    repairer_name: r.repairName,
    normalized_key: repairerBaseDisplayName(r.repairer_base_name || r.repairName).toUpperCase(),
    ticket_count: Number(r.ticketCount || 0),
    total_warranty_cost: Number(r.totalCost || 0),
    avg_warranty_cost: Number(r.avgCost || 0),
    unique_address_groups: r.top_address_group || r.top_dealer_name ? 1 : 0,
    unique_states: r.state ? 1 : 0,
    top_address_group: r.top_address_group || r.top_dealer_name || "",
    top_state: r.state || "",
    top_dealer_name: r.top_dealer_name || "",
    raw_name_variants: 1,
    raw_name_variants_text: r.repairName,
    first_created_on: r.first_created_on || "",
    last_created_on: r.last_created_on || "",
  }));
  raw.states = (Array.isArray(fast.states) ? fast.states : []).map(r => ({
    state: r.state,
    ticket_count: Number(r.ticket_count || 0),
    total_warranty_cost: Number(r.confirmed_cost || 0),
    avg_warranty_cost: Number(r.avg_confirmed_cost || 0),
    unique_repairers: Number(r.unique_repairers || 0),
    top_repairer: "",
    top_dealer_name: "",
  }));
  raw.addresses = deriveAddressRows(details);
  raw.variants = deriveVariantRows(details);
  return raw;
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
  const raw = JSON.parse(await fs.readFile(DATA_JSON, "utf8"));
  const fast = JSON.parse(await fs.readFile(FAST_JSON, "utf8").catch(() => "{}"));
  const light = JSON.parse(await fs.readFile(LIGHT_JSON, "utf8").catch(() => "{}"));
  if (Array.isArray(light.details) && light.details.length) {
    raw.details = light.details;
  }
  applyFastRepairerSource(raw, fast, light);

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
  details.getRange("A1:U1").values = [[
    "Created On",
    "SAP PO Date",
    "Changed On",
    "C4C Approved Date",
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
    "SAP PO Cost AUD",
    "SAP Invoice Amount AUD",
    "Invoice Number",
    "Invoice Date",
    "C4C Claim Total Amount",
    "C4C Repairer Parts Claim Total Amount",
  ]];
  const detailRows = raw.details;
  const variantByTicket = new Map(raw.variants.map((r) => [r.ticket_id, r]));
  const detailEnd = 2 + detailRows.length - 1;
  details.getRange(`A2:U${detailEnd}`).values = detailRows.map((r) => [
    r["Created On"],
    r["Posting Date"],
    r["Changed On"],
    r["C4C Claim Approved On"] || r.c4c_claim_approved_on || "",
    r["Ticket ID"],
    r["Ticket"],
    r["Ticket Type"],
    r["Status"],
    r.repairer_name || r.RepairerName || r["Service Technician"],
    r.normalized_key || repairerBaseDisplayName(r.repairer_name || r.RepairerName || r["Service Technician"]).toUpperCase(),
    r.state || r.State || variantByTicket.get(r["Ticket ID"])?.state || "",
    r.top_address_group || r.top_dealer_name || r.repairer_name || variantByTicket.get(r["Ticket ID"])?.address_group || "",
    r["Dealer Name"],
    r["Country/Region"],
    r["Service Requester Postal Code"],
    Number(r.confirmed_cost_aud || r.sap_po_net_value || 0),
    Number(r.sap_signed_invoice_amount || r.sap_raw_invoice_amount || 0),
    r.invoice_number || "",
    r.invoice_date || "",
    Number(r["ClaimTotalAmount"] || 0),
    Number(r["Repairer Parts Claim Total Amount"] || 0),
  ]);
  details.getRange(`P2:Q${detailEnd}`).format.numberFormat = "$#,##0.00";
  details.getRange(`T2:U${detailEnd}`).format.numberFormat = "$#,##0.00";
  styleTable(details, "A1:U1", `A2:U${detailEnd}`);

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
  details.getRange("D:D").format.columnWidthPx = 120;
  details.getRange("E:E").format.columnWidthPx = 120;
  details.getRange("F:F").format.columnWidthPx = 220;
  details.getRange("G:G").format.columnWidthPx = 160;
  details.getRange("H:H").format.columnWidthPx = 180;
  details.getRange("I:I").format.columnWidthPx = 180;
  details.getRange("J:J").format.columnWidthPx = 90;
  details.getRange("K:K").format.columnWidthPx = 90;
  details.getRange("L:L").format.columnWidthPx = 220;
  details.getRange("M:M").format.columnWidthPx = 220;
  details.getRange("N:O").format.columnWidthPx = 120;
  details.getRange("P:Q").format.columnWidthPx = 130;
  details.getRange("R:S").format.columnWidthPx = 130;
  details.getRange("T:U").format.columnWidthPx = 130;

  // Date formats
  details.getRange(`A2:D${detailEnd}`).format.numberFormat = "yyyy-mm-dd";
  details.getRange(`S2:S${detailEnd}`).format.numberFormat = "yyyy-mm-dd";
  repairers.getRange(`M2:N${repairerEnd}`).format.numberFormat = "yyyy-mm-dd";

  const xlsx = await SpreadsheetFile.exportXlsx(workbook);
  await xlsx.save(OUT_XLSX);
  console.log(`Saved ${OUT_XLSX}`);
}

await main();
