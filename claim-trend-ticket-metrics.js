// Shared read-only ticket rules for Claim Trend and Repairer denominators.
(function(root){
"use strict";
const clean=v=>String(v==null?"":v).trim();
const arr=v=>Array.isArray(v)?v:(v&&typeof v==="object"?Object.values(v):[]);

function getField(row,keys){
  for(const key of keys){
    const value=row?.[key];
    if(value!=null&&clean(value)!=="")return value;
  }
  return "";
}

function dateKey(value){
  const text=clean(value);
  if(!text)return "";
  const iso=text.match(/\d{4}-\d{2}-\d{2}/);
  if(iso)return iso[0];
  const slash=text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if(slash)return `${slash[3]}-${slash[2].padStart(2,"0")}-${slash[1].padStart(2,"0")}`;
  const parsed=new Date(text);
  if(Number.isNaN(parsed.getTime()))return "";
  return `${parsed.getFullYear()}-${String(parsed.getMonth()+1).padStart(2,"0")}-${String(parsed.getDate()).padStart(2,"0")}`;
}

function monthKeyFromValue(value){
  const day=dateKey(value);
  return /^\d{4}-\d{2}-\d{2}$/.test(day)?day.slice(0,7):"";
}

function ticketPayload(node){
  return node&&typeof node==="object"&&node.ticket&&typeof node.ticket==="object"?node.ticket:node;
}

function ticketId(ticket){
  return clean(getField(ticket,["TicketID","Ticket Id","Ticket ID","C4C Ticket ID","id","ID"]));
}

function ticketClaimText(ticket){
  return clean(
    ticket?.claimType||
    ticket?.ClaimType||
    ticket?.claim||
    ticket?.Claim||
    ticket?.TicketTypeText||
    ticket?.["Ticket Type Text"]||
    ticket?.TicketType||
    ticket?.["Ticket Type"]||
    ""
  ).toLowerCase();
}

function ticketIsPreDelivery(ticket){
  const text=ticketClaimText(ticket);
  return text.includes("pre delivery")||text.includes("pre-delivery")||text.includes("predelivery");
}

function ticketIsInField(ticket){
  const text=ticketClaimText(ticket);
  return text.includes("in field")||text.includes("in-field")||text.includes("infield")||text.includes("field warranty");
}

function ticketIsPdi(ticket){
  const code=clean(ticket?.TicketType||ticket?.["Ticket Type"]).toUpperCase();
  return code==="Z010"||ticketClaimText(ticket).includes("pdi");
}

function ticketCreatedOn(ticket){
  return clean(getField(ticket,[
    "CreatedOnDateTime","CreatedOnDate","CreatedOn","Created On","Created On DateTime",
    "createdOnDateTime","createdOnDate","createdOn","CreatedAt","createdAt"
  ]));
}

function ticketCreatedMonth(ticket){
  return monthKeyFromValue(ticketCreatedOn(ticket));
}

function ticketResolvedOn(ticket){
  return clean(getField(ticket,[
    "ResolvedOnDateTime","ResolvedOnDate","ResolvedOn","Resolved On","Resolved On DateTime",
    "resolvedOnDateTime","resolvedOnDate","resolvedOn"
  ]));
}

function resolvedMonth(ticket){
  const row=ticketPayload(ticket)||{};
  const value=clean(row?.ResolvedOnDateTime||row?.ResolvedOnDate||row?.resolvedOnDateTime||row?.resolvedOn||row?.ResolvedOn||row?.["Resolved On DateTime"]||row?.["Resolved On"]||row?.resolvedDateTime||row?.resolvedDate||"");
  const month=value.slice(0,7);
  return /^\d{4}-\d{2}$/.test(month)?month:"";
}

function claimClosedDecision(ticket){
  const row=ticketPayload(ticket)||{};
  const group=clean(row?.StatusGroup||row?.statusGroup).toLowerCase();
  if(group==="approved_closed")return "approved";
  if(group==="unapproved_closed")return "unapproved";
  const statusCode=clean(row?.TicketStatus||row?.TicketStatusCode||row?.StatusCode||row?.statusCode).toLowerCase();
  if(statusCode==="y7")return "approved";
  if(statusCode==="y8")return "unapproved";
  const status=clean(row?.TicketStatusText||row?.ticketStatusText||row?.statusText||row?.StatusText||row?.Status||row?.status||row?.TicketStatus||row?.["Ticket Status"]||"").toLowerCase();
  if(status.includes("unapproved claims closed")||status.includes("unapproved closed"))return "unapproved";
  if(status.includes("approved claims closed")||status.includes("claim approved closed")||status.includes("approved closed"))return "approved";
  return "";
}

function claimClosedLastModifiedUser(ticket){
  const row=ticketPayload(ticket)||{};
  return clean(
    row?.LastModifiedUser||
    row?.lastModifiedUser||
    row?.LastModifiedBy||
    row?.lastModifiedBy||
    row?.ChangedBy||
    row?.changedBy||
    row?.UpdatedBy||
    row?.updatedBy||
    row?.ModifiedBy||
    row?.modifiedBy||
    row?.["Last Modified User"]||
    row?.["Last Modified By"]||
    row?.["Changed By"]||
    row?.["Updated By"]||
    row?.["Modified By"]||
    ""
  );
}

function claimClosedIgnoreForMarch2026(ticket){
  const month=resolvedMonth(ticket);
  if(month!=="2026-03")return false;
  const normalized=claimClosedLastModifiedUser(ticket).toLowerCase().replace(/[^a-z0-9]+/g," ").trim();
  if(!normalized)return false;
  return normalized.includes("admin aba")||
    normalized.includes("aba admin")||
    (normalized.includes("admin")&&normalized.includes("aba"));
}

function ticketUnapprovedClosedNow(ticket){
  return claimClosedDecision(ticket)==="unapproved"&&!claimClosedIgnoreForMarch2026(ticket);
}

function unapprovedMonthly(tickets){
  const empty={unapprovedCreatedIn:0,unapprovedCreatedPre:0,unapprovedClosedIn:0,unapprovedClosedPre:0};
  const out={};
  const seen=new Set();
  arr(tickets).forEach(node=>{
    const ticket=ticketPayload(node)||{};
    if(ticketIsPdi(ticket)||!ticketUnapprovedClosedNow(ticket))return;
    if(!ticketIsPreDelivery(ticket)&&!ticketIsInField(ticket))return;
    const id=ticketId(ticket);
    if(!id||seen.has(id))return;
    seen.add(id);
    const suffix=ticketIsPreDelivery(ticket)?"Pre":"In";
    const dates=[["Created",ticketCreatedMonth(ticket)],["Closed",monthKeyFromValue(ticketResolvedOn(ticket))]];
    dates.forEach(([basis,month])=>{
      if(!month||month<"2025-01")return;
      if(!out[month])out[month]={month,...empty};
      out[month][`unapproved${basis}${suffix}`]+=1;
    });
  });
  return Object.values(out).filter(row=>row.month>="2025-01").sort((a,b)=>a.month.localeCompare(b.month));
}

function ticketApprovedActiveNow(ticket){
  const code=clean(ticket?.TicketStatus||ticket?.statusCode||ticket?.Status||ticket?.TicketStatusCode||ticket?.StatusCode).toUpperCase();
  const text=clean(ticket?.TicketStatusText||ticket?.ticketStatusText||ticket?.statusText||ticket?.StatusText||ticket?.Status||ticket?.status).toLowerCase();
  const claimApproved=clean(
    ticket?.ClaimApprovedOnDateTime||
    ticket?.["Claim Approved On DateTime"]||
    ticket?.["Claim Approved On"]||
    ticket?.ClaimApprovedOnDate||
    ticket?.ClaimApprovedOn||
    ""
  );
  if(!claimApproved)return false;
  return ["Z9","Y0","Y1","Y2","Y4","YB"].includes(code)||["sales order approved","partially picked","dispatch parts","repair in progress","repairer invoiced received","repairer invoiced processed"].includes(text);
}

function ticketApprovedIncludingClosed(ticket){
  if(ticketApprovedActiveNow(ticket))return true;
  if(!ticketClaimApprovedOn(ticket))return false;
  const code=clean(ticket?.TicketStatus||ticket?.statusCode||ticket?.Status||ticket?.TicketStatusCode||ticket?.StatusCode).toUpperCase();
  const text=clean(ticket?.TicketStatusText||ticket?.ticketStatusText||ticket?.statusText||ticket?.StatusText||ticket?.Status||ticket?.status).toLowerCase();
  // Approved ticket counts retain claims after they close; cost scope is separate.
  return code==="Y7"||/^approved claims closed(?:\s*\(closed\))?$/.test(text);
}

function ticketClaimApprovedOn(ticket){
  return clean(getField(ticket,[
    "ClaimApprovedOnDateTime","ClaimApprovedOnDate","ClaimApprovedOn","Claim Approved On","Claim Approved On DateTime",
    "claimApprovedOnDateTime","claimApprovedOnDate","claimApprovedOn"
  ]));
}

function approvedMonthly(tickets){
  const empty={approvedIn:0,approvedPre:0,approvedCreatedIn:0,approvedCreatedPre:0};
  const out={},seen=new Set();
  arr(tickets).forEach(node=>{
    const ticket=ticketPayload(node)||{};
    if(ticketIsPdi(ticket)||!ticketApprovedIncludingClosed(ticket))return;
    if(!ticketIsPreDelivery(ticket)&&!ticketIsInField(ticket))return;
    const id=ticketId(ticket);
    if(!id||seen.has(id))return;
    seen.add(id);
    const suffix=ticketIsPreDelivery(ticket)?"Pre":"In";
    const dates=[["",monthKeyFromValue(ticketClaimApprovedOn(ticket))],["Created",ticketCreatedMonth(ticket)]];
    dates.forEach(([basis,month])=>{
      if(!month||month<"2025-01")return;
      if(!out[month])out[month]={month,...empty};
      out[month][`approved${basis}${suffix}`]+=1;
    });
  });
  return Object.values(out).sort((a,b)=>a.month.localeCompare(b.month));
}

root.ClaimTrendTicketMetrics=Object.freeze({approvedMonthly,unapprovedMonthly,isApproved:ticketApprovedIncludingClosed,isActiveApproved:ticketApprovedActiveNow,isUnapprovedClosed:ticketUnapprovedClosedNow});
})(globalThis);
