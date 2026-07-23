(function(){
  "use strict";

  const DB_NAME = "warranty-dashboard-page-cache";
  const STORE_NAME = "pages";
  const DB_VERSION = 1;
  const DEFAULT_VERSION_URL = "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app/ctmTicketStatusMonitorV44/analytics/meta/generatedAt.json";
  const DELIVERY_VERSION_URL = "https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app/c4cTickets_test/deliveryFlowHistory/latestSyncAt.json";
  let dbPromise = null;

  function openDb(){
    if(dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      if(!("indexedDB" in window)){
        reject(new Error("IndexedDB is not available"));
        return;
      }
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if(!db.objectStoreNames.contains(STORE_NAME)){
          db.createObjectStore(STORE_NAME, { keyPath:"key" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error("IndexedDB open failed"));
    });
    return dbPromise;
  }

  function getRecord(key){
    return openDb().then(db => new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readonly");
      const store = tx.objectStore(STORE_NAME);
      const req = store.get(key);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error || new Error("IndexedDB get failed"));
      tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
    }));
  }

  function putRecord(record){
    return openDb().then(db => new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      const req = store.put(record);
      req.onerror = () => reject(req.error || new Error("IndexedDB put failed"));
      tx.oncomplete = () => resolve(true);
      tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
    }));
  }

  function normalizeVersion(value){
    if(value == null) return "";
    if(typeof value === "string") return value.trim();
    if(typeof value === "number") return String(value);
    if(value && typeof value === "object"){
      return String(value.generatedAt || value.latestSyncAt || value.lastScanAt || value.updatedAt || value.version || "").trim();
    }
    return String(value).trim();
  }

  async function fetchVersion(url){
    const target = url || DEFAULT_VERSION_URL;
    const res = await fetch(target, { cache:"no-store" });
    if(!res.ok) throw new Error(`Version HTTP ${res.status}`);
    return normalizeVersion(await res.json());
  }

  async function getPage(key, version){
    if(!key || !version) return null;
    try{
      const record = await getRecord(key);
      if(!record || record.version !== version) return null;
      return record.value || null;
    }catch(err){
      console.warn("Page cache read failed", err);
      return null;
    }
  }

  async function setPage(key, version, value){
    if(!key || !version || value == null) return false;
    try{
      await putRecord({ key, version, savedAt:new Date().toISOString(), value });
      return true;
    }catch(err){
      console.warn("Page cache write failed", err);
      return false;
    }
  }

  function formatVersion(version){
    const raw = normalizeVersion(version);
    if(!raw) return "unknown";
    const d = new Date(raw);
    if(!Number.isNaN(d.getTime())){
      return d.toLocaleString("en-AU", {
        day:"2-digit",
        month:"short",
        year:"numeric",
        hour:"2-digit",
        minute:"2-digit"
      });
    }
    return raw;
  }

  function showBadge(version, mode){
    const id = "warrantyDataUpdatedBadge";
    let el = document.getElementById(id);
    if(!el){
      el = document.createElement("div");
      el.id = id;
      el.style.cssText = [
        "position:fixed",
        "right:14px",
        "bottom:14px",
        "z-index:9999",
        "max-width:min(360px,calc(100vw - 28px))",
        "padding:8px 11px",
        "border:1px solid rgba(148,163,184,.38)",
        "border-radius:999px",
        "background:rgba(255,255,255,.94)",
        "box-shadow:0 10px 26px rgba(15,23,42,.12)",
        "color:#334155",
        "font:600 12px/1.25 Segoe UI,Arial,sans-serif",
        "backdrop-filter:blur(8px)",
        "-webkit-backdrop-filter:blur(8px)",
        "pointer-events:none"
      ].join(";");
      document.addEventListener("DOMContentLoaded", () => document.body.appendChild(el), { once:true });
      if(document.body) document.body.appendChild(el);
    }
    const suffix = mode === "cached" ? " - local cache" : (mode === "fresh" ? " - refreshed" : "");
    el.textContent = `Data updated: ${formatVersion(version)}${suffix}`;
  }

  window.WarrantyPageCache = {
    defaultVersionUrl: DEFAULT_VERSION_URL,
    deliveryVersionUrl: DELIVERY_VERSION_URL,
    fetchVersion,
    getPage,
    setPage,
    showBadge,
    formatVersion
  };
})();
