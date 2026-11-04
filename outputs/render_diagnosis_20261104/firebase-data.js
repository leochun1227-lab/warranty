(() => {
  'use strict';
  const base='https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app/issueSubcategoryAnalysis/v1/c4cTickets_test/pdvDashboard';
  const cacheKey='pdv-firebase-startup-created-2026-09-09-v1';
  async function read(url){
    const r=await fetch(url,{cache:'no-cache',signal:AbortSignal.timeout(15000)});
    if(!r.ok)throw Error('Firebase HTTP '+r.status);
    return r.json();
  }
  function decode(meta){
    if(meta?.schema!=='pdv-site-v1'||!/^[a-f0-9]{20}$/.test(meta.generation)||typeof meta.data!=='string')throw Error('Firebase snapshot unavailable');
    const data=JSON.parse(meta.data);
    if(data.scopeStart!=='2026-09-09'||data.schema!=='pdv-overview-v1'||!Array.isArray(data.monthly)||!Array.isArray(data.groups))throw Error('Scoped Firebase summary is not ready');
    return {data,meta};
  }
  async function load(apply){
    let saved;
    try{const raw=localStorage.getItem(cacheKey);if(raw){saved=decode(JSON.parse(raw));apply(saved.data,saved.meta,'checking');}}catch{}
    try{
      if(saved){const generation=await read(base+'/startup/generation.json');if(generation===saved.meta.generation){document.documentElement.dataset.pdvSource=base+'/startup.json';document.documentElement.dataset.pdvFirebaseLoadedMs=performance.now().toFixed(1);apply(saved.data,saved.meta,'fresh');return;}}
      const meta=await read(base+'/startup.json'),result=decode(meta);
      document.documentElement.dataset.pdvSource=base+'/startup.json';
      document.documentElement.dataset.pdvFirebaseLoadedMs=performance.now().toFixed(1);
      apply(result.data,result.meta,'fresh');
      try{localStorage.setItem(cacheKey,JSON.stringify(meta));}catch{}
    }catch(e){if(saved){apply(saved.data,saved.meta,'offline');return;}throw e;}
  }
  async function detail(url){
    if(!url.startsWith(base+'/generations/'))throw Error('Invalid detail source');
    const envelope=await read(url);
    if(typeof envelope?.data!=='string')throw Error('Firebase detail unavailable');
    const rows=JSON.parse(envelope.data);if(!Array.isArray(rows))throw Error('Invalid Firebase detail');return rows;
  }
  window.PDVData={load,detail};
})();
