// Intentionally excludes upstream run(), scheduler, UI, archive, statistics and callbacks.
export class BaseRuleClass {
 attachmentMode='naive'; charset=document.characterSet||'UTF-8'; concurrencyLimit=1; sleepTime=500; maxSleepTime=1000; needLogin=false; nsfw=false; book:any;
 async run(){throw new Error('The upstream download runner is disabled.');}
}
export type ChapterParseObject=any;
