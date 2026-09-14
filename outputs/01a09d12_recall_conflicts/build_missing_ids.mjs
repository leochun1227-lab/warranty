import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const out = path.dirname(fileURLToPath(import.meta.url));
const input = path.resolve(out, '../recall_postcode_20260914/hana_followup');
const source = JSON.parse(await fs.readFile(path.join(input, 'result_summary.json'), 'utf8'));
const ids = source.missing_ticket_ids;
if (ids.length !== 31 || new Set(ids).size !== 31) throw new Error('Expected 31 unique ticket IDs.');
const tickets = JSON.parse(await fs.readFile(path.join(input, 'firebase_after.json'), 'utf8')).tickets;
const clean = value => value == null || ['#', 'Not Assigned'].includes(String(value).trim()) ? '' : String(value).trim();
const rows = ids.map(id => {
  const ticket = tickets[id];
  if (!ticket || clean(ticket.postcode)) throw new Error(`Unexpected ticket status: ${id}`);
  const serial = clean(ticket.product?.serialId || ticket.ticket?.SerialID || ticket.product?.chassisNumber);
  let customer = clean(ticket.ticket?.TicketName || ticket.customer?.customer);
  for (const vehicle of [serial, clean(ticket.product?.chassisNumber)]) {
    if (vehicle && customer.toUpperCase().endsWith(vehicle.toUpperCase())) customer = customer.slice(0, -vehicle.length).trim();
  }
  const email = clean(ticket.customer?.email || ticket.ticket?.ServiceRequesterEmail);
  let reason = 'Only dealer / finance records found; owner address not confirmed.';
  if (['39453','39457'].includes(id)) reason = 'Staff / test contact; no confirmed customer address.';
  if (id === '39628') reason = 'Vehicle linked to John Lam; customer identity not confirmed.';
  if (id === '40211') reason = 'Vehicle linked to Daniel Hayward; customer identity not confirmed.';
  if (id === '41312') reason = 'Vehicle linked to Bronwyn Watson; customer identity not confirmed.';
  if (id === '40478') reason = 'Test ticket; HANA address is inconsistent (Guangzhou / NSW).';
  if (id === '41151') reason = 'Customer shown only as Snowy River; owner / branch not confirmed.';
  if (['40566','40586','40588','40590','40592','40593'].includes(id)) reason = 'Vehicle linked to ABCO (dealer); owner postcode not confirmed.';
  if (['39942','39954','40919','40926','40931','40933','40935'].includes(id)) reason = 'Conflicting HANA records: TCH - The Caravan Hub (205131): 4814; The CARAVAN HUB - TOWNSVILLE (200035): 4812.';
  return [id, customer, serial, email, reason];
});
const wb = Workbook.create();
const sheet = wb.worksheets.add('Missing postcodes');
sheet.showGridLines = false;
sheet.getRange('A1:E32').values = [['Ticket ID', 'Customer', 'Vehicle / Serial ID', 'Email', 'Reason'], ...rows];
sheet.getRange('A1:E32').format.font = { name: 'Arial', size: 10, color: '#222222' };
sheet.getRange('A1:E32').format.rowHeight = 36;
sheet.getRange('A1:E32').format.verticalAlignment = 'center';
sheet.getRange('A1:E32').format.horizontalAlignment = 'left';
sheet.getRange('A2:E32').setNumberFormat('@');
sheet.getRange('A1:E1').format.fill = '#EEEEEE';
sheet.getRange('A1:E1').format.font.bold = true;
sheet.getRange('A1:E1').format.rowHeight = 27;
sheet.getRange('A1:E1').format.horizontalAlignment = 'center';
sheet.getRange('B2:E32').format.wrapText = true;
for (const [column, width] of Object.entries({ A: 11, B: 32, C: 23, D: 44, E: 64 })) sheet.getRange(`${column}1:${column}32`).format.columnWidth = width;
rows.forEach((row, index) => {
  if (row[4].length > 90) sheet.getRange(`A${index+2}:E${index+2}`).format.rowHeight = 48;
});
sheet.freezePanes.freezeRows(1);
wb.recalculate();
console.log((await wb.inspect({ kind: 'table', range: "'Missing postcodes'!A1:E5", tableMaxRows: 5, tableMaxCols: 5, maxChars: 1400 })).ndjson);
const preview = await wb.render({ sheetName: sheet.name, range: 'A1:E12', scale: 1.5, format: 'png' });
await fs.writeFile(path.join(out, 'missing_31_preview.png'), new Uint8Array(await preview.arrayBuffer()));
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(out, 'Recall_Tickets_Missing_Postcode_31.xlsx'));
console.log('Exported 31 ticket IDs with details.');
