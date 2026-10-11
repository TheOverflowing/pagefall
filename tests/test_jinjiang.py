"""Actual upstream parser fixtures; no live accounts, tokens or API requests."""
import asyncio
import base64
import copy
import json
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright
from novelist import jinjiang
from novelist.browser import Reader, CaptureError

URL = 'https://www.jjwxc.net/onebook.php?novelid=42&chapterid=1'
TEXT = '小岚在雨后的书店等小溪。窗外的灯光亮了，她把那封尚未拆开的信放回口袋。'


class NetworkBoundaryTests(unittest.TestCase):
    def test_only_current_chapter_and_font_are_allowed(self):
        self.assertEqual(jinjiang.request_target(URL,URL,''),(URL,'web'))
        font='https://static.jjwxc.net/tmp/fonts/fixture_1.woff2?h=my.jjwxc.net'
        self.assertEqual(jinjiang.request_target(font,URL,'')[1],'font')
        for bad in ('https://www.jjwxc.net/onebook.php?novelid=42&chapterid=2',
                    'https://www.jjwxc.net/purchase.php?novelid=42',
                    'https://my.jjwxc.net/onebook.php?novelid=42&chapterid=1',
                    'http://www.jjwxc.net/onebook.php?novelid=42&chapterid=1',
                    'https://www.jjwxc.net:443/onebook.php?novelid=42&chapterid=1',
                    'https://user@www.jjwxc.net/onebook.php?novelid=42&chapterid=1',
                    'https://static.jjwxc.net/tmp/fonts/fixture.woff2?h=evil',
                    'https://evil.invalid/fixture.woff2', 'https://app.jjwxc.net/androidapi/login'):
            with self.subTest(url=bad),self.assertRaises(ValueError):jinjiang.request_target(bad,URL,'')

    def test_app_token_is_replaced_only_inside_host_for_the_current_chapter(self):
        url='https://app.jjwxc.net/androidapi/chapterContent?novelId=42&chapterId=1&versionCode=381&token=1_NOVELIST_OPAQUE_TOKEN'
        target,kind=jinjiang.request_target(url,URL,'fixture-secret-never-on-page')
        self.assertEqual(kind,'api')
        self.assertIn('fixture-secret-never-on-page',target)
        self.assertNotIn('OPAQUE',target)
        for bad in (url.replace('chapterId=1','chapterId=2'),url.replace('novelId=42','novelId=43'),url+'&buy=1',url.replace('1_NOVELIST_OPAQUE_TOKEN','fake')):
            with self.assertRaises(ValueError):jinjiang.request_target(bad,URL,'fixture-secret')
        with self.assertRaises(ValueError):jinjiang.request_target(url,URL,'')

    def test_invalid_token_cannot_reach_keychain_or_be_echoed(self):
        with patch.object(jinjiang,'token_helper') as helper:
            for token in ('not-a-token','42_short',[],42,'42_'+'a'*16+'\nsecret'):
                with self.assertRaises(ValueError) as error:jinjiang.save_token(token)
                self.assertNotIn('secret',str(error.exception))
            helper.assert_not_called()


class JinjiangRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_directory_scan_never_reads_keychain_credentials(self):
        reader = SimpleNamespace(
            page=SimpleNamespace(url=URL, evaluate=AsyncMock(return_value={'ok':True,'chapters':[]})),
        )
        with patch.object(jinjiang,'install',AsyncMock(return_value=True)), \
             patch.object(jinjiang,'has_token') as presence, \
             patch.object(jinjiang,'token_helper') as helper:
            result = await jinjiang.invoke(reader,'scan')
        self.assertTrue(result['ok'])
        self.assertFalse(reader.page.evaluate.call_args.args[1]['args']['has_token'])
        presence.assert_not_called()
        helper.assert_not_called()
        self.assertIsNone(reader._jinjiang_scope)

    async def test_presence_endpoint_never_returns_token_value(self):
        from novelist import server
        from test_translation_integration import http_json
        with patch.object(jinjiang,'has_token',return_value=True),patch.object(jinjiang,'token_helper') as helper:
            status,headers,data=await http_json('/api/jinjiang/settings')
        self.assertEqual(status,200)
        self.assertEqual(data,{'has_token':True})
        self.assertEqual(headers[b'cache-control'],b'no-store')
        helper.assert_not_called()

    async def test_settings_reject_invalid_boolean_and_cross_origin_before_keychain(self):
        from test_translation_integration import http_json
        with patch.object(jinjiang,'save_token') as save:
            status,_,_=await http_json('/api/jinjiang/settings',method='POST',data={'delete_token':'true'})
            self.assertEqual(status,400)
            status,_,_=await http_json('/api/jinjiang/settings',method='POST',data={'token':'fixture'},origin='https://evil.invalid')
            self.assertEqual(status,403)
            save.assert_not_called()


class UpstreamParserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(headless=True)
        self.context=await self.browser.new_context()
        self.page=await self.context.new_page()
        self.html=f'<html><body><div class="novelbody"><div><div id="paragraph_comment_content">{TEXT}</div></div></div></body></html>'
        async def route(route):await route.fulfill(status=200,content_type='text/html; charset=utf-8',body=self.html)
        await self.page.route('**/*',route)
        await self.page.goto(URL)
        self.reader=Reader(Path(self.temp.name)/'unused')
        self.reader.context=self.context
        self.reader.page=self.page
        self.reader.ensure=AsyncMock()
        self.chapter={'id':'1','title':'灯光','url':URL,'vip':False,'expected_words':0}
        self.calls=[]
        async def fetch(url,**kwargs):
            self.calls.append((url,kwargs))
            return SimpleNamespace(status=200,headers={},body=AsyncMock(return_value=self.html.encode('gb18030')),dispose=AsyncMock())
        self.fetch=patch.object(self.context.request,'get',side_effect=fetch)
        self.fetch.start()
        self.keys=patch.object(jinjiang,'has_token',return_value=False)
        self.keys.start()

    async def asyncTearDown(self):
        self.fetch.stop();self.keys.stop()
        await self.browser.close();await self.pw.stop()

    async def test_actual_public_parser_reads_text_and_skips_screenshot_ocr(self):
        original=copy.deepcopy(self.chapter)
        self.reader.goto=AsyncMock()
        with patch('novelist.browser.recognize_image',side_effect=AssertionError('OCR called')):
            result=await self.reader.capture(self.chapter,Path(self.temp.name)/'capture',mode='auto')
        self.assertEqual(result['text'],TEXT)
        self.assertEqual(result['method'],'jjwxc_upstream')
        self.assertEqual(result['screenshots'],[])
        self.assertEqual(self.chapter,original)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0][1]['max_redirects'],0)

    async def test_blank_reader_bootstraps_locally_then_fetches_only_requested_chapter(self):
        await self.page.goto('about:blank')
        self.reader.goto=AsyncMock(side_effect=AssertionError('Unnecessary rendered page navigation'))
        result=await self.reader.capture(self.chapter,Path(self.temp.name)/'capture',mode='auto')
        self.assertEqual(result['text'],TEXT)
        self.assertEqual(result['method'],'jjwxc_upstream')
        self.assertEqual(await self.page.locator('body').inner_text(),'')
        self.assertEqual([call[0] for call in self.calls],[URL])
        self.assertEqual(self.page.url,'https://www.jjwxc.net/onebook.php?novelid=42')
        self.assertIsNone(self.reader._jinjiang_scope)

    async def test_parser_failure_after_local_bootstrap_still_renders_real_page_for_ocr(self):
        await self.page.goto('about:blank')
        self.reader.hydrate_session=False
        self.html=f'<div id="paragraph_comment_content" style="width:700px;line-height:35px">{TEXT}</div>'
        with patch.object(jinjiang,'read',return_value=None),patch('novelist.browser.recognize_image',return_value=(TEXT,[])) as ocr:
            result=await self.reader.capture({**self.chapter,'vip':True},Path(self.temp.name)/'fallback',mode='auto')
        self.assertEqual(self.page.url,URL)
        self.assertEqual(await self.page.locator('#paragraph_comment_content').inner_text(),TEXT)
        self.assertEqual(result['method'],'ocr');ocr.assert_awaited_once()

    async def test_scan_after_local_bootstrap_loads_actual_directory(self):
        await self.page.goto('about:blank')
        self.reader.hydrate_session=False
        await self.reader._prepare_jinjiang(self.chapter)
        self.html='<table><tr><td><a href="'+URL+'">第一章</a></td><td>35</td><td>2026-10-10 10:00:00</td></tr></table>'
        with patch.object(jinjiang,'invoke',return_value=None),patch('novelist.catalog.read_public_source',side_effect=ValueError('Fixture uses rendered fallback')):
            result=await self.reader.scan('https://www.jjwxc.net/onebook.php?novelid=42')
        self.assertEqual([chapter['id'] for chapter in result['chapters']],['1'])
        self.assertEqual(await self.page.locator('a').inner_text(),'第一章')

    async def test_actual_api_parser_uses_private_host_token_and_returns_text(self):
        secret='42_'+'x'*20
        async def api(url,**kwargs):
            self.calls.append((url,kwargs))
            return SimpleNamespace(status=200,headers={},body=AsyncMock(return_value=json.dumps({'chapterId':1,'content':TEXT,'chapterName':'灯光'}).encode()),dispose=AsyncMock())
        with patch.object(jinjiang,'has_token',return_value=True),patch.object(jinjiang,'token_helper',return_value=secret),patch.object(self.context.request,'get',side_effect=api):
            result=await jinjiang.read(self.reader,self.chapter)
        self.assertEqual(result['method'],'jjwxc_api')
        self.assertEqual(result['text'],TEXT)
        self.assertEqual(len(self.calls),1)
        self.assertIn(secret,self.calls[0][0])
        self.assertNotIn(secret,json.dumps(await self.page.evaluate('({tokenOptions:window.tokenOptions, unsafeWindow:window.unsafeWindow,bridge:Object.keys(window.NovelistJinjiang)})')))
        self.assertIsNone(self.reader._jinjiang_scope)

    async def test_actual_vip_aes_decoder_handles_both_current_variants(self):
        await self.context.add_cookies([{'name':'readerid','value':'73','domain':'www.jjwxc.net','path':'/'}])
        javascript = r'''
const C=require('crypto-js');let input='';process.stdin.on('data',b=>input+=b);process.stdin.on('end',()=>{
const {text,chapterid}=JSON.parse(input),novelid=42,readerid=73,accessKey='fixture-access';
const n=[...accessKey].reduce((a,c)=>a+c.charCodeAt(0),0);
const base=(novelid+chapterid)%2 ? `${accessKey}-${novelid}-${chapterid}-${readerid}` : `${novelid}.${chapterid}.${readerid}.${accessKey}`;
const h=C.MD5(base).toString(),sp=n%((novelid+chapterid)%2 ? h.length+1 : h.length),rot=h.slice(sp)+h.slice(0,sp);
const encrypt=(value,key,iv)=>C.AES.encrypt(value,C.enc.Utf8.parse(key),{iv:C.enc.Utf8.parse(iv)}).toString();
const time=Math.floor(Date.now()/1000),secret='fixture-content-key',meta={time,key:secret,ver:'fixture'};
const cryptInfo=encrypt(Buffer.from(JSON.stringify(meta)).toString('base64'),rot.slice(-16),rot.slice(0,16));
const sum=C.MD5(secret+time+readerid).toString(),pos=n%sum.length,t=sum.slice(pos)+sum.slice(0,pos);
const content=encrypt('<p>'+text+'</p>',t.slice(0,16),t.slice(-16));
process.stdout.write(JSON.stringify({novelid,chapterid,accessKey,cryptInfo,content}));});
'''
        for number in (1,2):
            with self.subTest(variant=number):
                data=json.loads(subprocess.check_output(['node','-e',javascript],input=json.dumps({'text':TEXT,'chapterid':number}).encode()))
                from html import escape
                self.html='<div class="noveltext"><h2>灯光</h2></div><div id="contentvars">'+''.join(f'<input name="{k}" value="{escape(str(v),quote=True)}">' for k,v in data.items())+'</div>'
                chapter={**self.chapter,'url':URL.replace('chapterid=1',f'chapterid={number}'),'id':str(number),'vip':True}
                result=await jinjiang.read(self.reader,chapter)
                self.assertIsNotNone(result)
                self.assertEqual(result['text'],TEXT)
                self.assertEqual(result['method'],'jjwxc_upstream')

    async def test_wrong_api_chapter_is_rejected_and_web_fallback_used(self):
        async def fetch(url,**kwargs):
            body=json.dumps({'chapterId':99,'content':'错误章节。'*20}).encode() if '/androidapi/' in url else self.html.encode('gb18030')
            return SimpleNamespace(status=200,headers={},body=AsyncMock(return_value=body),dispose=AsyncMock())
        with patch.object(jinjiang,'has_token',return_value=True),patch.object(jinjiang,'token_helper',return_value='42_'+'x'*20),patch.object(self.context.request,'get',side_effect=fetch):
            result=await jinjiang.read(self.reader,self.chapter)
        self.assertEqual(result['text'],TEXT)
        self.assertEqual(result['method'],'jjwxc_upstream')

    async def test_api_can_read_when_webpage_is_on_login_page(self):
        await self.page.goto('https://www.jjwxc.net/login.php')
        async def fetch(url,**kwargs):
            return SimpleNamespace(status=200,headers={},body=AsyncMock(return_value=json.dumps({'chapterId':1,'content':TEXT}).encode()),dispose=AsyncMock())
        with patch.object(jinjiang,'has_token',return_value=True),patch.object(jinjiang,'token_helper',return_value='42_'+'x'*20),patch.object(self.context.request,'get',side_effect=fetch):
            result=await jinjiang.read(self.reader,self.chapter)
        self.assertEqual(result['method'],'jjwxc_api')
        self.assertEqual(result['text'],TEXT)

    async def test_api_error_falls_back_to_actual_web_parser(self):
        async def fetch(url,**kwargs):
            self.calls.append(url)
            body=json.dumps({'code':1,'message':'not readable'}).encode() if '/androidapi/' in url else self.html.encode('gb18030')
            return SimpleNamespace(status=200,headers={},body=AsyncMock(return_value=body),dispose=AsyncMock())
        with patch.object(jinjiang,'has_token',return_value=True),patch.object(jinjiang,'token_helper',return_value='42_'+'x'*20),patch.object(self.context.request,'get',side_effect=fetch):
            result=await jinjiang.read(self.reader,self.chapter)
        self.assertEqual(result['method'],'jjwxc_upstream')
        self.assertEqual(result['text'],TEXT)
        self.assertEqual(len(self.calls),2)

    async def test_failed_primary_falls_back_to_ocr_without_changing_source(self):
        self.reader.goto=AsyncMock()
        self.html=f'<div id="paragraph_comment_content" style="width:700px;line-height:35px">{TEXT}</div>'
        await self.page.set_content(self.html)
        with patch.object(jinjiang,'read',return_value=None),patch('novelist.browser.recognize_image',return_value=(TEXT,[])) as ocr:
            result=await self.reader.capture({**self.chapter,'vip':True},Path(self.temp.name)/'fallback',mode='auto')
        self.assertEqual(result['method'],'ocr')
        self.assertEqual(result['capture_strategy'],'upstream-fallback')
        self.assertTrue(Path(result['screenshots'][0]).is_file())
        ocr.assert_awaited_once()

    async def test_login_gate_is_not_ocr_text_even_after_parser_failure(self):
        self.reader.goto=AsyncMock()
        await self.page.set_content('<div class="noveltext">请先登录后再阅读正文。</div>')
        with patch.object(jinjiang,'read',return_value=None),patch('novelist.browser.recognize_image') as ocr:
            with self.assertRaises(CaptureError) as error:await self.reader.capture(self.chapter,Path(self.temp.name)/'blocked',mode='auto')
        self.assertTrue(error.exception.pause)
        ocr.assert_not_called()

    async def test_unmapped_glyph_short_body_and_gate_text_are_not_success(self):
        for text in ('太短','\ue001'+TEXT,'请先登录后再阅读正文。'+TEXT):
            with patch.object(jinjiang,'invoke',return_value={'ok':True,'text':text,'method':'jjwxc_upstream'}):
                self.assertIsNone(await jinjiang.read(self.reader,self.chapter))
        with patch.object(jinjiang,'invoke',return_value={'ok':True,'text':TEXT,'method':'jjwxc_upstream'}):
            self.assertIsNone(await jinjiang.read(self.reader,{**self.chapter,'expected_words':1000}))

    async def test_read_only_binding_is_closed_after_success(self):
        await jinjiang.read(self.reader,self.chapter)
        before=len(self.calls)
        denied=await self.page.evaluate('window.__novelistJinjiangRequest({url:location.href})')
        self.assertEqual(denied,{'ok':False})
        self.assertEqual(len(self.calls),before)
