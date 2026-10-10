import {sha256} from '@noble/hashes/sha256';
import {manifest,loaders} from './generated';
import {beginOperation,operationNeedsOCR,validateSiteURL} from './shims/http';
let active:any=null;let instance:any=null;let chapters=new Map<string,any>();let busy=false;
const plain=(v:any)=>String(v??'').trim();
function safeURL(value:string){const url=new URL(value,location.href);url.hash='';for(const key of [...url.searchParams.keys()])if(/^(utm_|token$|access_token$|authorization$|auth$|password$|session$|sessionid$|ticket$|sign$|signature$)/i.test(key))url.searchParams.delete(key);return url.href;}
async function chapterID(url:string){const digest=sha256(new TextEncoder().encode(safeURL(url)));return 'c_'+[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('').slice(0,24);}
function detect(){const rule=manifest.rules.find(r=>r.hosts.includes(location.hostname)&&!r.book_exclude_patterns.some(p=>new RegExp(p).test(location.pathname)));return rule?{id:rule.id,name:rule.name,hosts:rule.hosts,status:rule.status}:null;}
async function operation<T>(fn:()=>Promise<T>){if(busy)throw new Error('正在读取，请等待当前操作完成。');busy=true;try{return await fn();}finally{busy=false;}}
async function scan(options:{rule_id?:string}={}) {return operation(async()=>{
 const detected=detect();if(!detected||options.rule_id&&options.rule_id!==detected.id)throw new Error('当前页面没有匹配的小说规则。');
 active=manifest.rules.find(r=>r.id===detected.id);instance=null;chapters.clear();beginOperation([location.hostname]);
 let Export=await (loaders as any)[active.id]();
 if(!Export?.prototype?.bookParse)Export=await Export();
 if(!Export?.prototype?.bookParse)throw new Error('该站点规则暂时无法加载。');
 instance=new Export();
 const book=await instance.bookParse();instance.book=book;
 if(!plain(book?.bookname)||!Array.isArray(book.chapters)||book.chapters.length===0)throw new Error('尚未读取到完整目录，请打开小说目录页后重试。');
 if(book.chapters.length>20000)throw new Error('目录章节数量过多。');
 const output=[];
 for(const chapter of book.chapters){
  let url:string;try{url=validateSiteURL(chapter.chapterUrl).href;}catch{throw new Error('目录包含此规则无法安全读取的链接，暂未导入这本小说。');}
  const id=await chapterID(url);if(chapters.has(id))continue;
  chapters.set(id,chapter);
  output.push({id,title:plain(chapter.chapterName)||`第 ${output.length+1} 章`,url:safeURL(url),order:output.length+1,vip:!!chapter.isVIP,paid:chapter.isPaid===true,blocked:chapter.status===4||!!chapter.isVIP&&chapter.isPaid!==true});
 }
 if(!output.length)throw new Error('目录中没有此站点可读取的章节。');
 return{rule_id:active.id,title:plain(book.bookname),author:plain(book.author),url:safeURL(book.bookUrl||location.href),chapters:output,imported:true,verified:false};
 });}
async function read(options:{chapter_id:string}) {return operation(async()=>{
 if(!instance||!active)throw new Error('请先读取这本小说的目录。');
 const chapter=chapters.get(options.chapter_id);if(!chapter)throw new Error('目录中没有这个章节，请重新读取目录。');
 if(chapter.status===4||chapter.isVIP&&chapter.isPaid!==true)throw new Error('此章节尚未确认可读，请在原网站完成登录或订阅。');
 beginOperation([location.hostname]);
 const parser=typeof chapter.chapterParse==='function'?chapter.chapterParse:instance.chapterParse;
 const parsed=await parser.call(instance,chapter.chapterUrl,chapter.chapterName,chapter.isVIP,chapter.isPaid,chapter.charset||instance.charset,chapter.options||{});
 const raw=parsed?.contentRaw as HTMLElement|null;
 const text=plain(parsed?.contentText);
 if(!text)throw new Error('未读取到章节正文；该章节可能需要登录、验证或专门的图片识别。');
 const images=(parsed?.contentImages?.length||0)+(raw?.querySelectorAll('img,canvas,svg').length||0);
 const privateChars=/[\uE000-\uF8FF\u{F0000}-\u{FFFFD}\u{100000}-\u{10FFFD}]/u.test(text);
 const explicitFont=!!raw?.querySelector('[style*="font-family"],style')||!!raw?.style.fontFamily;
 const gate=/^(请先登录|请登录后阅读|此章节为付费章节|请购买本章|验证码)/u.test(text);
 return{title:plain(parsed.chapterName)||plain(chapter.chapterName),text:gate?'':text,raw_text:gate?'':text,method:'upstream_text',images,needs_ocr:images>0||privateChars||explicitFont||operationNeedsOCR(),gate:gate?'needs_login':undefined,review:true};
 });}
Object.defineProperty(window,'NovelistRules',{value:Object.freeze({version:1,detect,scan,read}),configurable:true});
