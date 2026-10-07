import fs from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

// Execute the same calculation functions as the page, without a browser,
// network, DOM rendering, or a second copy of the business rules.
export function buildDashboardStartup(input) {
  if (!input.team?.views?.all || !input.team.generatedAt || !input.employee) throw new Error('Incomplete dashboard inputs');
  const elements=new Map();
  let page;
  const context=vm.createContext({
    console,location:{hash:''},
    document:{getElementById(id){if(!elements.has(id))elements.set(id,{value:''});return elements.get(id);}},
    localStorage:{getItem(){return null;},setItem(){}},
    input,
    WarrantyPageCache:{setPage(_key,_version,value){page=value;}}
  });
  context.window=context;
  const html=fs.readFileSync(new URL('./index.html',import.meta.url),'utf8');
  const start=html.indexOf('let calcFloatHideTimer=');
  const end=html.indexOf('\nwindow.addEventListener("hashchange"',start);
  if(start<0||end<0)throw new Error('Dashboard calculation boundaries changed');
  vm.runInContext(fs.readFileSync(new URL('./dashboard-render-cache.js',import.meta.url),'utf8'),context);
  vm.runInContext(html.slice(start,end),context,{timeout:10000});
  vm.runInContext(`
    team=input.team;employeeAnalytics=input.employee;
    employeeStatusMapping=normalizeEmployeeDirectory(input.employeeDirectory||{});
    for(const view of Object.values(team.views)){
      view.__fullExportDetailsLoaded=true;
      for(const snapshot of Object.values(view.periodSnapshots||{}))snapshot.__fullExportDetailsLoaded=true;
    }
    prepareDashboardRenderSnapshot();
    saveTeamPageState(team.generatedAt);
  `,context,{timeout:120000});
  if(!page?.renderSnapshot?.values)throw new Error('Dashboard snapshot was not produced');
  const result={schema:'team-startup-v1',generatedAt:input.team.generatedAt,page};
  if(Buffer.byteLength(JSON.stringify(result))>12*1024*1024)throw new Error('Dashboard startup exceeds 12 MiB; publication was not replaced');
  return result;
}

if(process.argv[1]===fileURLToPath(import.meta.url)){
  const input=JSON.parse(fs.readFileSync(0,'utf8'));
  process.stdout.write(JSON.stringify(buildDashboardStartup(input)));
}
