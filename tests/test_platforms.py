"""Isolated platform routing and rendered-DOM fixtures; no real accounts used."""

import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from playwright.async_api import async_playwright
from PIL import Image

from novelist import platforms as registry


class PlatformRoutingTests(unittest.TestCase):
    def test_existing_jinjiang_identity_and_mobile_links_are_preserved(self):
        self.assertEqual(registry.book_identity("9669851"), "9669851")
        self.assertEqual(registry.normalize_book_url("https://wap.jjwxc.net/book2/9669851"), "https://www.jjwxc.net/onebook.php?novelid=9669851")
        self.assertEqual(registry.platform_for("https://my.jjwxc.net/onebook_vip.php?novelid=1&chapterid=2").id, "jjwxc")

    def test_other_platforms_have_namespaced_ids_and_preserve_large_numeric_ids(self):
        self.assertEqual(registry.book_identity("https://www.gongzicp.com/novel-123.html"), "gongzicp_123")
        self.assertEqual(registry.book_identity("https://fanqienovel.com/page/7137359676424850443"), "fanqie_7137359676424850443")
        self.assertNotEqual(registry.book_identity("123"), registry.book_identity("https://www.gongzicp.com/novel-123.html"))

    def test_rejects_unlisted_hosts_credentials_ports_and_nonbook_native_paths(self):
        for value in ("file:///etc/passwd", "http://127.0.0.1:8765/", "https://www.gongzicp.com.evil.test/novel-123.html", "https://user:password@www.gongzicp.com/novel-123.html", "https://fanqienovel.com:8765/page/123", "https://fanqienovel.com/reader/123", "https://www.gongzicp.com/read-123.html"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                registry.normalize_book_url(value)

    def test_chapter_navigation_cannot_change_platform_book_or_chapter(self):
        book = "https://www.gongzicp.com/novel-123.html"
        chapter = {"url": "https://www.gongzicp.com/read-987.html"}
        self.assertTrue(registry.chapter_matches(book, chapter, chapter["url"] + "?from=next"))
        self.assertFalse(registry.chapter_matches(book, chapter, "https://www.gongzicp.com/read-988.html"))
        self.assertFalse(registry.chapter_matches(book, chapter, "https://example.com/read-987.html"))
        jj = "https://www.jjwxc.net/onebook.php?novelid=123"
        self.assertFalse(registry.chapter_matches(jj, {"url": jj + "&chapterid=2"}, "https://www.jjwxc.net/onebook.php?novelid=124&chapterid=2"))

    def test_manifest_advertises_only_built_rules_without_overriding_native_routes(self):
        manifest = {"rules": [
            {"id": "Example", "name": "Fixture novels", "hosts": ["books.example.org"], "book_exclude_patterns": [r"/read/\d+"]},
            {"id": "qidian", "name": "Qidian", "hosts": ["www.qidian.com", "book.qidian.com"]},
            {"id": "jjwxc", "name": "Upstream JJ", "hosts": ["www.jjwxc.net"]},
        ]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.json"
            path.write_text(json.dumps(manifest))
            with patch.object(registry, "UPSTREAM_MANIFEST", path):
                spec = registry.platform_for("https://books.example.org/novel/one")
                self.assertEqual(spec.adapter_kind, "upstream")
                self.assertEqual(spec.status, "unverified")
                self.assertEqual(spec.rule_id, "Example")
                self.assertTrue(registry.book_identity("https://books.example.org/novel/one").startswith("upstream_example_"))
                self.assertEqual(registry.book_identity("https://book.qidian.com/info/123/"), "qidian_123")
                self.assertEqual(registry.platform_for("123").adapter_kind, "native")
                self.assertNotIn("token=", registry.normalize_book_url("https://books.example.org/novel/one?token=fixture&book=12#section"))
                with self.assertRaises(ValueError):
                    registry.normalize_book_url("https://books.example.org/read/123")


class RenderedPlatformTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 1280, "height": 900})

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def document(self, url, content):
        await self.page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=content))
        await self.page.goto(url)

    async def test_changpei_catalog_uses_chapter_rows_keeps_locked_and_vip_entries(self):
        url = "https://www.gongzicp.com/novel-123.html"
        await self.document(url, """<div class=novel-info><h3 class=name>Fixture Book</h3></div>
          <div class=cp-author-info><div class=author-name>Fixture Author</div></div>
          <a href='/read-999.html'>An unrelated latest link</a><div class=chapter-list>
          <div class=chapter><a href='/read-987.html'>First</a></div>
          <div class=chapter><a href='/read-654.html'>Second</a><i class=cp-icon-vip></i></div>
          <div class=chapter>章节已锁定</div></div>""")
        book = await registry.scan(self.page, url)
        self.assertEqual(book["id"], "gongzicp_123")
        self.assertEqual([chapter["id"] for chapter in book["chapters"]], ["987", "654", "locked_3"])
        self.assertTrue(book["chapters"][1]["vip"])
        self.assertTrue(book["chapters"][2]["locked"])
        self.assertEqual(book["chapters"][0]["book_url"], url)

    async def test_fanqie_catalog_keeps_exact_large_ids_and_ignores_other_links(self):
        url = "https://fanqienovel.com/page/7137359676424850443"
        await self.document(url, """<div class=info-name><h1>Fixture</h1></div><span class=author-name>Author</span>
          <a href='/reader/999'>Latest outside catalog</a><div class=page-directory-content>
          <div class=chapter-item><a href='/reader/7137980122702807592'>First</a></div>
          <div class=chapter-item><a href='/reader/7137980122702807593'>Second</a><span class=chapter-item-lock></span></div>
          </div>""")
        book = await registry.scan(self.page, url)
        self.assertEqual([chapter["id"] for chapter in book["chapters"]], ["7137980122702807592", "7137980122702807593"])
        self.assertTrue(book["chapters"][1]["vip"])

    async def test_changpei_waits_for_skeleton_rows_to_be_replaced(self):
        url = "https://www.gongzicp.com/novel-123.html"
        await self.document(url, """<div class=novel-info><h3 class=name>Fixture Book</h3></div>
          <div class=chapter-list><div class=chapter><span class='name cp-loading'></span></div></div>
          <script>setTimeout(()=>document.querySelector('.chapter-list').innerHTML='<div class=chapter><a href=/read-456.html>Loaded chapter</a></div>',200)</script>""")
        book = await registry.scan(self.page, url)
        self.assertEqual(book["chapters"][0]["id"], "456")

    async def test_visible_text_excludes_hidden_content_and_routes_custom_glyphs_to_ocr(self):
        url = "https://fanqienovel.com/reader/7137980122702807592"
        chapter = {"url": url, "book_url": "https://fanqienovel.com/page/7137359676424850443"}
        await self.document(url, """<div class=muye-reader-box>本章字数：120字
          <div class=muye-reader-content><p>这是用于验证正文提取边界的公开测试内容，不是真实小说。第一段应该保留。</p>
          <p style='display:none'>秘密占位不应该进入导出</p><p>第二段含有字体编码：&#xE123;，因此必须通过渲染后的 OCR 识别。</p></div></div>""")
        result = await registry.inspect_chapter(self.page, chapter)
        self.assertNotIn("秘密占位", result["text"])
        self.assertTrue(result["suspicious"])
        self.assertEqual(result["expected_words"], 120)
        self.assertGreater(result["clip"]["height"], 20)

    async def test_purchase_gate_is_never_returned_as_chapter_text(self):
        url = "https://www.gongzicp.com/read-987.html"
        await self.document(url, """<div class=cp-reader><div class='content unpaid'>当前章节为VIP章节，需要订阅才能阅读，请先订阅。<button>订阅本章</button></div></div>""")
        result = await registry.inspect_chapter(self.page, {"url": url, "book_url": "https://www.gongzicp.com/novel-123.html"})
        self.assertEqual(result, {"gate": "purchase"})

    async def test_inner_scrolling_reader_screenshot_contains_the_full_chapter(self):
        url = "https://fanqienovel.com/reader/123"
        content = "<p>这是滚动正文区域截图边界测试，字体编码&#xE123;需要 OCR。</p>" * 45
        await self.document(url, "<style>html,body,#app{height:100%;margin:0}.muye-reader{height:100%;overflow:auto}.muye-reader-inner{height:100%}p{line-height:35px;margin:15px}</style><div id=app><div class=muye-reader><div class=muye-reader-inner><div class=muye-reader-content>" + content + "</div></div></div></div>")
        result = await registry.inspect_chapter(self.page, {"url": url, "book_url": "https://fanqienovel.com/page/123"})
        image = Image.open(io.BytesIO(await self.page.screenshot(clip=result["clip"], full_page=True)))
        self.assertGreater(image.height, 1800)
        self.assertAlmostEqual(image.height, result["clip"]["height"], delta=2)

    async def test_visible_slider_is_reported_as_verification_not_empty_chapter(self):
        await self.document("https://www.gongzicp.com/read-123.html", "<div class=cp-reader><div class=content></div></div><div class=aliyun-captcha>请按住滑块，拖动到最右边</div>")
        self.assertEqual(await self.page.evaluate(registry.CONTENT_SCRIPT, "gongzicp"), {"gate": "captcha"})


if __name__ == "__main__":
    unittest.main()
