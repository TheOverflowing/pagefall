"""Opt-in anonymous smoke: directory plus one chapter; never writes novel text."""
import asyncio, json
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2]
URLS=[
 'https://ncode.syosetu.com/n7000mj/',
 'https://kakuyomu.jp/works/16818093091237324319',
]
async def main():
 results=[]
 async with async_playwright() as pw:
  browser=await pw.chromium.launch(headless=True)
  for url in URLS:
   item={'url':url,'anonymous':True,'checked_at':datetime.now(timezone.utc).isoformat()}
   context=await browser.new_context();page=await context.new_page()
   try:
    response=await page.goto(url,wait_until='domcontentloaded',timeout=35000)
    item['http_status']=response.status if response else None
    await page.wait_for_timeout(2500)
    await page.add_script_tag(path=str(ROOT/'engine/dist/novelist-rules.js'))
    await page.wait_for_function('!!window.NovelistRules')
    item['detected']=await page.evaluate('window.NovelistRules.detect()')
    book=await asyncio.wait_for(page.evaluate('window.NovelistRules.scan()'),timeout=75)
    item['scan_ok']=True;item['chapter_count']=len(book['chapters']);item['book_title']=book['title'];item['book_author']=book['author']
    first=next((c for c in book['chapters'] if not c.get('blocked')),None)
    if first:
     chapter=await asyncio.wait_for(page.evaluate('(id)=>window.NovelistRules.read({chapter_id:id})',first['id']),timeout=40)
     item['first_read_ok']=bool(chapter.get('text'));item['first_characters']=len(chapter.get('text',''));item['first_needs_ocr']=chapter.get('needs_ocr');item['first_gate']=chapter.get('gate')
   except Exception as error:
    item['error_type']=type(error).__name__;item['error']=str(error).splitlines()[0][:240]
   finally:
    await context.close();results.append(item);print(json.dumps(item,ensure_ascii=False),flush=True)
  await browser.close()
 (ROOT/'engine/live-smoke.json').write_text(json.dumps({'scope':'Anonymous directory and first accessible chapter only; no text retained.','results':results},ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':asyncio.run(main())
