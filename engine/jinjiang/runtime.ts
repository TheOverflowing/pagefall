// Private userscript compatibility layer for the pinned Jinjiang parser.
export const unsafeWindow:any = {tokenOptions:{}};
export const state = {route:'web', requests:0, fontMapped:0};
export const ReferrerMode = {none:0, origin:1, same:2};
export const Status = {pending:0, aborted:4};
export const retryLimit = 1;
const modelURL='https://github.com/xushengfeng/eSearch-OCR/releases/download/4.0.0/ppocr_v5_mobile.zip';
declare global { interface Window { __novelistJinjiangRequest:(arg:any)=>Promise<any>; } }
export async function request(url:string,options:any={}) {
 if((options.method||'GET')!=='GET')throw Error('Read only');
 state.requests++;
 if(state.requests>16)throw Error('Request limit');
 const result=await window.__novelistJinjiangRequest({url});
 if(!result?.ok)throw Error('Jinjiang request failed');
 const bytes=Uint8Array.from(atob(result.body),c=>c.charCodeAt(0));
 return {bytes,status:result.status,responseHeaders:result.headers||'',response:new Blob([bytes]),responseText:new TextDecoder(options.charset||'utf-8').decode(bytes)};
}
export async function gfetch(url:string,options:any={}) {return request(url,options);}
export async function getHtmlDOM(url:string,charset='GB18030') {
 state.route='web';
 const response=await request(url,{charset});
 const doc=new DOMParser().parseFromString(response.responseText,'text/html');
 // Scripts are retained as inert text: upstream reads embedded crypto metadata.
 // This detached document is never inserted in the live document.
 doc.querySelectorAll('iframe,object,embed').forEach(n=>n.remove());
 for(const node of doc.querySelectorAll('*'))for(const attr of [...node.attributes])if(/^on/i.test(attr.name))node.removeAttribute(attr.name);
 return doc;
}
export const ggetHtmlDOM=getHtmlDOM;
export function _GM_xmlhttpRequest(options:any) {
 state.route='api';
 request(options.url).then(r=>options.onload(r)).catch(()=>{
  // Always settle upstream's callback promise; never wait for its missing error handler.
  options.onload({status:200,responseText:'{"code":1,"message":"API unavailable"}',responseHeaders:''});
 });
}
function database():Promise<IDBDatabase> {return new Promise((resolve,reject)=>{const r=indexedDB.open('novelist-jinjiang-ocr',1);r.onupgradeneeded=()=>r.result.createObjectStore('models');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(Error('Cache unavailable'));});}
export async function _GM_getValue(key:string,fallback:any) {
 try{const db=await database();return await new Promise((resolve,reject)=>{const r=db.transaction('models').objectStore('models').get(key);r.onsuccess=()=>{db.close();resolve(r.result??fallback)};r.onerror=()=>{db.close();reject(Error('Cache read'))};});}catch{return fallback;}
}
export async function _GM_setValue(key:string,value:any) {
 const db=await database();await new Promise<void>((resolve,reject)=>{const tx=db.transaction('models','readwrite');tx.objectStore('models').put(value,key);tx.oncomplete=()=>{db.close();resolve()};tx.onerror=()=>{db.close();reject(Error('Cache write'))};});
}
