import {Jjwxc} from '../../vendor/novel-downloader/src/rules/special/original/jjwxc';
import {state,unsafeWindow} from './runtime';
const rule=new Jjwxc();
const suspicious=/[\uE000-\uF8FF\uFFFD\u{F0000}-\u{FFFFD}\u{100000}-\u{10FFFD}]/u;
function reset(hasToken:boolean,loggedIn:boolean){state.requests=0;state.route='web';state.fontMapped=0;unsafeWindow.tokenOptions={Jjwxc:hasToken?'1_NOVELIST_OPAQUE_TOKEN':undefined};unsafeWindow.novelistLoggedIn=loggedIn;}
async function read(args:any) {
 reset(args.has_token===true,args.logged_in===true);
 try {
  const result=await rule.chapterParse(args.url,args.title,args.vip===true,true,'GB18030',{});
  const text=result?.contentText;
  if(typeof text!=='string'||!text.trim()||suspicious.test(text)||result.contentImages?.length)return {ok:false,reason:'invalid_text'};
  return {ok:true,text:text.trim(),title:result.chapterName||args.title,method:state.route==='api'?'jjwxc_api':'jjwxc_upstream',requests:state.requests,font_mapped:state.fontMapped};
 }catch{return {ok:false,reason:'parser_error'};}
 finally {unsafeWindow.tokenOptions={};unsafeWindow.novelistLoggedIn=false;}
}
async function scan(args:any) {
 reset(args.has_token===true,args.logged_in===true);
 try{const book=await rule.bookParse();return {ok:true,title:book.bookname,author:book.author,chapters:book.chapters.map((c:any)=>({id:String(new URL(c.chapterUrl||location.href,location.href).searchParams.get('chapterid')||c.chapterNumber),title:c.chapterName,url:c.chapterUrl,vip:c.isVIP,locked:!c.chapterUrl,expected_words:0}))};}
 catch{return {ok:false,reason:'directory_error'};}
 finally{unsafeWindow.tokenOptions={};}
}
(window as any).NovelistJinjiang={read,scan};
