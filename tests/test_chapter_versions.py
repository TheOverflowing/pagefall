import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from novelist.chapter_versions import ChapterVersions


class ChapterVersionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'chapter-versions'
        self.store = ChapterVersions(self.directory)

    def record(self, text, kind='source', **metadata):
        return self.store.record('42', {'id': '1', 'title': '第一章', 'text': text, **metadata}, kind)

    def test_versions_survive_restart_with_source_dates_and_no_body_in_list(self):
        first = self.record('旧稿。', source_updated_at='2026-10-01 12:00:00')
        second = self.record('新稿。', source_updated_at='2026-10-10 12:00:00')
        restarted = ChapterVersions(self.directory)
        summaries = restarted.list('42', '1')
        self.assertEqual([item['id'] for item in summaries], [second['id'], first['id']])
        self.assertNotIn('text', summaries[0])
        self.assertEqual(summaries[0]['characters'], 3)
        self.assertEqual(restarted.get('42', '1', first['id']), first)
        self.assertTrue(first['recorded_at'].endswith('+00:00'))
        self.assertEqual(first['source_updated_at'], '2026-10-01 12:00:00')
        self.assertEqual((self.directory / '42/1.json').stat().st_mode & 0o777, 0o600)

    def test_unchanged_body_does_not_create_a_version_but_return_to_old_body_does(self):
        first = self.record('A')
        duplicate = self.record('A', title='Changed title', source_updated_at='2026-10-10 12:00:00')
        self.assertEqual(duplicate['id'], first['id'])
        second = self.record('B')
        third = self.record('A')
        self.assertNotEqual(third['id'], first['id'])
        self.assertEqual(len(self.store.list('42', '1')), 3)
        self.assertEqual([self.store.get('42', '1', row['id'])['text'] for row in self.store.list('42', '1')], ['A', 'B', 'A'])

    def test_old_local_copy_is_imported_once_without_reversing_latest_source(self):
        first = self.record('A', 'existing')
        second = self.record('B')
        imported_again = self.record('A', 'existing')
        self.assertEqual(imported_again['id'], first['id'])
        self.assertEqual(self.store.list('42', '1')[0]['id'], second['id'])
        self.assertEqual(len(self.store.list('42', '1')), 2)

    def test_edit_and_proofread_kinds_remain_distinct_from_source(self):
        for text, kind in [('A', 'source'), ('B', 'proofread'), ('C', 'edit'), ('D', 'proofread_undo')]:
            self.record(text, kind)
        self.assertEqual([row['kind'] for row in self.store.list('42', '1')], ['proofread_undo', 'edit', 'proofread', 'source'])

    def test_comparison_preserves_equal_lines_and_counts_added_deleted_lines(self):
        before = self.record('开场。\n\n旧句。\n尾声。')
        after = self.record('开场。\n\n新句。\n补充。\n尾声。')
        result = self.store.compare('42', '1', before['id'], after['id'])
        self.assertEqual((result['added'], result['deleted']), (2, 1))
        self.assertEqual(result['rows'], [
            {'kind': 'equal', 'text': '开场。'}, {'kind': 'equal', 'text': ''},
            {'kind': 'delete', 'text': '旧句。'}, {'kind': 'insert', 'text': '新句。'},
            {'kind': 'insert', 'text': '补充。'}, {'kind': 'equal', 'text': '尾声。'},
        ])
        self.assertNotIn('text', result['from_version'])
        same = self.store.compare('42', '1', after['id'], after['id'])
        self.assertEqual((same['added'], same['deleted']), (0, 0))

    def test_chapters_and_books_are_isolated_and_unknown_version_is_rejected(self):
        first = self.record('A')
        self.assertEqual(self.store.list('42', '2'), [])
        with self.assertRaises(ValueError):
            self.store.get('43', '1', first['id'])
        with self.assertRaises(ValueError):
            self.store.record('../escape', {'id': '1', 'text': 'A'})
        with self.assertRaises(ValueError):
            self.record('  ')

    def test_comparison_counts_paragraphs_without_counting_blank_separators(self):
        before = self.record('开场。\n\n旧句。')
        after = self.record('开场。\n\n新句。\n\n补充。')
        result = self.store.compare('42', '1', before['id'], after['id'])
        self.assertEqual((result['added'], result['deleted']), (2, 1))
        self.assertIn({'kind': 'insert', 'text': ''}, result['rows'])
        reversed_result = self.store.compare('42', '1', after['id'], before['id'])
        self.assertEqual((reversed_result['added'], reversed_result['deleted']), (1, 2))
        self.assertIn({'kind': 'delete', 'text': ''}, reversed_result['rows'])

    def test_interrupted_write_preserves_prior_history(self):
        first = self.record('A')
        with patch.object(Path, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.record('B')
        self.assertEqual([row['id'] for row in self.store.list('42', '1')], [first['id']])
        self.assertEqual(list((self.directory / '42').glob('*.tmp')), [])

    def test_concurrent_instances_do_not_lose_versions(self):
        def save(index):
            return ChapterVersions(self.directory).record('42', {'id': '1', 'text': str(index)})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            versions = list(pool.map(save, range(12)))
        self.assertEqual({row['id'] for row in self.store.list('42', '1')}, {row['id'] for row in versions})
        saved = json.loads((self.directory / '42/1.json').read_text())
        self.assertEqual(len(saved['versions']), 12)
