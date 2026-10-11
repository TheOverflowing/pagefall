"""Shelf organization stays local and survives source refreshes and restarts."""
import copy
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from novelist import server
from novelist.catalog import Catalog
import test_workbench as fixtures


class LibraryOrganizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.WorkbenchTests.setUp(self)
        patcher = patch.object(server, 'catalog', Catalog(self.data / 'catalogs'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.workbench.launch_tag_lookup = lambda: None
        self.source = {'account': {'state': 'connected', 'nickname': '本机整理测试'}, 'complete': True,
                       'folders': [{'id': '-1', 'name': '推荐'}, {'id': '0', 'name': '尚未分类'}, {'id': '12', 'name': '晋江分组'}, {'id': '13', 'name': '空分组'}],
                       'books': [{'id': '42', 'title': '甲', 'source_folder_ids': ['12'], 'updated_at': '2026-10-10 12:00:00'},
                                 {'id': '43', 'title': '乙', 'source_folder_ids': ['0']}]}
        self.workbench.reader.library_result = copy.deepcopy(self.source)

    async def asyncTearDown(self):
        await fixtures.WorkbenchTests.asyncTearDown(self)

    def request(self, **value):
        return fixtures.FakeRequest(value)

    def shelf_books(self):
        return {book['id']: book for book in self.workbench.library_state()['books']}

    async def test_import_local_rename_move_and_empty_folder_survive_source_refresh_and_restart(self):
        await self.workbench.refresh_library()
        self.assertEqual({folder['id'] for folder in self.workbench.library_state()['folders']}, {'jjwxc_12', 'jjwxc_13'})
        self.assertEqual(self.shelf_books()['43']['folder_ids'], [])
        await server.rename_library_folder(self.request(id='jjwxc_12', name='我的分组名称'))
        created = await server.create_library_folder(self.request(name='本机喜欢'))
        local_id = created['folder_id']
        await server.assign_library_folder(self.request(book_ids=['42'], folder_id=local_id))
        self.workbench.reader.library_result['folders'][2]['name'] = '网站后来改名'
        self.workbench.reader.library_result['books'][0]['source_folder_ids'] = ['13']
        await self.workbench.refresh_library()
        state = self.workbench.library_state()
        self.assertEqual(self.shelf_books()['42']['folder_ids'], [local_id])
        self.assertEqual(next(folder for folder in state['folders'] if folder['id'] == 'jjwxc_12')['name'], '我的分组名称')
        self.assertIn('jjwxc_13', {folder['id'] for folder in state['folders']})
        restarted = server.Workbench()
        self.assertEqual(restarted.library_state()['folders'], state['folders'])
        self.assertEqual(restarted.library_state()['books'], state['books'])
        self.assertEqual(json.loads((self.data / 'library.json').read_text())['folders'][2]['name'], '网站后来改名')

    async def test_delete_source_folder_keeps_books_and_does_not_reimport_on_sync(self):
        await self.workbench.refresh_library()
        result = await server.delete_library_folder(self.request(id='jjwxc_12'))
        self.assertEqual({book['id'] for book in result['books']}, {'42', '43'})
        self.assertEqual(self.shelf_books()['42']['folder_ids'], [])
        await self.workbench.refresh_library()
        self.assertNotIn('jjwxc_12', {folder['id'] for folder in self.workbench.library_state()['folders']})
        restarted = server.Workbench()
        self.assertNotIn('jjwxc_12', {folder['id'] for folder in restarted.library_state()['folders']})
        self.assertEqual(len(restarted.library_state()['books']), 2)

    async def test_delete_local_folder_leaves_explicit_unfiled_instead_of_restoring_source_group(self):
        await self.workbench.refresh_library()
        folder_id = (await server.create_library_folder(self.request(name='稍后阅读')))['folder_id']
        await server.assign_library_folder(self.request(book_ids=['42', '43'], folder_id=folder_id))
        await server.delete_library_folder(self.request(id=folder_id))
        await self.workbench.refresh_library()
        self.assertEqual([book['folder_ids'] for book in self.shelf_books().values()], [[], []])
        self.assertEqual(self.workbench.library_organization.data['assignments'], {'42': None, '43': None})
        self.assertIn('jjwxc_12', {folder['id'] for folder in self.workbench.library_state()['folders']})

    async def test_read_timestamp_changes_only_on_explicit_read_event_and_persists(self):
        await self.workbench.refresh_library()
        self.assertIsNone(self.shelf_books()['42']['last_read_at'])
        self.workbench.library_state()
        self.workbench.remember_metadata('42', {'updated_at': '2026-10-11 12:00:00'})
        self.assertIsNone(self.shelf_books()['42']['last_read_at'])
        with patch('novelist.library_organization.time.time', return_value=1791750000.25):
            response = await server.mark_library_read(self.request(book_id='42'))
        self.assertEqual(response, {'book_id': '42', 'last_read_at': 1791750000.25})
        await self.workbench.refresh_library()
        self.assertEqual(self.shelf_books()['42']['last_read_at'], 1791750000.25)
        restarted = server.Workbench()
        self.assertEqual(restarted.library_state()['books'][0]['last_read_at'], 1791750000.25)

    async def test_partial_sync_retains_groups_membership_and_later_source_updates(self):
        await self.workbench.refresh_library()
        self.workbench.library_organization.remember_updated('42', '2026-10-10T13:00:00')
        self.workbench.reader.library_result = {'account': {'state': 'connected'}, 'complete': False,
                                              'folders': [{'id': '14', 'name': '新组'}],
                                              'books': [{'id': '42', 'title': '甲', 'updated_at': '2026-10-11 12:00:00'}]}
        await self.workbench.refresh_library()
        self.assertEqual(self.shelf_books()['42']['folder_ids'], ['jjwxc_12'])
        self.assertEqual(self.shelf_books()['42']['updated_at'], '2026-10-11 12:00:00')
        self.assertEqual(set(self.shelf_books()), {'42', '43'})
        self.assertEqual({folder['id'] for folder in self.workbench.library_state()['folders']}, {'jjwxc_12', 'jjwxc_13', 'jjwxc_14'})

    async def test_unknown_book_or_folder_rejected_before_assignment_mutates(self):
        await self.workbench.refresh_library()
        for payload, status in (({'book_ids': ['42', '999'], 'folder_id': 'jjwxc_12'}, 404),
                                ({'book_ids': ['42'], 'folder_id': 'missing'}, 404),
                                ({'book_ids': ['../42'], 'folder_id': None}, 400)):
            with self.subTest(payload=payload), self.assertRaises(HTTPException) as error:
                await server.assign_library_folder(self.request(**payload))
            self.assertEqual(error.exception.status_code, status)
        self.assertEqual(self.workbench.library_organization.data['assignments'], {})
        with self.assertRaises(HTTPException) as error:
            await server.create_library_folder(self.request(name='  '))
        self.assertEqual(error.exception.status_code, 400)
        with self.assertRaises(HTTPException) as error:
            await server.mark_library_read(self.request(book_id='missing'))
        self.assertEqual(error.exception.status_code, 404)

    async def test_polling_library_uses_memory_without_saving_or_reading_catalogs(self):
        await self.workbench.refresh_library()
        with patch.object(self.workbench.library_organization, 'save') as save, patch.object(server.catalog, 'cached') as cached:
            for _ in range(5):
                self.workbench.library_state()
        save.assert_not_called()
        cached.assert_not_called()

    def prepare_large_shelf(self):
        identifiers = [str(10000 + index) for index in range(1100)]
        self.workbench.library_cache = server.clean_library({
            'account': {'state': 'connected'}, 'complete': True,
            'folders': [{'id': '12', 'name': '原分组'}],
            'books': [{'id': identifier, 'title': f'作品 {identifier}', 'source_folder_ids': ['12']} for identifier in identifiers],
        })
        self.workbench.library_organization.merge_source(self.workbench.library_cache['folders'])
        server.write_json(self.data / 'library.json', self.workbench.library_cache)
        return identifiers

    async def test_bulk_move_1094_books_saves_once_preserves_unselected_and_survives_restart(self):
        identifiers = self.prepare_large_shelf()
        selected, unselected = identifiers[:1094], identifiers[1094:]
        destination = (await server.create_library_folder(self.request(name='批量整理')))['folder_id']
        untouched = (await server.create_library_folder(self.request(name='原本机分组')))['folder_id']
        await server.assign_library_folder(self.request(book_ids=[unselected[0]], folder_id=untouched))
        organization = self.workbench.library_organization
        with patch.object(organization, 'save', wraps=organization.save) as save, patch.object(server.catalog, 'cached') as cached:
            response = await server.assign_library_folder(self.request(book_ids=selected, folder_id=destination))
        save.assert_called_once()
        cached.assert_not_called()
        books = {book['id']: book for book in response['books']}
        self.assertEqual(len(books), 1100)
        for identifier in selected:
            self.assertEqual(books[identifier]['folder_ids'], [destination])
        self.assertEqual(books[unselected[0]]['folder_ids'], [untouched])
        for identifier in unselected[1:]:
            self.assertEqual(books[identifier]['folder_ids'], ['jjwxc_12'])
        restarted = server.Workbench()
        self.assertEqual({book['id']: book['folder_ids'] for book in restarted.library_state()['books']},
                         {identifier: book['folder_ids'] for identifier, book in books.items()})

    async def test_invalid_last_book_rejects_entire_1094_book_move_without_saving(self):
        selected = self.prepare_large_shelf()[:1094]
        destination = (await server.create_library_folder(self.request(name='批量整理')))['folder_id']
        before = (self.data / 'library-organization.json').read_bytes()
        organization = self.workbench.library_organization
        with patch.object(organization, 'save', wraps=organization.save) as save, self.assertRaises(HTTPException) as error:
            await server.assign_library_folder(self.request(book_ids=[*selected, 'missing-book'], folder_id=destination))
        self.assertEqual(error.exception.status_code, 404)
        save.assert_not_called()
        self.assertEqual(organization.data['assignments'], {})
        self.assertEqual((self.data / 'library-organization.json').read_bytes(), before)
        self.assertTrue(all(book['folder_ids'] == ['jjwxc_12'] for book in self.workbench.library_state()['books']))
