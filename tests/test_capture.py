"""Offline checks using original prose and synthetic reader-page fixtures."""

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

from novelist.browser import CONTENT_SCRIPT, TOC_SCRIPT, GATE_ERRORS, CaptureError, Reader, normalize_url, recognize_image


ROOT = Path(__file__).resolve().parent.parent
BOOK_URL = "https://www.jjwxc.net/onebook.php?novelid=9669851"


class URLTests(unittest.TestCase):
    def test_normalizes_book_id_and_mobile_urls(self):
        for value in (
            "9669851",
            " 9669851 ",
            "https://wap.jjwxc.net/book2/9669851",
            "https://wap.jjwxc.net/book/9669851",
            "https://www.jjwxc.net/onebook.php?novelid=9669851&chapterid=3",
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_url(value), BOOK_URL)

    def test_rejects_bad_hosts_schemes_and_credentials(self):
        for value in (
            "https://evil.example/onebook.php?novelid=9669851",
            "https://www.jjwxc.net.evil.example/onebook.php?novelid=9669851",
            "https://eviljjwxc.net/onebook.php?novelid=9669851",
            "javascript:alert(9669851)",
            "file:///onebook.php?novelid=9669851",
            "ftp://www.jjwxc.net/onebook.php?novelid=9669851",
            "https://user@www.jjwxc.net/onebook.php?novelid=9669851",
            "https://user:password@www.jjwxc.net/onebook.php?novelid=9669851",
            "https://www.jjwxc.net:8080/onebook.php?novelid=9669851",
            "https://www.jjwxc.net/onebook.php?novelid=not-a-number",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_url(value)


class ReaderScriptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 1100, "height": 900})

        async def local_response(route):
            # Set a representative origin for relative links without network I/O.
            await route.fulfill(status=200, content_type="text/html", body="<!doctype html>")

        await self.page.route("**/*", local_response)
        await self.page.goto(BOOK_URL)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_toc_deduplicates_sorts_and_preserves_expected_word_counts(self):
        await self.page.set_content("""
            <!doctype html><html><head>
              <meta property="og:novel:book_name" content="雨后的来信">
              <meta property="og:novel:author" content="测试作者">
            </head><body>
              <a href="onebook.php?novelid=9669851&amp;chapterid=2">旧的导航标题</a>
              <table>
                <tr><td><a href="onebook_vip.php?novelid=9669851&amp;chapterid=10">第十章 灯下</a></td><td>[VIP]</td><td>3200</td><td>2026-10-08 08:00</td></tr>
                <tr><td><a href="onebook.php?novelid=9669851&amp;chapterid=2">第二章  纸船</a></td><td>1800</td><td>2026-10-07 08:00</td></tr>
                <tr><td><a href="onebook.php?novelid=9669851&amp;chapterid=1">第一章</a></td><td>1200</td><td>2026-10-06 08:00</td></tr>
              </table>
              <a href="onebook.php?novelid=9669851&amp;chapterid=2">最新章节</a>
              <a href="onebook.php?novelid=123&amp;chapterid=8">别的作品</a>
              <a href="https://example.invalid/onebook.php?novelid=9669851&amp;chapterid=8">外站</a>
              <a href="other.php?novelid=9669851&amp;chapterid=9">其他页面</a>
              <a href="onebook.php?novelid=9669851&amp;chapterid=0">无效章节</a>
              <a href="onebook.php?novelid=9669851&amp;chapterid=oops">无效编号</a>
            </body></html>
        """)
        result = await self.page.evaluate(TOC_SCRIPT)
        self.assertEqual(result["id"], "9669851")
        self.assertEqual(result["title"], "雨后的来信")
        self.assertEqual(result["author"], "测试作者")
        self.assertEqual([c["id"] for c in result["chapters"]], ["1", "2", "10"])
        self.assertEqual([c["expected_words"] for c in result["chapters"]], [1200, 1800, 3200])
        self.assertEqual(result["chapters"][1]["title"], "第二章 纸船")
        self.assertTrue(result["chapters"][2]["vip"])
        self.assertFalse(result["chapters"][0]["vip"])
        self.assertEqual([c["source_updated_at"] for c in result["chapters"]], ["2026-10-06 08:00:00", "2026-10-07 08:00:00", "2026-10-08 08:00:00"])

    async def test_current_jinjiang_toc_uses_semantic_counts_vip_rel_and_locked_rows(self):
        # Original labels reproduce the public page structure, not novel content.
        await self.page.set_content("""
            <!doctype html><html><body>
              <a href="onebook.php?novelid=9669851&amp;chapterid=99">站外目录导航</a>
              <table id="oneboolt"><tbody>
                <tr><td colspan="6"><span class="bigtext">雨后的来信</span><h2>作者：<a href="oneauthor.php?authorid=1">测试作者</a></h2></td></tr>
                <tr itemprop="chapter">
                  <td>17</td>
                  <td><a itemprop="url" rel="http://my.jjwxc.net/onebook_vip.php?novelid=9669851&amp;chapterid=17" id="vip_17">第十七章 信封</a> [VIP]
                    <a href="https://my.jjwxc.net/backend/buynovel.php?novelid=9669851&amp;chapterid=17">购买本章</a></td>
                  <td>原创的章节简介</td><td itemprop="wordCount" colspan="2">3217</td><td>2026-10-08 09:00:00</td>
                </tr>
                <tr itemprop="chapter">
                  <td>16</td>
                  <td><a itemprop="url" href="http://www.jjwxc.net/onebook.php?novelid=9669851&amp;chapterid=16">第十六章 纸船</a></td>
                  <td>另一条原创简介</td><td itemprop="wordCount">2416</td><td>883210</td><td>2026-10-07 09:00:00</td>
                </tr>
                <tr itemprop="chapter">
                  <td>18</td><td>第十八章 窗前[锁]</td><td>章节暂不可读</td>
                  <td itemprop="wordCount" colspan="2">3018</td><td>2026-10-08 10:00:00</td>
                </tr>
                <tr itemprop="chapter newestChapter">
                  <td>19</td>
                  <td><a itemprop="url" rel="http://my.jjwxc.net/onebook_vip.php?novelid=9669851&amp;chapterid=19" id="vip_19">第十九章 灯光</a> [VIP]</td>
                  <td>最新章节的原创简介</td><td itemprop="wordCount" colspan="2">3519</td><td>2026-10-08 11:00:00</td>
                </tr>
              </tbody></table>
            </body></html>
        """)
        result = await self.page.evaluate(TOC_SCRIPT)
        chapters = result["chapters"]
        self.assertEqual(result["title"], "雨后的来信")
        self.assertEqual(result["author"], "测试作者")
        self.assertEqual([c["id"] for c in chapters], ["16", "17", "18", "19"])
        self.assertEqual([c["expected_words"] for c in chapters], [2416, 3217, 3018, 3519])
        self.assertFalse(chapters[0]["vip"])
        self.assertTrue(chapters[1]["vip"])
        self.assertEqual(chapters[1]["title"], "第十七章 信封")
        self.assertEqual(chapters[1]["url"], "https://my.jjwxc.net/onebook_vip.php?novelid=9669851&chapterid=17")
        self.assertEqual(chapters[2]["state"], "blocked")
        self.assertTrue(chapters[2]["locked"])
        self.assertIn("[锁]", chapters[2]["title"])
        self.assertTrue(chapters[3]["vip"])
        self.assertEqual([c["source_updated_at"] for c in chapters], ["2026-10-07 09:00:00", "2026-10-08 09:00:00", "2026-10-08 10:00:00", "2026-10-08 11:00:00"])
        self.assertEqual(chapters[3]["state"], "pending")
        self.assertTrue(all("buynovel.php" not in c["url"] for c in chapters))

    async def test_chapter_update_uses_visible_last_column_not_publication_or_book_dates(self):
        await self.page.set_content('''<meta itemprop="dateModified" content="2026-10-09 12:00:00">
          <table id="oneboolt"><tbody>
            <tr itemprop="chapter newestChapter"><td>1</td><td><a href="onebook.php?novelid=9669851&chapterid=1">One</a></td>
              <td>2025-01-01 00:00:00 is mentioned in this chapter.</td><td itemprop="wordCount">2000</td>
              <td title="章节存稿时间：2026-01-01 00:00:00 章节首发时间：2026-09-12 00:00:00"><span>2026-09-12 01:42:53</span><span> *最新更新</span></td></tr>
            <tr itemprop="chapter"><td>2</td><td><a href="onebook.php?novelid=9669851&chapterid=2">Two</a></td>
              <td>2026-01-01 00:00:00</td><td itemprop="wordCount">2000</td><td title="章节首发时间：2026-09-12 00:00:00">未知</td></tr>
            <tr itemprop="chapter"><td>3</td><td><a href="onebook.php?novelid=9669851&chapterid=3">Three</a></td>
              <td>Summary</td><td itemprop="wordCount">2000</td><td>2026-02-30 12:00:00</td></tr>
          </tbody></table>''')
        result = await self.page.evaluate(TOC_SCRIPT)
        self.assertEqual([c['source_updated_at'] for c in result['chapters']], ['2026-09-12 01:42:53', '', ''])

    async def test_plain_text_preserves_lines_and_excludes_hidden_or_unrelated_nodes(self):
        await self.page.set_content("""
            <style>.secret { display: none } .invisible { visibility: hidden }</style>
            <aside>网页导航</aside>
            <div class="noveltext" style="font: 24px sans-serif">
              <div class="noveltitle">不应进入正文的章节标题</div>
              <p>清晨，林溪推开书店的窗。<br>一封蓝色的信躺在门边。</p>
              <p>她把信放在灯下，读到了熟悉的名字。</p>
              <span class="secret">秘密正文绝不能导出</span>
              <div hidden><span>隐藏父元素里的文字</span></div>
              <span class="invisible">不可见的文字</span>
              <script type="application/json">{"private": "脚本里的内容"}</script>
              <div class="ad">广告文案</div>
              <div class="author_say">作者说明不属于正文</div>
            </div>
        """)
        result = await self.page.evaluate(CONTENT_SCRIPT)
        self.assertNotIn("gate", result)
        self.assertIn("清晨，林溪推开书店的窗。\n一封蓝色的信躺在门边。", result["text"])
        self.assertIn("她把信放在灯下，读到了熟悉的名字。", result["text"])
        for excluded in ("秘密", "隐藏父元素", "不可见", "脚本", "广告", "作者说明", "章节标题", "网页导航"):
            with self.subTest(excluded=excluded):
                self.assertNotIn(excluded, result["text"])
        self.assertFalse(result["customFont"])
        self.assertFalse(result["suspicious"])
        self.assertGreater(result["clip"]["width"], 0)
        self.assertGreater(result["clip"]["height"], 0)

    async def test_app_only_bonus_notice_is_not_saved_as_chapter_body(self):
        await self.page.set_content("""
            <div id="paragraph_comment_content">
              你收到作者赠送的一章福利番外，由于技术问题，目前无法在PC站显示，
              请使用晋江小说阅读App扫描右侧二维码阅读~
            </div>
            <div id="buy_content">其他订阅入口</div>
        """)
        result = await self.page.evaluate(CONTENT_SCRIPT)
        self.assertEqual(result, {"gate": "app_only"})
        reader = Reader(Path("unused-fixture-profile"))
        reader.page = self.page

        async def already_loaded(_url):
            pass

        reader.goto = already_loaded
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(CaptureError) as caught:
            await reader.capture({"id": "143", "url": BOOK_URL + "&chapterid=143"}, Path(temp), "ocr")
        self.assertEqual(caught.exception.kind, "blocked")
        self.assertFalse(caught.exception.pause)
        self.assertEqual(str(caught.exception), GATE_ERRORS["app_only"][0])
        self.assertIn("App 专属福利番外", str(caught.exception))

    async def test_visible_custom_font_requires_ocr(self):
        await self.page.set_content("""
            <style>@font-face { font-family: FixtureGlyph; src: local("Arial"); }</style>
            <div class="noveltext"><p style="font-family: FixtureGlyph, sans-serif">原创测试文字，等待灯光照亮窗户。</p></div>
        """)
        result = await self.page.evaluate(CONTENT_SCRIPT)
        self.assertTrue(result["customFont"])
        self.assertTrue(result["suspicious"])

    async def test_unused_hidden_custom_font_does_not_change_plain_text_mode(self):
        await self.page.set_content("""
            <style>@font-face { font-family: FixtureGlyph; src: local("Arial"); }</style>
            <div class="noveltext"><p>原创测试文字，窗前的台灯亮了。</p>
              <span style="display: none; font-family: FixtureGlyph">隐藏字体</span>
            </div>
        """)
        result = await self.page.evaluate(CONTENT_SCRIPT)
        self.assertFalse(result["customFont"])
        self.assertFalse(result["suspicious"])
        self.assertNotIn("隐藏字体", result["text"])

    async def test_css_generated_text_requires_ocr(self):
        await self.page.set_content("""
            <style>.first-word::before { content: "清晨"; }</style>
            <div id="paragraph_comment_content"><p><span class="first-word"></span>，雨点轻轻落在窗边。</p></div>
        """)
        result = await self.page.evaluate(CONTENT_SCRIPT)
        self.assertTrue(result["suspicious"])
        self.assertFalse(result["customFont"])
        self.assertNotIn("清晨", result["text"])
        self.assertIn("雨点轻轻落在窗边。", result["text"])

    async def test_paragraph_comment_content_takes_priority_over_surrounding_reader(self):
        await self.page.set_content("""
            <div class="noveltext">外层读者操作和导航
              <div class="noveltitle">原创章节标题</div>
              <div id="paragraph_comment_content"><p>林溪把信放进抽屉，等待明天的日出。</p></div>
              <div class="author_say">原创作者说明</div>
            </div>
        """)
        result = await self.page.evaluate(CONTENT_SCRIPT)
        self.assertEqual(result["text"], "林溪把信放进抽屉，等待明天的日出。")
        self.assertFalse(result["suspicious"])

    async def test_visible_buy_content_is_purchase_gate_even_with_preview(self):
        await self.page.set_content("""
            <div id="paragraph_comment_content"><p>这是原创的章节预览，窗外又下起了小雨。</p></div>
            <div id="buy_content">本章需要订阅，价格和购买选项。</div>
        """)
        self.assertEqual(await self.page.evaluate(CONTENT_SCRIPT), {"gate": "purchase"})

    async def test_hidden_buy_content_does_not_block_visible_body(self):
        await self.page.set_content("""
            <div id="paragraph_comment_content"><p>林溪推开店门，向送信的人招了招手。</p></div>
            <div id="buy_content" style="display: none">购买选项</div>
        """)
        result = await self.page.evaluate(CONTENT_SCRIPT)
        self.assertNotIn("gate", result)
        self.assertEqual(result["text"], "林溪推开店门，向送信的人招了招手。")

    async def test_purchase_login_locked_and_other_gates_stop_extraction(self):
        cases = (
            ("purchase", "您尚未购买本章，请先订阅。"),
            ("login", "请先登录后阅读。"),
            ("locked", "本章已被锁定。"),
            ("captcha", "请完成验证。"),
            ("antitheft", "当前订阅比例不足。"),
        )
        for kind, message in cases:
            for inside_body in (True, False):
                with self.subTest(kind=kind, inside_body=inside_body):
                    html = f'<div class="noveltext">{message}</div>' if inside_body else f"<main>{message}</main>"
                    await self.page.set_content(html)
                    self.assertEqual(await self.page.evaluate(CONTENT_SCRIPT), {"gate": kind})

    async def test_missing_body_is_unknown(self):
        await self.page.set_content("<main>书店今天照常营业。</main>")
        self.assertEqual(await self.page.evaluate(CONTENT_SCRIPT), {"gate": "unknown"})

    async def test_login_page_url_is_a_login_gate_without_matching_message(self):
        for name in ("login.php", "login2014.php"):
            with self.subTest(page=name):
                await self.page.goto(f"https://my.jjwxc.net/{name}")
                await self.page.set_content("<main>账号服务页面</main>")
                self.assertEqual(await self.page.evaluate(CONTENT_SCRIPT), {"gate": "login"})

    @unittest.skipUnless(sys.platform == "darwin", "Apple Vision OCR requires macOS")
    async def test_capture_retains_both_ends_of_a_chapter_taller_than_the_viewport(self):
        self.assertTrue((ROOT / ".build" / "vision-ocr").is_file(), "Compile the native OCR engine first.")
        first_marker = "THE FIRST MORNING AT THE BOOKSHOP"
        last_marker = "THE FINAL EVENING UNDER THE LAMPLIGHT"
        middle = "".join(
            f"<p>Original line {i:02d}. Rain fell outside the quiet bookshop.</p>"
            for i in range(1, 61)
        )
        html = f"""<!doctype html><html><head><meta charset="utf-8">
            <style>
              body {{ margin: 32px; background: white; }}
              #paragraph_comment_content {{ width: 900px; padding: 24px; color: #111;
                background: white; font: 26px/56px Arial, sans-serif; }}
              p {{ margin: 0; }}
            </style></head><body>
              <div id="paragraph_comment_content"><p>{first_marker}</p>{middle}<p>{last_marker}</p></div>
            </body></html>"""
        chapter_url = BOOK_URL + "&chapterid=1"

        async def original_chapter(route):
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

        await self.page.route(chapter_url, original_chapter)
        with tempfile.TemporaryDirectory(prefix="novelist-long-capture-test-") as name:
            folder = Path(name)
            reader = Reader(folder / "unused-profile", headless=True)
            # Reuse the isolated routed page. Reader.ensure sees an open page and
            # cannot open a user profile or a second, un-intercepted browser.
            reader.page = self.page
            result = await reader.capture(
                {"id": "1", "title": "Original long chapter", "url": chapter_url, "expected_words": 0},
                folder / "capture", mode="ocr",
            )
            self.assertEqual(result["method"], "ocr")
            self.assertEqual(len(result["screenshots"]), 1)
            with Image.open(result["screenshots"][0]) as screenshot:
                self.assertGreater(screenshot.height, 3000)
                self.assertGreater(screenshot.height, self.page.viewport_size["height"] * 3)
            self.assertIn(first_marker, result["text"])
            self.assertIn(last_marker, result["text"])
            self.assertLess(result["text"].index(first_marker), result["text"].index(last_marker))
            self.assertFalse(result["incomplete"])


class LocalOCRTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(sys.platform == "darwin", "Apple Vision OCR requires macOS")
    async def test_original_chinese_image_returns_text_and_ordered_lines(self):
        image_path = ROOT / "artifacts" / "test-fixtures" / "original-01.png"
        self.assertTrue(image_path.is_file(), "Generate the original screenshot fixture first.")
        self.assertTrue((ROOT / ".build" / "vision-ocr").is_file(), "Compile the native OCR engine first.")
        with tempfile.TemporaryDirectory(prefix="novelist-ocr-test-") as name:
            folder = Path(name)
            text, lines = await recognize_image(image_path, folder)
            self.assertIn("清晨", text)
            self.assertIn("雨水", text)
            self.assertIn("书店", text)
            self.assertGreaterEqual(len(lines), 10)
            self.assertEqual(text, "\n".join(line["text"] for line in lines))
            self.assertEqual([line["y"] for line in lines], sorted(line["y"] for line in lines))
            self.assertTrue(all(0 <= line["y"] <= 1 for line in lines))
            self.assertEqual(list(folder.glob("tile-*.png")), [])


if __name__ == "__main__":
    unittest.main()
