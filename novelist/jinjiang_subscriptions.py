"""Read the existing automatic-renewal list without changing subscriptions."""

import asyncio
from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit


SUBSCRIPTIONS_URL = "https://my.jjwxc.net/backend/vip_services.php"
_TABS = ("concerned", "notconcerned")


def subscriptions_url(value=SUBSCRIPTIONS_URL, tab="concerned"):
    """Only the observed GET filters and pagination are navigation targets."""
    parsed = urlsplit(urljoin(SUBSCRIPTIONS_URL, value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"my.jjwxc.net", "my.jjwxc.com"}
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in {None, 80, 443}
            or parsed.path != "/backend/vip_services.php"
            or set(query) - {"type", "renew_status", "page", "jsid"}):
        raise ValueError("自动续订列表链接无效。")
    for name in ("type", "renew_status", "page"):
        if name in query and len(query[name]) != 1:
            raise ValueError("自动续订列表链接无效。")
    selected_tab = query.get("type", [tab])[0]
    page = query.get("page", ["1"])[0]
    if selected_tab not in _TABS or not re.fullmatch(r"[1-9]\d{0,4}", page):
        raise ValueError("自动续订列表链接无效。")
    if "renew_status" in query and query["renew_status"] != ["1"]:
        raise ValueError("自动续订列表链接无效。")
    return SUBSCRIPTIONS_URL + "?" + urlencode({"type": selected_tab, "renew_status": "1", "page": str(int(page))})


# This document stays detached. Its scripts and subscription controls are never
# executed; only server-rendered rows and pagination are inspected.
SUBSCRIPTIONS_SCRIPT = r"""({html,url}) => {
 const doc=new DOMParser().parseFromString(html,'text/html');
 const clean=value=>(value||'').replace(/\s+/g,' ').trim();
 const rows=[...doc.querySelectorAll('tr')];
 const hasTable=rows.some(row=>[...row.cells].some(cell=>clean(cell.textContent)==='是否自动续订'));
 const text=doc.body?.textContent||'';
 if(!hasTable){
   const gate=/访问过于频繁|访问太过频繁|请求过于频繁|请完成验证|完成安全验证|请输入验证码/.test(text)?'captcha':
     /请先登[录陆]|尚未登[录陆]|重新登[录陆]/.test(text)||doc.querySelector('input[type=password]')?'login':'structure';
   return {books:[],pages:[],complete:false,gate};
 }
 const books=new Map();let complete=true;
 for(const cell of doc.querySelectorAll('td[id^="renew_"]')){
   const match=cell.id.match(/^renew_(\d{1,12})$/);if(!match)continue;
   const state=clean(cell.textContent);
   if(/^未续订(?:\s|$)/.test(state))continue;
   if(!/^续订中(?:\s|$)/.test(state)){complete=false;continue;}
   const row=cell.closest('tr');let book=null;
   for(const a of row?.cells[0]?.querySelectorAll('a[href]')||[]){
     let link;try{link=new URL(a.getAttribute('href'),url)}catch{continue;}
     if(!['http:','https:'].includes(link.protocol)||link.username||link.password||!['','80','443'].includes(link.port)
       ||!['www.jjwxc.net','www.jjwxc.com','jjwxc.net','jjwxc.com'].includes(link.hostname)
       ||link.pathname!=='/onebook.php'||link.searchParams.getAll('novelid').length!==1)continue;
     const id=link.searchParams.get('novelid');const title=clean(a.textContent);
     if(!/^\d{1,12}$/.test(id||'')||Number(id)<1||Number(id)!==Number(match[1])||!title)continue;
     book={id:String(Number(id)),title,url:`https://www.jjwxc.net/onebook.php?novelid=${Number(id)}`,auto_subscribed:true};break;
   }
   if(book)books.set(book.id,book);else complete=false;
 }
 const pages=[...doc.querySelectorAll('.pagelist a[href]')].map(a=>new URL(a.getAttribute('href'),url).href);
 return {books:[...books.values()],pages,complete,gate:null};
}"""


def _decode_html(content, content_type):
    charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
    if not charset:
        charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content[:4096].decode("ascii", "ignore"), re.I)
    encoding = charset.group(1).lower() if charset else "utf-8"
    return content.decode("gb18030" if encoding in {"gb2312", "gbk", "gb18030"} else "utf-8", "replace")


async def read_subscriptions(reader):
    """Return confirmed renewals; incomplete results must not remove old entries."""
    books, visited = {}, set()
    pending = [subscriptions_url(tab=tab) for tab in _TABS]
    complete, error, page = True, "", None
    try:
        await reader.ensure()
        page = await reader.context.new_page()
        # Even images in a detached parsed document should not create traffic.
        await page.route("**/*", lambda route: route.abort())
        while pending and len(visited) < 100:
            url = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)
            response = await reader.context.request.get(url, max_redirects=0, timeout=30000)
            try:
                if response.status != 200:
                    raise ValueError("自动续订列表暂时无法同步，请稍后重试。")
                content = await response.body()
                html = _decode_html(content, response.headers.get("content-type", ""))
            finally:
                await response.dispose()
            result = await page.evaluate(SUBSCRIPTIONS_SCRIPT, {"html": html, "url": url})
            if result.get("gate"):
                message = "请到账号页面重新连接晋江。" if result["gate"] == "login" else "自动续订列表暂时无法同步，请稍后重试。"
                raise ValueError(message)
            complete = complete and result["complete"]
            books.update((book["id"], book) for book in result["books"])
            tab = parse_qs(urlsplit(url).query)["type"][0]
            for target in result["pages"]:
                safe = subscriptions_url(target, tab)
                if safe not in visited and safe not in pending:
                    pending.append(safe)
        if pending or not complete:
            complete = False
            error = "自动续订列表尚未全部同步，请稍后重试。"
    except asyncio.CancelledError:
        raise
    except Exception as exception:
        complete = False
        error = str(exception) if isinstance(exception, ValueError) else "自动续订列表暂时无法同步，请稍后重试。"
    finally:
        if page is not None:
            await page.close()
    return {"books": list(books.values()), "complete": complete,
            "checked_at": datetime.now(timezone.utc).isoformat(), "error": error}
