import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = path.join(ROOT, "outputs");

const MODEL_ANALYSIS_YEAR = 2026;
const CUTOFF = new Date(2025, 0, 1);
const APPROVED_COST_SANITY_MIN_AUD = Number(process.env.APPROVED_COST_SANITY_MIN_AUD || "50000");
const APPROVED_COST_SANITY_RATIO = Number(process.env.APPROVED_COST_SANITY_RATIO || "10");
const OTHERS_SERIES = "Others";
const SERIES_ORDER = ["SRC", "SRH", "SRT", "SRM", "SRP", "SRL", "SRV", "SRS", "NG", OTHERS_SERIES];
const TRACKED_SERIES = new Set(SERIES_ORDER);
const EXCLUDED_SERIES = new Set(["UNKNOWN", "RO", "SR", "SCR", "STR", "RVV", "RR", "SPV", "SRO", "SEV", "RRC", "VRV"]);
const MODEL_SERIES_PREFIXES = [
  "SRC", "SRH", "SRT", "SRM", "SRP", "SRL", "SRV", "SRS",
  "NGB", "NG",
  "LRV", "LRT", "LRH", "LRM", "LRP", "LRL", "LRS", "LRC", "LTR", "LVR", "LPV", "LEP",
  "RRV", "RV",
];

const paths = {
  ticketTimingCsv: path.join(OUTPUT_DIR, "analysis_ticket_failure_timing.csv"),
  ticketBaseCsv: path.join(OUTPUT_DIR, "analysis_ticket_base.csv"),
  repairersJson: path.join(OUTPUT_DIR, "repairers_2026", "repairers_2026_data.json"),
  partsCostJson: path.join(OUTPUT_DIR, "analysis_parts_ticket_cost_map.json"),
  approvedCostJson: path.join(OUTPUT_DIR, "analysis_approved_cost_by_ticket.json"),
  vehicleBaseJson: path.join(OUTPUT_DIR, "analysis_vehicle_base_summary.json"),
  outJson: path.join(OUTPUT_DIR, "analysis_model_mtm_cache.json"),
  outJs: path.join(OUTPUT_DIR, "analysis_model_mtm_cache.js"),
};

const FIREBASE_DB_URL = (process.env.FIREBASE_DB_URL || "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app").replace(/\/+$/, "");
const FIREBASE_ROOT = process.env.FIREBASE_ROOT || "c4cTickets_test";
const MONITOR_ROOT = process.env.MONITOR_ROOT || "ctmTicketStatusMonitorV44";

const state = {
  approvedCostByTicketId: {},
  rejectionByTicketId: {},
  chassisPgiMap: {},
  salesOrderPgiMap: {},
  chassisSeriesMap: {},
  salesOrderSeriesMap: {},
  salesBaseBySeries: {},
  deliveredBaseBySeries: {},
  vehicleBaseSummary: null,
  autoBaseBySeries: {},
};

function clean(value) {
  return value == null ? "" : String(value).trim();
}

function asNumber(value) {
  const text = clean(value).replace(/,/g, "");
  const n = Number(text);
  return Number.isFinite(n) ? n : 0;
}

function parseDmy(value) {
  const text = clean(value);
  if (!text || text === "#") return null;
  let m = text.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
  if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
  m = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
  m = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  return null;
}

function dateKeyFromDate(d) {
  if (!d) return "";
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function monthKeyFromDate(d) {
  if (!d) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function median(values) {
  const nums = values.filter(Number.isFinite).slice().sort((a, b) => a - b);
  if (!nums.length) return null;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
}

function round(value, digits = 6) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const factor = 10 ** digits;
  return Math.round(n * factor) / factor;
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
    let numericTicketId = "";
    headers.forEach((header, index) => {
      const value = clean(row[index]);
      if (header) {
        obj[header] = value;
        return;
      }
      const prev = clean(headers[index - 1]);
      const next = clean(headers[index + 1]);
      if (!numericTicketId && /^\d+$/.test(value) && (prev === "Ticket" || prev === "Ticket ID" || next === "Ticket ID")) {
        numericTicketId = value;
      }
    });
    if (numericTicketId) {
      const currentTicketId = clean(obj["Ticket ID"]);
      if (!/^\d+$/.test(currentTicketId) || currentTicketId === clean(obj.Ticket)) {
        obj["Ticket ID"] = numericTicketId;
        obj.TicketID = numericTicketId;
        obj.TicketId = numericTicketId;
      }
    }
    return obj;
  });
}

function readJson(filePath, fallback = null) {
  if (!fs.existsSync(filePath)) return fallback;
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function readCsvObjects(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return rowsToObjects(parseCSV(fs.readFileSync(filePath, "utf8")));
}

function writeJsGlobal(filePath, globalName, payload) {
  const text = `globalThis.${globalName} = ${JSON.stringify(payload)};\n`;
  fs.writeFileSync(filePath, text, "utf8");
}

function partsCostKey(value) {
  return clean(value).replace(/\s+/g, "").toUpperCase();
}

function vehicleLookupKey(value) {
  return clean(value).replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}

function normalizeSeriesCode(code) {
  const raw = clean(code).toUpperCase();
  if (!raw) return "UNKNOWN";
  if (raw === "OTHERS" || raw === "OTHER") return OTHERS_SERIES;
  if (raw.startsWith("NG")) return "NG";
  if (raw === "RRV" || raw.startsWith("RRV")) return "SRL";
  if (raw === "LRV" || raw.startsWith("LRV")) return "SRC";
  if (raw.startsWith("L")) return `S${raw.slice(1)}`;
  return raw;
}

function isExcludedSeries(series) {
  return EXCLUDED_SERIES.has(normalizeSeriesCode(series));
}

function isTrackedSeries(series) {
  return TRACKED_SERIES.has(normalizeSeriesCode(series));
}

function isDisplaySeries(series) {
  return normalizeSeriesCode(series) !== OTHERS_SERIES;
}

function seriesCodeFromVehicleToken(token) {
  const key = vehicleLookupKey(token);
  if (!key) return "";
  const prefix = MODEL_SERIES_PREFIXES.find((code) => key.startsWith(code));
  return prefix ? normalizeSeriesCode(prefix) : "";
}

function seriesCodeFromText(value) {
  const text = clean(value).toUpperCase();
  if (!text || text === "#") return "";
  const tokens = text.match(/\b[A-Z]{2,4}[A-Z0-9-]*\d[A-Z0-9-]*\b/g) || [];
  for (const token of tokens) {
    const series = seriesCodeFromVehicleToken(token);
    if (series && !isExcludedSeries(series)) return series;
  }
  return "";
}

function vehicleTokenFromText(value) {
  const text = clean(value).toUpperCase();
  if (!text || text === "#") return "";
  const tokens = text.match(/\b[A-Z]{2,4}[A-Z0-9-]*\d[A-Z0-9-]*\b/g) || [];
  for (const token of tokens) {
    if (seriesCodeFromVehicleToken(token)) return vehicleLookupKey(token);
  }
  return "";
}

function ticketSeriesCandidateSources(row) {
  return [
    { value: row["Ticket Serial ID"] || row.ticketSerialId },
    { value: row["Ticket Chassis Number"] || row.ticketChassisNumber },
    { value: row["Vehicle Dispatch Serial"] || row.VehicleDispatchSerial || row.vehicleDispatchSerial },
    { value: row["Chassis Number"] || row.ChassisNumber || row.ChassisID || row.chassisNumber },
    { value: row["Serial ID"] || row.SerialID || row.SerialId || row.serialId },
    { value: row["Ticket Name"] || row.TicketName || row.ticketName },
    { value: row.Ticket || row["Ticket"] },
    { value: row["Ticket ID"] || row.TicketID || row.TicketId },
    { value: row["Registered Product"] || row.RegisteredProduct || row.registeredProduct },
    { value: row.Product || row["Product"] || row.product },
  ];
}

function ticketSeriesCandidateValues(row) {
  return ticketSeriesCandidateSources(row).map((item) => clean(item.value)).filter((v) => v && v !== "#");
}

function ticketVehicleIdentifierValues(row) {
  return [
    row["Ticket Chassis Number"], row.ticketChassisNumber,
    row["Chassis Number"], row.ChassisNumber, row.ChassisID, row.chassisNumber,
    row["Vehicle Dispatch Serial"], row.VehicleDispatchSerial, row.vehicleDispatchSerial,
    row["Ticket Serial ID"], row.ticketSerialId,
    row["Serial ID"], row.SerialID, row.SerialId, row.serialId,
  ].map(clean).filter((v) => v && v !== "#");
}

function ticketNameVehicleValues(row) {
  return [
    row["Ticket Name"], row.TicketName, row.ticketName,
    row.Ticket, row["Ticket"],
  ].map(clean).filter((v) => v && v !== "#");
}

function normalizeVehicleIdentifierValue(value, allowFullValue = true) {
  const token = vehicleTokenFromText(value);
  if (token) return token;
  if (!allowFullValue) return "";
  const key = vehicleLookupKey(value);
  if (!key || key.length < 5) return "";
  if (/^\d+$/.test(key)) return "";
  if (key === "NOTASSIGNED" || key === "UNKNOWN" || key === "NIL" || key === "NA") return "";
  if (/^800000\d{2,}$/.test(key)) return "";
  return key;
}

function normalizeExplicitModelChassisValue(value) {
  const token = vehicleTokenFromText(value);
  if (token) return token;
  const key = vehicleLookupKey(value);
  if (!key || key.length < 2) return "";
  if (key === "NOTASSIGNED" || key === "UNKNOWN" || key === "NIL" || key === "NA") return "";
  if (/^800000\d{2,}$/.test(key)) return "";
  return key;
}

function rawTicketIdValue(row) {
  return clean(row["Ticket ID"] || row.TicketID || row.TicketId || row.ticketId || row.id);
}

function numericTicketIdValue(row) {
  const raw = rawTicketIdValue(row);
  return /^\d+$/.test(raw) ? raw : "";
}

function matchedVehicleKeyValue(row) {
  return clean(row["Matched Vehicle Key"] || row.MatchedVehicleKey || row.matchedVehicleKey);
}

function matchedSerialValue(row) {
  return clean(row["Matched Serial"] || row.MatchedSerial || row.matchedSerial);
}

function matchedChassisValue(row) {
  return clean(row["Matched Chassis"] || row.MatchedChassis || row.matchedChassis);
}

function matchedSalesOrderValue(row) {
  return clean(row["Matched Sales Order"] || row.MatchedSalesOrder || row.matchedSalesOrder);
}

function pgiDateValue(row) {
  return clean(row["PGI Date"] || row.PGIDate || row.pgiDate || row["Vehicle Delivery Date"] || row.vehicleDeliveryDate);
}

function normalizedChassis(row) {
  const explicit = normalizeExplicitModelChassisValue(row.ModelChassis || row.modelChassis || row["Model Chassis"] || row.modelChassisOverride);
  if (explicit) return explicit;
  for (const value of ticketVehicleIdentifierValues(row)) {
    const key = normalizeVehicleIdentifierValue(value, true);
    if (key) return key;
  }
  for (const value of ticketNameVehicleValues(row)) {
    const key = normalizeVehicleIdentifierValue(value, false);
    if (key) return key;
  }
  for (const value of [matchedChassisValue(row), matchedSerialValue(row), matchedVehicleKeyValue(row)]) {
    const key = normalizeVehicleIdentifierValue(value, true);
    if (key) return key;
  }
  return "";
}

function vehicleSeriesLookupKeys(row) {
  const raw = [
    row["Chassis Number"], row.ChassisNumber, row.chassisNumber,
    row["Ticket Chassis Number"], row.ticketChassisNumber,
    row["Ticket Serial ID"], row.ticketSerialId,
    row["Serial ID"], row.SerialID, row.SerialId, row.serialId,
    row["Vehicle Dispatch Serial"], row.VehicleDispatchSerial, row.vehicleDispatchSerial,
    normalizedChassis(row),
    row["Matched Chassis"], row.MatchedChassis, row.matchedChassis,
    row["Matched Serial"], row.MatchedSerial, row.matchedSerial,
  ].map(clean).filter(Boolean);
  return Array.from(new Set(raw.flatMap((v) => {
    const canonical = vehicleLookupKey(v);
    return canonical && canonical !== v ? [v, canonical] : [v];
  })));
}

function salesOrderLookupKeys(row) {
  const raw = [
    row["Matched Sales Order"], row.MatchedSalesOrder, row.matchedSalesOrder,
    row["Sales Order"], row.SalesOrder, row.salesOrder,
    row["Ticket Sales Order"], row.ticketSalesOrder,
    row.LookupSalesOrder, row.lookupSalesOrder,
    row["Vehicle Dispatch Sales Order"], row.VehicleDispatchSalesOrder, row.vehicleDispatchSalesOrder,
  ].map(clean).filter(Boolean);
  return Array.from(new Set(raw.flatMap((v) => {
    const canonical = vehicleLookupKey(v);
    return canonical && canonical !== v ? [v, canonical] : [v];
  })));
}

function mappedSeriesForRow(row) {
  for (const key of vehicleSeriesLookupKeys(row)) {
    if (Object.prototype.hasOwnProperty.call(state.chassisSeriesMap, key)) {
      return normalizeSeriesCode(state.chassisSeriesMap[key]);
    }
  }
  for (const key of salesOrderLookupKeys(row)) {
    if (Object.prototype.hasOwnProperty.call(state.salesOrderSeriesMap, key)) {
      return normalizeSeriesCode(state.salesOrderSeriesMap[key]);
    }
  }
  return "";
}

function extractSeries(row) {
  const explicit = normalizeSeriesCode(row.ModelSeries || row.modelSeries || row["Model Series"] || row.modelSeriesOverride);
  if (explicit && explicit !== "UNKNOWN" && !isExcludedSeries(explicit)) return explicit;
  for (const value of ticketSeriesCandidateValues(row)) {
    const series = seriesCodeFromText(value);
    if (series && !isExcludedSeries(series)) return series;
  }
  const mapped = mappedSeriesForRow(row);
  if (mapped && !isExcludedSeries(mapped)) return mapped;
  for (const value of [matchedChassisValue(row), matchedSerialValue(row), matchedVehicleKeyValue(row)]) {
    const series = seriesCodeFromText(value);
    if (series && !isExcludedSeries(series)) return series;
  }
  return "UNKNOWN";
}

function rowCreatedDate(row) {
  return parseDmy(
    row["Created On"] || row.CreatedOn || row.createdOn ||
    row["Created On ISO"] || row.CreatedOnISO || row.createdOnISO ||
    row["Ticket Created On"] || row.TicketCreatedOn || row.ticketCreatedOn
  );
}

function isCreatedInModelYear(row) {
  const created = rowCreatedDate(row);
  return !!(created && created.getFullYear() === MODEL_ANALYSIS_YEAR);
}

function modelMonthKey(row) {
  const explicit = clean(row.ModelPeriodMonth || row.modelPeriodMonth || row["Model Period Month"]);
  if (/^\d{4}-\d{2}$/.test(explicit)) return explicit;
  const created = rowCreatedDate(row);
  return created && created.getFullYear() === MODEL_ANALYSIS_YEAR ? monthKeyFromDate(created) : "";
}

function ticketStatusText(row) {
  return Array.from(new Set([
    row.Status, row["Status"],
    row.TicketStatus, row.ticketStatus, row["Ticket Status"],
    row.TicketStatusText, row.ticketStatusText, row["Ticket Status Text"],
  ].map(clean).filter(Boolean))).join(" ");
}

function ticketApprovedMarker(row) {
  const marker = clean(
    row["Claim Approved On"] || row.ClaimApprovedOn || row.claimApprovedOn ||
    row["Claim Approved On Date"] || row.ClaimApprovedOnDate ||
    row.ClaimApprovedOnDateTime || row.claimApprovedOnDateTime ||
    row["Approved On"] || row.ApprovedOn || row.approvedOn ||
    row["Approval Number"] || row.ApprovalNumber || row.approvalNumber
  );
  return marker && marker !== "#" ? marker : "";
}

function isTicketApprovedFlow(row) {
  const status = ticketStatusText(row).toLowerCase();
  if (status.includes("unapproved")) return false;
  if (ticketApprovedMarker(row)) return true;
  return (
    status.includes("approved") ||
    status.includes("repairer invoiced") ||
    status.includes("repair in progress") ||
    status.includes("partially picked") ||
    status.includes("picked") ||
    status.includes("dispatch parts") ||
    status.includes("reimbursement required")
  );
}

function claimScope(row) {
  const text = [
    row["Claim Scope"], row.ClaimScope, row.claimScope,
    row.claimType, row.ClaimType, row["Claim Type"],
    row["Ticket Type"], row.TicketType, row.TicketTypeText,
    row["Ticket Type Text"], row.WarrantyClaimType, row["Warranty Claim Type"],
  ].map(clean).join(" ").toLowerCase();
  if (text.includes("pre delivery") || text.includes("pre-delivery") || text.includes("predelivery") || text.includes("pdi")) return "PRE";
  if (text.includes("in field") || text.includes("in-field") || text.includes("infield") || text.includes("field warranty")) return "FIELD";
  return "OTHER";
}

function rowMatchesScope(row, scope) {
  const series = extractSeries(row);
  if (isExcludedSeries(series) || !isTrackedSeries(series)) return false;
  if (scope === "PRE") return claimScope(row) === "PRE";
  if (scope === "FIELD") return claimScope(row) === "FIELD";
  return true;
}

function isModelAnalysisTicket(row) {
  if (!numericTicketIdValue(row)) return false;
  if (!isCreatedInModelYear(row)) return false;
  if (!isTicketApprovedFlow(row)) return false;
  if (!normalizedChassis(row)) return false;
  return rowMatchesScope(row, "ALL");
}

function ticketIdentity(row) {
  const rawTicketId = rawTicketIdValue(row);
  const numericTicketId = numericTicketIdValue(row);
  if (numericTicketId) return `id:${numericTicketId}`;
  const createdOn = clean(row["Created On"] || row.CreatedOn || row["Posting Date"] || row.PostingDate);
  const salesOrder = clean(row["Sales Order"] || row.SalesOrder || row.salesOrder || row["Ticket Sales Order"] || row.ticketSalesOrder || row["Matched Sales Order"] || row.matchedSalesOrder || row["Vehicle Dispatch Sales Order"] || row.vehicleDispatchSalesOrder);
  const chassis = normalizedChassis(row);
  const serialId = clean(row["Serial ID"] || row.SerialID || row.SerialId || row.serialId || row["Ticket Serial ID"] || row.ticketSerialId || row["Matched Serial"] || row.matchedSerial);
  const dealer = clean(row["Dealer Name"] || row.DealerName || row.dealerName);
  const repairer = clean(row["Repair Shop"] || row["Repair Shop Name"] || row["Service Technician"]);
  const ticketType = clean(row["Ticket Type"] || row.TicketType || row["Claim Type"]);
  const fallback = [rawTicketId, salesOrder, chassis, serialId, createdOn, ticketType, dealer, repairer].filter(Boolean).join("|");
  return fallback ? `sig:${fallback}` : "";
}

function uniqueTicketCount(rows) {
  const seen = new Set();
  for (const row of rows) {
    const key = ticketIdentity(row);
    if (key) seen.add(key);
  }
  return seen.size;
}

function partsCostLookupKeys(row) {
  return [
    row["Ticket ID"], row.TicketID, row.Ticket, row["Ticket"], row.TicketId,
    row["Sales Order"], row.SalesOrder, row.salesOrder, row.LookupSalesOrder, row.lookupSalesOrder,
    row["Serial ID"], row.SerialID, row.SerialId, row.serialId,
    row["Chassis Number"], row.ChassisNumber, row.ChassisID, row.chassisNumber,
    normalizedChassis(row),
  ].map(partsCostKey).filter(Boolean);
}

function currentClaimAmount(row) {
  const direct = [
    row?.ClaimTotalAmount,
    row?.["ClaimTotalAmount"],
    row?.["Claim Total Amount"],
    row?.claim_total_amount,
    row?.claimTotalAmount,
    row?.claim_total_amount_aud,
    row?.AmountIncludingTax,
    row?.["Amount Including Tax"],
    row?.rawTicketAmount,
  ];
  for (const value of direct) {
    const amount = asNumber(value);
    if (amount > 0) return amount;
  }
  const parts = [
    row?.["Factory Parts Claim Total Amount"],
    row?.FactoryPartsClaimTotalAmount,
    row?.LabourHoursTotalAmount,
    row?.["Repairer Parts Claim Total Amount"],
    row?.RepairerPartsClaimTotalAmount,
  ].reduce((sum, value) => sum + asNumber(value), 0);
  return parts > 0 ? parts : 0;
}

function approvedCostAfterSanityCheck(row, cost) {
  const amount = Number(cost);
  if (!Number.isFinite(amount) || amount <= 0) return 0;
  const claim = currentClaimAmount(row);
  if (amount >= APPROVED_COST_SANITY_MIN_AUD && claim > 0 && amount / claim >= APPROVED_COST_SANITY_RATIO) {
    return claim;
  }
  return amount;
}

function getApprovedWarrantyCost(row) {
  const explicitCost = row.ModelApprovedCost ?? row.modelApprovedCost ?? row["Model Approved Cost"] ?? row.page1Amount ?? row["Page1 Amount"];
  if (explicitCost !== undefined && explicitCost !== null && clean(explicitCost) !== "") {
    return approvedCostAfterSanityCheck(row, asNumber(explicitCost));
  }
  for (const key of partsCostLookupKeys(row)) {
    if (Object.prototype.hasOwnProperty.call(state.approvedCostByTicketId, key)) {
      const cost = Number(state.approvedCostByTicketId[key]);
      return approvedCostAfterSanityCheck(row, cost);
    }
  }
  return 0;
}

function isRejectedStatus(text) {
  return clean(text).toLowerCase().includes("rejected");
}

function isFullyRejected(text) {
  return clean(text).toLowerCase() === "fully rejected";
}

function isPartiallyRejected(text) {
  return clean(text).toLowerCase() === "partially rejected";
}

function getRejection(row) {
  const csvRej = clean(row["Order Rejection Status"]);
  if (csvRej) return csvRej;
  for (const key of partsCostLookupKeys(row)) {
    if (Object.prototype.hasOwnProperty.call(state.rejectionByTicketId, key)) return state.rejectionByTicketId[key];
  }
  return "";
}

function getDeliveryDate(row) {
  const explicitPgi = parseDmy(pgiDateValue(row));
  if (explicitPgi) return { date: explicitPgi, source: "pgi" };
  for (const key of vehicleSeriesLookupKeys(row)) {
    if (Object.prototype.hasOwnProperty.call(state.chassisPgiMap, key)) {
      const d = parseDmy(state.chassisPgiMap[key]);
      if (d) return { date: d, source: "pgi" };
    }
  }
  for (const key of salesOrderLookupKeys(row)) {
    if (Object.prototype.hasOwnProperty.call(state.salesOrderPgiMap, key)) {
      const d = parseDmy(state.salesOrderPgiMap[key]);
      if (d) return { date: d, source: "pgi" };
    }
  }
  return { date: null, source: "missing" };
}

function failureAgeDays(row) {
  const created = rowCreatedDate(row);
  const delivery = getDeliveryDate(row);
  const age = created && delivery.date ? Math.floor((created - delivery.date) / 86400000) : null;
  return Number.isFinite(age) && age >= 0 ? { age, source: delivery.source } : { age: null, source: delivery.source };
}

function normalizeSeriesCountMap(map) {
  const out = {};
  if (!map || typeof map !== "object") return out;
  for (const [rawSeries, value] of Object.entries(map)) {
    const series = normalizeSeriesCode(rawSeries);
    if (!isTrackedSeries(series) || isExcludedSeries(series)) continue;
    out[series] = (out[series] || 0) + (Number(value) || 0);
  }
  return out;
}

function monthlyBaseMap(monthKey) {
  const payload = state.vehicleBaseSummary || {};
  const sources = [
    payload.seriesSalesCumulativeByMonth,
    payload.seriesBaseCumulativeByMonth,
    payload.cumulativeSalesBySeriesMonth,
    payload.cumulativeBaseBySeriesMonth,
  ].filter((source) => source && typeof source === "object");
  for (const source of sources) {
    const exact = normalizeSeriesCountMap(source[monthKey]);
    if (Object.keys(exact).length) return exact;
    const fallbackMonth = Object.keys(source).filter((key) => key <= monthKey).sort().pop();
    const fallback = normalizeSeriesCountMap(fallbackMonth ? source[fallbackMonth] : null);
    if (Object.keys(fallback).length) return fallback;
  }
  return {};
}

function activeBaseMap(periodKey) {
  if (periodKey && periodKey !== "total") {
    const monthly = monthlyBaseMap(periodKey);
    if (Object.keys(monthly).length) return monthly;
  }
  const sales = normalizeSeriesCountMap(state.salesBaseBySeries);
  if (Object.keys(sales).length) return sales;
  const delivered = normalizeSeriesCountMap(state.deliveredBaseBySeries);
  if (Object.keys(delivered).length) return delivered;
  return {};
}

function effectiveBase(series, activeBase) {
  const key = normalizeSeriesCode(series);
  const base = Number(activeBase?.[key]) || 0;
  if (base > 0) return base;
  return Math.max(0, Number(state.autoBaseBySeries[key]) || 0);
}

function salesRateTotalVehicles(activeBase) {
  return SERIES_ORDER.reduce((sum, series) => sum + effectiveBase(series, activeBase), 0);
}

function traceBaseBySeries(rows) {
  const map = new Map();
  for (const row of rows) {
    const series = extractSeries(row);
    if (isExcludedSeries(series)) continue;
    const key = normalizedChassis(row);
    if (!key) continue;
    if (!map.has(series)) map.set(series, new Set());
    map.get(series).add(key);
  }
  return Object.fromEntries(Array.from(map.entries()).map(([series, set]) => [series, set.size]));
}

function trackedSummarySeriesKeys(activeBase) {
  return Array.from(new Set([
    ...Object.keys(state.autoBaseBySeries || {}),
    ...Object.keys(state.salesBaseBySeries || {}),
    ...Object.keys(state.deliveredBaseBySeries || {}),
    ...Object.keys(activeBase || {}),
  ].map(normalizeSeriesCode).filter(isTrackedSeries).filter((series) => !isExcludedSeries(series))));
}

function createSeriesBucket(series) {
  return {
    series,
    tickets: 0,
    costTickets: 0,
    cost: 0,
    vehicles: new Set(),
    firstFailureByVehicle: new Map(),
    repairedTickets: 0,
    buckets: [0, 0, 0, 0, 0],
    timingSources: { pgi: 0 },
    fullyRej: 0,
    partRej: 0,
    anyRej: 0,
    dealers: new Map(),
  };
}

function compactSummaryValue(row) {
  const out = {};
  for (const [key, value] of Object.entries(row)) {
    out[key] = typeof value === "number" ? round(value, 6) : value;
  }
  return out;
}

function summaryBySeries(rows, activeBase) {
  const map = new Map();
  for (const row of rows) {
    const series = extractSeries(row);
    if (!isTrackedSeries(series)) continue;
    const cost = getApprovedWarrantyCost(row);
    const failureAge = failureAgeDays(row);
    const key = normalizedChassis(row);
    const rej = getRejection(row);
    if (!map.has(series)) map.set(series, createSeriesBucket(series));
    const bucket = map.get(series);
    bucket.tickets += 1;
    bucket.cost += cost;
    if (cost > 0) bucket.costTickets += 1;
    if (key) bucket.vehicles.add(key);
    bucket.repairedTickets += 1;
    if (isFullyRejected(rej)) {
      bucket.fullyRej += 1;
      bucket.anyRej += 1;
    } else if (isPartiallyRejected(rej)) {
      bucket.partRej += 1;
      bucket.anyRej += 1;
    } else if (isRejectedStatus(rej)) {
      bucket.anyRej += 1;
    }
    const dealer = clean(row["Dealer Name"]);
    if (dealer) bucket.dealers.set(dealer, (bucket.dealers.get(dealer) || 0) + 1);
    const age = failureAge.age;
    if (key && Number.isFinite(age) && age >= 0) {
      const prev = bucket.firstFailureByVehicle.get(key);
      if (!Number.isFinite(prev) || age < prev) bucket.firstFailureByVehicle.set(key, age);
    }
  }
  for (const series of trackedSummarySeriesKeys(activeBase)) {
    if (!map.has(series)) map.set(series, createSeriesBucket(series));
  }
  const summary = Array.from(map.values()).map((item) => {
    const vehicles = item.vehicles.size;
    const firstFailureAges = Array.from(item.firstFailureByVehicle.values()).filter(Number.isFinite);
    const buckets = [0, 0, 0, 0, 0];
    for (const age of firstFailureAges) {
      if (age <= 30) buckets[0] += 1;
      else if (age <= 90) buckets[1] += 1;
      else if (age <= 180) buckets[2] += 1;
      else if (age <= 360) buckets[3] += 1;
      else buckets[4] += 1;
    }
    const avgRepairs = vehicles ? (item.repairedTickets || 0) / vehicles : null;
    const costPerVehicle = vehicles ? item.cost / vehicles : null;
    const costPerTicket = item.costTickets ? item.cost / item.costTickets : null;
    const rejectionRate = item.tickets ? item.anyRej / item.tickets : null;
    const top = Array.from(item.dealers.entries()).sort((a, b) => b[1] - a[1])[0];
    return {
      series: item.series,
      tickets: item.tickets,
      costTickets: item.costTickets,
      cost: round(item.cost, 2) || 0,
      vehicles,
      repairedTickets: item.repairedTickets || 0,
      avgRepairs,
      costPerVehicle,
      costPerTicket,
      medianAge: median(firstFailureAges),
      buckets,
      timingSources: { pgi: firstFailureAges.length },
      pgiMatchedVehicles: firstFailureAges.length,
      fullyRej: item.fullyRej,
      partRej: item.partRej,
      anyRej: item.anyRej,
      rejectionRate,
      topDealer: top ? top[0] : "",
    };
  });
  const totalTickets = summary.reduce((sum, item) => sum + (Number(item.tickets) || 0), 0);
  const totalVehicles = summary.reduce((sum, item) => sum + (Number(item.vehicles) || 0), 0);
  const totalSoldVehicles = salesRateTotalVehicles(activeBase);
  return summary.map((item) => {
    const salesBase = effectiveBase(item.series, activeBase);
    const ticketShare = totalTickets ? item.tickets / totalTickets : null;
    const vehicleShare = totalVehicles ? item.vehicles / totalVehicles : null;
    const repairRate = salesBase ? item.vehicles / salesBase : null;
    const salesRate = totalSoldVehicles ? salesBase / totalSoldVehicles : null;
    const salesShare = salesRate;
    const mixGap = Number.isFinite(ticketShare) && Number.isFinite(salesShare) ? ticketShare - salesShare : null;
    const costPerSold = salesBase ? item.cost / salesBase : null;
    return compactSummaryValue({
      ...item,
      sold: salesBase,
      csvBase: salesBase,
      repairRate,
      salesRate,
      salesShare,
      mixGap,
      costPerSold,
      ticketShare,
      vehicleShare,
    });
  });
}

function isUnassignedRepairer(value) {
  const text = clean(value).toLowerCase();
  if (!text) return true;
  return ["not assigned", "unassigned", "notassigned", "#", "-", "n/a", "unknown"].includes(text);
}

function isCustomerLike(value) {
  const text = clean(value).toLowerCase();
  return text.includes("customer") && (text.includes("repair") || text.includes("repairer"));
}

function repairerAggregation(rows) {
  const map = new Map();
  for (const row of rows) {
    const name = clean(row["Repair Shop"] || row["Repair Shop Name"] || row["Service Technician"]);
    if (!name || isUnassignedRepairer(name) || isCustomerLike(name)) continue;
    if (!map.has(name)) map.set(name, { name, tickets: 0, rejected: 0, fullyRej: 0, partRej: 0, cost: 0, series: new Map() });
    const bucket = map.get(name);
    bucket.tickets += 1;
    bucket.cost += getApprovedWarrantyCost(row);
    const rej = getRejection(row);
    if (isFullyRejected(rej)) {
      bucket.fullyRej += 1;
      bucket.rejected += 1;
    } else if (isPartiallyRejected(rej)) {
      bucket.partRej += 1;
      bucket.rejected += 1;
    } else if (isRejectedStatus(rej)) {
      bucket.rejected += 1;
    }
    const series = extractSeries(row);
    bucket.series.set(series, (bucket.series.get(series) || 0) + 1);
  }
  return Array.from(map.values()).map((bucket) => {
    const top = Array.from(bucket.series.entries()).sort((a, b) => b[1] - a[1])[0];
    return {
      name: bucket.name,
      tickets: bucket.tickets,
      rejected: bucket.rejected,
      cost: round(bucket.cost, 2) || 0,
      fullyRej: bucket.fullyRej,
      partRej: bucket.partRej,
      rejectionRate: bucket.tickets ? round(bucket.rejected / bucket.tickets, 6) : 0,
      topSeries: top ? `${top[0]} - ${top[1]}` : "-",
    };
  });
}

function allDetailForRows(rows, summary) {
  const displaySummary = summary.filter((item) => isDisplaySeries(item.series));
  const totalTickets = displaySummary.reduce((sum, item) => sum + (Number(item.tickets) || 0), 0);
  const totalVehicles = displaySummary.reduce((sum, item) => sum + (Number(item.vehicles) || 0), 0);
  const totalCost = displaySummary.reduce((sum, item) => sum + (Number(item.cost) || 0), 0);
  const totalRepairedTickets = displaySummary.reduce((sum, item) => sum + (Number(item.repairedTickets) || 0), 0);
  const totalCostTickets = displaySummary.reduce((sum, item) => sum + (Number(item.costTickets) || 0), 0);
  const buckets = [0, 0, 0, 0, 0];
  const timingSources = { pgi: 0 };
  for (const item of displaySummary) {
    const itemBuckets = Array.isArray(item.buckets) ? item.buckets : [0, 0, 0, 0, 0];
    itemBuckets.forEach((count, index) => {
      buckets[index] += Number(count) || 0;
    });
    timingSources.pgi += Number(item.pgiMatchedVehicles ?? item.timingSources?.pgi) || 0;
  }
  const firstAgeByVehicle = new Map();
  for (const row of rows.filter((item) => isDisplaySeries(extractSeries(item)))) {
    const timing = failureAgeDays(row);
    const key = normalizedChassis(row);
    if (timing.source === "pgi" && key && Number.isFinite(timing.age) && timing.age >= 0) {
      const prev = firstAgeByVehicle.get(key);
      if (!Number.isFinite(prev) || timing.age < prev) firstAgeByVehicle.set(key, timing.age);
    }
  }
  const ages = Array.from(firstAgeByVehicle.values()).filter(Number.isFinite);
  return compactSummaryValue({
    series: "All Series",
    tickets: totalTickets,
    costTickets: totalCostTickets,
    vehicles: totalVehicles,
    cost: round(totalCost, 2) || 0,
    repairedTickets: totalRepairedTickets,
    avgRepairs: totalVehicles ? totalRepairedTickets / totalVehicles : null,
    costPerVehicle: totalVehicles ? totalCost / totalVehicles : null,
    costPerTicket: totalCostTickets ? totalCost / totalCostTickets : null,
    buckets,
    medianAge: median(ages),
    timingSources,
    pgiMatchedVehicles: timingSources.pgi,
  });
}

function compactModelDetailRow(row) {
  const ticketId = rawTicketIdValue(row);
  const amount = round(getApprovedWarrantyCost(row), 2) || 0;
  const timing = failureAgeDays(row);
  const delivery = getDeliveryDate(row);
  const series = extractSeries(row);
  const chassis = normalizedChassis(row);
  const matchSource = sourceValue(row, "Model Match Source", "ModelMatchSource", "modelMatchSource");
  const bucket = sourceValue(row, "Model Bucket", "ModelBucket", "modelBucket") || (series === OTHERS_SERIES ? OTHERS_SERIES : "Model series");
  const othersReason = sourceValue(row, "Model Others Reason", "ModelOthersReason", "modelOthersReason");
  const approvedOn = sourceValue(row, "Claim Approved On", "ClaimApprovedOnDateTime", "ClaimApprovedOn", "Approved On") || ticketApprovedMarker(row);
  return {
    "Ticket ID": ticketId,
    TicketID: ticketId,
    TicketId: ticketId,
    ticketId,
    "Model Series": series,
    ModelSeries: series,
    "Model Chassis": chassis,
    ModelChassis: chassis,
    "Model Match Source": matchSource,
    ModelMatchSource: matchSource,
    "Model Bucket": bucket,
    ModelBucket: bucket,
    "Model Others Reason": othersReason,
    ModelOthersReason: othersReason,
    "Model Approved Cost": amount,
    ModelApprovedCost: amount,
    "Page1 Amount": amount,
    page1Amount: amount,
    page1CostGt0: amount > 0,
    "Page1 Decision Date": sourceValue(row, "Page1 Decision Date"),
    "Page1 Customer": sourceValue(row, "Page1 Customer"),
    "Page1 Employee": sourceValue(row, "Page1 Employee"),
    "Model Period Month": modelMonthKey(row),
    ModelPeriodMonth: modelMonthKey(row),
    "Ticket Name": sourceValue(row, "Ticket Name", "TicketName", "Ticket"),
    TicketName: sourceValue(row, "TicketName", "Ticket Name"),
    "Ticket Serial ID": sourceValue(row, "Ticket Serial ID", "ticketSerialId"),
    "Ticket Chassis Number": sourceValue(row, "Ticket Chassis Number", "ticketChassisNumber"),
    "Serial ID": sourceValue(row, "Serial ID", "SerialID", "SerialId", "serialId"),
    SerialID: sourceValue(row, "SerialID", "Serial ID", "SerialId", "serialId"),
    "Chassis Number": sourceValue(row, "Chassis Number", "ChassisNumber", "ChassisID", "chassisNumber"),
    ChassisNumber: sourceValue(row, "ChassisNumber", "Chassis Number", "ChassisID", "chassisNumber"),
    "Matched Vehicle Key": matchedVehicleKeyValue(row),
    "Matched Serial": matchedSerialValue(row),
    "Matched Chassis": matchedChassisValue(row),
    "Matched Sales Order": matchedSalesOrderValue(row),
    "Match Source": sourceValue(row, "Match Source", "MatchSource", "matchSource"),
    "Registered Product": sourceValue(row, "Registered Product", "RegisteredProduct", "registeredProduct"),
    Product: sourceValue(row, "Product", "product"),
    "Claim Scope": sourceValue(row, "Claim Scope", "ClaimScope", "claimScope"),
    "Ticket Type": sourceValue(row, "Ticket Type", "TicketTypeText", "TicketType", "Claim Type", "ClaimType"),
    TicketTypeText: sourceValue(row, "TicketTypeText", "Ticket Type", "TicketType", "Claim Type", "ClaimType"),
    Status: ticketStatusText(row),
    "Status": ticketStatusText(row),
    TicketStatusText: ticketStatusText(row),
    "Claim Approved On": approvedOn,
    ClaimApprovedOnDateTime: approvedOn,
    "Dealer": sourceValue(row, "Dealer"),
    "Dealer Name": sourceValue(row, "Dealer Name", "DealerName", "dealerName"),
    DealerName: sourceValue(row, "DealerName", "Dealer Name", "dealerName"),
    "Repair Shop": sourceValue(row, "Repair Shop", "Repair Shop Name", "RepairShop", "repairShop"),
    "Service Technician": sourceValue(row, "Service Technician", "ServiceTechnician", "serviceTechnician"),
    "Created On": sourceValue(row, "Created On", "CreatedOn", "createdOn"),
    CreatedOn: sourceValue(row, "CreatedOn", "Created On", "createdOn"),
    "Created On ISO": sourceValue(row, "Created On ISO", "CreatedOnISO", "createdOnISO"),
    "Date of Purchase": sourceValue(row, "Date of Purchase", "DateOfPurchase", "dateOfPurchase"),
    "Posting Date": sourceValue(row, "Posting Date", "PostingDate", "postingDate"),
    "Changed On": sourceValue(row, "Changed On", "ChangedOn", "changedOn"),
    "PGI Date": sourceValue(row, "PGI Date", "PGIDate", "pgiDate"),
    PGIDate: sourceValue(row, "PGIDate", "PGI Date", "pgiDate"),
    "Vehicle Delivery Date": sourceValue(row, "Vehicle Delivery Date", "vehicleDeliveryDate"),
    "Timing Date": delivery.date ? dateKeyFromDate(delivery.date) : "",
    "Timing Date Source": timing.source || "",
    "Failure Days": timing.age ?? "",
    "Ticket Sales Order": sourceValue(row, "Ticket Sales Order", "ticketSalesOrder"),
    "Sales Order": sourceValue(row, "Sales Order", "SalesOrder", "salesOrder"),
    SalesOrder: sourceValue(row, "SalesOrder", "Sales Order", "salesOrder"),
    "Lookup Sales Order": sourceValue(row, "Lookup SalesOrder", "LookupSalesOrder", "lookupSalesOrder"),
    LookupSalesOrder: sourceValue(row, "LookupSalesOrder", "lookupSalesOrder"),
    "ERP Free Order ID": sourceValue(row, "ERP Free Order ID", "ERPFreeOrderID"),
    "ERP Purchase Order ID": sourceValue(row, "ERP Purchase Order ID", "ERPPurchaseOrderID"),
    "ERP Service Order ID": sourceValue(row, "ERP Service Order ID", "ERPServiceOrderID"),
    ApprovedCostMatchKey: "Page1 approved cost",
    "Approved Cost Match Key": "Page1 approved cost",
    HasApprovedCost: amount > 0,
    "Has Approved Cost": amount > 0 ? "Yes" : "No",
    "Repair Rejection": getRejection(row),
    "Order Rejection Status": getRejection(row),
    "Vehicle Dispatch Date": sourceValue(row, "Vehicle Dispatch Date", "VehicleDispatchDate", "vehicleDispatchDate"),
    "Vehicle Dispatch Source": sourceValue(row, "Vehicle Dispatch Source", "VehicleDispatchSource", "vehicleDispatchSource"),
    "Vehicle Dispatch Serial": sourceValue(row, "Vehicle Dispatch Serial", "VehicleDispatchSerial", "vehicleDispatchSerial"),
    "Vehicle Dispatch Sales Order": sourceValue(row, "Vehicle Dispatch Sales Order", "VehicleDispatchSalesOrder", "vehicleDispatchSalesOrder"),
  };
}

function createDispatchRows(partsPayload) {
  const rows = Array.isArray(partsPayload) ? partsPayload : (Array.isArray(partsPayload?.rows) ? partsPayload.rows : []);
  const dispatchRows = [];
  for (const raw of rows) {
    const ticket = raw && raw.ticket && typeof raw.ticket === "object" ? raw.ticket : (raw || {});
    const ticketId = clean(ticket.TicketID || ticket.TicketId || ticket.ticketId || ticket.id || ticket["Ticket ID"] || ticket.ticketId);
    const salesOrder = clean(ticket["Sales Order"] || ticket.SalesOrder || ticket.salesOrder || ticket.LookupSalesOrder);
    const serialId = clean(ticket.SerialID || ticket.SerialId || ticket.serialId);
    const chassis = clean(ticket.ChassisNumber || ticket.ChassisID || ticket.chassisNumber);
    const dispatchSerial = clean(ticket["Vehicle Dispatch Serial"] || ticket.vehicleDispatchSerial || raw.vehicleDispatchSerial || serialId);
    const dispatchSalesOrder = clean(ticket["Vehicle Dispatch Sales Order"] || ticket.vehicleDispatchSalesOrder || raw.vehicleDispatchSalesOrder || salesOrder);
    const rejection = clean(ticket["Order Rejection Status"] || ticket.orderRejectionStatus);
    const dispatchDate = clean(ticket["Vehicle Dispatch Date"] || ticket.vehicleDispatchDate || raw.vehicleDispatchDate);
    const dispatchSource = clean(ticket["Vehicle Dispatch Source"] || ticket.vehicleDispatchSource || raw.vehicleDispatchSource);
    dispatchRows.push({
      "Ticket ID": ticketId,
      TicketID: ticketId,
      "Sales Order": salesOrder,
      "Serial ID": serialId,
      "Chassis Number": chassis,
      "Vehicle Dispatch Date": dispatchDate,
      "Vehicle Dispatch Source": dispatchSource,
      "Vehicle Dispatch Serial": dispatchSerial,
      "Vehicle Dispatch Sales Order": dispatchSalesOrder,
      "Order Rejection Status": rejection,
    });
    [ticketId, salesOrder, serialId, chassis, dispatchSerial, dispatchSalesOrder].map(partsCostKey).filter(Boolean).forEach((key) => {
      if (rejection && !Object.prototype.hasOwnProperty.call(state.rejectionByTicketId, key)) {
        state.rejectionByTicketId[key] = rejection;
      }
    });
  }
  return dispatchRows;
}

function ingestApprovedCosts(payload) {
  const byTicket = payload && typeof payload.byTicket === "object" ? payload.byTicket : payload;
  if (!byTicket || typeof byTicket !== "object") return;
  for (const [rawKey, rawVal] of Object.entries(byTicket)) {
    const bucket = rawVal && typeof rawVal === "object" ? rawVal : {};
    const ticketId = clean(bucket.ticketNumber || bucket.ticketId || rawKey).replace(/^ticket_/i, "");
    const amount = Number(bucket.amount);
    if (!ticketId || !Number.isFinite(amount)) continue;
    [ticketId, rawKey, `ticket_${ticketId}`].map(partsCostKey).filter(Boolean).forEach((key) => {
      if (!Object.prototype.hasOwnProperty.call(state.approvedCostByTicketId, key)) state.approvedCostByTicketId[key] = amount;
    });
  }
}

function applyVehicleBase(payload) {
  if (!payload || typeof payload !== "object") return;
  state.vehicleBaseSummary = payload;
  state.chassisPgiMap = payload.pgiByChassis && typeof payload.pgiByChassis === "object" ? payload.pgiByChassis : {};
  state.salesOrderPgiMap = payload.pgiBySalesOrder && typeof payload.pgiBySalesOrder === "object" ? payload.pgiBySalesOrder : {};
  state.chassisSeriesMap = payload.seriesByChassis && typeof payload.seriesByChassis === "object" ? payload.seriesByChassis : {};
  state.salesOrderSeriesMap = payload.seriesBySalesOrder && typeof payload.seriesBySalesOrder === "object" ? payload.seriesBySalesOrder : {};
  state.salesBaseBySeries =
    payload.seriesSales && typeof payload.seriesSales === "object" ? payload.seriesSales :
    payload.seriesBaseBreakdown && typeof payload.seriesBaseBreakdown === "object"
      ? Object.fromEntries(Object.entries(payload.seriesBaseBreakdown).map(([series, bucket]) => [series, Number(bucket?.shipped) || 0]))
      : payload.seriesBase && typeof payload.seriesBase === "object" ? payload.seriesBase : {};
  state.deliveredBaseBySeries = payload.seriesBase && typeof payload.seriesBase === "object" ? payload.seriesBase : {};
}

function mergeTicketRow(base, incoming) {
  const merged = { ...base };
  const keys = new Set([...Object.keys(base || {}), ...Object.keys(incoming || {})]);
  for (const key of keys) {
    const current = clean(base?.[key]);
    const next = clean(incoming?.[key]);
    if (!current && next) merged[key] = incoming[key];
    else if (!current && !next && !(key in merged) && key in (incoming || {})) merged[key] = incoming[key];
  }
  return merged;
}

function mergeRows({ sourceRows, legacyRows, processedRows, dispatchRows }) {
  const mergedByTicket = new Map();
  const addRows = (rows, csvWins) => {
    for (const row of rows) {
      const ticketId = numericTicketIdValue(row);
      const serialId = clean(row["Serial ID"] || row.SerialID || row.SerialId || row.serialId || row["Ticket Serial ID"] || row.ticketSerialId || row["Matched Serial"] || row.matchedSerial);
      const chassis = clean(row["Chassis Number"] || row.ChassisNumber || row.ChassisID || row.chassisNumber || row["Ticket Chassis Number"] || row.ticketChassisNumber || row["Matched Chassis"] || row.matchedChassis);
      const identity = ticketIdentity(row);
      const key = identity || ticketId || serialId || chassis;
      if (!key) continue;
      const prev = mergedByTicket.get(key);
      if (!prev) mergedByTicket.set(key, row);
      else mergedByTicket.set(key, csvWins ? mergeTicketRow(row, prev) : mergeTicketRow(prev, row));
    }
  };
  addRows(sourceRows, true);
  addRows(legacyRows, false);
  addRows(processedRows, false);
  addRows(dispatchRows, false);
  return Array.from(mergedByTicket.values());
}

function firebaseUrl(pathParts, query = "") {
  const encoded = pathParts.map((part) => encodeURIComponent(String(part))).join("/");
  return `${FIREBASE_DB_URL}/${encoded}.json${query}`;
}

async function fetchFirebaseJson(pathParts, fallback = null, query = "") {
  const res = await fetch(firebaseUrl(pathParts, query), { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`Firebase ${pathParts.join("/")} failed: ${res.status} ${res.statusText}`);
  const data = await res.json();
  return data == null ? fallback : data;
}

function ticketNodeValues(node) {
  if (Array.isArray(node)) return node.map((value, index) => [String(index), value]).filter(([, value]) => value != null);
  if (node && typeof node === "object") return Object.entries(node);
  return [];
}

function normalizeLiveTicketMap(root) {
  const map = new Map();
  for (const [key, entry] of ticketNodeValues(root)) {
    if (!entry || typeof entry !== "object") continue;
    const ticket = entry.ticket && typeof entry.ticket === "object" ? entry.ticket : entry;
    const ticketId = clean(ticket.TicketID || ticket.TicketId || ticket.ticketId || ticket.id || key);
    if (ticketId) map.set(ticketId, ticket);
  }
  return map;
}

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function monthlyPeriodKey(year, month) {
  const mm = String(month).padStart(2, "0");
  return `${year}-${mm}-01|${year}-${mm}-${String(daysInMonth(year, month)).padStart(2, "0")}`;
}

function monthKeyFromPeriodKey(periodKey) {
  const m = clean(periodKey).match(/^(\d{4}-\d{2})-\d{2}\|/);
  return m ? m[1] : "";
}

function approvalRowAmountValue(row) {
  for (const candidate of [row?.exportAmount, row?.amount, row?.approvedAmount, row?.AmountIncludingTax, row?.value]) {
    if (candidate == null || candidate === "") continue;
    const numeric = Number(clean(candidate).replace(/[$,]/g, ""));
    if (Number.isFinite(numeric) && numeric > 0) return approvedCostAfterSanityCheck(row, numeric);
  }
  return 0;
}

async function loadPage1ApprovedRowsByMonth() {
  const out = new Map();
  const allRows = await fetchFirebaseJson([MONITOR_ROOT, "analytics", "team", "views", "all", "approvalTicketRows"], []);
  if (Array.isArray(allRows) && allRows.length) {
    for (const row of allRows) {
      if (clean(row?.decisionKey).toLowerCase() !== "approved") continue;
      const monthKey = clean(row?.decisionDate).slice(0, 7);
      if (!new RegExp(`^${MODEL_ANALYSIS_YEAR}-\\d{2}$`).test(monthKey)) continue;
      if (!out.has(monthKey)) out.set(monthKey, []);
      out.get(monthKey).push(row);
    }
    if (out.size) return new Map(Array.from(out.entries()).sort((a, b) => a[0].localeCompare(b[0])));
  }
  for (let month = 1; month <= 12; month += 1) {
    const periodKey = monthlyPeriodKey(MODEL_ANALYSIS_YEAR, month);
    const snapshot = await fetchFirebaseJson([MONITOR_ROOT, "analytics", "team", "views", "all", "periodSnapshots", periodKey], null);
    const rows = Array.isArray(snapshot?.approvalTicketRows)
      ? snapshot.approvalTicketRows.filter((row) => clean(row.decisionKey).toLowerCase() === "approved")
      : [];
    if (rows.length) out.set(monthKeyFromPeriodKey(periodKey), rows);
  }
  return out;
}

function sourceValue(row, ...keys) {
  for (const key of keys) {
    const value = clean(row?.[key]);
    if (value && value !== "#") return value;
  }
  return "";
}

function stableSeriesForVehicleKey(vehicleKey) {
  const canonical = vehicleLookupKey(vehicleKey);
  if (!canonical) return "";
  if (Object.prototype.hasOwnProperty.call(state.chassisSeriesMap, canonical)) {
    const series = normalizeSeriesCode(state.chassisSeriesMap[canonical]);
    if (isTrackedSeries(series) && !isExcludedSeries(series) && series !== OTHERS_SERIES) return series;
  }
  const series = seriesCodeFromVehicleToken(canonical);
  return isTrackedSeries(series) && !isExcludedSeries(series) && series !== OTHERS_SERIES ? series : "";
}

function addVehicleCandidate(candidates, source, rawValue) {
  const raw = clean(rawValue);
  if (!raw || raw === "#") return;
  const candidate = vehicleLookupKey(raw);
  if (!candidate || candidate.length < 2) return;
  if (["NOTASSIGNED", "UNKNOWN", "NIL", "NA"].includes(candidate)) return;
  if (/^800000\d{2,}$/.test(candidate)) return;
  candidates.push({
    source,
    chassis: candidate,
    raw,
    series: stableSeriesForVehicleKey(candidate),
    numeric: /^\d+$/.test(candidate),
  });
}

function addTextVehicleCandidates(candidates, source, value) {
  const text = clean(value).toUpperCase();
  if (!text || text === "#") return;
  const tokens = text.match(/\b[A-Z]{2,4}[A-Z0-9-]*\d[A-Z0-9-]*\b/g) || [];
  for (const token of tokens) addVehicleCandidate(candidates, source, token);
}

function liveTicketClaimType(ticket) {
  return sourceValue(ticket, "TicketTypeText", "Ticket Type Text", "Ticket Type", "TicketType", "WarrantyClaimType");
}

function liveTicketStatus(ticket) {
  return sourceValue(ticket, "TicketStatusText", "Ticket Status Text", "TicketStatus", "Status", "statusText");
}

function classifyPage1Ticket(pageRow, liveTicket, localRow) {
  const candidates = [];
  addVehicleCandidate(candidates, "Salesforce SerialID", sourceValue(liveTicket, "SerialID", "Serial ID", "Serial Id"));
  addVehicleCandidate(candidates, "Salesforce ChassisNumber", sourceValue(liveTicket, "ChassisNumber", "Chassis Number", "ChassisID"));
  addTextVehicleCandidates(candidates, "Salesforce TicketName", sourceValue(liveTicket, "TicketName", "Ticket Name", "name"));
  addTextVehicleCandidates(candidates, "Salesforce RegisteredProduct", sourceValue(liveTicket, "RegisteredProduct", "Registered Product"));
  addTextVehicleCandidates(candidates, "Salesforce Product", sourceValue(liveTicket, "Product", "product"));
  addVehicleCandidate(candidates, "Local Ticket Serial ID", sourceValue(localRow, "Ticket Serial ID", "ticketSerialId", "Serial ID", "SerialID", "SerialId"));
  addVehicleCandidate(candidates, "Local Ticket Chassis Number", sourceValue(localRow, "Ticket Chassis Number", "ticketChassisNumber", "Chassis Number", "ChassisNumber", "ChassisID"));
  addVehicleCandidate(candidates, "Local Matched Chassis", matchedChassisValue(localRow));
  addVehicleCandidate(candidates, "Local Matched Serial", matchedSerialValue(localRow));
  addVehicleCandidate(candidates, "Local Matched Vehicle Key", matchedVehicleKeyValue(localRow));
  addTextVehicleCandidates(candidates, "Local Ticket Name", sourceValue(localRow, "Ticket Name", "TicketName", "Ticket"));
  addTextVehicleCandidates(candidates, "Page1 customer", pageRow.customer);
  addTextVehicleCandidates(candidates, "Page1 repair", pageRow.repair);

  const withSeries = candidates.find((item) => item.chassis && item.series);
  if (withSeries) {
    return {
      series: withSeries.series,
      chassis: withSeries.chassis,
      matchSource: withSeries.source,
      bucket: "Model series",
      othersReason: "",
    };
  }
  const withChassis = candidates.find((item) => item.chassis);
  if (withChassis) {
    return {
      series: OTHERS_SERIES,
      chassis: withChassis.chassis,
      matchSource: withChassis.source,
      bucket: OTHERS_SERIES,
      othersReason: withChassis.numeric
        ? "Numeric Salesforce/local chassis; no stable tracked model series"
        : "Chassis found; no stable tracked model series",
    };
  }
  return {
    series: OTHERS_SERIES,
    chassis: "",
    matchSource: "",
    bucket: OTHERS_SERIES,
    othersReason: "No chassis found in available fields",
  };
}

function buildAlignedRow({ pageRow, liveTicket, localRow, monthKey }) {
  const ticketId = clean(pageRow.ticketId || pageRow.id || liveTicket?.TicketID || localRow?.TicketID);
  const amount = round(approvalRowAmountValue({ ...(localRow || {}), ...(liveTicket || {}), ...(pageRow || {}) }), 2) || 0;
  const model = classifyPage1Ticket(pageRow, liveTicket, localRow);
  const claim = clean(pageRow.claim) || liveTicketClaimType(liveTicket) || sourceValue(localRow, "Ticket Type", "TicketTypeText", "TicketType");
  const status = clean(pageRow.status) || liveTicketStatus(liveTicket) || ticketStatusText(localRow);
  const created = clean(pageRow.created) || sourceValue(liveTicket, "CreatedOn", "Created On") || sourceValue(localRow, "Created On", "CreatedOn");
  const dealer = clean(pageRow.dealer) || sourceValue(liveTicket, "DealerName", "Dealer Name") || sourceValue(localRow, "Dealer Name", "DealerName");
  const repairer = sourceValue(liveTicket, "RepairerBusinessNameID", "RepairerNamePointOfContact", "Service Technician") || sourceValue(localRow, "Repair Shop", "Repair Shop Name", "Service Technician");
  const salesOrder = sourceValue(liveTicket, "Sales Order", "SalesOrder", "ERPFreeOrder") || sourceValue(localRow, "Sales Order", "SalesOrder", "Ticket Sales Order");
  return {
    ...(localRow || {}),
    "Ticket ID": ticketId,
    TicketID: ticketId,
    TicketId: ticketId,
    ticketId,
    "Ticket Name": sourceValue(liveTicket, "TicketName", "Ticket Name") || sourceValue(localRow, "Ticket Name", "Ticket") || clean(pageRow.customer),
    TicketName: sourceValue(liveTicket, "TicketName") || sourceValue(localRow, "TicketName"),
    "Created On": created,
    CreatedOn: created,
    "Claim Approved On": clean(pageRow.decisionDate) || sourceValue(liveTicket, "ClaimApprovedOnDateTime", "ClaimApprovedOn"),
    ClaimApprovedOnDateTime: sourceValue(liveTicket, "ClaimApprovedOnDateTime") || clean(pageRow.decisionDate),
    "Ticket Type": claim,
    TicketTypeText: claim,
    "Claim Scope": claim,
    Status: status,
    "Status": status,
    TicketStatusText: status,
    "Dealer Name": dealer,
    DealerName: dealer,
    "Repair Shop": repairer,
    "Service Technician": sourceValue(liveTicket, "Service Technician") || clean(pageRow.employee) || sourceValue(localRow, "Service Technician"),
    "Serial ID": sourceValue(liveTicket, "SerialID", "Serial ID") || sourceValue(localRow, "Serial ID", "SerialID"),
    SerialID: sourceValue(liveTicket, "SerialID") || sourceValue(localRow, "SerialID"),
    "Chassis Number": sourceValue(liveTicket, "ChassisNumber", "Chassis Number") || sourceValue(localRow, "Chassis Number", "ChassisNumber"),
    ChassisNumber: sourceValue(liveTicket, "ChassisNumber") || sourceValue(localRow, "ChassisNumber"),
    "Sales Order": salesOrder,
    SalesOrder: salesOrder,
    "ERP Purchase Order ID": sourceValue(liveTicket, "ERPPurchaseOrder", "ERP Purchase Order ID") || sourceValue(localRow, "ERP Purchase Order ID"),
    ModelSeries: model.series,
    "Model Series": model.series,
    ModelChassis: model.chassis,
    "Model Chassis": model.chassis,
    ModelMatchSource: model.matchSource,
    "Model Match Source": model.matchSource,
    ModelBucket: model.bucket,
    "Model Bucket": model.bucket,
    ModelOthersReason: model.othersReason,
    "Model Others Reason": model.othersReason,
    ModelApprovedCost: amount,
    "Model Approved Cost": amount,
    "Page1 Amount": amount,
    page1Amount: amount,
    page1CostGt0: amount > 0,
    "Page1 Decision Date": clean(pageRow.decisionDate),
    "Page1 Customer": clean(pageRow.customer),
    "Page1 Employee": clean(pageRow.employee || pageRow.owner),
    "Model Period Month": monthKey,
    ModelPeriodMonth: monthKey,
  };
}

async function buildPage1AlignedModelRows(allRaw) {
  const pageRowsByMonth = await loadPage1ApprovedRowsByMonth();
  if (!pageRowsByMonth.size) throw new Error("No Page1 approved monthly snapshots found.");
  const liveTickets = normalizeLiveTicketMap(await fetchFirebaseJson([FIREBASE_ROOT, "tickets"], {}));
  const localByTicket = new Map();
  for (const row of allRaw) {
    const id = rawTicketIdValue(row);
    if (id && !localByTicket.has(id)) localByTicket.set(id, row);
  }
  const aligned = [];
  const seen = new Set();
  for (const [monthKey, pageRows] of pageRowsByMonth.entries()) {
    for (const pageRow of pageRows) {
      const ticketId = clean(pageRow.ticketId || pageRow.id);
      if (!ticketId) continue;
      const dedupeKey = `${monthKey}:${ticketId}`;
      if (seen.has(dedupeKey)) continue;
      seen.add(dedupeKey);
      aligned.push(buildAlignedRow({
        pageRow,
        liveTicket: liveTickets.get(ticketId) || {},
        localRow: localByTicket.get(ticketId) || {},
        monthKey,
      }));
    }
  }
  return aligned.sort((a, b) => String(modelMonthKey(a)).localeCompare(String(modelMonthKey(b))) || rawTicketIdValue(a).localeCompare(rawTicketIdValue(b), undefined, { numeric: true }));
}

function buildSlice(rows, periodKey, scope) {
  const activeBase = activeBaseMap(periodKey);
  const summary = summaryBySeries(rows, activeBase);
  const repairers = repairerAggregation(rows);
  const totalCost = summary.reduce((sum, item) => sum + (Number(item.cost) || 0), 0);
  const totalVehicles = summary.reduce((sum, item) => sum + (Number(item.vehicles) || 0), 0);
  const rejectedTickets = rows.filter((row) => isRejectedStatus(getRejection(row))).length;
  return {
    scope,
    periodKey,
    sourceRows: rows.length,
    summary,
    detailAll: allDetailForRows(rows, summary),
    detailRows: rows.map(compactModelDetailRow),
    repairers,
    totals: {
      sourceRows: rows.length,
      ticketCount: uniqueTicketCount(rows),
      costTickets: rows.filter((row) => getApprovedWarrantyCost(row) > 0).length,
      seriesCount: summary.length,
      repairerCount: repairers.filter((row) => row.tickets >= 5).length,
      repairers: repairers.length,
      repairCars: totalVehicles,
      rejectedTickets,
      approvedWarrantyCost: round(totalCost, 2) || 0,
    },
  };
}

function buildCache(modelRows) {
  state.autoBaseBySeries = traceBaseBySeries(modelRows);
  const months = Array.from(new Set(modelRows.map(modelMonthKey).filter(Boolean))).sort();
  const periods = {};
  const periodKeys = ["total", ...months];
  for (const periodKey of periodKeys) {
    const periodRows = periodKey === "total" ? modelRows : modelRows.filter((row) => modelMonthKey(row) === periodKey);
    periods[periodKey] = {
      periodKey,
      scopes: {},
      activeBaseBySeries: activeBaseMap(periodKey),
    };
    for (const scope of ["ALL", "PRE", "FIELD"]) {
      const scoped = periodRows.filter((row) => rowMatchesScope(row, scope));
      periods[periodKey].scopes[scope] = buildSlice(scoped, periodKey, scope);
    }
  }
  return {
    schema: "model-mtm-page1-approved-v4",
    generatedAt: new Date().toISOString(),
    modelAnalysisYear: MODEL_ANALYSIS_YEAR,
    ticketPoolRule: "Page1 approved tickets by approved/decision month; Salesforce/live ticket and vehicle base enrich chassis/model; unmatched stable-series rows remain in Others.",
    costRule: "Page1 approved approvalTicketRows cost aligned to Claim Trend Overview approvedAmountMonthly, with a stale SAP PO sanity guard; Cost/Ticket denominator is cost > 0 tickets.",
    monthOptions: months,
    autoBaseBySeries: state.autoBaseBySeries,
    salesBaseBySeries: normalizeSeriesCountMap(state.salesBaseBySeries),
    deliveredBaseBySeries: normalizeSeriesCountMap(state.deliveredBaseBySeries),
    vehicleBaseSummary: {
      generatedAt: state.vehicleBaseSummary?.generatedAt || "",
      cutoff: state.vehicleBaseSummary?.cutoff || "",
      totalSales: state.vehicleBaseSummary?.totalSales || 0,
      totalVehicles: state.vehicleBaseSummary?.totalVehicles || 0,
      totalShipped: state.vehicleBaseSummary?.totalShipped || 0,
      totalInStock: state.vehicleBaseSummary?.totalInStock || 0,
      totalInTransit: state.vehicleBaseSummary?.totalInTransit || 0,
      seriesBaseBreakdown: state.vehicleBaseSummary?.seriesBaseBreakdown || {},
      seriesSalesCumulativeByMonth: state.vehicleBaseSummary?.seriesSalesCumulativeByMonth || {},
    },
    totals: {
      modelRows: modelRows.length,
      uniqueTickets: uniqueTicketCount(modelRows),
      costTickets: modelRows.filter((row) => getApprovedWarrantyCost(row) > 0).length,
      repairCars: new Set(modelRows.map(normalizedChassis).filter(Boolean)).size,
      periods: periodKeys.length,
    },
    periods,
  };
}

async function main() {
  console.time("model-mtm-cache");
  applyVehicleBase(readJson(paths.vehicleBaseJson, {}));
  ingestApprovedCosts(readJson(paths.approvedCostJson, {}));
  const partsPayload = readJson(paths.partsCostJson, {});
  const dispatchRows = createDispatchRows(partsPayload);
  const ticketTimingRows = readCsvObjects(paths.ticketTimingCsv);
  const legacyRows = readCsvObjects(paths.ticketBaseCsv);
  const processedPayload = readJson(paths.repairersJson, {});
  const processedRows = Array.isArray(processedPayload?.details) ? processedPayload.details : [];
  const sourceRows = ticketTimingRows.length ? ticketTimingRows : legacyRows;
  const allRaw = mergeRows({ sourceRows, legacyRows, processedRows, dispatchRows });
  const modelRows = await buildPage1AlignedModelRows(allRaw);
  const cache = buildCache(modelRows);
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  fs.writeFileSync(paths.outJson, JSON.stringify(cache, null, 2), "utf8");
  writeJsGlobal(paths.outJs, "ANALYSIS_MODEL_MTM_CACHE", cache);
  console.log(`Wrote ${path.relative(ROOT, paths.outJson)} (${modelRows.length} Page1-approved model tickets, ${cache.monthOptions.length} months).`);
  console.timeEnd("model-mtm-cache");
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
