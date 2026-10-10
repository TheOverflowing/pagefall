"""Pinned upstream Jinjiang parser with a host-owned read-only network bridge."""
import asyncio
import base64
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / 'engine/dist/novelist-jinjiang.js'
HELPER = ROOT / '.build/api-key-store'
token_expired = False

MODEL_URL = 'https://github.com/xushengfeng/eSearch-OCR/releases/download/4.0.0/ppocr_v5_mobile.zip'


def token_helper(command, token=None):
    if not HELPER.is_file():
        if command == 'has':
            return {'has_key': False}
        raise ValueError('晋江连接组件尚未准备好，请重新构建拾页。')
    payload = json.dumps({'key': token}).encode() if token is not None else None
    try:
        result = subprocess.run([str(HELPER), command, 'jjwxc'], input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode:
            raise ValueError()
        return json.loads(result.stdout) if command == 'has' else result.stdout.decode()
    except Exception:
        raise ValueError('晋江 Token 钥匙串操作未完成，请稍后重试。') from None


def has_token():
    return token_helper('has').get('has_key') is True


def save_token(value=None, delete=False):
    global token_expired
    if delete:
        token_helper('delete')
        token_expired = False
    elif value is not None and value != '':
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]+_[A-Za-z0-9_]{16,4000}', value.strip()):
            raise ValueError('请输入有效的晋江 Android Token。')
        token_helper('set', value.strip())
        token_expired = False
    return {'ok': True, 'has_token': has_token()}


def request_target(value, chapter_url, token):
    """Only the current chapter, its font and the exact public OCR model URL."""
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError('Invalid URL')
    url = urlsplit(value)
    current = urlsplit(chapter_url)
    if url.scheme != 'https' or url.username or url.password or url.port or url.fragment:
        raise ValueError('Invalid URL')
    if value == MODEL_URL:
        return value, 'model'
    if url.hostname == 'static.jjwxc.net' and re.fullmatch(r'/tmp/fonts/[A-Za-z0-9_-]+\.woff2', url.path) and url.query == 'h=my.jjwxc.net':
        return value, 'font'
    expected = parse_qs(current.query)
    params = parse_qs(url.query)
    if url.hostname == 'app.jjwxc.net' and url.path == '/androidapi/chapterContent' and token:
        if set(params) != {'novelId', 'chapterId', 'versionCode', 'token'} or params.get('novelId') != expected.get('novelid') or params.get('chapterId') != expected.get('chapterid') or params.get('token') != ['1_NOVELIST_OPAQUE_TOKEN'] or params.get('versionCode') != ['381']:
            raise ValueError('Wrong chapter')
        params['token'] = [token]
        return urlunsplit((url.scheme,url.netloc,url.path,urlencode(params,doseq=True),'')), 'api'
    if url.hostname in {'www.jjwxc.net','my.jjwxc.net'} and url.hostname == current.hostname and url.path == current.path and params == expected:
        return value, 'web'
    raise ValueError('Outside current chapter')


async def install(reader):
    if not BUNDLE.is_file():
        return False
    if not getattr(reader, '_jinjiang_binding', False):
        async def request(source, data):
            try:
                if source.get('page') is not reader.page or source.get('frame') != reader.page.main_frame:
                    return {'ok': False}
                scope = getattr(reader, '_jinjiang_scope', None)
                if not scope or reader.page.url != scope['page_url'] or urlsplit(reader.page.url).hostname not in {'www.jjwxc.net','my.jjwxc.net','wap.jjwxc.net','m.jjwxc.net'}:
                    return {'ok': False}
                scope['requests'] += 1
                if scope['requests'] > 16:
                    return {'ok': False}
                url, kind = request_target(data.get('url'), scope['url'], scope['token'])
                if kind == 'model':
                    cache = ROOT / '.local/ocr-models/ppocr_v5_mobile.zip'
                    if cache.is_file():
                        content = await asyncio.to_thread(cache.read_bytes)
                        return {'ok': True, 'status': 200, 'body': base64.b64encode(content).decode(), 'headers': ''}
                headers = {'Referer': scope['url']}
                if kind == 'api':
                    headers = {'Referer':'http://android.jjwxc.net?v=381','User-Agent':'Mozilla/5.0 (Linux; Android 16) JINJIANG-Android/381'}
                # Release assets are public, with no Jinjiang cookies or credentials.
                # The model redirect is handled separately and never for account requests.
                if kind == 'model':
                    content = await asyncio.to_thread(download_model)
                    return {'ok': True, 'status': 200, 'body': base64.b64encode(content).decode(), 'headers': ''}
                response = await reader.context.request.get(url, headers=headers, max_redirects=0, timeout=30000)
                try:
                    if response.status != 200 or int(response.headers.get('content-length','0')) > 8*1024*1024:
                        return {'ok': False}
                    content = await response.body()
                    if kind == 'api':
                        mark_expired(content)
                    if len(content) > 8*1024*1024:
                        return {'ok': False}
                    safe_headers = '\n'.join(k+':'+v for k,v in response.headers.items() if k.lower() in {'accesskey','keystring'})
                    return {'ok': True, 'status': 200, 'body': base64.b64encode(content).decode(), 'headers': safe_headers}
                finally:
                    await response.dispose()
            except Exception:
                return {'ok': False}
        await reader.context.expose_binding('__novelistJinjiangRequest', request)
        reader._jinjiang_binding = True
    if not await reader.page.evaluate('Boolean(window.NovelistJinjiang)'):
        await reader.page.add_script_tag(path=str(BUNDLE))
    return True


def download_model():
    import ssl
    from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
    class ModelRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urlsplit(newurl)
            if target.scheme != 'https' or target.hostname != 'release-assets.githubusercontent.com' or target.username or target.password or target.port:
                raise ValueError('Model redirect')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')
    with build_opener(ModelRedirect(),HTTPSHandler(context=context)).open(Request(MODEL_URL),timeout=45) as response:
        content=response.read(64*1024*1024+1)
    if len(content)>64*1024*1024 or not content.startswith(b'PK'):
        raise ValueError('Invalid model archive')
    cache=ROOT/'.local/ocr-models/ppocr_v5_mobile.zip'
    cache.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=cache.with_suffix('.tmp')
    temporary.write_bytes(content)
    temporary.chmod(0o600)
    temporary.replace(cache)
    return content


async def invoke(reader, method, chapter=None):
    token = ''
    try:
        try:
            if not token_expired and await asyncio.to_thread(has_token):
                token = await asyncio.to_thread(token_helper,'get')
        except ValueError:
            # Optional app credentials must not disable webpage parsing.
            token = ''
        if not await install(reader):
            return None
        reader._jinjiang_scope = {'url':chapter['url'] if chapter else reader.page.url,'page_url':reader.page.url,'token':token,'requests':0}
        args = {'has_token':bool(token),'logged_in':True}
        if chapter:
            args.update(url=chapter['url'],title=chapter.get('title',''),vip=bool(chapter.get('vip')))
        result = await asyncio.wait_for(reader.page.evaluate('async ({method,args}) => window.NovelistJinjiang[method](args)',{'method':method,'args':args}),timeout=180)
        if not isinstance(result,dict) or result.get('ok') is not True:
            return None
        return result
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
    finally:
        reader._jinjiang_scope = None


async def read(reader, chapter):
    result = await invoke(reader,'read',chapter)
    if not result:
        return None
    text=result.get('text')
    if not isinstance(text,str) or not 20 <= len(text.strip()) <= 2_000_000 or re.search(r'[\uE000-\uF8FF\uFFFD\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]',text):
        return None
    if re.search(r'请先登[录陆]|您尚未购买|您还没有购买|本章尚未购买|当前订阅比例不足|防盗时间未过|请输入验证码|请完成验证',text):
        return None
    expected=chapter.get('expected_words') or 0
    if expected and len(re.sub(r'\s','',text)) < expected*0.8:
        return None
    return {'text':text.strip(),'raw_text':text.strip(),'method':result['method'],'review':True,'incomplete':False,'screenshots':[],'error':'晋江原文已读取，请确认内容与完整性。','capture_strategy':'upstream-first','capture_details':{'requests':result.get('requests',0),'font_mapped':result.get('font_mapped',0)}}


def mark_expired(content):
    global token_expired
    try:
        result = json.loads(content) if len(content)<=65536 else {}
        if not isinstance(result,dict) or result.get('content'):
            return
        message = str(result.get('message',''))[:500]
        if re.search(r'请(?:先|重新)登[录陆]|登录(?:已)?(?:过期|失效)|token.{0,12}(?:过期|失效|无效)',message,re.I):
            token_expired = True
    except (ValueError,TypeError):
        pass
