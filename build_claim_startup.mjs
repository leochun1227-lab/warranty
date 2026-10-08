import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

export function buildClaimStartup(input){
  if(!input.tickets||!input.teamVersion||!input.coreVersion||!input.soVersion||!Array.isArray(input.amounts))throw Error('Incomplete claim trend sources');
  const html=fs.readFileSync(new URL('./infieldpredelivery.html',import.meta.url),'utf8');
  const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
  const end=script.indexOf('\nsetupNav();');
  if(end<0)throw Error('Claim calculation boundary changed');
  const context=vm.createContext({input,document:{getElementById(){return null;}}});
  context.window=context;
  vm.runInContext(fs.readFileSync(new URL('./claim-trend-ticket-metrics.js',import.meta.url),'utf8'),context);
  vm.runInContext(script.slice(0,end),context,{timeout:10000});
  const data=vm.runInContext(`(()=>{
    RAW_TICKETS=nonPdiTicketNodes(input.tickets);
    return {monthly:buildMonthly({approvedAmountMonthly:input.amounts}),closedIndex:buildClaimClosedIndex(RAW_TICKETS)};
  })()`,context,{timeout:120000});
  if(!data.monthly.length)throw Error('Empty claim trend summary');
  return {schema:'claim-startup-v1',generatedAt:input.teamVersion,
    sourceVersion:JSON.stringify([input.teamVersion,input.coreVersion,input.soVersion]),...data};
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  process.stdout.write(JSON.stringify(buildClaimStartup(JSON.parse(fs.readFileSync(0,'utf8')))));
}
