// Attachment descriptors only: never download cover/images, upload or start workers.
let sequence=0;
export class AttachmentClass { [key:string]:any; constructor(value:any){Object.assign(this,value);} }
export async function getAttachment(url:string,_mode?:any,prefix='image-'){return new AttachmentClass({url,name:prefix+(++sequence),status:3});}
export const getAttachmentClassCache=(_url:string)=>undefined;
export const getRandomName=()=>`image-${++sequence}`;
export const clearAttachmentClassCache=()=>{};
