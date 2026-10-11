"""Book information and on-demand chapter reading, separate from downloads."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
from datetime import date
from html.parser import HTMLParser
import threading

from .browser import Reader, ROOT, CaptureError, normalize_url, book_id
from .chapter_versions import ChapterVersions

CLASSIFICATION_VERSION = 2
CHAPTER_METADATA_VERSION = 1
PUBLIC_REQUEST_INTERVAL = 0.4
PUBLIC_REQUEST_LOCK = threading.Lock()
PUBLIC_REQUEST_SLOTS = threading.BoundedSemaphore(3)
PUBLIC_LAST_REQUEST = 0.0
AUTHOR_LOCKS = {}
AUTHOR_LOCKS_LOCK = threading.Lock()


class PublicMetadataInterrupted(Exception):
    def __init__(self, kind):
        self.kind=kind
        super().__init__(kind)


def read_public_source(url):
    import subprocess
    from urllib.parse import urlparse
    from urllib.error import HTTPError, URLError
    global PUBLIC_LAST_REQUEST
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in {'app-cdn.jjwxc.net','wap.jjwxc.net','www.jjwxc.net'} or parsed.port not in (None,443) or parsed.username or parsed.password:raise ValueError('Unsupported public metadata source')
    command=['/usr/bin/curl','--disable','--silent','--show-error','--proto','=https','--no-location','--max-redirs','0','--cacert','/etc/ssl/cert.pem','--max-time','15','--max-filesize',str(2*1024*1024),'--user-agent','Pagefall/0.7','--write-out','\n%{http_code}','--url',url]
    with PUBLIC_REQUEST_SLOTS:
        with PUBLIC_REQUEST_LOCK:
            delay=PUBLIC_LAST_REQUEST+PUBLIC_REQUEST_INTERVAL-time.monotonic()
            if delay>0:time.sleep(delay)
            PUBLIC_LAST_REQUEST=time.monotonic()
        try:response=subprocess.run(command,capture_output=True,timeout=17,check=False)
        except (subprocess.TimeoutExpired,OSError) as error:raise URLError(str(error)) from error
    if response.returncode==63:raise ValueError('Public metadata is too large')
    if response.returncode:raise URLError('curl transport error '+str(response.returncode))
    raw,separator,status=response.stdout.rpartition(b'\n')
    if not separator or not status.isdigit():raise URLError('Missing HTTP status')
    if int(status)!=200:raise HTTPError(url,int(status),'Public metadata unavailable',None,None)
    if len(raw)>2*1024*1024:raise ValueError('Public metadata is too large')
    return raw


def public_failure(error):
    from urllib.error import HTTPError, URLError
    if isinstance(error,HTTPError):
        if error.code==429:raise PublicMetadataInterrupted('rate_limit') from error
        if error.code>=500:raise PublicMetadataInterrupted('network') from error
    elif isinstance(error,(URLError,TimeoutError,ConnectionError)):
        raise PublicMetadataInterrupted('network') from error

DETAILS_SCRIPT = r'''() => {
 const text = (s) => document.querySelector(s)?.innerText?.trim() || '';
 const item = (name) => text('[itemprop~="'+name+'"]');
 const intro = document.querySelector('#novelintro,[itemprop="description"]');
 const clone = intro?.cloneNode(true);
 clone?.querySelectorAll('script,style,iframe').forEach(e=>e.remove());
 const description = intro?.innerText?.trim() || clone?.textContent?.trim() || '';
 const nodes = [...document.querySelectorAll('li,span,div')].filter(e=>e.children.length<3&&!e.closest('#novelintro')&&e.innerText?.length<1200);
 const line = (label) => nodes.map(e=>e.innerText.trim()).find(t=>t.startsWith(label+'：')||t.startsWith(label+':'))?.replace(new RegExp('^'+label+'[：:]\\s*'),'').trim() || '';
 const full = document.body.innerText;
 const perspective = line('作品视角') || line('文章视角') || line('视角') || (full.match(/文章视角[：:]\s*([^\n]+)/)?.[1]||'').trim();
 const tags = [...document.querySelectorAll('.smallreadbody a')].filter(e=>/search|tag/.test(e.href)).map(e=>e.innerText.trim()).filter(Boolean);
 return {description:description.slice(0,50000),genre:item('genre'),status:item('updataStatus'),word_count:item('wordCount').replace(/字.*$/,''),protagonists:line('主角'),supporting:line('配角'),perspective,tagline:line('一句话简介'),theme:line('立意'),tags:[...new Set(tags)].slice(0,30),updated_at:document.querySelector('[itemprop="dateModified"]')?.content||'',source:'jjwxc'};
}'''


class Catalog:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = asyncio.Lock()
        self.details_locks = {}
        self.versions = ChapterVersions(self.directory.parent/'chapter-versions')

    def cached(self, identifier):
        try:
            result=json.loads((self.directory/(identifier+'.json')).read_text())
            return result if isinstance(result,dict) else None
        except (OSError,ValueError):
            return None

    def write(self, path, value):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False));temp.chmod(0o600);temp.replace(path)

    def cached_read(self, identifier, chapter_id):
        path=self.directory.parent/'reading'/identifier/(chapter_id+'.json')
        try:cached=json.loads(path.read_text())
        except (OSError,ValueError):return None
        if isinstance(cached,dict) and cached.get('book_id')==identifier and cached.get('id')==chapter_id and isinstance(cached.get('text'),str) and cached['text'].strip():return cached
        return None

    def details_stale(self, cached, now):
        return now-cached.get('cached_at',0)>=600 or (cached.get('platform','jjwxc')=='jjwxc' and cached.get('chapter_metadata_version')!=CHAPTER_METADATA_VERSION)

    async def fetch(self, url, refresh=False):
        url=normalize_url(url);identifier=book_id(url)
        requested_at=time.time()
        cached=self.cached(identifier)
        if cached and cached.get('schema')==2 and not refresh:
            return {**cached,'details_stale':self.details_stale(cached,requested_at)}
        async with self.details_locks.setdefault(identifier,asyncio.Lock()):
            cached=self.cached(identifier)
            if cached and cached.get('schema')==2 and (not refresh or cached.get('cached_at',0)>=requested_at):
                return {**cached,'details_stale':self.details_stale(cached,time.time())}
            with tempfile.TemporaryDirectory(prefix='shiye-catalog-') as temporary:
                from .platforms import platform_for
                reader=Reader(Path(temporary)/'browser',hydrate_session=False)
                metadata_task=asyncio.create_task(asyncio.to_thread(details_app_metadata,identifier)) if platform_for(url).id=='jjwxc' else None
                try:
                    result=await reader.scan(url)
                    if result.get('id')!=identifier:raise CaptureError('作品页面跳转到了其他书籍，请重新打开。','blocked',False)
                    if result.get('platform')=='jjwxc':
                        result['metadata']=await reader.page.evaluate(DETAILS_SCRIPT)
                        extra=await metadata_task
                        result['metadata'].update({k:v for k,v in extra.items()if v})
                    if result.get('platform')=='jjwxc':
                        summaries=await reader.page.evaluate(TOC_SUMMARIES_SCRIPT)
                        for chapter in result['chapters']:
                            chapter['summary']=summaries.get(chapter['id'],'')
                        result['chapter_metadata_version']=CHAPTER_METADATA_VERSION
                    result['schema']=2
                    result['cached_at']=time.time()
                    self.write(self.directory/(identifier+'.json'),result)
                    return {**result,'details_stale':False}
                finally:
                    if metadata_task and not metadata_task.done():
                        metadata_task.cancel()
                        await asyncio.gather(metadata_task,return_exceptions=True)
                    await reader.close(remember=False)

    async def read(self, book, chapter_id, refresh=False, previous_source_hash=None):
        chapter=next((c for c in book['chapters'] if c['id']==chapter_id),None)
        if not chapter:raise ValueError('章节不存在。')
        chapter={**chapter,'book_url':book['url']}
        path=self.directory.parent/'reading'/book['id']/(chapter_id+'.json')
        async with self.lock:
            cached=self.cached_read(book['id'],chapter_id)
            if cached:
                self.versions.record(book['id'],cached,kind='existing')
                if not refresh:return cached
            if chapter.get('locked'):raise CaptureError('本章已锁定，暂时无法更新。' if refresh else '目录标注本章已锁定，当前无法阅读。','blocked',False)
            with tempfile.TemporaryDirectory(prefix='shiye-reading-') as temporary:
                reader=Reader(Path(temporary)/'browser')
                try:
                    result=await reader.capture(chapter,Path(temporary)/'source',mode='auto')
                    if result.get('incomplete'):raise CaptureError('本章正文可能不完整，请稍后重试。','failed',False)
                    value={'id':chapter_id,'book_id':book['id'],'title':chapter['title'],'url':chapter['url'],'text':result['text'],'method':result['method'],'source_updated_at':chapter.get('source_updated_at')}
                    import hashlib
                    if previous_source_hash is None or hashlib.sha256(value['text'].encode()).hexdigest()!=previous_source_hash:
                        self.versions.record(book['id'],value,kind='source')
                    self.write(path,value)
                    return value
                finally:await reader.close(remember=False)

    async def watch_fetch(self, book):
        from .platforms import platform_for
        from .chapter_catalog import parse_directory
        url = normalize_url(book['url'])
        if platform_for(url).id != 'jjwxc':
            return await self.fetch(url, refresh=True)
        identifier = book_id(url)
        raw = await asyncio.to_thread(read_public_source, url)
        result = parse_directory(raw, identifier)
        async with self.details_locks.setdefault(identifier, asyncio.Lock()):
            cached = self.cached(identifier) or {}
            result = {**book, **cached, **result, 'chapter_metadata_version': CHAPTER_METADATA_VERSION,
                      'catalog_checked_at': time.time()}
            # A directory check does not refresh synopsis or other book metadata.
            self.write(self.directory/(identifier+'.json'), result)
        return result


def details_app_metadata(identifier):
    try:return public_app_basic_metadata(identifier)
    except PublicMetadataInterrupted:return {}


def public_app_basic_metadata(identifier):
    """Basic public fields are independent of publication-date availability."""
    import re
    if not re.fullmatch(r'\d{1,12}',identifier):return {}
    try:
        url='https://app-cdn.jjwxc.net/androidapi/novelbasicinfo?novelId='+identifier+'&versionCode=402'
        value=json.loads(read_public_source(url))
        result=parse_app_metadata(value,identifier)
        if not result:return {}
        author=str(value.get('authorId',''))
        result['author_id']=author if re.fullmatch(r'\d{1,12}',author) else None
        result['basic_checked']=True
        return result
    except Exception as error:
        public_failure(error)
        return {}


def public_app_metadata(identifier, directory=None):
    try:
        result=public_app_basic_metadata(identifier)
    except PublicMetadataInterrupted:return {}
    if not result:return {}
    author=result['author_id']
    try:
        publications=public_author_publications(author,directory,identifier) if author else {}
    except PublicMetadataInterrupted:return result
    if publications is not None:
        if publications.get(identifier):result['classification']['published_at']=publications[identifier]
        result['classification_version']=CLASSIFICATION_VERSION
    return result


def platform_date(value):
    import re
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?',value.strip()):return None
    try:return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:return None


def parse_author_publications(html, author):
    """Match each explicit publication-date cell to its own book, not updates."""
    import re
    class Publications(HTMLParser):
        def __init__(self):
            super().__init__();self.current=None;self.depth=0;self.cell=None;self.result={}
        def handle_starttag(self,tag,attrs):
            attributes=dict(attrs)
            if tag=='table':
                if self.current is not None:self.depth+=1
                elif {'author','novel'}.issubset(set(attributes.get('class','').split())):
                    self.current={};self.depth=1
            if self.current is None:return
            if tag=='a':
                match=re.fullmatch(r'jump_book2\(\s*(\d+)\s*,\s*(\d+)\s*\);?',attributes.get('onclick','').strip())
                if match and match[2]==author:self.current['id']=match[1]
            if tag=='td':self.cell=[]
        def handle_data(self,data):
            if self.cell is not None:self.cell.append(data)
        def handle_endtag(self,tag):
            if self.current is None:return
            if tag=='td' and self.cell is not None:
                match=re.fullmatch(r'发表时间[：:]\s*(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?)', ''.join(self.cell).strip())
                if match and platform_date(match[1]):self.current['published_at']=platform_date(match[1])
                self.cell=None
            if tag=='table':
                self.depth-=1
                if self.depth==0:
                    if self.current.get('id'):self.result[self.current['id']]=self.current.get('published_at')
                    self.current=None;self.cell=None
    parser=Publications();parser.feed(html);return parser.result


def public_author_publications(author, directory=None, identifier=None):
    directory=Path(directory) if directory is not None else ROOT/'.local'/'author-publications'
    path=directory/(author+'.json')
    with AUTHOR_LOCKS_LOCK:lock=AUTHOR_LOCKS.setdefault(str(path.resolve()),threading.Lock())
    with lock:
        try:
            cached=json.loads(path.read_text())
            if cached.get('author_id')==author and isinstance(cached.get('dates'),dict) and (identifier is None or identifier in cached['dates']):return cached['dates']
        except (OSError,ValueError,AttributeError):pass
        try:
            raw=read_public_source('https://wap.jjwxc.net/wapauthor/'+author)
            result=parse_author_publications(raw.decode('gb18030','replace'),author)
            if identifier is not None:result.setdefault(identifier,None)
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps({'author_id':author,'dates':result},ensure_ascii=False));temporary.chmod(0o600);temporary.replace(path)
            return result
        except Exception as error:
            public_failure(error)
            return None


def parse_app_metadata(value, identifier):
    from .metadata import normalize_cover_url
    if not isinstance(value,dict)or str(value.get('novelId'))!=identifier:return {}
    def text(key,limit=1000):
        value_text=value.get(key,'')
        return str(value_text)[:limit] if isinstance(value_text,(str,int)) else ''
    characters=[]
    raw_characters=value.get('characters')
    for item in (raw_characters if isinstance(raw_characters,list) else [])[:50]:
        if not isinstance(item,dict)or str(item.get('novelid'))!=identifier or str(item.get('character_masked'))!='0':continue
        picture=None
        try:picture=normalize_cover_url(item.get('character_pic',''))
        except ValueError:pass
        characters.append({'id':str(item.get('character_id',''))[:40],'name':str(item.get('character_name',''))[:200],'picture':picture})
    ids={c['id']for c in characters}
    relationships=[]
    raw_relations=value.get('character_relations')
    for item in (raw_relations if isinstance(raw_relations,list) else [])[:50]:
        if isinstance(item,dict)and str(item.get('start'))in ids and str(item.get('end'))in ids and item.get('direction')in ('left_to_right','right_to_left','both','both_way','bidirectional'):
            relationships.append({'start':str(item['start']),'end':str(item['end']),'direction':item['direction']})
    classification=parse_classification(text('novelClass'),text('mainview'),{'1':'连载','2':'完结'}.get(text('novelStep'),''),text('novelSize'))
    favorites=text('novelbefavoritedcount').replace(',','').replace('，','').strip()
    if favorites.isascii() and favorites.isdecimal():classification['favorite_count']=int(favorites)
    return {'protagonists':text('protagonist').removeprefix('主角：').strip(),'supporting':text('costar').removeprefix('配角：').strip(),'perspective':text('mainview'),'tags':[tag.strip()for tag in text('novelTags').split(',')if tag.strip()],'word_count':text('novelSize'),'characters':characters,'relationships':relationships,'classification':classification}


def parse_classification(novel_class='', perspective='', status='', word_count=None):
    """The platform's four-part class and explicit viewpoint, without guessing."""
    import re
    def label(value):
        value=value.strip() if isinstance(value,str) else ''
        return value if re.fullmatch(r'[\u4e00-\u9fff·]+|无CP',value) and value not in {'未知','不明','不限','无'} else None
    result={}
    parts=novel_class.split('-') if isinstance(novel_class,str) else []
    if len(parts)>=4:
        for field,value in zip(('originality','orientation','era','genre'),parts):
            if label(value):result[field]=label(value)
        if not perspective and len(parts)>=5:perspective=parts[4]
    if perspective=='不明' or label(perspective):result['perspective']=perspective if perspective=='不明' else label(perspective)
    statuses={'连载':'连载','连载中':'连载','完结':'完结','已完结':'完结','暂停':'暂停','暂停中':'暂停'}
    if isinstance(status,str) and status in statuses:result['status']=statuses[status]
    if isinstance(word_count,(str,int)) and not isinstance(word_count,bool):
        number=re.sub(r'[,，\s]|字$','',str(word_count))
        if re.fullmatch(r'\d+',number) and int(number)>0:result['word_count']=int(number)
    return result


def metadata_classification(metadata):
    if not isinstance(metadata,dict):return None
    if isinstance(metadata.get('classification'),dict):
        fields={'orientation','perspective','originality','era','genre','status'}
        return {key:value for key,value in metadata['classification'].items() if (key in fields and isinstance(value,str) and value) or (key=='word_count' and isinstance(value,int) and not isinstance(value,bool) and value>0) or (key=='favorite_count' and isinstance(value,int) and not isinstance(value,bool) and value>=0) or (key=='published_at' and isinstance(value,str) and platform_date(value)==value)}
    if metadata.get('source')=='jjwxc' and metadata.get('genre'):
        return parse_classification(metadata['genre'],metadata.get('perspective',''),metadata.get('status',''),metadata.get('word_count'))
    return None


TOC_SUMMARIES_SCRIPT = r"""() => Object.fromEntries([...document.querySelectorAll('#oneboolt tr[itemprop~="chapter"]')].map(row=>[row.cells[0]?.innerText?.trim(),row.cells[2]?.innerText?.trim().slice(0,4000)||'']).filter(([id])=>/^\d+$/.test(id||'')))"""
