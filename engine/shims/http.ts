// Parser-only network boundary. Does not replace the page's own fetch or access cookie stores.
let hosts=new Set<string>(); let budget=0; let deadline=0;
let needsOCR=false;
export function beginOperation(allowedHosts:string[]) {hosts=new Set(allowedHosts);budget=256;deadline=Date.now()+120000;needsOCR=false;}
export function operationNeedsOCR(){return needsOCR;}
export function validateSiteURL(input:string|URL) {
 const url=new URL(String(input),document.location.href);
 if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.port||!hosts.has(url.hostname.toLowerCase()))throw new Error('此规则不能读取这个站点的链接。');
 return url;
}
export async function safeFetch(input:RequestInfo|URL,init:RequestInit={}) {
 const target=validateSiteURL(input instanceof Request?input.url:String(input));
 if(--budget<0||Date.now()>deadline)throw new Error('页面请求过多或耗时过长，请稍后重试。');
 const method=(init.method||(input instanceof Request?input.method:'GET')).toUpperCase();
 const directoryPost=target.hostname==='b.guidaye.com'&&target.pathname==='/e/extend/bookpage/pages.php';
 if(!['GET','HEAD'].includes(method)&&!(method==='POST'&&directoryPost))throw new Error('解析规则只能读取页面。');
 const headers=new Headers(init.headers);
 for(const key of [...headers.keys()])if(/authorization|cookie|proxy-|x-api-key/i.test(key))throw new Error('不接受自定义身份凭据。');
 const response=await globalThis.fetch(target.href,{...init,method,headers,credentials:'include',redirect:'error',signal:AbortSignal.timeout(30000)});
 if(!response.ok)throw new Error(`网站暂时无法读取（${response.status}）。`);
 if(Number(response.headers.get('content-length')||0)>8*1024*1024)throw new Error('页面内容过大。');
 return response;
}
export async function getText(url:any,charset='UTF-8',options:RequestInit={}) {
 const response=await safeFetch(url,options);const bytes=await response.arrayBuffer();
 if(bytes.byteLength>8*1024*1024)throw new Error('页面内容过大。');
 return new TextDecoder(charset||'UTF-8').decode(bytes);
}
export async function getHtmlDOM(url:string,charset='UTF-8',options:RequestInit={}) {
 const address=validateSiteURL(url).href;
 const text=await getText(address,charset,options);
 const doc=new DOMParser().parseFromString(text,'text/html');
 // Detached documents never execute scripts. Remove them and their event handlers before rule processing.
 if([...doc.querySelectorAll('style')].some(s=>/@font-face/i.test(s.textContent||'')))needsOCR=true;
 doc.querySelectorAll('script,iframe,object,embed,base').forEach(n=>n.remove());
 for(const el of doc.querySelectorAll('*'))for(const attr of [...el.attributes])if(/^on/i.test(attr.name))el.removeAttribute(attr.name);
 const base=doc.createElement('base');base.href=address;doc.head.prepend(base);
 return doc;
}
export const ggetHtmlDOM=getHtmlDOM;
export const getHtmlDomWithRetry=getHtmlDOM;
export const fetchWithRetry=safeFetch;
export const fetchWithTimeout=safeFetch;
export async function getJson(url:any,options:RequestInit={}){return(await safeFetch(url,options)).json();}
export const ggetText=getText;
