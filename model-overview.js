/* Pure presentation helpers: source aggregates and export rows stay unchanged. */
(function(root){
  function modelRows(rows){return rows.filter(row=>row.series!=="Historical Gap");}
  function metricValue(row,metric){
    if(metric==="vehicles")return Number(row.pgiMatchedVehicles ?? row.timingSources?.pgi ?? 0);
    if(metric==="average")return row.costPerTicket==null?null:Number(row.costPerTicket);
    return Number(row[metric==="tickets"?"tickets":"cost"]||0);
  }
  function rankedRows(rows,metric){
    return modelRows(rows).slice().sort((a,b)=>(metricValue(b,metric)??-1)-(metricValue(a,metric)??-1)||a.series.localeCompare(b.series));
  }
  function failureRows(rows,selected="ALL"){
    return modelRows(rows).filter(row=>selected==="ALL"||row.series===selected).map(row=>{
      const buckets=Array.isArray(row.buckets)?row.buckets:[0,0,0,0,0];
      const early=Number(buckets[0]||0),mid=Number(buckets[1]||0);
      const dated=Number(row.pgiMatchedVehicles ?? row.timingSources?.pgi ?? 0)||buckets.reduce((sum,n)=>sum+Number(n||0),0);
      return {series:row.series,early,mid,count:early+mid,dated,share:dated?(early+mid)/dated*100:0};
    });
  }
  function failureSegments(row,mode,maxCount){
    const denominator=mode==="share"?row.dated:maxCount;
    return {early:denominator?row.early/denominator*100:0,mid:denominator?row.mid/denominator*100:0};
  }
  root.ModelOverview={modelRows,metricValue,rankedRows,failureRows,failureSegments};
})(typeof window!=="undefined"?window:globalThis);
