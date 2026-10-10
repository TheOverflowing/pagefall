"""Offline browser tests of real vendored rules; these do not claim live-site compatibility."""
import json
import hashlib
import unittest
from pathlib import Path
from playwright.async_api import async_playwright
ROOT = Path(__file__).resolve().parents[2]

class RulesEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.requests = []
        self.responses = {}
        async def route(req):
            address = req.request.url
            self.requests.append(address)
            payload = self.responses.get(address)
            if isinstance(payload, dict): await req.fulfill(**payload)
            elif payload is not None: await req.fulfill(status=200, content_type='text/html; charset=utf-8', body=payload)
            else: await req.abort()
        await self.context.route('**/*', route)
    async def asyncTearDown(self):
        await self.context.close(); await self.browser.close(); await self.pw.stop()
    async def inject(self, url, html):
        self.responses[url]=html
        await self.page.goto(url)
        await self.page.add_script_tag(path=str(ROOT/'engine/dist/novelist-rules.js'))
        await self.page.wait_for_function('!!window.NovelistRules')
    def basic_book(self, links=None):
        return '<div class="booktitle"><h1>测试小说</h1></div><div id="author">测试作者</div><div id="bookintro">测试简介</div><div id="bookimg"><img src="/cover.jpg"></div><ul id="chapterlist">'+(links or '<li><a href="/book/1/1.html">第一章</a></li>')+'</ul>'
    async def scan(self): return await self.page.evaluate('window.NovelistRules.scan()')
    async def read(self, id): return await self.page.evaluate('(id)=>window.NovelistRules.read({chapter_id:id})',id)
    async def test_real_onepage_rule_and_reorder_stability(self):
        url='https://www.60ksw.com/book/1/'
        self.responses[url+'1.html']='<div id="content"><p>第一段正文。</p><p>第二段正文。</p></div>'
        await self.inject(url,self.basic_book())
        book=await self.scan()
        self.assertEqual((book['rule_id'],book['title'],book['author']),('i60ksw','测试小说','测试作者'))
        id=book['chapters'][0]['id'];self.assertRegex(id,r'^c_[0-9a-f]{24}$')
        result=await self.read(id)
        self.assertIn('第一段正文',result['text']);self.assertIn('第二段正文',result['text'])
        self.assertFalse(result['needs_ocr'])
        await self.page.evaluate("document.querySelector('#chapterlist').insertAdjacentHTML('afterbegin','<li><a href=\"/book/1/2.html\">序章</a></li>')")
        self.assertEqual((await self.scan())['chapters'][1]['id'],id)
    async def test_http_origin_uses_pure_javascript_sha256(self):
        url='http://www.60ksw.com/book/1/'
        await self.inject(url,self.basic_book())
        self.assertFalse(await self.page.evaluate('!!crypto.subtle'))
        book=await self.scan()
        expected='c_'+hashlib.sha256((url+'1.html').encode()).hexdigest()[:24]
        self.assertEqual(book['chapters'][0]['id'],expected)
    async def test_multiline_upstream_import_hosts_are_not_misattributed(self):
        manifest=json.loads((ROOT/'engine/rules-manifest.json').read_text())
        rules={r['id']:r for r in manifest['rules']}
        self.assertEqual(rules['alphapolis']['hosts'],['www.alphapolis.co.jp'])
        self.assertEqual(rules['hongxiuzhao']['hosts'],['hongxiuzhao.net'])
        self.assertEqual(set(rules['houhuayuan']['hosts']),{'zhaoze.vip','houhuayuan.vip'})
        self.assertEqual(rules['fantasybooks']['hosts'],['colorful-fantasybooks.com'])
    async def test_direct_class_export_fuxiaoshu(self):
        url='https://www.fxshu.top/book/1.html'
        html='<h1>另一部小说</h1><div class="af_lst"><strong><a>另一位作者</a></strong></div><ul class="alt_page"><li><a>共两页</a></li><li><a>下一页</a></li><li><a href="/book/1_1.html">第一页</a></li></ul>'
        self.responses['https://www.fxshu.top/book/1_1.html']='<div class="wznrb"><p>正文内容，保留段落。</p></div>'
        await self.inject(url,html);book=await self.scan()
        self.assertEqual(book['title'],'另一部小说')
        self.assertIn('正文内容',(await self.read(book['chapters'][0]['id']))['text'])
    async def test_real_twopage_rule(self):
        url='https://101kks.com/book/123.html'
        html='<div class="bookbox"><h1>双页目录</h1></div><div class="booknav2"><p>作者甲</p></div><div class="bookimg2"><img src="/cover.jpg"></div>'
        self.responses['https://101kks.com/ajax_novels/chapterlist/123.html']='<div class="navtxt"><p>简介</p></div><a href="/read/123/1.html">第一章</a>'
        self.responses['https://101kks.com/read/123/1.html']='<div id="txtcontent">第一段<br><br>第二段<div class="txtad">广告移除</div></div>'
        await self.inject(url,html);book=await self.scan()
        self.assertEqual(book['rule_id'],'c101kanshu')
        content=await self.read(book['chapters'][0]['id'])
        self.assertIn('第二段',content['text']);self.assertNotIn('广告移除',content['text'])
    async def test_private_use_and_images_require_ocr(self):
        url='https://www.60ksw.com/book/1/'
        await self.inject(url,self.basic_book());book=await self.scan()
        self.responses[url+'1.html']='<div id="content">此处有\ue123字符<img src="https://unrelated.example/novel.png"></div>'
        result=await self.read(book['chapters'][0]['id'])
        self.assertTrue(result['needs_ocr']);self.assertGreater(result['images'],0)
        self.assertFalse(any('unrelated.example' in q for q in self.requests))
    async def test_font_face_is_not_accepted_as_plain_text(self):
        url='https://www.60ksw.com/book/1/'
        await self.inject(url,self.basic_book());book=await self.scan()
        self.responses[url+'1.html']='<style>@font-face {font-family: chapter;src:url(/font.woff)}</style><div id="content">看起来正常的汉字也可能被字体替换。</div>'
        self.assertTrue((await self.read(book['chapters'][0]['id']))['needs_ocr'])
    async def test_redirect_is_rejected_without_following(self):
        url='https://www.60ksw.com/book/1/'
        await self.inject(url,self.basic_book());book=await self.scan()
        self.responses[url+'1.html']={'status':302,'headers':{'location':'https://unrelated.example/collect'},'body':''}
        with self.assertRaises(Exception): await self.read(book['chapters'][0]['id'])
        self.assertFalse(any('unrelated.example' in q for q in self.requests))
    async def test_unpaid_chapter_is_blocked_before_network(self):
        url='https://masiro.me/admin/novelView?novel_id=1'
        html='<div class="novel-title">付费测试</div><div class="author"><a>作者</a></div><div class="brief">简介</div><div class="mailbox-attachment-icon"><a><img class="img" src="/cover.jpg"></a></div><div class="chapter-ul"><ul class="episode-ul"><a href="/chapter/1"><span style="overflow: hidden;">付费章节</span><small>2币</small></a></ul></div>'
        await self.inject(url,html);book=await self.scan();before=list(self.requests)
        self.assertTrue(book['chapters'][0]['blocked'])
        with self.assertRaisesRegex(Exception,'尚未确认可读'): await self.read(book['chapters'][0]['id'])
        self.assertEqual(before,self.requests)
    async def test_unrelated_chapter_cannot_be_fetched(self):
        url='https://www.60ksw.com/book/1/'
        await self.inject(url,self.basic_book('<li><a href="https://unrelated.example/read">外部章节</a></li>'))
        with self.assertRaises(Exception): await self.scan()
        self.assertFalse(any('unrelated.example' in q for q in self.requests))
    async def test_shared_template_does_not_allow_another_website(self):
        url='https://www.666biquge.com/book/1/'
        html='<div id="info"><h1>模板小说</h1><p>作者甲</p></div><div id="intro">简介</div><div id="list"><a href="https://www.81zw.com/book/1/1.html">另一站点</a></div>'
        await self.inject(url,html)
        with self.assertRaisesRegex(Exception,'无法安全读取'): await self.scan()
        self.assertFalse(any('81zw.com' in q for q in self.requests))

if __name__=='__main__':unittest.main()
