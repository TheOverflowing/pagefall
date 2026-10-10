// Data containers only. No logging, retries, settings, downloads or lifecycle callbacks.
export class Book { [key:string]:any; constructor(value:any){Object.assign(this,value);} }
export class Chapter { [key:string]:any; status=0; constructor(value:any){Object.assign(this,value);} }
export class ChapterAbortError extends Error {}
export type BookAdditionalMetadate=any;
export type ChapterAdditionalMetadate=any;
