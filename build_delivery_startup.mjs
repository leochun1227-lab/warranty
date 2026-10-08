import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

// Use the page's business calculations once on the publishing machine.
// No browser, network, or ticket details are needed to display the result.
export function buildDeliveryStartup(input){
  if(!input.history || !input.tickets || !input.historyVersion || !input.ticketVersion)throw Error('Incomplete delivery inputs');
  const html=fs.readFileSync(new URL('./delivery_flow.html',import.meta.url),'utf8');
  const start=html.indexOf('  const FB_HISTORY =');
  const end=html.indexOf('  async function loadDeliveryPage()',start);
  if(start<0||end<0)throw Error('Delivery calculation boundaries changed');
  const context=vm.createContext({input,console,document:{getElementById(){return null;}}});
  vm.runInContext(html.slice(start,end),context,{timeout:10000});
  const page=vm.runInContext(`(()=>{
    const history=collapseHistoryByDay(Object.values(input.history).filter(Boolean).map(normalizeSnapshot).filter(row=>row.asOf));
    if(!history.length)throw Error('Empty delivery history');
    const latest=history[history.length-1];
    const rows=normalizeCurrentTickets(input.tickets);
    if(!hasUsableTicketDetails(rows))throw Error('Live delivery ticket details are incomplete');
    let summary=mergeCurrentSummary(input.summary,buildCurrentTicketsSummary(rows,latest));
    if(!hasMeaningfulDeliverySummary(summary))throw Error('Empty delivery summary');
    // Rendering formerly preferred ticket-derived lead times over the merged
    // summary. Preserve that exact choice without sending those tickets.
    summary.issuedLeadSeriesMonth=buildEffectiveMonthlyLeadSeries(latest,summary,rows);
    summary.costTicketCount=uniqueTicketCountForCost(rows,latest);
    return {cacheMode:'summary-v3',granularity:'week',history,currentTickets:[],currentSummary:summary,costReport:latest.costReport};
  })()`,context,{timeout:120000});
  const result={schema:'delivery-startup-v1',generatedAt:input.historyVersion,sourceVersion:input.historyVersion+'|'+input.ticketVersion,page};
  if(Buffer.byteLength(JSON.stringify(result))>12*1024*1024)throw Error('Delivery startup exceeds the browser cache limit');
  return result;
}

if(process.argv[1]===fileURLToPath(import.meta.url)){
  process.stdout.write(JSON.stringify(buildDeliveryStartup(JSON.parse(fs.readFileSync(0,'utf8')))));
}
