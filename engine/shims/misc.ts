export type PublicConstructor<T>=new()=>T;
export const sleep=(ms:number)=>new Promise(resolve=>setTimeout(resolve,Math.min(Math.max(ms,0),3000)));
export const deepcopy=<T>(v:T):T=>structuredClone(v);
export const regexpEscape=(s:string)=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
export async function concurrencyRun(list:any[], _limit:number, handler:any, options:any={}) {const out=[];for(const [i,item]of list.entries()){if(options.signal?.aborted)throw new Error('已暂停');out.push(await handler(item,i));}return out;}
