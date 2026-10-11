import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright

from novelist.jinjiang_subscriptions import (
    SUBSCRIPTIONS_SCRIPT, SUBSCRIPTIONS_URL, _decode_html,
    read_subscriptions, subscriptions_url,
)


HEADER = "<tr><td>文章名</td><td>文章进度</td><td>是否自动续订</td></tr>"


def row(identifier, state="续订中", title="雨后的来信"):
    return f'''<tr><td><a href="//www.jjwxc.net/onebook.php?novelid={identifier}&amp;jsid=fixture">{title}</a></td>
      <td><table><tr><td>是</td></tr></table></td>
      <td id="renew_{identifier}">{state} <a onclick="window.changed=true">[取消续订]</a></td></tr>'''


class SubscriptionURLTests(unittest.TestCase):
    def test_two_tabs_and_pagination_strip_session_parameters(self):
        self.assertEqual(subscriptions_url(), SUBSCRIPTIONS_URL + "?type=concerned&renew_status=1&page=1")
        self.assertEqual(subscriptions_url("?renew_status=1&page=2&jsid=secret", "notconcerned"),
                         SUBSCRIPTIONS_URL + "?type=notconcerned&renew_status=1&page=2")

    def test_rejects_mutations_other_endpoints_and_ambiguous_filters(self):
        for value in ("?action=renew&id=42", "?type=delete", "?renew_status=0", "?page=0", "?page=2&page=3",
                      "?type=concerned&type=notconcerned", "https://evil.example/backend/vip_services.php",
                      "https://my.jjwxc.net/backend/buynovel.php", "https://me:secret@my.jjwxc.net/backend/vip_services.php"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                subscriptions_url(value)

    def test_gbk_and_utf8_response_decoding(self):
        value = "<meta charset=gbk>续订中"
        self.assertEqual(_decode_html(value.encode("gbk"), "text/html"), value)
        self.assertEqual(_decode_html("续订中".encode(), "text/html; charset=UTF-8"), "续订中")


class SubscriptionDOMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        await self.page.route("**/*", lambda route: route.abort())

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def parse(self, html):
        return await self.page.evaluate(SUBSCRIPTIONS_SCRIPT, {"html": html, "url": subscriptions_url()})

    async def test_only_confirmed_rows_are_returned_and_controls_never_execute(self):
        value = await self.parse("<input type=password hidden><script>window.changed=true</script><table>" + HEADER
                                + row("42") + row("43", "未续订") + row("42")
                                + "<tr><td id=renew_status>未续订包月 [自动续订包月]</td></tr></table>")
        self.assertEqual(value["books"], [{"id": "42", "title": "雨后的来信",
                                          "url": "https://www.jjwxc.net/onebook.php?novelid=42", "auto_subscribed": True}])
        self.assertTrue(value["complete"])
        self.assertIsNone(await self.page.evaluate("window.changed"))

    async def test_unknown_state_or_mismatched_book_prevents_complete_snapshot(self):
        for bad in (row("43", "处理中"), row("43").replace("novelid=43", "novelid=44"),
                    row("43").replace("www.jjwxc.net", "evil.example")):
            value = await self.parse("<table>" + HEADER + row("42") + bad + "</table>")
            self.assertFalse(value["complete"])
            self.assertEqual([book["id"] for book in value["books"]], ["42"])

    async def test_recognized_empty_table_is_complete_but_login_and_changed_markup_are_not(self):
        value = await self.parse("<table>" + HEADER + "<tr><td colspan=3>暂无记录</td></tr></table>")
        self.assertTrue(value["complete"])
        self.assertEqual(value["books"], [])
        for html, gate in (("<input type=password>请先登录", "login"), ("请完成验证", "captcha"), ("页面结构已变化", "structure")):
            value = await self.parse(html)
            self.assertFalse(value["complete"])
            self.assertEqual(value["gate"], gate)

    async def test_pagination_only_comes_from_list_pager(self):
        value = await self.parse("<table>" + HEADER + row("42") + '</table><a href="?action=delete">取消</a>'
                                '<span class="pagelist"><a href="?renew_status=1&amp;page=2">[2]</a></span>')
        self.assertEqual(value["pages"], [SUBSCRIPTIONS_URL + "?renew_status=1&page=2"])


class SubscriptionReadTests(unittest.IsolatedAsyncioTestCase):
    def reader(self, results, status=200):
        responses = []
        async def get(url, **kwargs):
            self.assertEqual(kwargs, {"max_redirects": 0, "timeout": 30000})
            response = SimpleNamespace(status=status, headers={"content-type": "text/html; charset=utf-8"},
                                       body=AsyncMock(return_value=url.encode()), dispose=AsyncMock())
            responses.append(response)
            return response
        async def evaluate(script, payload):
            self.assertEqual(script, SUBSCRIPTIONS_SCRIPT)
            value = results[payload["url"]]
            if isinstance(value, Exception):
                raise value
            return value
        page = SimpleNamespace(route=AsyncMock(), evaluate=AsyncMock(side_effect=evaluate), close=AsyncMock())
        reader = SimpleNamespace(ensure=AsyncMock(), context=SimpleNamespace(
            new_page=AsyncMock(return_value=page), request=SimpleNamespace(get=AsyncMock(side_effect=get))))
        return reader, page, responses

    async def test_walks_both_tabs_and_all_pages_without_duplicate_fetches(self):
        urls = [subscriptions_url(), subscriptions_url(tab="notconcerned"), subscriptions_url("?page=2")]
        results = {
            urls[0]: {"books": [{"id": "42"}], "pages": ["?page=2", "?page=1"], "complete": True},
            urls[1]: {"books": [{"id": "43"}], "pages": [], "complete": True},
            urls[2]: {"books": [{"id": "44"}], "pages": ["?page=1", "?page=2"], "complete": True},
        }
        reader, page, responses = self.reader(results)
        value = await read_subscriptions(reader)
        self.assertTrue(value["complete"])
        self.assertEqual([book["id"] for book in value["books"]], ["42", "43", "44"])
        self.assertEqual([call.args[0] for call in reader.context.request.get.await_args_list], urls)
        self.assertTrue(value["checked_at"])
        page.close.assert_awaited_once()
        for response in responses:
            response.dispose.assert_awaited_once()

    async def test_failure_of_second_tab_retains_confirmed_partial_books(self):
        results = {subscriptions_url(): {"books": [{"id": "42"}], "pages": [], "complete": True},
                   subscriptions_url(tab="notconcerned"): {"books": [], "pages": [], "complete": False, "gate": "login"}}
        reader, page, _ = self.reader(results)
        value = await read_subscriptions(reader)
        self.assertFalse(value["complete"])
        self.assertEqual(value["books"], [{"id": "42"}])
        self.assertEqual(value["error"], "请到账号页面重新连接晋江。")
        page.close.assert_awaited_once()

    async def test_http_failure_disposes_response_and_reports_incomplete(self):
        reader, page, responses = self.reader({}, status=429)
        value = await read_subscriptions(reader)
        self.assertFalse(value["complete"])
        self.assertTrue(value["error"])
        responses[0].dispose.assert_awaited_once()
        page.close.assert_awaited_once()

    async def test_cancelled_sync_closes_its_page(self):
        reader, page, _ = self.reader({})
        page.evaluate.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await read_subscriptions(reader)
        page.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
