"""Browser-free chapter API and directory reads, using only local fixtures."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from Cryptodome.Cipher import DES
from Cryptodome.Util.Padding import pad
from novelist import catalog, jinjiang
from novelist.browser import Reader

URL='https://www.jjwxc.net/onebook.php?novelid=42&chapterid=1'
CHAPTER={'id':'1','title':'灯光','url':URL,'vip':True}
TEXT='小岚在雨后的书店等小溪。窗外的灯光亮了，她把那封尚未拆开的信放回口袋。'
DIRECTORY='''<meta charset="gb18030"><meta property="og:novel:book_name" content="书店"><meta property="og:novel:author" content="作者">
<div id="novelintro">第一段。<br>第二段。<script>hidden()</script></div>
<span itemprop="genre">原创-百合-近代现代-爱情</span><span itemprop="wordCount">5000字</span><li>作品视角：<span>主受</span></li>
<table id="oneboolt"><tr itemprop="chapter"><td>1</td><td><a rel="https://my.jjwxc.net/onebook_vip.php?novelid=42&chapterid=1">灯光</a></td><td>在书店相遇</td><td itemprop="wordCount">36</td><td>2026-10-10 12:00:01</td></tr></table>'''


def encrypt(text,key=b'KW8Dvm2N',iv=b'1ae2c94b'):
    return base64.b64encode(DES.new(key,DES.MODE_CBC,iv).encrypt(pad(text.encode(),8))).decode()


class DecoderTests(unittest.TestCase):
    def decode(self,value,headers=''):
        body=json.dumps(value).encode() if isinstance(value,dict) else value.encode()
        return jinjiang.decode_api_response(body,headers,CHAPTER)

    def test_plain_double_encoded_entities_paragraphs_and_postscript(self):
        value=self.decode({'chapterId':1,'novelId':42,'content':TEXT+'\n &amp;lt;约定&amp;gt; &amp;amp; 灯光\u200c','sayBodyV2':'作者的补充。'})
        self.assertEqual(value['text'],TEXT+'\n\n<约定> & 灯光\n\n作者有话说：\n\n作者的补充。')
        self.assertEqual(value['method'],'jjwxc_api')

    def test_upstream_jj_content_and_postscript_decoder(self):
        value=self.decode({'chapterId':'1','content':encrypt(TEXT),'sayBodyV2':encrypt('后记。'),'encryptField':['content','sayBodyV2'],'encryptType':'jj'})
        self.assertEqual(value['text'],TEXT+'\n\n作者有话说：\n\n后记。')

    def test_upstream_encrypted_response_both_key_variants(self):
        for access in ('fixture-a','fixture-b'):
            key_string='0123456789abcdefghijklmnopqrstuvwxyz'*4
            total=sum(ord(char) for char in access);start=total%len(key_string);length=total//65
            segment=key_string[start:start+length]
            marker='0123456789ab'
            key=hashlib.md5((segment+marker).encode()).hexdigest()[:8].encode()
            iv=hashlib.md5(marker.encode()).hexdigest()[:8].encode()
            body=encrypt(json.dumps({'chapterId':1,'content':TEXT}),key,iv)
            body=body+marker if ord(access[-1])&1 else marker+body
            self.assertEqual(self.decode(body,'accesskey:'+access+'\nkeystring:'+key_string)['text'],TEXT)

    def test_wrong_identity_unknown_encryption_and_error_body_fail_closed(self):
        for value in ({'chapterId':99,'content':TEXT},{'novelId':99,'content':TEXT},{'content':TEXT,'encryptField':['content'],'encryptType':'unsupported'},{'message':'本章尚未购买'}):
            with self.subTest(value=value):self.assertIsNone(self.decode(value))


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.reader=Reader(Path(self.temp.name)/'browser')
        self.chapter={**CHAPTER,'book_url':'https://www.jjwxc.net/onebook.php?novelid=42'}

    async def test_token_success_never_initializes_browser(self):
        body=json.dumps({'chapterId':1,'novelId':42,'content':TEXT}).encode()
        with patch.object(jinjiang,'has_token',return_value=True),patch.object(jinjiang,'token_helper',return_value='fixture-private'),patch.object(jinjiang,'api_response',return_value=(body,'')) as network,patch.object(self.reader,'ensure',side_effect=AssertionError('Browser started')),patch.object(self.reader,'goto',side_effect=AssertionError('Page loaded')):
            result=await self.reader.capture(self.chapter,Path(self.temp.name)/'source')
        self.assertEqual(result['text'],TEXT);self.assertEqual(result['method'],'jjwxc_api')
        self.assertIsNone(self.reader.context);self.assertIsNone(self.reader.page)
        network.assert_called_once();self.assertNotIn('fixture-private',json.dumps(result))

    async def test_failed_api_starts_existing_web_fallback_without_second_token_request(self):
        with patch.object(jinjiang,'has_token',return_value=True),patch.object(jinjiang,'token_helper',return_value='fixture-private'),patch.object(jinjiang,'api_response',return_value=(b'{"message":"not subscribed"}','')),patch.object(self.reader,'_prepare_jinjiang',AsyncMock()) as prepare,patch.object(jinjiang,'read',AsyncMock(return_value={'method':'jjwxc_upstream','text':TEXT})) as fallback:
            result=await self.reader.capture(self.chapter,Path(self.temp.name)/'source')
        self.assertEqual(result['method'],'jjwxc_upstream');prepare.assert_awaited_once()
        fallback.assert_awaited_once_with(self.reader,self.chapter,allow_token=False)

    async def test_short_or_corrupt_api_text_is_rejected_before_fallback(self):
        for value in ('太短','\ue000'+TEXT,TEXT):
            chapter={**self.chapter,'expected_words':1000 if value==TEXT else 0}
            with patch.object(jinjiang,'has_token',return_value=True),patch.object(jinjiang,'token_helper',return_value='fixture-private'),patch.object(jinjiang,'api_response',return_value=(json.dumps({'content':value}).encode(),'')):
                self.assertIsNone(await jinjiang.read_api(chapter))

    async def test_public_directory_and_details_do_not_open_browser(self):
        with patch.object(catalog,'read_public_source',return_value=DIRECTORY.encode('gb18030')),patch.object(self.reader,'ensure',side_effect=AssertionError('Browser started')),patch.object(jinjiang,'token_helper',side_effect=AssertionError('Directory accessed token')):
            result=await self.reader.scan(self.chapter['book_url'])
        self.assertEqual(result['title'],'书店');self.assertEqual(result['author'],'作者')
        chapter=result['chapters'][0]
        self.assertTrue(chapter['vip']);self.assertEqual(chapter['source_updated_at'],'2026-10-10 12:00:01');self.assertEqual(chapter['summary'],'在书店相遇')
        self.assertEqual(result['metadata']['description'],'第一段。\n第二段。');self.assertEqual(result['metadata']['perspective'],'主受');self.assertEqual(result['metadata']['word_count'],'5000')
        self.assertIsNone(self.reader.page)

    async def test_directory_without_open_graph_uses_current_native_title_and_author(self):
        html=DIRECTORY.replace('<meta property="og:novel:book_name" content="书店"><meta property="og:novel:author" content="作者">','<title>不使用网页后缀_晋江文学城</title><h1 itemprop="name">书店</h1><span itemprop="author">作者</span>')
        with patch.object(catalog,'read_public_source',return_value=html.encode('gb18030')),patch.object(self.reader,'ensure',side_effect=AssertionError('Browser started')):
            result=await self.reader.scan(self.chapter['book_url'])
        self.assertEqual((result['title'],result['author']),('书店','作者'))

    async def test_catalog_details_keep_direct_summaries_and_public_metadata(self):
        instance=catalog.Catalog(Path(self.temp.name)/'catalogs')
        with patch.object(catalog,'read_public_source',return_value=DIRECTORY.encode('gb18030')),patch.object(catalog,'details_app_metadata',return_value={'characters':[{'id':'a','name':'小岚'}]}),patch.object(Reader,'ensure',side_effect=AssertionError('Browser started')):
            result=await instance.fetch(self.chapter['book_url'])
        self.assertEqual(result['chapters'][0]['summary'],'在书店相遇');self.assertEqual(result['metadata']['characters'][0]['name'],'小岚')
        self.assertEqual(result['metadata']['description'],'第一段。\n第二段。')


class TransportTests(unittest.TestCase):
    def test_transport_is_exact_read_only_and_does_not_leak_token_on_failure(self):
        with patch.object(jinjiang._API_TRANSPORT,'read',side_effect=ValueError('https://app.jjwxc.net/?token=fixture-private')) as read,patch.object(jinjiang,'API_REQUEST_INTERVAL',0):
            with self.assertRaises(ValueError) as error:jinjiang.api_response(CHAPTER,'fixture-private')
        self.assertNotIn('fixture-private',str(error.exception))
        path,headers=read.call_args.args
        self.assertTrue(path.startswith('/androidapi/chapterContent?novelId=42&chapterId=1&versionCode=381&token='))
        self.assertNotIn('Cookie',headers)
