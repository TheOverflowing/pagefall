"""Platform routing and independently implemented visible-page adapters.

Native adapters only inspect rendered public page structures. The separately
built upstream engine advertises its available rules through a local manifest;
being present in that manifest does not imply a site has passed live testing.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse


from .runtime import RESOURCE_ROOT as ROOT
UPSTREAM_MANIFEST = ROOT / "engine" / "rules-manifest.json"


@dataclass(frozen=True)
class PlatformSpec:
    id: str
    name: str
    book_hosts: tuple[str, ...]
    reader_hosts: tuple[str, ...]
    status: str = "unverified"
    notes: str = "尚未完成真实页面验证。"
    adapter_kind: str = "native"
    rule_id: str | None = None
    book_patterns: tuple[str, ...] = ()
    book_exclude_patterns: tuple[str, ...] = ()

    def public(self):
        return {"id": self.id, "name": self.name, "status": self.status,
                "notes": self.notes, "adapter_kind": self.adapter_kind,
                "hosts": list(self.book_hosts), "rule_id": self.rule_id}


_JJ_HOSTS = ("jjwxc.net", "www.jjwxc.net", "my.jjwxc.net", "m.jjwxc.net", "wap.jjwxc.net", "jjwxc.com", "www.jjwxc.com", "m.jjwxc.com", "wap.jjwxc.com")
_NATIVE = (
    PlatformSpec("jjwxc", "晋江文学城", _JJ_HOSTS, _JJ_HOSTS, "verified", "已验证登录、收藏与可阅读章节。"),
    PlatformSpec("gongzicp", "长佩文学", ("gongzicp.com", "www.gongzicp.com"), ("gongzicp.com", "www.gongzicp.com"), "public_verified", "已验证公开目录与正文结构；账号与付费正文尚未验证。访问可能需要滑块验证。"),
    PlatformSpec("fanqie", "番茄小说", ("fanqienovel.com", "www.fanqienovel.com"), ("fanqienovel.com", "www.fanqienovel.com"), "public_verified", "已验证公开目录与免费阅读页；自定义字体使用 OCR，会员章节尚未验证。"),
)


def _parsed_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("请粘贴小说作品主页链接。")
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            raise ValueError()
        if any(ord(character) < 32 for character in value):
            raise ValueError()
    except ValueError:
        raise ValueError("请输入有效的小说网站链接。") from None
    return parsed


def platforms():
    """Native platforms plus only the rules present in the compiled engine."""
    result = list(_NATIVE)
    covered = {host for spec in result for host in spec.book_hosts}
    try:
        manifest = json.loads(UPSTREAM_MANIFEST.read_text())
        for rule in manifest.get("rules", []):
            identifier = rule.get("id", "")
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", identifier):
                continue
            hosts = tuple(sorted({host.lower() for host in rule.get("hosts", []) if isinstance(host, str) and re.fullmatch(r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", host) and host.lower() not in covered}))
            if not hosts:
                continue
            rule_name = str(rule.get("name") or identifier)
            patterns = tuple(pattern for pattern in rule.get("book_patterns", []) if isinstance(pattern, str))
            excluded = tuple(pattern for pattern in rule.get("book_exclude_patterns", []) if isinstance(pattern, str))
            qidian = identifier.lower() == "qidian"
            result.append(PlatformSpec("qidian" if qidian else "upstream_" + identifier.lower(), "起点中文网" if qidian else rule_name, hosts, hosts, "unverified", "参考项目规则已接入，尚未逐站验证；以实际读取结果为准。", "upstream", identifier, patterns, excluded))
            covered.update(hosts)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return tuple(result)


def platform_for(value):
    if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value.strip()):
        return _NATIVE[0]
    parsed = _parsed_url(value)
    for spec in _NATIVE:
        if parsed.hostname in spec.book_hosts or parsed.hostname in spec.reader_hosts:
            return spec
    for spec in platforms():
        if parsed.hostname in spec.book_hosts or parsed.hostname in spec.reader_hosts:
            return spec
    raise ValueError("这个网站尚未接入，请在应用中查看平台列表。")


def normalize_book_url(value):
    if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value.strip()):
        value = f"https://www.jjwxc.net/onebook.php?novelid={int(value.strip())}"
    parsed = _parsed_url(value)
    spec = platform_for(value)
    query = parse_qs(parsed.query)
    if spec.id == "jjwxc":
        identifier = query.get("novelid", [""])[0]
        if not identifier:
            match = re.fullmatch(r"/(?:book2?|novel)/(\d{1,12})/?", parsed.path)
            identifier = match[1] if match else ""
        if not re.fullmatch(r"\d{1,12}", identifier):
            raise ValueError("没有找到晋江作品 ID，请复制作品主页链接。")
        return f"https://www.jjwxc.net/onebook.php?novelid={int(identifier)}"
    patterns = {"gongzicp": r"/novel-(\d{1,16})\.html", "fanqie": r"/page/(\d{1,24})/?", "qidian": r"/(?:book|info)/(\d{1,20})/?"}
    if spec.id in patterns:
        match = re.fullmatch(patterns[spec.id], parsed.path)
        if not match:
            raise ValueError(f"请粘贴{spec.name}的作品主页链接。")
        identifier = str(int(match[1]))
        return {"gongzicp": f"https://www.gongzicp.com/novel-{identifier}.html", "fanqie": f"https://fanqienovel.com/page/{identifier}", "qidian": f"https://www.qidian.com/book/{identifier}/"}[spec.id]
    # Upstream routers often match a hostname only. Keep their path/query intact;
    # only a successful rule scan can establish that it is a book page.
    for pattern in spec.book_exclude_patterns:
        try:
            if re.search(pattern, value):
                raise ValueError("这个链接是阅读页，请复制作品目录或作品主页链接。")
        except re.error:
            # JavaScript-only expressions are checked by the upstream bridge.
            continue
    safe_query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in {"token", "access_token", "authorization", "password", "session", "auth"} and not key.lower().startswith("utm_")]
    return urlunparse((parsed.scheme, parsed.hostname, parsed.path or "/", "", urlencode(safe_query), ""))


def book_identity(value):
    url = normalize_book_url(value)
    spec = platform_for(url)
    if spec.id == "jjwxc":
        return parse_qs(urlparse(url).query)["novelid"][0]
    if spec.id in {"gongzicp", "fanqie", "qidian"}:
        return spec.id + "_" + re.search(r"\d+", urlparse(url).path)[0]
    return spec.id + "_" + hashlib.sha256(url.encode()).hexdigest()[:16]


def normalize_chapter_url(book_url, value):
    """Validate each catalog chapter within its book's platform boundary."""
    book_url = normalize_book_url(book_url)
    spec = platform_for(book_url)
    parsed = _parsed_url(value)
    if parsed.hostname not in spec.reader_hosts:
        raise ValueError("章节链接跳转到了其他平台。")
    if spec.id == "jjwxc":
        query = parse_qs(parsed.query)
        chapter = query.get("chapterid", [""])[0]
        if query.get("novelid", [""])[0] != book_identity(book_url) or not re.fullmatch(r"\d{1,12}", chapter) or parsed.path not in {"/onebook.php", "/onebook_vip.php"}:
            raise ValueError("章节链接与当前作品不一致。")
        return f"https://{parsed.hostname}{parsed.path}?novelid={book_identity(book_url)}&chapterid={chapter}"
    patterns = {"gongzicp": r"/read-(\d{1,16})\.html", "fanqie": r"/reader/(\d{1,24})/?", "qidian": r"/chapter/(\d{1,20})/(\d{1,24})/?"}
    if spec.id in patterns:
        match = re.fullmatch(patterns[spec.id], parsed.path)
        if not match or (spec.id == "qidian" and match[1] != book_identity(book_url).split("_", 1)[1]):
            raise ValueError("章节链接与当前作品不一致。")
        host = {"gongzicp": "www.gongzicp.com", "fanqie": "fanqienovel.com", "qidian": "www.qidian.com"}[spec.id]
        return urlunparse(("https", host, parsed.path.rstrip("/") + ("/" if spec.id == "qidian" else ""), "", "", ""))
    return urlunparse((parsed.scheme, parsed.hostname, parsed.path, "", parsed.query, ""))


def chapter_matches(book_url, chapter, actual_url):
    try:
        return normalize_chapter_url(book_url, chapter["url"]) == normalize_chapter_url(book_url, actual_url)
    except (KeyError, TypeError, ValueError):
        return False


class PlatformError(ValueError):
    pass


TOC_SCRIPT = r"""kind => {
 const read=e=>(e?.textContent||'').replace(/\s+/g,' ').trim();
 const cp=kind==='gongzicp';
 const rows=[...document.querySelectorAll(cp?'.chapter-list .chapter':'.page-directory-content .chapter-item')];
 const seen=new Set(),chapters=[];
 for(const [index,row] of rows.entries()){
   const a=row.querySelector('a[href]');
   const locked=/锁定|已锁|审核中/.test(read(row));
   if(!a){if(locked)chapters.push({id:`locked_${index+1}`,title:read(row)||`第 ${index+1} 章`,url:location.href,locked:true,state:'blocked',expected_words:0,vip:false});continue;}
   const u=new URL(a.href,location.href),m=u.pathname.match(cp?/^\/read-(\d+)\.html$/:/^\/reader\/(\d+)\/?$/);
   if(!m||seen.has(m[1]))continue;
   seen.add(m[1]);
   chapters.push({id:m[1],title:read(a),url:u.href,locked,state:locked?'blocked':'pending',expected_words:0,vip:!!row.querySelector(cp?'.cp-icon-vip,.icon-vip':'.chapter-item-lock')});
 }
 return {title:read(document.querySelector(cp?'.novel-info h3.name':'.info-name h1')),author:read(document.querySelector(cp?'.cp-author-info .author-name':'.author-name')),chapters};
}"""


CONTENT_SCRIPT = r"""kind => {
 const visible=e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0};
 const selector=kind==='gongzicp'?'.cp-reader .content':'.muye-reader-content';
 const el=[...document.querySelectorAll(selector)].find(visible);
 const region=el?.innerText||document.body.innerText;
 if([...document.querySelectorAll('.aliyun-captcha,[class*=captcha]')].some(node=>visible(node)&&/滑动验证|请按住滑块|完成安全验证/.test(node.innerText)))return {gate:'captcha'};
 const gates=[['captcha',/访问过于频繁|完成安全验证|请完成验证|请求过于频繁|滑动验证|请按住滑块/],['login',/请先登录|登录后(?:阅读|查看)|登录即可阅读|登录才能阅读/],['purchase',/购买本章|订阅本章|购买后阅读|订阅后阅读|付费阅读|开通会员|开通SVIP/],['locked',/章节已锁定|章节被锁定|本章已锁|内容正在审核|本章节暂时无法阅读/],['app_only',/本章(?:节)?仅支持.{0,10}APP|请(?:下载|前往).{0,10}APP.{0,10}(?:阅读|查看)/i]];
 for(const [gate,pattern] of gates)if(pattern.test(region))return {gate};
 if(!el || el.innerText.trim().length<30)return {gate:'unknown'};
 const originals=[el,...el.querySelectorAll('*')],copy=el.cloneNode(true),copies=[copy,...copy.querySelectorAll('*')];
 originals.forEach((node,index)=>{if(node.matches('script,style,noscript,.author-say,.authorSay,.comment,.advertisement')||(node.tagName==='BR'?!visible(node.parentElement):!visible(node)))copies[index]?.remove()});
 copy.querySelectorAll('br').forEach(node=>node.replaceWith('\n'));
 copy.querySelectorAll('p,div').forEach(node=>node.append('\n'));
 const text=copy.textContent.replace(/\u00a0/g,' ').replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();
 const fontNames=new Set([...document.fonts].map(font=>font.family.replace(/["']/g,'')));
 const customFont=originals.some(node=>visible(node)&&getComputedStyle(node).fontFamily.split(',').map(name=>name.trim().replace(/["']/g,'')).some(name=>fontNames.has(name)));
 const generated=originals.some(node=>visible(node)&&['::before','::after'].some(pseudo=>{const content=getComputedStyle(node,pseudo).content;return content&&content!=='none'&&content!=='normal'&&content!=='""'}));
 const suspicious=customFont||generated||/[\uE000-\uF8FF\uFFFD]/u.test(text)||!!el.querySelector('img,canvas,svg');
 // Some readers scroll a fixed-height ancestor rather than the document. A
 // full-page screenshot otherwise silently clips the chapter to one viewport.
 // Expanding that visible layout changes no content or access permissions.
 if(suspicious){for(let ancestor=el.parentElement;ancestor;ancestor=ancestor.parentElement){const style=getComputedStyle(ancestor);if(/auto|scroll|hidden|clip/.test(style.overflowY)&&ancestor.scrollHeight>ancestor.clientHeight+2){ancestor.style.setProperty('overflow','visible','important');ancestor.style.setProperty('height','auto','important');ancestor.style.setProperty('max-height','none','important');if(style.position==='fixed')ancestor.style.setProperty('position','static','important');}}}
 const bounds=el.getBoundingClientRect();
 const header=kind==='gongzicp'?document.querySelector('.cp-reader')?.innerText:document.querySelector('.muye-reader-box')?.innerText;
 const count=(header||'').slice(0,300).match(/(?:本章字数[：:]\s*)?([\d,]+)\s*字/);
 return {text,customFont,suspicious,expected_words:count?Number(count[1].replace(/,/g,'')):0,clip:{x:Math.max(0,bounds.left+scrollX),y:Math.max(0,bounds.top+scrollY),width:bounds.width,height:bounds.height}};
}"""


async def scan(page, url):
    url = normalize_book_url(url)
    spec = platform_for(url)
    if spec.id not in {"gongzicp", "fanqie"}:
        raise PlatformError("此平台应使用对应的独立读取引擎。")
    if normalize_book_url(page.url) != url:
        raise PlatformError("页面与当前作品不一致。")
    selector = ".novel-info h3.name" if spec.id == "gongzicp" else ".info-name h1"
    try:
        await page.locator(selector).wait_for(state="visible", timeout=15000)
        await page.wait_for_function("([title,selector]) => {const rows=[...document.querySelectorAll(selector)];return document.querySelector(title)?.textContent.trim() && rows.length>0 && rows.every(row=>!row.querySelector('.cp-loading') && row.textContent.trim())}", arg=[selector, ".chapter-list .chapter" if spec.id == "gongzicp" else ".page-directory-content .chapter-item"], timeout=15000)
        if spec.id == "gongzicp":
            expand = page.get_by_text("展开全部", exact=True).first
            if await expand.count() and await expand.is_visible():
                await expand.click()
    except Exception:
        raise PlatformError(f"{spec.name}目录未加载完成，可能需要登录或验证。") from None
    result = await page.evaluate(TOC_SCRIPT, spec.id)
    if not result.get("title") or not result.get("chapters"):
        raise PlatformError(f"尚未识别到{spec.name}的完整作品目录。")
    for ordinal, chapter in enumerate(result["chapters"], 1):
        if not chapter.get("locked"):
            chapter["url"] = normalize_chapter_url(url, chapter["url"])
        chapter["book_url"] = url
        chapter["platform"] = spec.id
        chapter["ordinal"] = ordinal
    result.update(id=book_identity(url), url=url, platform=spec.id, platform_name=spec.name)
    return result


async def inspect_chapter(page, chapter):
    spec = platform_for(chapter.get("book_url") or chapter["url"])
    if spec.id not in {"gongzicp", "fanqie"}:
        raise PlatformError("此平台应使用对应的独立读取引擎。")
    if chapter.get("book_url") and not chapter_matches(chapter["book_url"], chapter, page.url):
        raise PlatformError("阅读页跳转到了其他章节，已停止处理。")
    # Fonts and asynchronous SPA content must be ready before deciding text/OCR.
    selector = ".cp-reader .content" if spec.id == "gongzicp" else ".muye-reader-content"
    try:
        await page.locator(selector).wait_for(state="visible", timeout=12000)
        await page.wait_for_function("selector => (document.querySelector(selector)?.innerText.trim().length||0)>=20 || [...document.querySelectorAll('.aliyun-captcha')].some(e=>e.getBoundingClientRect().height>0)", arg=selector, timeout=12000)
        await page.evaluate("() => Promise.race([document.fonts.ready,new Promise(resolve=>setTimeout(resolve,8000))])")
    except Exception:
        pass  # The gate inspection below reports the actual rendered barrier.
    return await page.evaluate(CONTENT_SCRIPT, spec.id)
