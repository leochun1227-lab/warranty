(function(root){
  "use strict";
  // Cache calculated render inputs, never markup or event handlers. A snapshot
  // is used only during a render of the exact same claim/month/period; exports
  // and detail dialogs continue through their normal detail loaders.
  function create(){
    let saved=null, session=null, depth=0;
    const copy=value=>value == null ? value : JSON.parse(JSON.stringify(value));
    function wrap(name, fn, project=value=>value){
      return function(...args){
        if(!session || depth) return fn.apply(this,args);
        const key=JSON.stringify([name,args]);
        if(Object.prototype.hasOwnProperty.call(session.values,key))return copy(session.values[key]);
        depth++;
        try{
          const value=fn.apply(this,args);
          session.values[key]=copy(project(value));
          return value;
        }finally{depth--;}
      };
    }
    return {
      wrap,
      restore(value){saved=value?.schema===1&&value.values&&typeof value.values==="object"?value:null;},
      snapshot(){return saved;},
      matches(selection){return saved?.selection===selection;},
      clear(){saved=null;},
      render(selection, fn){
        session={schema:1,selection,values:saved?.selection===selection?{...saved.values}:{}};
        try{const result=fn();saved=session;return result;}
        finally{session=null;}
      }
    };
  }
  root.DashboardRenderCache={create};
})(typeof window!=="undefined"?window:globalThis);
