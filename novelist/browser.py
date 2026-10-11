import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from PIL import Image
from .library import FAVORITES_URL, PROFILE_URL, PROFILE_NICKNAME_SCRIPT, BOOKSHELF_SCRIPT, BOOKSHELF_META_SCRIPT, BOOKSHELF_PAGES_SCRIPT, BOOKSHELF_READY_SCRIPT, BOOKSHELF_NICKNAME_SCRIPT, BOOKSHELF_FOLDERS_SCRIPT, normalize_favorites_url
from .session import load_cookies_sync, save_cookies_sync, normalize_cookies, connection_nonce
from . import platforms

from .runtime import DATA_ROOT as ROOT, RESOURCE_ROOT
ALLOWED_HOSTS = {"jjwxc.net", "www.jjwxc.net", "my.jjwxc.net", "m.jjwxc.net", "wap.jjwxc.net", "jjwxc.com", "www.jjwxc.com", "m.jjwxc.com", "wap.jjwxc.com"}


def normalize_url(value):
    return platforms.normalize_book_url(value)


def book_id(url):
    return platforms.book_identity(url)


TOC_SCRIPT = r"""() => {
 const id = new URL(location.href).searchParams.get('novelid');
 const items = new Map();
 const rows=[...document.querySelectorAll('#oneboolt > tbody > tr[itemprop~="chapter"]')];
 function sourceUpdatedAt(row){
   const cell=row?.cells[row.cells.length-1];
   const match=cell?.innerText.trim().match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?(?=\s|$)/);
   if(!match)return '';
   const [year,month,day,hour,minute,second]=match.slice(1).map(value=>Number(value||0));
   const value=new Date(Date.UTC(year,month-1,day,hour,minute,second));
   if(value.getUTCFullYear()!==year||value.getUTCMonth()!==month-1||value.getUTCDate()!==day||value.getUTCHours()!==hour||value.getUTCMinutes()!==minute||value.getUTCSeconds()!==second)return '';
   // The visible final column is the last update; its tooltip contains first-publication and draft times.
   return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6]||'00'}`;
 }
 function readingURL(a){
   for(const value of [a.getAttribute('href'),a.getAttribute('rel')]){
     if(!value)continue;
     let u;try{u=new URL(value,location.href)}catch{continue}
     if (/^(?:www\.|my\.)?jjwxc\.(?:net|com)$/.test(u.hostname)&&/\/onebook(?:_vip)?\.php$/.test(u.pathname)&&u.searchParams.get('novelid')===id&&/^\d+$/.test(u.searchParams.get('chapterid')||''))return u;
   }
   return null;
 }
 for (const a of (rows.length ? rows.flatMap(r=>[...r.querySelectorAll('a')]) : document.querySelectorAll('a[href],a[rel]'))) {
  const u=readingURL(a);if(!u)continue;
  const cid=u.searchParams.get('chapterid');
  if (!cid || !/^\d+$/.test(cid) || Number(cid)<1 || u.searchParams.get('novelid')!==id) continue;
  if (!/^(?:www\.|my\.)?jjwxc\.(?:net|com)$/.test(u.hostname)) continue;
  if (!/\/onebook(?:_vip)?\.php$/.test(u.pathname)) continue;
  const title=a.innerText.trim().replace(/\s+/g,' ');
  if (!title || /^(上一章|下一章|第一章|最新章节|点击阅读)$/.test(title) && !a.closest('tr')) continue;
  const tr=a.closest('tr'); const row=tr?.innerText || '';
  const cells=tr ? [...tr.cells].map(c=>c.innerText.trim()) : [];
  const count=cells.findIndex(x=>/^\d{4}-\d{2}-\d{2}/.test(x));
  let expected=Number(tr?.querySelector('[itemprop~="wordCount"]')?.textContent?.trim()||0);
  if(!expected && count>0 && /^\d+$/.test(cells[count-1])) expected=Number(cells[count-1]);
  const candidate={id:cid,title,url:`https://${u.hostname}${u.pathname}?novelid=${id}&chapterid=${cid}`,expected_words:expected,vip:u.pathname.includes('_vip'),state:'pending',source_updated_at:sourceUpdatedAt(tr)};
  if (!items.has(cid) || tr) items.set(cid,candidate);
 }
 // Locked chapters may have no link at all. Keep them in the manifest.
 for(const row of rows){
   const cid=row.cells[0]?.innerText.trim();const cell=row.cells[1];
   if(!/^\d+$/.test(cid||'')||items.has(cid)||!cell)continue;
   const label=cell.innerText.trim();
   items.set(cid,{id:cid,title:label||`第 ${cid} 章`,url:`https://www.jjwxc.net/onebook.php?novelid=${id}&chapterid=${cid}`,state:'blocked',locked:/\[锁\]|锁定|锁章/.test(label),expected_words:Number(row.querySelector('[itemprop~="wordCount"]')?.textContent?.trim()||0),source_updated_at:sourceUpdatedAt(row),error:'目录中的本章没有可用阅读链接。'});
 }
 const meta=n=>document.querySelector(`meta[property="${n}"],meta[name="${n}"]`)?.content || '';
 let title=meta('og:novel:book_name') || document.querySelector('#oneboolt .bigtext')?.textContent?.trim() || document.querySelector('[itemprop="articleSection"]')?.textContent?.trim() || document.querySelector('h1')?.innerText?.trim() || document.title;
 title=title.replace(/_晋江文学城.*$/,'').trim();
 const author=meta('og:novel:author') || document.querySelector('#oneboolt h2 > a')?.textContent?.trim() || document.querySelector('[itemprop="author"]')?.textContent?.trim() || '';
 return {id,title,author,url:location.href,chapters:[...items.values()].sort((a,b)=>Number(a.id)-Number(b.id))};
}"""

# This fallback adapter inspects rendered body regions. Jinjiang auto mode
# first uses the separate pinned upstream parser; OCR renders visible glyphs.
CONTENT_SCRIPT = r"""() => {
 const visible=e=>{const s=getComputedStyle(e);const r=e.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0};
 const selectors=['#paragraph_comment_content','.noveltext','div[id^="content_"]','.novelbody > div[style*="font-size"]','#novelcontent','#chaptercontent'];
 let el=null;
 for(const sel of selectors){el=[...document.querySelectorAll(sel)].filter(visible).sort((a,b)=>(b.innerText?.length||0)-(a.innerText?.length||0))[0];if(el)break;}
 if(/\/(?:login|login2014)\.php$/.test(location.pathname) || (!el && [...document.querySelectorAll('input[type="password"]')].some(visible)))return {gate:'login'};
 const body=document.body.innerText;
 const gates=[
  ['captcha',/请输入验证码|完成安全验证|访问过于频繁|访问太过频繁|请求过于频繁|请完成验证/],
  ['app_only',/作者赠送的一章福利番外[\s\S]{0,100}无法在\s*PC\s*站显示/i],
  ['locked',/本章(?:节)?(?:已被|被)?锁定|章节(?:已被|被)?锁定|本章节暂时无法阅读/],
  ['login',/请先登[录陆](?:后|再|，|,|。|\s)|登录后(?:才能|即可|可)阅读/],
  ['purchase',/您尚未购买本章|您还没有购买|本章尚未购买|购买本章后|请购买本章|请订阅本章|购买后(?:即可|才能|可)阅读/],
  ['antitheft',/当前订阅比例不足|您的订阅比例|防盗时间未过|请等待防盗时间/]
 ];
 const region=el?.innerText || body;
 for(const [kind,pattern] of gates){if(pattern.test(region) || (!el && pattern.test(body)))return {gate:kind};}
 if([...document.querySelectorAll('#buy_content')].some(visible))return {gate:'purchase'};
 if(!el)return {gate:'unknown'};
 el.setAttribute('data-novelist-body','1');
 const rect=el.getBoundingClientRect();
 let top=rect.top+scrollY, bottom=rect.bottom+scrollY;
 const exclusions='script,style,noscript,.noveltitle,#favoriteshow_3,#note_danmu_wrapper,.readsmall,.novelreview,.author_say,#authorSay,[id^="favorite"],.tools,.ad';
 const clone=el.cloneNode(true);
 const original=[el,...el.querySelectorAll('*')];
 const copies=[clone,...clone.querySelectorAll('*')];
 original.forEach((n,i)=>{const hidden=n.tagName==='BR' ? !visible(n.parentElement) : !visible(n);if(hidden || n.matches(exclusions))copies[i]?.remove();});
 for(const n of el.querySelectorAll('#favoriteshow_3,#note_danmu_wrapper,.author_say,#authorSay,.readsmall')){if(visible(n)){const y=n.getBoundingClientRect().top+scrollY;if(y>top+50)bottom=Math.min(bottom,y);}}
 const heading=el.querySelector('.noveltitle'); if(heading&&visible(heading))top=Math.max(top,heading.getBoundingClientRect().bottom+scrollY);
 // Range text from the clone preserves BR and paragraph boundaries without hidden nodes.
 clone.querySelectorAll('br').forEach(n=>n.replaceWith('\n'));
 clone.querySelectorAll('p,div').forEach(n=>{n.appendChild(document.createTextNode('\n'));});
 const text=clone.textContent.replace(/\u00a0/g,' ').replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();
 const fonts=new Set([...document.fonts].map(f=>f.family.replace(/["']/g,'')));
 const customFont=original.some(n=>visible(n)&&[...fonts].some(f=>getComputedStyle(n).fontFamily.replace(/["']/g,'').split(',').map(x=>x.trim()).includes(f)));
 const generated=original.some(n=>visible(n)&&['::before','::after'].some(p=>{const c=getComputedStyle(n,p).content;return c&&c!=='none'&&c!=='normal'&&c!=='""'}));
 const suspicious=/[\uE000-\uF8FF\uFFFD]/u.test(text)||customFont||generated||!!el.querySelector('img,canvas,svg,[class*="jjwxcfont_"]');
 return {text,suspicious,customFont,clip:{x:Math.max(0,rect.left+scrollX),y:Math.max(0,top),width:rect.width,height:Math.max(0,bottom-top)}};
}"""


class CaptureError(Exception):
    def __init__(self, message, kind="failed", pause=False):
        super().__init__(message)
        self.kind, self.pause = kind, pause


GATE_ERRORS = {
    "captcha": ("晋江要求验证或限制了访问。请在阅读窗口处理，稍后继续。", True),
    "app_only": ("本章为 App 专属福利番外，网页端无法显示，请使用晋江 App 阅读。", False),
    "login": ("请先在阅读窗口登录，再继续。", True),
    "purchase": ("当前账号未显示本章正文，请在晋江确认订阅。", False),
    "locked": ("本章已锁定，当前无法阅读。", False),
    "antitheft": ("当前显示订阅比例或等待时间提示，请在晋江确认后重试。", False),
    "unknown": ("没有定位到正文。请检查登录状态或页面结构。", True),
}


class Reader:
    def __init__(self, profile: Path, headless=True, hydrate_session=True):
        self.profile, self.headless = profile, headless
        self.hydrate_session = hydrate_session
        self.pw = self.context = self.page = None
        self._nonce = None
        self._initializing = asyncio.Lock()
        self._library_lock = asyncio.Lock()
        self._engine_book_url = None
        self._jinjiang_binding = False
        self._jinjiang_scope = None

    @property
    def opened(self):
        return self.page is not None and not self.page.is_closed()

    async def ensure(self):
        if self.opened:
            if self.hydrate_session and self.context and connection_nonce() != self._nonce:
                await self._hydrate_session()
            return
        async with self._initializing:
            if self.opened:
                return
            await self.close()
            self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.pw = await async_playwright().start()
            self.context = await self.pw.chromium.launch_persistent_context(
                str(self.profile), headless=self.headless, viewport={"width": 1280, "height": 900},
                device_scale_factor=2, locale="zh-CN", accept_downloads=False,
            )
            self._jinjiang_binding = False
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.page.set_default_timeout(15000)
            await self._hydrate_session()

    async def _hydrate_session(self):
        if not self.hydrate_session:
            return
        nonce = connection_nonce()
        cookies = await asyncio.to_thread(load_cookies_sync)
        if cookies or (nonce and nonce != self._nonce):
            # A deliberate connector update replaces account cookies rather than
            # mixing identities. Even if the imported cookies have all expired,
            # do not silently fall back to a different account in the old profile.
            for suffix in (r"(^|\.)jjwxc\.net$", r"(^|\.)jjwxc\.com$"):
                await self.context.clear_cookies(domain=re.compile(suffix))
            if cookies:
                await self.context.add_cookies(cookies)
        self._nonce = nonce

    async def _session_cookies(self):
        # URL-filtered cookies omit values scoped to /backend or other paths.
        # This is our dedicated browser profile; retain only Jinjiang domains
        # before anything reaches the session store.
        cookies = await self.context.cookies()
        return normalize_cookies([
            cookie for cookie in cookies
            if any(cookie.get("domain", "").lstrip(".").lower() == suffix
                   or cookie.get("domain", "").lstrip(".").lower().endswith("." + suffix)
                   for suffix in ("jjwxc.net", "jjwxc.com"))
        ])

    async def _remember_session(self):
        if not self.hydrate_session:
            return
        cookies = await self._session_cookies()
        if cookies:
            await asyncio.to_thread(save_cookies_sync, cookies)

    async def _switch_mode(self, headless):
        # An explicit Chrome connection may have arrived since this context was
        # last used. Apply it before snapshotting, or old cookies would overwrite
        # the newly imported account in Keychain.
        if self.hydrate_session and self.context and connection_nonce() != self._nonce:
            await self._hydrate_session()
        if self.headless == headless and self.opened:
            return
        cookies = []
        if self.context:
            cookies = await self._session_cookies()
            if cookies and self.hydrate_session:
                await asyncio.to_thread(save_cookies_sync, cookies)
        await self.close(remember=False)
        self.headless = headless
        await self.ensure()
        if cookies:
            await self.context.add_cookies(normalize_cookies(cookies))

    async def show_login(self):
        await self._switch_mode(False)
        await self.goto(FAVORITES_URL)
        await self.page.bring_to_front()

    async def background(self):
        await self._switch_mode(True)

    async def close(self, remember=True):
        self._engine_book_url = None
        if self.context:
            # Preserve session-only and refreshed cookies on normal app shutdown.
            # Never overwrite a newer, unapplied Chrome import with this context.
            if self.hydrate_session and remember and connection_nonce() == self._nonce:
                try:
                    await self._remember_session()
                except Exception:
                    pass
            try:
                await self.context.close()
            except Exception:
                pass
        if self.pw:
            await self.pw.stop()
        self.context = self.pw = self.page = None

    async def goto(self, url):
        spec = platforms.platform_for(url)
        await self.ensure()
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if response and response.status in {403, 429}:
            raise CaptureError(f"{spec.name}限制了当前访问，请稍后再试。", "blocked", True)
        if self.hydrate_session:
            await self.page.wait_for_timeout(1800)
        if urlparse(self.page.url).hostname not in spec.reader_hosts:
            raise CaptureError("页面跳转到了其他网站，可能需要在原平台完成验证。", "blocked", True)
        if not self.hydrate_session:
            return
        # Wait for glyphs and images to finish rendering, within a bounded timeout.
        try:
            await self.page.evaluate("() => Promise.race([document.fonts.ready,new Promise(r=>setTimeout(r,8000))])")
        except Exception:
            pass

    async def open(self, url):
        if self.headless:
            await self.show_login()
            return
        if self.opened:
            await self.page.bring_to_front()
            return
        await self.goto(normalize_url(url) if url else "https://www.jjwxc.net/")
        await self.page.bring_to_front()

    async def library(self):
        async with self._library_lock:
            await self.ensure()
            page = await self.context.new_page()
            page.set_default_timeout(15000)
            books, folders, visited, pending = {}, {}, set(), [FAVORITES_URL]
            expected = None
            nickname = ""
            partial = False
            try:
                while pending and len(visited) < 50:
                    url = pending.pop(0)
                    if url in visited:
                        continue
                    visited.add(url)
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    if response and response.status in {403, 429}:
                        raise CaptureError("收藏页暂时限制访问，请稍后刷新。", "blocked", True)
                    if urlparse(page.url).hostname not in ALLOWED_HOSTS:
                        raise CaptureError("收藏页跳转到了非晋江地址。", "blocked", True)
                    await page.wait_for_timeout(1200)
                    meta = await page.evaluate(BOOKSHELF_META_SCRIPT)
                    if meta["gate"]:
                        return {"books": [], "account": {"state": "expired", "message": "晋江要求重新验证，请到账号页面完成连接。"}, "message": "需要验证晋江登录", "complete": False}
                    if not meta["has_shelf_structure"]:
                        raise CaptureError("收藏页结构未识别，已保留上次书架。")
                    expected = meta.get("expected_count") if meta.get("expected_count") is not None else expected
                    for control in meta["collapsed_controls"]:
                        try:
                            await page.locator(control["selector"]).click()
                            await page.wait_for_timeout(250)
                        except Exception:
                            partial = True
                    # Large categories can take several seconds to arrive after
                    # their controls already say “收起”. Wait for rendered rows,
                    # with a bound so failed categories remain explicitly partial.
                    try:
                        await page.wait_for_function(BOOKSHELF_READY_SCRIPT, timeout=20000, polling=250)
                    except PlaywrightTimeoutError:
                        partial = True
                    for book in await page.evaluate(BOOKSHELF_SCRIPT):
                        previous=books.get(book['id'],{})
                        memberships=list(dict.fromkeys(previous.get('source_folder_ids',[])+book.get('source_folder_ids',[])))
                        updated=max(previous.get('updated_at',''),book.get('updated_at',''))
                        books[book['id']]={**previous,**book}
                        if memberships:books[book['id']]['source_folder_ids']=memberships
                        if updated:books[book['id']]['updated_at']=updated
                    for folder in await page.evaluate(BOOKSHELF_FOLDERS_SCRIPT):
                        folders[folder['id']]=folder
                    final_meta = await page.evaluate(BOOKSHELF_META_SCRIPT)
                    partial = partial or bool(final_meta["collapsed_controls"] or final_meta["loading_controls"])
                    for target in await page.evaluate(BOOKSHELF_PAGES_SCRIPT):
                        safe = normalize_favorites_url(target)
                        if safe not in visited and safe not in pending:
                            pending.append(safe)
                    if not nickname:
                        nickname = await page.evaluate(BOOKSHELF_NICKNAME_SCRIPT)
                if not nickname:
                    # The shelf header abbreviates long names. Read only the
                    # visible nickname cell from the ordinary account page.
                    try:
                        await page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=15000)
                        if urlparse(page.url).hostname in ALLOWED_HOSTS and urlparse(page.url).path == "/backend/userinfo.php":
                            nickname = await page.evaluate(PROFILE_NICKNAME_SCRIPT)
                    except Exception:
                        pass
                await self._remember_session()
                complete = not partial and not pending and expected is not None and len(books) == expected
                message = f"已同步 {len(books)} 本收藏。" if complete else f"已同步 {len(books)} 本；部分收藏尚未加载完成。"
                return {"books": list(books.values()), "folders":list(folders.values()), "expected_count": expected, "complete": complete, "message": message, "account": {"state": "connected", "nickname": nickname, "verified_at": datetime.now(timezone.utc).isoformat()}}
            finally:
                await page.close()

    async def scan(self, url):
        url = normalize_url(url)
        spec = platforms.platform_for(url)
        if spec.id == 'jjwxc':
            from .catalog import read_public_source, parse_web_metadata
            from .chapter_catalog import parse_directory
            try:
                raw=await asyncio.to_thread(read_public_source,url)
                result=parse_directory(raw,book_id(url))
                result['metadata']=parse_web_metadata(raw)
                for field in ('title','author'):
                    value=result['metadata'].pop(field,'')
                    result[field]=result.get(field) or value
                self._engine_book_url=None
                return result
            except (ValueError,OSError):
                # Keep the existing rendered parser for a changed public page.
                pass
        await self.goto(url)
        self._engine_book_url = None
        if spec.adapter_kind == "upstream":
            from .engine import scan as engine_scan
            result = await engine_scan(self.page, url)
            self._engine_book_url = url
            return result
        if spec.id != "jjwxc":
            return await platforms.scan(self.page, url)
        from .jinjiang import invoke
        upstream = await invoke(self, 'scan')
        result = await self.page.evaluate(TOC_SCRIPT)
        if upstream and upstream.get('chapters'):
            # Keep local semantic word counts and blocked rows while using the
            # upstream directory whenever its chapter coverage agrees.
            rows = {str(c['id']): c for c in result['chapters']}
            imported = upstream['chapters']
            if {str(c['id']) for c in imported} == set(rows):
                result['chapters'] = [{**rows[str(c['id'])], **{k:v for k,v in c.items() if k in {'title','vip'} and v is not None}} for c in imported]
                result['title'] = upstream.get('title') or result['title']
                result['author'] = upstream.get('author') or result['author']
        if not result["chapters"]:
            raise CaptureError("未读取到章节目录。请在阅读窗口确认作品页已加载，再重新读取。")
        result.update(id=book_id(url), url=url, platform="jjwxc", platform_name="晋江文学城")
        return result

    async def _prepare_jinjiang(self, chapter):
        await self.ensure()
        if urlparse(self.page.url).hostname in ALLOWED_HOSTS:
            return
        url=normalize_url(chapter['url'])

        async def bootstrap(route):
            await route.fulfill(status=200,content_type='text/html',body='<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>')

        # The upstream parser needs a Jinjiang origin for its scoped bridge and
        # font cache, but fetches chapter content itself. A local document avoids
        # loading the whole book and waiting for its images before that request.
        await self.page.route(url,bootstrap,times=1)
        try:
            await self.page.goto(url,wait_until='domcontentloaded',timeout=15000)
        finally:
            await self.page.unroute(url,bootstrap)

    async def capture(self, chapter, folder: Path, mode="auto"):
        spec = platforms.platform_for(chapter.get("book_url") or chapter["url"])
        if spec.adapter_kind == "upstream":
            from .engine import read as engine_read
            if mode == "ocr":
                raise CaptureError("这个平台的兼容规则暂只支持文字读取，请在下载偏好中选择“自动选择”。", "blocked", True)
            if not self.opened or self._engine_book_url != chapter["book_url"]:
                await self.scan(chapter["book_url"])
            return await engine_read(self.page, chapter)
        if spec.id == 'jjwxc' and mode == 'auto':
            from .jinjiang import read, read_api
            result = await read_api(chapter)
            if result is not None:
                return result
            await self._prepare_jinjiang(chapter)
            result = await read(self, chapter, allow_token=False)
            if result is not None:
                return result
        await self.goto(chapter["url"])
        if spec.id == "jjwxc":
            await self.page.add_style_tag(content=".onebook_paragraph_comment_bubble_mount { visibility: hidden !important; }")
            data = await self.page.evaluate(CONTENT_SCRIPT)
        else:
            data = await platforms.inspect_chapter(self.page, chapter)
        if data.get("gate"):
            message, pause = GATE_ERRORS[data["gate"]]
            if spec.id != "jjwxc":
                message = message.replace("晋江", spec.name)
                if data["gate"] == "app_only":
                    message = f"当前章节需要在{spec.name} App 阅读，网页没有提供正文。"
            raise CaptureError(message, "blocked", pause)
        expected_query = parse_qs(urlparse(chapter["url"]).query)
        current_query = parse_qs(urlparse(self.page.url).query)
        if spec.id == "jjwxc" and any(current_query.get(key) != expected_query.get(key) for key in ("novelid", "chapterid")):
            raise CaptureError("阅读窗口跳转到了其他章节，已暂停以避免重复或错章。", "blocked", True)
        folder.mkdir(parents=True, exist_ok=True)
        expected = chapter.get("expected_words") or data.get("expected_words", 0)
        if mode == "auto" and not chapter.get("vip") and not data["suspicious"] and len(data["text"].strip()) >= 30:
            text = data["text"]
            warning = bool(expected and len(re.sub(r"\s", "", text)) < expected * 0.8)
            return {"text": text, "raw_text": text, "method": "text", "review": warning, "incomplete": warning, "screenshots": [], "error": "字数明显少于目录标注，请确认正文完整。" if warning else ""}
        clip = data["clip"]
        if clip["height"] < 20 or clip["width"] < 50:
            raise CaptureError("正文区域过小，无法识别。请检查页面。")
        if clip["height"] > 100000:
            raise CaptureError("本章页面异常过长，请在阅读窗口检查正文区域。")
        screenshot = folder / "source.png"
        await self.page.screenshot(path=str(screenshot), clip=clip, full_page=True, animations="disabled", timeout=45000)
        text, lines = await recognize_image(screenshot, folder)
        if len(text.strip()) < 20:
            raise CaptureError("识别出的正文不足 20 字，请检查原图后重试。")
        warning = bool(expected and len(re.sub(r"\s", "", text)) < expected * 0.8)
        return {"text": text, "raw_text": text, "method": "ocr", "review": True, "incomplete": warning, "screenshots": [str(screenshot)], "lines": lines, "error": "识别字数明显少于目录标注，请检查原图是否完整。" if warning else "OCR 已保存，请对照原图校对。", "capture_strategy": "upstream-fallback" if spec.id == 'jjwxc' and mode == 'auto' else 'ocr'}


async def recognize_image(path: Path, folder: Path):
    binary = RESOURCE_ROOT / ".build" / "vision-ocr"
    if not binary.is_file():
        raise CaptureError("本地识别引擎尚未编译，请通过「启动拾页.command」启动。")
    # Tall chapter screenshots are tiled with overlap; keep observations only in
    # each tile's central band so wrapped lines at crop edges cannot duplicate.
    image = Image.open(path).convert("RGB")
    width, height = image.size
    lines = []
    for start in range(0, height, 2200):
        top, bottom = max(0, start - 160), min(height, start + 2360)
        tile_path = folder / f"tile-{start:06d}.png"
        image.crop((0, top, width, bottom)).save(tile_path)
        proc = await asyncio.create_subprocess_exec(str(binary), str(tile_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise CaptureError("系统 OCR 失败：" + stderr.decode(errors="replace")[:300])
        payload = json.loads(stdout)
        for line in payload["lines"]:
            midpoint = top + (line["y"] + line["height"] / 2) * (bottom - top)
            if start <= midpoint < min(start + 2200, height):
                line["y"] = (top + line["y"] * (bottom - top)) / height
                line["height"] = line["height"] * (bottom - top) / height
                lines.append(line)
        tile_path.unlink(missing_ok=True)
    lines.sort(key=lambda line: (round(line["y"] * height / 12), line["x"]))
    # Preserve line boundaries for auditability rather than inventing paragraphs.
    return "\n".join(line["text"] for line in lines), lines
