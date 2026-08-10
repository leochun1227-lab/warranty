import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = path.join(ROOT, "outputs", "repairers_2026");
const TICKET_BASE_CSV = path.join(ROOT, "outputs", "analysis_ticket_base.csv");
const FAST_JSON = path.join(OUTPUT_DIR, "repairers_2026_fast.json");
const LIGHT_JSON = path.join(OUTPUT_DIR, "repairers_2026_light.json");
const DATA_JSON = path.join(OUTPUT_DIR, "repairers_2026_data.json");
const REPAIR_YEAR = 2026;
const REPAIR_COST_SANITY_MIN_AUD = Number(process.env.REPAIR_COST_SANITY_MIN_AUD || "50000");
const REPAIR_COST_SANITY_RATIO = Number(process.env.REPAIR_COST_SANITY_RATIO || "10");
const LIGHT_DETAIL_FIELDS = [
  "Ticket ID", "TicketID", "Ticket", "Created On", "Posting Date", "Changed On", "Approved Date",
  "Ticket Type", "Status", "Service Technician", "repairer_name", "repairer_base_name",
  "raw_repairer_name", "repairer_split_key", "repairshop_id", "RepairerBusinessNameID",
  "Dealer", "Dealer Name", "Country/Region", "Service Requester Postal Code", "state",
  "ERP Purchase Order ID", "Sales Order", "ClaimTotalAmount", "Factory Parts Claim Total Amount",
  "LabourHoursTotalAmount", "Repairer Parts Claim Total Amount", "invoice_status", "invoice_number",
  "invoice_date", "current_claim_amount_aud", "sap_po_cost_native", "sap_po_cost_currency",
  "sap_po_cost_aud", "confirmed_cost_source", "po_cost_override_reason", "confirmed_cost_aud",
  "pending_amount_aud", "is_snowy_river", "Serial ID", "Chassis Number", "Registered Product",
  "Product",
];

const STATE_ABBR_LABELS = new Set(["QLD", "NSW", "VIC", "WA", "SA", "TAS", "ACT", "NT", "NZ"]);
const VEHICLE_PREFIXES = [
  "SRC", "SRH", "SRT", "SRM", "SRP", "SRL", "SRV", "SRS",
  "NGB", "NG",
  "LRV", "LRT", "LRH", "LRM", "LRP", "LRL", "LRS", "LRC", "LTR", "LVR", "LPV", "LEP",
  "RRV", "RRT", "RRH", "RRM", "RRP", "RRL", "RRS", "RRC",
  "SCR",
];

function clean(value) {
  return value == null ? "" : String(value).trim();
}

function parseAmount(value) {
  const n = Number(clean(value).replace(/,/g, ""));
  return Number.isFinite(n) ? n : 0;
}

function round(value, digits = 2) {
  const n = Number(value) || 0;
  const factor = 10 ** digits;
  return Math.round(n * factor) / factor;
}

function readJson(filePath, fallback = null) {
  if (!fs.existsSync(filePath)) return fallback;
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function readDetailPayload() {
  for (const filePath of [DATA_JSON, LIGHT_JSON]) {
    const payload = readJson(filePath, null);
    if (payload && Array.isArray(payload.details)) return { payload, filePath };
  }
  return { payload: null, filePath: "" };
}

function lightDetailRow(row) {
  const out = {};
  for (const field of LIGHT_DETAIL_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(row, field)) out[field] = row[field];
  }
  return out;
}

function writeLightPayload(sourcePayload, details) {
  const payload = {
    meta: sourcePayload?.meta || {},
    summary: sourcePayload?.summary || {},
    repairers: Array.isArray(sourcePayload?.repairers) ? sourcePayload.repairers : [],
    addresses: Array.isArray(sourcePayload?.addresses) ? sourcePayload.addresses : [],
    states: Array.isArray(sourcePayload?.states) ? sourcePayload.states : [],
    weekly: Array.isArray(sourcePayload?.weekly) ? sourcePayload.weekly : [],
    details: details.map(lightDetailRow),
  };
  fs.writeFileSync(LIGHT_JSON, JSON.stringify(payload), "utf8");
}

function parseCSV(text) {
  if (!text) return [];
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === "\"") {
        if (text[i + 1] === "\"") {
          cell += "\"";
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += ch;
      }
    } else if (ch === "\"") {
      quoted = true;
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
  row.push(cell);
  rows.push(row);
  return rows.filter((r) => r.some((c) => clean(c) !== ""));
}

function rowsToObjects(rows) {
  if (!rows.length) return [];
  const headers = rows[0].map(clean);
  return rows.slice(1).map((row) => {
    const obj = {};
    headers.forEach((header, index) => {
      if (header) obj[header] = clean(row[index]);
    });
    return obj;
  });
}

function readCsvObjects(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return rowsToObjects(parseCSV(fs.readFileSync(filePath, "utf8")));
}

function parseDate(value) {
  const text = clean(value);
  if (!text || text === "#") return null;
  let m = text.match(/^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{4})$/);
  if (m) {
    const d = new Date(+m[3], +m[2] - 1, +m[1]);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (/^\d{8}$/.test(text)) {
    const d = new Date(+text.slice(0, 4), +text.slice(4, 6) - 1, +text.slice(6, 8));
    return Number.isNaN(d.getTime()) ? null : d;
  }
  m = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) {
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const d = new Date(text);
  return Number.isNaN(d.getTime()) ? null : d;
}

function dateKey(value) {
  const d = parseDate(value);
  if (!d) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function monthEnd(month) {
  if (!/^\d{4}-\d{2}$/.test(clean(month))) return "";
  const d = new Date(+month.slice(0, 4), +month.slice(5, 7), 0);
  return dateKey(d);
}

function ticketKey(row) {
  return clean(row?.["Ticket ID"] || row?.TicketID || row?.Ticket || row?.TicketName || "");
}

function sourceTicketKey(row) {
  return clean(row?.["Ticket ID"] || row?.Ticket || "");
}

function addMapValue(map, key, value) {
  const k = clean(key);
  if (!k || map.has(k)) return;
  map.set(k, value);
}

function buildSourceEnrichment() {
  const sourceRows = readCsvObjects(TICKET_BASE_CSV);
  const map = new Map();
  sourceRows.forEach((row) => {
    const value = {
      serialId: clean(row["Serial ID"] || row.SerialID),
      chassisNumber: clean(row["Chassis Number"] || row.ChassisNumber),
      registeredProduct: clean(row["Registered Product"] || row.RegisteredProduct),
      product: clean(row.Product),
      salesOrder: clean(row["Sales Order"] || row.SalesOrder || row["ERP Free Order ID"]),
    };
    addMapValue(map, sourceTicketKey(row), value);
    addMapValue(map, row.Ticket, value);
    addMapValue(map, row["Ticket ID"], value);
  });
  return map;
}

function normalizeVehicleToken(value) {
  let text = clean(value).toUpperCase();
  if (!text || text === "#") return "";
  text = text.replace(/[\[\]{}()]/g, " ").replace(/[-_]/g, "").replace(/[^A-Z0-9]+/g, "");
  if (!text || text.length < 5) return "";
  if (/^\d{5,7}$/.test(text)) return text;
  if (VEHICLE_PREFIXES.some((prefix) => text.startsWith(prefix)) && /\d/.test(text)) return text;
  if (/^[A-Z]{1,4}\d{4,}[A-Z0-9]*$/.test(text)) return text;
  return "";
}

function vehicleTokenFromText(value) {
  const raw = clean(value);
  if (!raw || raw === "#") return "";
  const direct = normalizeVehicleToken(raw);
  if (direct) return direct;
  const matches = raw.toUpperCase().match(/[A-Z]{1,4}[-_\s]?\d{4,7}[A-Z0-9-]*/g) || [];
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const token = normalizeVehicleToken(matches[i]);
    if (token) return token;
  }
  const numeric = raw.match(/\b\d{5,7}\b/g) || [];
  return numeric.length ? numeric[numeric.length - 1] : "";
}

function enrichDetailRows(rows, sourceMap) {
  return (Array.isArray(rows) ? rows : []).map((row) => {
    const source = sourceMap.get(ticketKey(row)) || sourceMap.get(clean(row.Ticket)) || {};
    const out = { ...row };
    if (!clean(out["Serial ID"]) && !clean(out.SerialID)) out["Serial ID"] = source.serialId || "";
    if (!clean(out["Chassis Number"]) && !clean(out.ChassisNumber)) out["Chassis Number"] = source.chassisNumber || "";
    if (!clean(out["Registered Product"]) && !clean(out.RegisteredProduct)) out["Registered Product"] = source.registeredProduct || "";
    if (!clean(out.Product)) out.Product = source.product || "";
    if (!clean(out["Sales Order"]) && !clean(out.SalesOrder)) out["Sales Order"] = source.salesOrder || "";
    return out;
  });
}

function chassisFromRow(row) {
  const candidates = [
    row.SerialID,
    row["Serial ID"],
    row.ChassisNumber,
    row["Chassis Number"],
    row.VehicleDispatchSerial,
    row["Vehicle Dispatch Serial"],
    row.RegisteredProduct,
    row["Registered Product"],
    row.Product,
    row.TicketName,
    row.Ticket,
    row["Ticket ID"],
  ];
  for (const value of candidates) {
    const token = vehicleTokenFromText(value);
    if (token) return token;
  }
  return "";
}

function isClosedCase(row) {
  const status = clean(row.invoice_status || row["Invoice Status"] || row.InvoiceStatus).toLowerCase();
  const invoiceNumber = clean(row.invoice_number || row["Invoice Number"] || row.InvoiceNumber);
  const ticketStatus = clean(row.Status || row.TicketStatusText || row.TicketStatus || row["Ticket Status"]).toLowerCase();
  return !!invoiceNumber || status === "invoiced" || status === "closed" || ticketStatus.includes("invoiced") || ticketStatus.includes("closed");
}

function isUnapprovedRepairTicket(row) {
  const status = clean(row.Status || row.TicketStatusText || row.TicketStatus || row["Ticket Status"]).toLowerCase();
  return status.includes("unapproved");
}

function isApprovedRepairCostTicket(row) {
  if (isUnapprovedRepairTicket(row)) return false;
  const status = clean(row.Status || row.TicketStatusText || row.TicketStatus || row["Ticket Status"]).toLowerCase();
  const approvedDate = clean(row.approved_date || row.ApprovedDate || row["Approved Date"]);
  return !!approvedDate ||
    status.includes("approved") ||
    [
      "partially picked",
      "dispatch parts",
      "repair in progress",
      "repairer invoiced received",
      "repairer invoiced processed",
    ].includes(status);
}

function repairDecisionDateKey(row) {
  return dateKey(
    row.approved_date ||
    row.ApprovedDate ||
    row["Approved Date"] ||
    row.ClaimApprovedOnDateTime ||
    row.ClaimApprovedOn ||
    row["Claim Approved On"] ||
    row.PostingDate ||
    row["Posting Date"] ||
    row.ChangedOn ||
    row["Changed On"] ||
    row.CreatedOn ||
    row["Created On"] ||
    row.createdOn ||
    ""
  );
}

function repairCostFromTicket(row) {
  const fields = ["confirmed_cost_aud", "confirmed_cost", "invoice_amount_aud", "invoice_amount", "po_amount_aud", "po_amount"];
  for (const field of fields) {
    if (Object.prototype.hasOwnProperty.call(row, field)) return repairCostAfterSanityCheck(row, parseAmount(row[field]));
  }
  return 0;
}

function openPoRepairCostFromTicket(row) {
  const cost = repairCostFromTicket(row);
  return cost > 0 && isApprovedRepairCostTicket(row) ? cost : 0;
}

function claimAmountFromTicket(row) {
  const fields = ["ClaimTotalAmount", "Claim Total Amount", "claim_total_amount", "claim_total_amount_aud", "AmountIncludingTax", "Amount Including Tax", "rawTicketAmount", "amount"];
  for (const field of fields) {
    if (Object.prototype.hasOwnProperty.call(row, field)) {
      const amount = parseAmount(row[field]);
      if (amount > 0) return amount;
    }
  }
  const parts = [
    row["Factory Parts Claim Total Amount"],
    row.FactoryPartsClaimTotalAmount,
    row.LabourHoursTotalAmount,
    row["Repairer Parts Claim Total Amount"],
    row.RepairerPartsClaimTotalAmount,
  ].reduce((sum, value) => sum + parseAmount(value), 0);
  return parts > 0 ? parts : 0;
}

function repairCostAfterSanityCheck(row, cost) {
  const amount = Number(cost);
  if (!Number.isFinite(amount) || amount <= 0) return 0;
  const claim = claimAmountFromTicket(row);
  if (amount >= REPAIR_COST_SANITY_MIN_AUD && claim > 0 && amount / claim >= REPAIR_COST_SANITY_RATIO) {
    return claim;
  }
  return amount;
}

function stateAbbr(value) {
  const raw = clean(value).toUpperCase();
  if (STATE_ABBR_LABELS.has(raw)) return raw;
  const map = {
    "NEW SOUTH WALES": "NSW",
    VICTORIA: "VIC",
    "WESTERN AUSTRALIA": "WA",
    "SOUTH AUSTRALIA": "SA",
    TASMANIA: "TAS",
    QUEENSLAND: "QLD",
    "AUSTRALIAN CAPITAL TERRITORY": "ACT",
    "NORTHERN TERRITORY": "NT",
    "NEW ZEALAND": "NZ",
  };
  return map[raw] || raw || "";
}

function repairInfo(row) {
  const repairId = clean(row.repairer_split_key || row.repairerSplitKey || row.repairer_id || row.repairerId);
  const repairName = clean(row.repairer_name || row.RepairerName || row["Service Technician"] || row.ServiceTechnician);
  const state = stateAbbr(row.state || row.State);
  if (!repairId && !repairName) return null;
  if ((repairId || "").toLowerCase() === "no-repair" || repairName === "No Repair Shop Assigned") return null;
  return {
    repairId: repairId || `${repairName}|${state || "NA"}`,
    repairName: repairName || repairId || "Unknown Repair Shop",
    state,
  };
}

function isSnowyRiverTicket(row) {
  if (row.is_snowy_river === true || String(row.is_snowy_river).toLowerCase() === "true") return true;
  const text = clean(row["Service Technician"] || row.ServiceTechnician || row.raw_repairer_name || row.rawRepairerName || row.RepairerName || row.repairer_name).toUpperCase();
  return text.includes("SNOWY RIVER RV PTY LTD") || /\bSNOWY\s+RIVER\b/.test(text);
}

function emptyRepair(id, name, state = "") {
  return {
    repairId: id,
    repairName: name,
    state,
    totalCost: 0,
    avgCost: 0,
    ticketCount: 0,
    openCases: 0,
    closedCases: 0,
    chassisTicketCount: 0,
    uniqueChassisCount: 0,
    repeatChassisOver2Count: 0,
    repeatChassisOver2Ratio: 0,
    chassisTicketRatio: 0,
    repeatedClaimAmount: 0,
    repeatedClaimTicketCount: 0,
    avgRepeatedClaimAmount: 0,
    costByType: {},
    costRanges: { low: 0, medium: 0, high: 0 },
    top_dealer_name: "",
    top_address_group: "",
    first_created_on: "",
    last_created_on: "",
    _chassisRecords: new Map(),
    _dealerCounter: new Map(),
  };
}

function addCounter(map, key) {
  const k = clean(key);
  if (!k) return;
  map.set(k, (map.get(k) || 0) + 1);
}

function topCounterValue(map) {
  return [...map.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] || "";
}

function analyzeRepair(rows) {
  const map = new Map();
  rows.forEach((row) => {
    const info = repairInfo(row);
    if (!info) return;
    const rec = map.get(info.repairId) || emptyRepair(info.repairId, info.repairName, info.state);
    if (!rec.state && info.state) rec.state = info.state;
    if (!rec.repairName && info.repairName) rec.repairName = info.repairName;
    const created = repairDecisionDateKey(row);
    const cost = openPoRepairCostFromTicket(row);
    const hasCost = cost > 0;
    const chassis = chassisFromRow(row);
    const type = clean(row["Ticket Type"] || row.TicketTypeText || row.TicketType || "Unknown") || "Unknown";

    rec.totalCost += cost;
    if (hasCost) {
      rec.ticketCount += 1;
      rec.openCases += 1;
      rec.costByType[type] = (rec.costByType[type] || 0) + cost;
      if (cost < 500) rec.costRanges.low += 1;
      else if (cost < 2000) rec.costRanges.medium += 1;
      else rec.costRanges.high += 1;
    }
    if (hasCost && chassis) {
      rec.chassisTicketCount += 1;
      const c = rec._chassisRecords.get(chassis) || { count: 0, costAmount: 0 };
      c.count += 1;
      c.costAmount += cost;
      rec._chassisRecords.set(chassis, c);
    }
    const dealerName = clean(row["Dealer Name"] || row.DealerName || row.Dealer || "");
    addCounter(rec._dealerCounter, dealerName);
    if (created) {
      if (!rec.first_created_on || created < rec.first_created_on) rec.first_created_on = created;
      if (!rec.last_created_on || created > rec.last_created_on) rec.last_created_on = created;
    }
    map.set(info.repairId, rec);
  });
  return [...map.values()].map((rec) => {
    const repeatChassisRecords = [...rec._chassisRecords.values()].filter((item) => Number(item.count || 0) >= 2);
    rec.uniqueChassisCount = rec._chassisRecords.size;
    rec.repeatChassisOver2Count = repeatChassisRecords.length;
    rec.repeatedClaimTicketCount = repeatChassisRecords.reduce((sum, item) => sum + Number(item.count || 0), 0);
    rec.repeatedClaimAmount = round(repeatChassisRecords.reduce((sum, item) => sum + Number(item.costAmount || 0), 0));
    rec.avgRepeatedClaimAmount = rec.repeatedClaimTicketCount ? round(rec.repeatedClaimAmount / rec.repeatedClaimTicketCount) : 0;
    rec.avgCost = rec.ticketCount ? round(rec.totalCost / rec.ticketCount) : 0;
    rec.totalCost = round(rec.totalCost);
    rec.chassisTicketRatio = rec.ticketCount ? round((rec.chassisTicketCount / rec.ticketCount) * 100, 4) : 0;
    rec.repeatChassisOver2Ratio = rec.uniqueChassisCount ? round((rec.repeatChassisOver2Count / rec.uniqueChassisCount) * 100, 4) : 0;
    rec.repeatRate = rec.chassisTicketCount ? round((rec.repeatedClaimTicketCount / rec.chassisTicketCount) * 100, 4) : 0;
    rec.top_dealer_name = topCounterValue(rec._dealerCounter);
    rec.top_address_group = rec.top_dealer_name;
    delete rec._chassisRecords;
    delete rec._dealerCounter;
    return rec;
  }).filter((row) => Number(row.ticketCount || 0) > 0 || Number(row.repeatedClaimTicketCount || 0) > 0)
    .sort((a, b) => b.repeatChassisOver2Count - a.repeatChassisOver2Count || b.repeatedClaimAmount - a.repeatedClaimAmount || b.ticketCount - a.ticketCount || a.repairName.localeCompare(b.repairName));
}

function analyzeStateRows(rows) {
  const map = new Map();
  rows.forEach((row) => {
    const state = stateAbbr(row.state || row.State);
    if (!state) return;
    const info = repairInfo(row);
    if (!info) return;
    const cost = openPoRepairCostFromTicket(row);
    if (cost <= 0) return;
    const rec = map.get(state) || {
      state,
      ticket_count: 0,
      confirmed_cost: 0,
      invoiced_tickets: 0,
      open_tickets: 0,
      snowy_ticket_count: 0,
      snowy_confirmed_cost: 0,
      _repairers: new Set(),
      _snowyRepairers: new Set(),
    };
    rec.ticket_count += 1;
    rec.confirmed_cost += cost;
    rec.open_tickets += 1;
    rec._repairers.add(info.repairId);
    if (isSnowyRiverTicket(row)) {
      rec.snowy_ticket_count += 1;
      rec.snowy_confirmed_cost += cost;
      rec._snowyRepairers.add(info.repairId);
    }
    map.set(state, rec);
  });
  return [...map.values()].map((rec) => {
    rec.confirmed_cost = round(rec.confirmed_cost);
    rec.avg_confirmed_cost = rec.ticket_count ? round(rec.confirmed_cost / rec.ticket_count) : 0;
    rec.snowy_confirmed_cost = round(rec.snowy_confirmed_cost);
    rec.snowy_avg_confirmed_cost = rec.snowy_ticket_count ? round(rec.snowy_confirmed_cost / rec.snowy_ticket_count) : 0;
    rec.unique_repairers = rec._repairers.size;
    rec.snowy_unique_repairers = rec._snowyRepairers.size;
    delete rec._repairers;
    delete rec._snowyRepairers;
    return rec;
  }).sort((a, b) => b.confirmed_cost - a.confirmed_cost || b.ticket_count - a.ticket_count || a.state.localeCompare(b.state));
}

function chassisRepeatCostBucket(cost, buckets) {
  return buckets.find((bucket) => cost >= bucket.min && cost < bucket.max) || null;
}

function computeChassisDistribution(rows) {
  const chassisMap = new Map();
  rows.forEach((row) => {
    const created = repairDecisionDateKey(row);
    if (!created) return;
    const cost = openPoRepairCostFromTicket(row);
    if (cost <= 0) return;
    const chassis = chassisFromRow(row);
    if (!chassis) return;
    const info = repairInfo(row);
    if (!info) return;
    const rec = chassisMap.get(chassis) || { chassis, count: 0, tickets: [] };
    rec.count += 1;
    rec.tickets.push({
      cost,
      created,
      repairId: info.repairId,
      repairName: info.repairName,
    });
    chassisMap.set(chassis, rec);
  });
  const repeatBuckets = [
    { key: "unique", label: "Unique (1 claim)", min: 1, max: 2 },
    { key: "repeat23", label: "2 - 3 repeats", min: 2, max: 4 },
    { key: "repeat4", label: "4+ repeats", min: 4, max: Infinity },
  ];
  const costBuckets = [
    { key: "low", label: "$0.01 - $500", min: 0, max: 500 },
    { key: "mid", label: "$500 - $2k", min: 500, max: 2000 },
    { key: "high", label: "$2k - $5k", min: 2000, max: 5000 },
    { key: "prem", label: "$5k+", min: 5000, max: Infinity },
  ];
  const distribution = repeatBuckets.map((rb) => ({
    key: rb.key,
    label: rb.label,
    cells: Object.fromEntries(costBuckets.map((cb) => [cb.key, 0])),
    total: 0,
    costTotal: 0,
    chassisCount: 0,
  }));
  const totals = { unique: 0, repeat23: 0, repeat4: 0, ticketsMatched: 0, costTicketsMatched: 0 };
  chassisMap.forEach((rec) => {
    const rb = repeatBuckets.find((bucket) => rec.count >= bucket.min && rec.count < bucket.max);
    if (!rb) return;
    const row = distribution.find((item) => item.key === rb.key);
    totals[rb.key] += 1;
    totals.ticketsMatched += rec.count;
    row.chassisCount += 1;
    rec.tickets.forEach((ticket) => {
      const cost = Number(ticket.cost || 0);
      if (cost <= 0) return;
      const cb = chassisRepeatCostBucket(cost, costBuckets);
      if (!cb) return;
      row.cells[cb.key] += 1;
      row.total += 1;
      row.costTotal += cost;
      totals.costTicketsMatched += 1;
    });
  });
  distribution.forEach((row) => {
    row.costTotal = round(row.costTotal);
  });
  return {
    distribution,
    costBuckets,
    totals,
    uniqueChassis: chassisMap.size,
    bucketDetails: {},
    repeatLabels: Object.fromEntries(repeatBuckets.map((rb) => [rb.key, rb.label])),
    costLabels: Object.fromEntries(costBuckets.map((cb) => [cb.key, cb.label])),
    chassisRows: [],
    detailMode: "lazy",
  };
}

function costRangesFromRepairers(repairers) {
  return (repairers || []).reduce((acc, row) => {
    acc.low += Number(row.costRanges?.low || 0);
    acc.medium += Number(row.costRanges?.medium || 0);
    acc.high += Number(row.costRanges?.high || 0);
    return acc;
  }, { low: 0, medium: 0, high: 0 });
}

function periodRows(allRows, period, basePeriod) {
  const rows = allRows.filter((row) => repairDecisionDateKey(row).startsWith(String(REPAIR_YEAR)));
  if (period === "total") {
    const end = clean(basePeriod?.end) || rows.map(repairDecisionDateKey).filter(Boolean).sort().slice(-1)[0] || `${REPAIR_YEAR}-12-31`;
    return rows.filter((row) => {
      const d = repairDecisionDateKey(row);
      return d && d >= `${REPAIR_YEAR}-01-01` && d <= end;
    });
  }
  if (!/^\d{4}-\d{2}$/.test(period)) return [];
  const start = clean(basePeriod?.start) || `${period}-01`;
  const end = clean(basePeriod?.end) || monthEnd(period);
  return rows.filter((row) => {
    const d = repairDecisionDateKey(row);
    return d && d >= start && d <= end;
  });
}

function periodBounds(rows, period, basePeriod) {
  if (period !== "total" && /^\d{4}-\d{2}$/.test(period)) {
    return {
      start: clean(basePeriod?.start) || `${period}-01`,
      end: clean(basePeriod?.end) || rows.map(repairDecisionDateKey).filter(Boolean).sort().slice(-1)[0] || monthEnd(period),
    };
  }
  return {
    start: clean(basePeriod?.start) || `${REPAIR_YEAR}-01-01`,
    end: clean(basePeriod?.end) || rows.map(repairDecisionDateKey).filter(Boolean).sort().slice(-1)[0] || `${REPAIR_YEAR}-12-31`,
  };
}

function buildPeriod(allRows, period, basePeriod) {
  const rows = periodRows(allRows, period, basePeriod);
  const repairers = analyzeRepair(rows);
  const states = analyzeStateRows(rows);
  const costRanges = costRangesFromRepairers(repairers);
  const totalCost = round(repairers.reduce((sum, row) => sum + Number(row.totalCost || 0), 0));
  const costTickets = repairers.reduce((sum, row) => sum + Number(row.ticketCount || 0), 0);
  const invoicedTickets = rows.filter(isClosedCase).length;
  const openTickets = rows.length - invoicedTickets;
  return {
    repairers,
    states,
    summary: {
      total_tickets: costTickets,
      invoiced_tickets: invoicedTickets,
      open_tickets: openTickets,
      confirmed_cost: totalCost,
      avg_confirmed_cost: costTickets ? totalCost / costTickets : 0,
      unique_repairers: repairers.length,
      detail_rows: rows.length,
    },
    costRanges,
    chassis: computeChassisDistribution(rows),
    ...periodBounds(rows, period, basePeriod),
  };
}

function defaultPeriodKeys(baseFast, allRows) {
  const keys = Object.keys(baseFast?.periods || {});
  if (keys.length) return keys;
  const months = [...new Set(allRows.map((row) => repairDecisionDateKey(row).slice(0, 7)).filter((m) => m.startsWith(String(REPAIR_YEAR))))].sort();
  return ["total", ...months];
}

function main() {
  const baseFast = readJson(FAST_JSON, {});
  const { payload: detailPayload, filePath: detailSourcePath } = readDetailPayload();
  if (!detailPayload || !Array.isArray(detailPayload.details)) {
    throw new Error(`No repair detail payload found at ${LIGHT_JSON} or ${DATA_JSON}`);
  }
  const sourceMap = buildSourceEnrichment();
  const details = enrichDetailRows(detailPayload.details, sourceMap);
  writeLightPayload(detailPayload, details);
  const periods = {};
  for (const key of defaultPeriodKeys(baseFast, details)) {
    periods[key] = buildPeriod(details, key, baseFast?.periods?.[key] || {});
  }
  const total = periods.total || buildPeriod(details, "total", baseFast?.periods?.total || {});
  const output = {
    ...baseFast,
    meta: {
      ...(detailPayload.meta || {}),
      ...(baseFast.meta || {}),
      cache_schema: "repairers-fast-approved-decision-cost-chassis-v3",
      cache_generated_at: new Date().toISOString(),
      cache_source: path.relative(ROOT, detailSourcePath).replace(/\\/g, "/"),
      cache_light_refreshed_at: new Date().toISOString(),
      cache_ticket_base_enrichment_rows: sourceMap.size,
      cost_rule: "approved decision-period tickets with approved repair cost > 0; includes invoiced and approved closed; excludes unapproved",
      repeated_chassis_rule: "repeat buckets and repair-shop repeated chassis use approved decision-period cost tickets; repeated means 2+ eligible tickets",
      chassis_rule: "Serial ID -> Chassis Number -> Vehicle Dispatch Serial -> Registered Product -> Product -> Ticket text token",
    },
    weekly: Array.isArray(baseFast.weekly) ? baseFast.weekly : (Array.isArray(detailPayload.weekly) ? detailPayload.weekly : []),
    periods,
    summary: total.summary,
    repairers: total.repairers,
    states: total.states,
  };
  fs.writeFileSync(FAST_JSON, JSON.stringify(output), "utf8");
  console.log(JSON.stringify({
    output: path.relative(ROOT, FAST_JSON),
    bytes: fs.statSync(FAST_JSON).size,
    periods: Object.keys(periods),
    totalTicketsWithApprovedCost: total.summary.total_tickets,
    totalApprovedCost: round(total.summary.confirmed_cost),
    uniqueChassis: total.chassis.uniqueChassis,
    repairers: total.repairers.length,
  }, null, 2));
}

main();
