"""Read-only adapters for the rendered Jinjiang favorites page.

Public selector evidence (not a verification of today's authenticated DOM):
https://greasyfork.org/en/scripts/436147/code -- .favoritelisttable
https://greasyfork.org/en/scripts/436146/code -- a[id^="open"] category toggles
Both original userscripts target my.jjwxc.net/backend/favorite.php.

These scripts inspect visible DOM only. They do not click category controls,
submit forms, read cookies/storage, fetch private APIs, or navigate. A caller
must distinguish an empty visible category from a complete empty bookshelf.
"""

import re
from urllib.parse import parse_qs, urlencode, urlparse


FAVORITES_URL = "https://my.jjwxc.net/backend/favorite.php"
PROFILE_URL = "https://my.jjwxc.net/backend/userinfo.php"
_FAVORITES_HOSTS = {"my.jjwxc.net", "my.jjwxc.com"}
_PAGE_KEYS = ("type", "page")
_MUTATION_KEYS = {"action", "act", "op", "operate", "delete", "del", "remove", "save", "submit"}


def normalize_favorites_url(value=FAVORITES_URL):
    """Return an HTTPS favorites URL containing only numeric view parameters.

    Unknown query values, including session/tracking parameters, are dropped.
    Mutation-shaped links are rejected rather than converted into pagination.
    Additional view keys need live DOM evidence before joining the allowlist.
    """
    if not isinstance(value, str):
        raise ValueError("收藏页面网址无效。")
    try:
        parsed = urlparse(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in _FAVORITES_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80, 443}
            or parsed.path != "/backend/favorite.php"
            or parsed.params
        ):
            raise ValueError()
        query = parse_qs(parsed.query, keep_blank_values=True)
        if _MUTATION_KEYS.intersection(key.lower() for key in query):
            raise ValueError()
        safe = {}
        for key in _PAGE_KEYS:
            if key not in query:
                continue
            values = query[key]
            if len(values) != 1 or not re.fullmatch(r"\d{1,6}", values[0]):
                raise ValueError()
            number = int(values[0])
            if key == "page" and number < 1:
                raise ValueError()
            safe[key] = str(number)
    except ValueError:
        raise ValueError("仅支持晋江收藏页面及其数字分页链接。") from None
    suffix = "?" + urlencode(safe) if safe else ""
    return FAVORITES_URL + suffix


_DOM_HELPERS = r"""
 const readingHosts=new Set(['jjwxc.net','www.jjwxc.net','my.jjwxc.net','m.jjwxc.net','wap.jjwxc.net','jjwxc.com','www.jjwxc.com','my.jjwxc.com','m.jjwxc.com','wap.jjwxc.com']);
 const clean=s=>(s||'').replace(/\s+/g,' ').trim();
 function visible(el){
   if(!el || !el.getClientRects().length)return false;
   for(let n=el;n&&n.nodeType===1;n=n.parentElement){
     const s=getComputedStyle(n);
     if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;
   }
   const r=el.getBoundingClientRect();return r.width>0&&r.height>0;
 }
 function safeURL(value){
   let u;try{u=new URL(value,location.href)}catch{return null}
   if(!['http:','https:'].includes(u.protocol)||u.username||u.password||!['','80','443'].includes(u.port)||!readingHosts.has(u.hostname))return null;
   return u;
 }
 function readingLink(a){
   const raw=a.getAttribute('href');if(!raw)return null;
   const u=safeURL(raw);if(!u)return null;
   let id,chapter=false;
   if(/^\/onebook(?:_vip)?\.php$/.test(u.pathname)){
     const ids=u.searchParams.getAll('novelid');if(ids.length!==1)return null;
     id=ids[0];
     chapter=Number(u.searchParams.get('chapterid')||0)>0||u.pathname.includes('_vip');
   }else{
     const m=u.pathname.match(/^\/(?:book2?|novel)\/(\d{1,12})(?:\/(\d+))?\/?$/);
     if(!m)return null;id=m[1];chapter=!!m[2]&&Number(m[2])>0;
   }
   if(!/^\d{1,12}$/.test(id||'')||Number(id)<1)return null;
   return {id:String(Number(id)),chapter};
 }
 function favoriteURL(value){
   const u=safeURL(value);
   if(!u||!['my.jjwxc.net','my.jjwxc.com'].includes(u.hostname)||u.pathname!=='/backend/favorite.php')return null;
   if([...u.searchParams.keys()].some(k=>['action','act','op','operate','delete','del','remove','save','submit'].includes(k.toLowerCase())))return null;
   const out=new URL('https://my.jjwxc.net/backend/favorite.php');
   for(const key of ['type','page']){
     const values=u.searchParams.getAll(key);if(!values.length)continue;
     if(values.length!==1||!/^\d{1,6}$/.test(values[0])||(key==='page'&&Number(values[0])<1))return null;
     out.searchParams.set(key,String(Number(values[0])));
   }
   return out.href;
 }
 function loadingCategories(){
   // Current favorites DOM shows five preview rows, then populates a separate
   // open_list_<category> table asynchronously. The label changes before that
   // request finishes, so “收起” alone does not mean the category has loaded.
   return [...document.querySelectorAll('a[id^="open_"]')].filter(a=>{
     if(!visible(a)||!/^open_\d+$/.test(a.id)||!/收起/.test(a.innerText))return false;
     const total=a.innerText.match(/该类别有\s*(\d+)\s*条收藏/);
     if(!total||Number(total[1])<=5)return false;
     const target=document.getElementById('open_list_'+a.id.slice(5));
     const row=target?.querySelector('.favoritelisttable tr');
     return !row||!visible(row);
   }).map(a=>({id:a.id,selector:'#'+CSS.escape(a.id)}));
 }
"""


BOOKSHELF_SCRIPT = "() => {" + _DOM_HELPERS + r"""
 const tables=[...document.querySelectorAll('.favoritelisttable')];
 const anchors=tables.length ? tables.flatMap(t=>[...t.querySelectorAll('a[href]')]) : [...document.querySelectorAll('a[href]')];
 const items=new Map();
 const controls=/^(?:阅读|开始阅读|继续阅读|全文阅读|阅读全文|查看全文|最新章节|上一章|下一章|第一章|最后一章|目录|书评|评论|购买|订阅|收藏|删除|移除|详情|查看详情)(?:[>›»→…]+)?$/;
 function authorFor(a){
   const row=a.closest('tr,li,article,.book-item,.bookitem,.book-card,.bookcard,[data-novelid]')||a.parentElement;
   if(!row)return '';
   for(const author of row.querySelectorAll('a[href]')){
     if(!visible(author))continue;
     const u=safeURL(author.getAttribute('href'));
     if(u&&((u.pathname==='/oneauthor.php'&&/^\d+$/.test(u.searchParams.get('authorid')||''))||/^\/author\/\d+\/?$/.test(u.pathname)))return clean(author.innerText);
   }
   const author=[...row.querySelectorAll('[itemprop~="author"],.author,.book-author')].find(visible);
   return author ? clean(author.innerText).replace(/^作者\s*[:：]\s*/,'') : '';
 }
 for(const a of anchors){
   if(!visible(a)||(!tables.length&&a.closest('nav,header,footer,.ad,.advertisement')))continue;
   const link=readingLink(a);if(!link)continue;
   const title=clean(a.innerText);
   if(!title||controls.test(title)||(link.chapter&&/^第[\d零一二三四五六七八九十百千两〇]+[章节回]/.test(title)))continue;
   const score=(link.chapter?0:10)+(a.matches('[itemprop~="name"],.novelname,.bookname')?2:0);
   const item={id:link.id,title,author:authorFor(a),url:`https://www.jjwxc.net/onebook.php?novelid=${link.id}`};
   const old=items.get(link.id);
   if(!old||score>old.score){if(old&&!item.author)item.author=old.item.author;items.set(link.id,{score,item});}
   else if(!old.item.author&&item.author)old.item.author=item.author;
 }
 return [...items.values()].map(entry=>entry.item);
}"""


BOOKSHELF_PAGES_SCRIPT = "() => {" + _DOM_HELPERS + r"""
 const current=favoriteURL(location.href);
 const pages=new Set();
 for(const a of document.querySelectorAll('a[href]')){
   if(!visible(a))continue;
   const url=favoriteURL(a.getAttribute('href'));
   if(url&&url!==current)pages.add(url);
 }
 return [...pages];
}"""


BOOKSHELF_META_SCRIPT = "() => {" + _DOM_HELPERS + r"""
 const tables=[...document.querySelectorAll('.favoritelisttable')];
 const login=/\/(?:login|login2014)\.php$/.test(location.pathname)||(!tables.length&&[...document.querySelectorAll('input[type="password"]')].some(visible));
 const body=document.body?.innerText||'';
 const captcha=/请输入验证码|完成安全验证|访问过于频繁|访问太过频繁|请求过于频繁|请完成验证/.test(body);
 const total=body.match(/(?:文章|小说|作品)?收藏\s*[【［\[]\s*(\d+)\s*[/／]\s*(\d+)\s*[】］\]]/);
 const controls=[...document.querySelectorAll('a[id^="open"]')].filter(visible);
 const collapsed=controls.filter(a=>/展开/.test(a.innerText)&&!/收起/.test(a.innerText)).map(a=>({id:a.id,selector:'#'+CSS.escape(a.id),label:clean(a.innerText)}));
 return {gate:captcha?'captcha':login?'login':null,expected_count:total?Number(total[1]):null,capacity:total?Number(total[2]):null,has_shelf_structure:tables.length>0,visible_tables:tables.filter(visible).length,hidden_tables:tables.filter(t=>!visible(t)).length,category_controls:controls.length,collapsed_controls:collapsed,loading_controls:loadingCategories()};
}"""

BOOKSHELF_READY_SCRIPT = "() => {" + _DOM_HELPERS + "return loadingCategories().length === 0;}"

BOOKSHELF_NICKNAME_SCRIPT = "() => {" + _DOM_HELPERS + r"""
 const element=document.querySelector('#t_user_info .nickname');
 if(!element||!visible(element))return '';
 const candidate=clean(element.innerText);
 // The favorites header may contain an already truncated nickname. Use the
 // regular basic-information page for its full, visible value in that case.
 return !candidate || /客户号|客戶號|客户编号|客戶編號|\.{3}$|…$/.test(candidate) ? '' : candidate.slice(0,60);
}"""

PROFILE_NICKNAME_SCRIPT = "() => {" + _DOM_HELPERS + r"""
 for(const label of document.querySelectorAll('td,th,dt')){
   if(!visible(label)||!['昵称','暱稱'].includes(clean(label.innerText)))continue;
   const value=label.nextElementSibling;
   if(!value||!visible(value)||!['TD','DD'].includes(value.tagName))continue;
   const nickname=clean(value.innerText);
   if(nickname&&nickname.length<=60&&!/客户号|客戶號|客户编号|客戶編號/.test(nickname))return nickname;
 }
 return '';
}"""

# Backward-compatible name for callers interested only in gates/visibility.
BOOKSHELF_STATUS_SCRIPT = BOOKSHELF_META_SCRIPT
