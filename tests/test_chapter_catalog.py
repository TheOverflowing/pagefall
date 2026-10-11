import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from novelist.chapter_catalog import parse_directory
from novelist.catalog import Catalog


HTML = '''<meta charset="gb2312"><meta property="og:novel:book_name" content="雨后的来信">
<meta property="og:novel:author" content="作者"><table id="oneboolt"><tbody>
<tr itemprop="chapter"><td>1</td><td><a href="/onebook.php?novelid=42&amp;chapterid=1">第一章</a></td><td>一封信</td><td itemprop="wordCount">3217</td><td><span title="首发：2020-01-01 00:00:00">2026-10-10 12:00:00</span></td></tr>
<tr itemprop="chapter newestChapter"><td>2</td><td><a href="javascript:;" rel="http://my.jjwxc.net/onebook_vip.php?novelid=42&amp;chapterid=2">第二章</a></td><td>第二封信</td><td itemprop="wordCount">4567</td><td>2026-10-10 12:30</td></tr>
<tr itemprop="chapter"><td>3</td><td>第三章[锁]</td><td>已经锁定</td><td itemprop="wordCount">3333</td><td>2026-02-30 12:00:00</td></tr>
</tbody></table>'''


class DirectoryTests(unittest.TestCase):
    def test_public_directory_covers_rel_locked_rows_counts_and_visible_updated_time(self):
        result = parse_directory(HTML.encode('gb18030'), '42')
        self.assertEqual((result['title'], result['author']), ('雨后的来信', '作者'))
        self.assertEqual([c['id'] for c in result['chapters']], ['1', '2', '3'])
        self.assertEqual(result['chapters'][0]['source_updated_at'], '2026-10-10 12:00:00')
        vip = result['chapters'][1]
        self.assertTrue(vip['vip'])
        self.assertEqual(vip['expected_words'], 4567)
        self.assertEqual(vip['source_updated_at'], '2026-10-10 12:30:00')
        self.assertEqual(vip['url'], 'https://my.jjwxc.net/onebook_vip.php?novelid=42&chapterid=2')
        self.assertTrue(result['chapters'][2]['locked'])
        self.assertEqual(result['chapters'][2]['source_updated_at'], '')

    def test_redirect_login_and_navigation_cannot_be_a_valid_empty_catalog(self):
        for html in ('<form><input type="password"></form>', '<a href="onebook.php?novelid=42&chapterid=9">Other</a>', ''):
            with self.subTest(html=html), self.assertRaises(ValueError):
                parse_directory(html, '42')

    def test_wrong_book_and_external_read_links_are_not_followed(self):
        html = HTML.replace('/onebook.php?novelid=42&amp;chapterid=1', 'https://example.invalid/onebook.php?novelid=42&amp;chapterid=1')
        first = parse_directory(html, '42')['chapters'][0]
        self.assertEqual(first['state'], 'blocked')
        self.assertNotIn('example.invalid', first['url'])


class DirectoryFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_watch_uses_public_text_and_preserves_metadata_age(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = Catalog(Path(temporary)/'catalogs')
            old = {'id': '42', 'title': 'Old title', 'url': 'https://www.jjwxc.net/onebook.php?novelid=42', 'chapters': [], 'metadata': {'description': 'Local synopsis'}, 'cached_at': 123, 'schema': 2}
            catalog.write(catalog.directory/'42.json', old)
            with patch('novelist.catalog.read_public_source', return_value=HTML), patch('novelist.catalog.Reader') as reader:
                updated = await catalog.watch_fetch(copy.deepcopy(old))
                reader.assert_not_called()
            self.assertEqual(updated['cached_at'], 123)
            self.assertEqual(updated['metadata'], old['metadata'])
            self.assertEqual(len(updated['chapters']), 3)
            self.assertGreater(updated['catalog_checked_at'], 123)
            self.assertEqual(catalog.cached('42'), updated)

    async def test_failed_public_directory_does_not_erase_previous_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalog = Catalog(Path(temporary)/'catalogs')
            old = {'id': '42', 'title': 'Saved', 'url': 'https://www.jjwxc.net/onebook.php?novelid=42', 'chapters': [{'id': '1'}]}
            catalog.write(catalog.directory/'42.json', old)
            with patch('novelist.catalog.read_public_source', return_value='unavailable'), self.assertRaises(ValueError):
                await catalog.watch_fetch(old)
            self.assertEqual(catalog.cached('42'), old)
