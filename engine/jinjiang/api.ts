import {decodeVIPResopnce, decodeVIPText} from 'upstream-jinjiang-api';

// Only response decoding runs here. Credentials and networking stay in the host.
export function decode(input:any) {
 let result:any;
 try { result=JSON.parse(input.body); }
 catch { result=JSON.parse(decodeVIPResopnce(input.headers||'',input.body)); }
 if(!result||typeof result!=='object'||Array.isArray(result))throw Error('Invalid response');
 for(const [key,expected] of [['chapterId',input.chapter_id],['chapterid',input.chapter_id],['novelId',input.book_id],['novelid',input.book_id]]) {
  if(result[key]!=null&&String(result[key])!==String(expected))throw Error('Wrong chapter');
 }
 if(typeof result.content!=='string'||!result.content.trim())return {ok:false,message:typeof result.message==='string'?result.message:''};
 let content=result.content,postscript=typeof result.sayBodyV2==='string'?result.sayBodyV2:'';
 const fields=Array.isArray(result.encryptField)?result.encryptField:[];
 if(fields.includes('content'))content=decodeVIPText(content,result.encryptType,'');
 if(fields.includes('sayBodyV2'))postscript=decodeVIPText(postscript,result.encryptType,'');
 return {ok:true,content,postscript,title:typeof result.chapterName==='string'?result.chapterName:''};
}

let input='';
process.stdin.setEncoding('utf8');
process.stdin.on('data',chunk=>{input+=chunk;});
process.stdin.on('end',()=>{
 try{process.stdout.write(JSON.stringify(decode(JSON.parse(input))));}
 catch{process.stdout.write('{"ok":false}');}
});
