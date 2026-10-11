"""Offline fixture checks, not proof of current authenticated Jinjiang markup."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright
from novelist.browser import Reader

from novelist.library import (
    BOOKSHELF_META_SCRIPT,
    BOOKSHELF_PAGES_SCRIPT,
    BOOKSHELF_SCRIPT,
    BOOKSHELF_STATUS_SCRIPT,
    BOOKSHELF_READY_SCRIPT,
    BOOKSHELF_NICKNAME_SCRIPT,
    BOOKSHELF_FOLDERS_SCRIPT,
    PROFILE_NICKNAME_SCRIPT,
    FAVORITES_URL,
    normalize_favorites_url,
)


class FavoritesURLTests(unittest.TestCase):
    def test_canonicalizes_only_safe_view_parameters(self):
        self.assertEqual(normalize_favorites_url(), FAVORITES_URL)
        self.assertEqual(
            normalize_favorites_url("http://my.jjwxc.com/backend/favorite.php?token=private-value&type=02&page=003&jsid=private-value#anchor"),
            FAVORITES_URL + "?type=2&page=3",
        )

    def test_rejects_other_hosts_paths_credentials_and_mutation_links(self):
        for value in (
            "https://evil.example/backend/favorite.php?page=2",
            "https://my.jjwxc.net.evil.example/backend/favorite.php",
            "https://my.jjwxc.net/backend/favorite.php?action=delete&id=1",
            "https://my.jjwxc.net/backend/favorite.php?DELETE=1",
            "https://my.jjwxc.net/backend/favorite.php?page=2&page=3",
            "https://my.jjwxc.net/backend/favorite.php?page=0",
            "https://my.jjwxc.net/backend/favorite.php?type=delete",
            "https://my.jjwxc.net/backend/favorite.php;action=delete",
            "https://my.jjwxc.net/backend/favorite.php?page=1000000",
            "https://my.jjwxc.net/backend/favorite.php?page=abc",
            "https://user:secret@my.jjwxc.net/backend/favorite.php",
            "https://my.jjwxc.net:9000/backend/favorite.php",
            "https://my.jjwxc.net/backend/buynovel.php",
            "javascript:alert(1)",
            None,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_favorites_url(value)


class BookshelfScriptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.page = await self.browser.new_page()

        async def local_page(route):
            await route.fulfill(status=200, content_type="text/html", body="<!doctype html>")

        await self.page.route("**/*", local_page)
        await self.page.goto(FAVORITES_URL)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_known_favorites_table_scope_deduplication_authors_and_canonical_urls(self):
        await self.page.set_content("""
            <a href="https://www.jjwxc.net/onebook.php?novelid=999">页侧推荐，不属于书架</a>
            <table class="favoritelisttable"><tbody>
              <tr><td><a href="https://www.jjwxc.net/onebook.php?novelid=101&chapterid=3">第三章 灯光</a>
                <a href="http://www.jjwxc.net/onebook.php?novelid=000101&token=secret"> 雨后的  来信 </a></td>
                <td><a href="https://www.jjwxc.net/oneauthor.php?authorid=7">测试作者甲</a></td><td>连载</td></tr>
              <tr><td><a href="https://www.jjwxc.net/onebook.php?novelid=101">雨后的来信重复入口</a></td><td>连载</td></tr>
              <tr><td><a href="https://www.jjwxc.net/onebook.php?novelid=202#private-anchor">长街微雨</a></td>
                <td><span itemprop="author">作者：测试作者乙</span></td><td>完结</td></tr>
            </tbody></table>
        """)
        result = await self.page.evaluate(BOOKSHELF_SCRIPT)
        self.assertEqual(result, [
            {"id": "101", "title": "雨后的 来信", "author": "测试作者甲", "url": "https://www.jjwxc.net/onebook.php?novelid=101"},
            {"id": "202", "title": "长街微雨", "author": "测试作者乙", "url": "https://www.jjwxc.net/onebook.php?novelid=202"},
        ])

    async def test_fallback_accepts_mobile_book_links_and_bookmarked_titles(self):
        await self.page.set_content("""
            <ul><li><a href="https://wap.jjwxc.net/book2/101">雨后的来信</a><span class="author">作者：测试作者甲</span></li>
              <li><a href="https://m.jjwxc.net/book2/202/17?jsid=secret">长街微雨</a><a href="https://m.jjwxc.net/author/8">测试作者乙</a></li>
              <li><a href="https://my.jjwxc.net/onebook_vip.php?novelid=303&chapterid=6">未读的信</a></li>
            </ul>
        """)
        result = await self.page.evaluate(BOOKSHELF_SCRIPT)
        self.assertEqual([book["id"] for book in result], ["101", "202", "303"])
        self.assertEqual([book["author"] for book in result], ["测试作者甲", "测试作者乙", ""])
        self.assertTrue(all("chapterid" not in book["url"] and "jsid" not in book["url"] for book in result))

    async def test_real_class_selectors_include_empty_folders_and_default_class(self):
        await self.page.set_content('''
          <form id="frmLogList">
           <select id="classid1"><option value="">选择分类</option><option value="7">在追</option><option value="8">想看</option></select>
           <select id="classid"><option value="7">在追</option><option value="8">想看</option></select>
           <table><tr><td>尚未分类 <a id="open_0">该类别有6条收藏 [+点击展开]</a> [生成书单]</td></tr></table>
           <table class="favoritelisttable"><tr><td><input class="class_0"></td><td><a href="https://www.jjwxc.net/onebook.php?novelid=101">来信</a></td></tr></table>
          </form>''')
        before=await self.page.content()
        folders=await self.page.evaluate(BOOKSHELF_FOLDERS_SCRIPT)
        self.assertEqual(folders,[{'id':'7','name':'在追'},{'id':'8','name':'想看'},{'id':'0','name':'尚未分类'}])
        self.assertEqual((await self.page.evaluate(BOOKSHELF_SCRIPT))[0]['source_folder_ids'],['0'])
        self.assertEqual(await self.page.content(),before)

    async def test_empty_default_class_without_expand_control_is_included(self):
        await self.page.set_content('<form id="frmLogList"><table><tr><td>尚未分类 该类别有0条收藏</td></tr></table></form>')
        self.assertEqual(await self.page.evaluate(BOOKSHELF_FOLDERS_SCRIPT),[{'id':'0','name':'尚未分类'}])

    async def test_membership_and_update_time_survive_duplicate_shortcut_rows(self):
        await self.page.set_content('''<table class="favoritelisttable">
          <tr><td><input class="class_-1"></td><td><a href="https://www.jjwxc.net/onebook.php?novelid=101">来信</a></td><td class="chapterupdatetime"><font>2026-10-11 12:03:04</font></td><td>2026-10-10 09:00:00</td></tr>
          <tr><td><input class="class_7"></td><td><a href="https://www.jjwxc.net/onebook.php?novelid=101">来信</a></td><td class="chapterupdatetime">2026-10-11 12:01:02</td></tr>
          <tr><td><input class="class_8"></td><td><a href="https://www.jjwxc.net/onebook.php?novelid=101">来信</a></td><td class="chapterupdatetime">2026-10-11 12:02</td></tr>
        </table>''')
        result=await self.page.evaluate(BOOKSHELF_SCRIPT)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['source_folder_ids'],['7','8'])
        self.assertEqual(result[0]['updated_at'],'2026-10-11 12:03:04')
        self.assertNotIn('last_read_at',result[0])

    async def test_collection_dates_and_invalid_update_dates_are_not_updates(self):
        await self.page.set_content('''<table class="favoritelisttable">
          <tr><td><input class="class_-1"></td><td><a href="https://www.jjwxc.net/onebook.php?novelid=101">来信</a></td><td>2026-10-11 12:03:04</td></tr>
          <tr><td><a href="https://www.jjwxc.net/onebook.php?novelid=202">长街</a></td><td class="chapterupdatetime">2026-02-30 12:03:04</td></tr>
          <tr><td><a href="https://www.jjwxc.net/onebook.php?novelid=303">书店</a></td><td class="chapterupdatetime">2026-10-11 12:03</td></tr>
        </table>''')
        result=await self.page.evaluate(BOOKSHELF_SCRIPT)
        self.assertNotIn('source_folder_ids',result[0]);self.assertNotIn('updated_at',result[0]);self.assertNotIn('updated_at',result[1])
        self.assertEqual(result[2]['updated_at'],'2026-10-11 12:03:00')

    async def test_reader_preserves_folders_and_memberships_across_group_pagination(self):
        def row(identifier,group,updated):
            return f'<tr><td><input class="class_{group}"></td><td><a href="https://www.jjwxc.net/onebook.php?novelid={identifier}">样本 {identifier}</a></td><td class="chapterupdatetime">{updated}</td></tr>'
        first='<select id="classid"><option value="7">在追</option><option value="9">空组</option></select><table class="favoritelisttable">'+row(1,7,'2026-10-11 10:00:00')+row(2,7,'2026-10-11 12:00:00')+'</table><a href="favorite.php?type=8&page=2">下一页</a>'
        second='<select id="classid"><option value="8">已读</option></select><table class="favoritelisttable">'+row(2,8,'2026-10-11 11:00:00')+row(3,8,'2026-10-10 10:00:00')+'</table>'
        async def local_favorites(route):
            body=second if 'page=2' in route.request.url else first
            await route.fulfill(status=200,content_type='text/html; charset=utf-8',body='<h1>文章收藏[3/5000]</h1><div id="t_user_info"><span class="nickname">读者</span></div>'+body)
        context=await self.browser.new_context();await context.route('**/*',local_favorites)
        reader=Reader(Path('unused-folder-fixture-profile'));reader.context=context;reader.ensure=AsyncMock();reader._remember_session=AsyncMock()
        try:result=await reader.library()
        finally:await context.close()
        self.assertTrue(result['complete'],{'count':len(result['books']),'expected':result['expected_count'],'message':result['message']})
        self.assertEqual(result['folders'],[{'id':'7','name':'在追'},{'id':'9','name':'空组'},{'id':'8','name':'已读'}])
        books={b['id']:b for b in result['books']}
        self.assertEqual(books['2']['source_folder_ids'],['7','8'])
        self.assertEqual(books['2']['updated_at'],'2026-10-11 12:00:00')

    async def test_rejects_invalid_links_navigation_and_reading_controls(self):
        await self.page.set_content("""
            <nav><a href="https://www.jjwxc.net/onebook.php?novelid=11">导航推荐</a></nav>
            <a href="https://evil.example/onebook.php?novelid=12">外站</a>
            <a href="https://www.jjwxc.net.evil.example/onebook.php?novelid=13">仿冒</a>
            <a href="https://user:secret@www.jjwxc.net/onebook.php?novelid=14">凭据链接</a>
            <a href="https://www.jjwxc.net:9000/onebook.php?novelid=15">其他端口</a>
            <a href="javascript:alert(1)">脚本</a>
            <a href="https://my.jjwxc.net/backend/buynovel.php?novelid=16">购买页</a>
            <a href="https://www.jjwxc.net/onebook.php?novelid=0">无效编号</a>
            <a href="https://www.jjwxc.net/onebook.php?novelid=17&novelid=18">冲突编号</a>
            <a href="https://www.jjwxc.net/onebook.php?novelid=18&chapterid=2">继续阅读</a>
            <a href="https://www.jjwxc.net/onebook.php?novelid=19&chapterid=10">第十章 雨声</a>
            <a href="https://www.jjwxc.net/onebook.php?novelid=20">最新章节</a>
        """)
        self.assertEqual(await self.page.evaluate(BOOKSHELF_SCRIPT), [])

    async def test_only_visible_rows_are_returned_without_expanding_categories(self):
        await self.page.set_content("""
            <a id="open1" href="#" onclick="window.expanded=true">展开分类</a>
            <table class="favoritelisttable" style="display:none"><tr><td><a href="https://www.jjwxc.net/onebook.php?novelid=101">折叠分类中的书</a></td></tr></table>
            <table class="favoritelisttable"><tbody>
              <tr style="display:none"><td><a href="https://www.jjwxc.net/onebook.php?novelid=202">隐藏行</a></td></tr>
              <tr><td><a style="visibility:hidden" href="https://www.jjwxc.net/onebook.php?novelid=303">不可见链接</a></td></tr>
              <tr style="opacity:0"><td><a href="https://www.jjwxc.net/onebook.php?novelid=404">透明行</a></td></tr>
              <tr><td><a href="https://www.jjwxc.net/onebook.php?novelid=505">门边的信<span hidden>隐藏信息</span></a></td></tr>
            </tbody></table>
        """)
        before = await self.page.content()
        result = await self.page.evaluate(BOOKSHELF_SCRIPT)
        status = await self.page.evaluate(BOOKSHELF_STATUS_SCRIPT)
        self.assertEqual([book["id"] for book in result], ["505"])
        self.assertEqual(result[0]["title"], "门边的信")
        self.assertEqual(status["hidden_tables"], 1)
        self.assertEqual(status["visible_tables"], 1)
        self.assertEqual(status["category_controls"], 1)
        self.assertIsNone(await self.page.evaluate("window.expanded"))
        self.assertEqual(await self.page.content(), before)

    async def test_pagination_drops_private_parameters_and_rejects_actions(self):
        await self.page.set_content("""
            <a href="favorite.php?page=2&type=01&token=secret&jsid=secret#tracking">下一页</a>
            <a href="http://my.jjwxc.com/backend/favorite.php?type=1&page=2">重复入口</a>
            <a href="favorite.php?page=3&type=1">第三页</a>
            <a href="favorite.php?page=4&type=1&action=delete">不是翻页</a>
            <a href="favorite.php?page=2&page=4">冲突分页</a>
            <a href="favorite.php?page=abc">非法分页</a>
            <a href="favorite.php?type=delete">操作类型</a>
            <a href="https://evil.example/backend/favorite.php?page=5">外站</a>
            <a href="https://www.jjwxc.net/onebook.php?novelid=101">作品链接</a>
            <a style="display:none" href="favorite.php?page=6">隐藏链接</a>
        """)
        self.assertEqual(await self.page.evaluate(BOOKSHELF_PAGES_SCRIPT), [
            FAVORITES_URL + "?type=1&page=2", FAVORITES_URL + "?type=1&page=3",
        ])

    async def test_login_and_verification_are_distinguished_from_an_empty_shelf(self):
        await self.page.goto("https://my.jjwxc.net/login.php")
        await self.page.set_content("<main>账号服务</main>")
        self.assertEqual((await self.page.evaluate(BOOKSHELF_STATUS_SCRIPT))["gate"], "login")
        await self.page.goto(FAVORITES_URL)
        await self.page.set_content('<form><input type="password"></form>')
        self.assertEqual((await self.page.evaluate(BOOKSHELF_STATUS_SCRIPT))["gate"], "login")
        await self.page.set_content("<main>请完成验证</main>")
        self.assertEqual((await self.page.evaluate(BOOKSHELF_STATUS_SCRIPT))["gate"], "captcha")
        await self.page.set_content('<table class="favoritelisttable"><tr><td>此分类暂无作品</td></tr></table>')
        status = await self.page.evaluate(BOOKSHELF_STATUS_SCRIPT)
        self.assertIsNone(status["gate"])
        self.assertTrue(status["has_shelf_structure"])
        self.assertEqual(await self.page.evaluate(BOOKSHELF_SCRIPT), [])

    async def test_metadata_reports_expected_total_and_only_collapsed_controls(self):
        await self.page.set_content("""
            <h1>文章收藏[12/5000]</h1>
            <a id="open1" href="#">[+点击展开]</a>
            <a id="open2" href="#">[-点击收起]</a>
            <a id="open3" href="#" style="display:none">[+点击展开]</a>
            <table class="favoritelisttable"><tr><td>
              <a href="https://www.jjwxc.net/onebook.php?novelid=101">原创书名</a>
            </td></tr></table>
        """)
        metadata = await self.page.evaluate(BOOKSHELF_META_SCRIPT)
        self.assertEqual(metadata["expected_count"], 12)
        self.assertEqual(metadata["capacity"], 5000)
        self.assertEqual(metadata["category_controls"], 2)
        self.assertEqual(metadata["collapsed_controls"], [
            {"id": "open1", "selector": "#open1", "label": "[+点击展开]"},
        ])
        self.assertEqual(len(await self.page.evaluate(BOOKSHELF_SCRIPT)), 1)

    async def test_reader_waits_for_delayed_expanded_rows_before_reporting_complete(self):
        def rows(start, end):
            return ''.join(f'<tr><td><a href="https://www.jjwxc.net/onebook.php?novelid={i}">原创样本 {i}</a></td></tr>' for i in range(start, end))

        markup = f'''<meta charset="utf-8"><h1>文章收藏[7/5000]</h1>
          <table class="favoritelisttable">{rows(1, 6)}</table>
          <a id="open_0" href="#" onclick="this.textContent='该类别有7条收藏 [-点击收起]'; setTimeout(() => {{ document.querySelector('#open_list_0').innerHTML=document.querySelector('#later').innerHTML }}, 1800); return false">该类别有7条收藏 [+点击展开]</a>
          <div id="open_list_0"></div>
          <template id="later"><table class="favoritelisttable">{rows(6, 8)}</table></template>'''

        async def local_favorites(route):
            await route.fulfill(status=200, content_type="text/html", body=markup)

        context = await self.browser.new_context()
        await context.route("**/*", local_favorites)
        reader = Reader(Path("unused-library-fixture-profile"))
        reader.context = context
        reader.ensure = AsyncMock()
        reader._remember_session = AsyncMock()
        try:
            result = await reader.library()
        finally:
            await context.close()
        self.assertEqual([book["id"] for book in result["books"]], [str(i) for i in range(1, 8)])
        self.assertEqual(result["expected_count"], 7)
        self.assertTrue(result["complete"])
        self.assertEqual(result["account"]["state"], "connected")
        reader._remember_session.assert_awaited_once()

    async def test_changed_expand_label_is_not_ready_until_rows_arrive(self):
        await self.page.set_content('''
          <a id="open_0" href="#">该类别有493条收藏 [-点击收起]</a>
          <div id="open_list_0">正在加载</div>
          <table class="favoritelisttable"><tr><td>预览行</td></tr></table>''')
        self.assertFalse(await self.page.evaluate(BOOKSHELF_READY_SCRIPT))
        self.assertEqual((await self.page.evaluate(BOOKSHELF_META_SCRIPT))["loading_controls"], [{"id": "open_0", "selector": "#open_0"}])
        await self.page.locator("#open_list_0").evaluate("e => e.innerHTML='<table class=\"favoritelisttable\"><tr><td>加载后的行</td></tr></table>'")
        self.assertTrue(await self.page.evaluate(BOOKSHELF_READY_SCRIPT))

    async def test_customer_number_is_not_presented_as_a_nickname(self):
        await self.page.set_content('<span class="readerid">(客户号：123456)</span>')
        self.assertEqual(await self.page.evaluate(BOOKSHELF_NICKNAME_SCRIPT), "")

    async def test_header_uses_nickname_but_defers_server_truncated_text_to_profile(self):
        await self.page.set_content('<div id="t_user_info"><span class="nickname">原创读者</span><span class="readerid">(客户号：123456)</span></div>')
        self.assertEqual(await self.page.evaluate(BOOKSHELF_NICKNAME_SCRIPT), "原创读者")
        await self.page.locator(".nickname").evaluate("e => e.textContent='Original...' ")
        self.assertEqual(await self.page.evaluate(BOOKSHELF_NICKNAME_SCRIPT), "")

    async def test_profile_reads_only_visible_nickname_value_not_other_account_fields(self):
        await self.page.set_content('''<table>
          <tr><td>注册邮箱</td><td>private@example.invalid</td></tr>
          <tr style="display:none"><td>昵称</td><td>隐藏昵称不读取</td></tr>
          <tr><td>昵称</td><td> 完整的原创读者名 </td></tr>
          <tr><td>客户号</td><td>123456</td></tr>
        </table>''')
        self.assertEqual(await self.page.evaluate(PROFILE_NICKNAME_SCRIPT), "完整的原创读者名")


if __name__ == "__main__":
    unittest.main()
