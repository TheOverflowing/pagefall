"""On-demand reading does not alter download books or make AI calls."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from fastapi import HTTPException
from novelist import catalog as module,server
from novelist.browser import CaptureError
from test_translation_integration import http_json

BOOK={'id':'42','url':'https://www.jjwxc.net/onebook.php?novelid=42','title':'Fixture','author':'Author','platform':'jjwxc','chapters':[{'id':'1','url':'https://www.jjwxc.net/onebook.php?novelid=42&chapterid=1','title':'One','state':'pending','vip':False}]}

class CatalogTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.catalog=module.Catalog(Path(self.temp.name)/'catalogs')
  self.reader=SimpleNamespace(scan=AsyncMock(return_value=BOOK.copy()),page=SimpleNamespace(evaluate=AsyncMock(return_value={'description':'Fixture synopsis'})),close=AsyncMock(),capture=AsyncMock(return_value={'text':'A self-authored chapter.','method':'jjwxc_api','incomplete':False}))
  self.patcher=patch.object(module,'Reader',return_value=self.reader);self.patcher.start();self.addCleanup(self.patcher.stop)
  self.extra=patch.object(module,'public_app_metadata',return_value={});self.extra.start();self.addCleanup(self.extra.stop)
 async def test_details_are_cached_without_creating_a_download(self):
  value=await self.catalog.fetch(BOOK['url']);self.assertEqual(value['metadata']['description'],'Fixture synopsis')
  await self.catalog.fetch(BOOK['url']);self.reader.scan.assert_awaited_once()
  self.assertFalse((Path(self.temp.name)/'books').exists())
 async def test_refresh_and_wrong_book_identity(self):
  await self.catalog.fetch(BOOK['url']);await self.catalog.fetch(BOOK['url'],True);self.assertEqual(self.reader.scan.await_count,2)
  self.reader.scan.return_value={**BOOK,'id':'43'}
  with self.assertRaises(CaptureError):await self.catalog.fetch(BOOK['url'],True)
 async def test_reading_fetches_only_requested_chapter_and_reuses_cache(self):
  value=await self.catalog.read(BOOK,'1');self.assertEqual(value['text'],'A self-authored chapter.')
  await self.catalog.read(BOOK,'1');self.reader.capture.assert_awaited_once();self.assertEqual(self.reader.capture.call_args.args[0]['id'],'1')
  self.assertFalse((Path(self.temp.name)/'books').exists());self.reader.close.assert_awaited_with(remember=False)
 async def test_unavailable_and_incomplete_chapters_are_not_cached(self):
  with self.assertRaises(ValueError):await self.catalog.read(BOOK,'unknown')
  self.reader.capture.return_value={'text':'partial','incomplete':True}
  with self.assertRaises(CaptureError):await self.catalog.read(BOOK,'1')
  self.assertFalse((Path(self.temp.name)/'reading/42/1.json').exists())
  self.reader.capture.side_effect=CaptureError('本章尚未购买','blocked',False)
  with self.assertRaises(CaptureError):await self.catalog.read(BOOK,'1')
 async def test_locked_chapter_does_not_request_content(self):
  book={**BOOK,'chapters':[{**BOOK['chapters'][0],'locked':True}]}
  with self.assertRaises(CaptureError):await self.catalog.read(book,'1')
  self.reader.capture.assert_not_awaited()

class MetadataTests(unittest.TestCase):
 def test_author_dates_are_reused_from_disk_for_another_book_after_restart(self):
  html='<table class="author novel"><tr><td><a onclick="jump_book2(42,7)">One</a></td></tr><tr><td>发表时间：2026-03-30 19:00:00</td></tr></table><table class="author novel"><tr><td><a onclick="jump_book2(43,7)">Two</a></td></tr><tr><td>发表时间：2025-09-12 00:00:00</td></tr></table>'
  with tempfile.TemporaryDirectory() as directory,patch.object(module,'read_public_source',return_value=html.encode('gb18030')) as fetch:
   self.assertEqual(module.public_author_publications('7',directory,'42')['42'],'2026-03-30')
   module.AUTHOR_LOCKS.clear()
   self.assertEqual(module.public_author_publications('7',directory,'43')['43'],'2025-09-12')
   fetch.assert_called_once()
   self.assertTrue((Path(directory)/'7.json').exists())
 def test_author_tls_eof_is_a_network_failure_and_is_not_cached(self):
  import ssl
  from urllib.error import URLError
  error=URLError(ssl.SSLEOFError('EOF occurred in violation of protocol'))
  with tempfile.TemporaryDirectory() as directory,patch.object(module,'read_public_source',side_effect=error):
   with self.assertRaises(module.PublicMetadataInterrupted) as caught:module.public_author_publications('7',directory,'42')
   self.assertEqual(caught.exception.kind,'network')
   self.assertFalse((Path(directory)/'7.json').exists())
 def test_details_keep_basic_fields_when_the_author_host_has_failed(self):
  basic={'author_id':'7','basic_checked':True,'tags':['甜文'],'classification':{'favorite_count':172}}
  with patch.object(module,'public_app_basic_metadata',return_value=basic),patch.object(module,'public_author_publications',side_effect=module.PublicMetadataInterrupted('network')):
   result=module.public_app_metadata('42')
  self.assertEqual(result,basic)
  self.assertNotIn('classification_version',result)
 def test_public_requests_share_spacing_and_three_connection_limit(self):
  import concurrent.futures,threading,time
  starts=[];active=0;maximum=0;lock=threading.Lock()
  def run(*args,**kwargs):
   nonlocal active,maximum
   with lock:
    starts.append(time.monotonic());active+=1;maximum=max(maximum,active)
   time.sleep(.12)
   with lock:active-=1
   return SimpleNamespace(returncode=0,stdout=b'{}\n200')
  with patch('subprocess.run',side_effect=run),patch.object(module,'PUBLIC_REQUEST_INTERVAL',.04),patch.object(module,'PUBLIC_LAST_REQUEST',0):
   with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    list(pool.map(module.read_public_source,['https://app-cdn.jjwxc.net/test']*6))
  self.assertLessEqual(maximum,3)
  self.assertGreaterEqual(maximum,2)
  self.assertTrue(all(right-left>=.03 for left,right in zip(starts,starts[1:])),starts)
 def test_curl_transport_preserves_tls_limits_and_does_not_follow_redirects(self):
  url='https://app-cdn.jjwxc.net/androidapi/novelbasicinfo?novelId=42&versionCode=402'
  with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'{"novelId":"42"}\n200')) as run:
   self.assertEqual(module.read_public_source(url),b'{"novelId":"42"}')
  args=run.call_args.args[0]
  self.assertEqual(args[:2],['/usr/bin/curl','--disable'])
  for name,value in (('--proto','=https'),('--cacert','/etc/ssl/cert.pem'),('--max-time','15'),('--max-filesize','2097152'),('--max-redirs','0'),('--user-agent','Pagefall/0.7'),('--url',url)):
   self.assertEqual(args[args.index(name)+1],value)
  self.assertIn('--no-location',args)
  self.assertNotIn('--insecure',args)
  self.assertEqual(run.call_args.kwargs['timeout'],17)
  with patch('subprocess.run') as run:
   for rejected in ('http://wap.jjwxc.net/wapauthor/7','https://evil.invalid/info','https://name:password@wap.jjwxc.net/wapauthor/7'):
    with self.assertRaises(ValueError):module.read_public_source(rejected)
   run.assert_not_called()
 def test_curl_http_and_network_errors_keep_the_queue_failure_kinds(self):
  from urllib.error import HTTPError,URLError
  url='https://app-cdn.jjwxc.net/androidapi/novelbasicinfo?novelId=42&versionCode=402'
  with patch.object(module,'PUBLIC_REQUEST_INTERVAL',0):
   for status,kind in ((429,'rate_limit'),(503,'network')):
    with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'body\n'+str(status).encode())):
     with self.assertRaises(module.PublicMetadataInterrupted) as caught:module.public_app_basic_metadata('42')
     self.assertEqual(caught.exception.kind,kind)
   with patch('subprocess.run',return_value=SimpleNamespace(returncode=35,stdout=b'\n000')):
    with self.assertRaises(module.PublicMetadataInterrupted) as caught:module.public_app_basic_metadata('42')
    self.assertEqual(caught.exception.kind,'network')
   with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'not found\n404')):
    self.assertEqual(module.public_app_basic_metadata('42'),{})
   with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'redirect\n302')):
    with self.assertRaises(HTTPError):module.read_public_source(url)
   with patch('subprocess.run',return_value=SimpleNamespace(returncode=63,stdout=b'\n200')):
    with self.assertRaises(ValueError):module.read_public_source(url)
 def test_favorites_use_the_public_book_count_including_zero(self):
  value={'novelId':'42','novelbefavoritedcount':'65,960','novip_clicks':'99,999','renewDate':'2026-10-07 21:44:11','chapterdateNewest':'2026-10-07 21:44:11','cache_create_date':'2026-09-14 18:13:23'}
  result=module.parse_app_metadata(value,'42')['classification']
  self.assertEqual(result['favorite_count'],65960)
  self.assertNotIn('published_at',result)
  value['novelbefavoritedcount']='0'
  self.assertEqual(module.parse_app_metadata(value,'42')['classification']['favorite_count'],0)
  for invalid in ('','未知','1.2万',-1,True):
   value['novelbefavoritedcount']=invalid
   self.assertNotIn('favorite_count',module.parse_app_metadata(value,'42')['classification'])
 def test_author_publication_is_matched_to_the_book_and_author_not_updates(self):
  html='''<table class="author"><tr><td>最近更新时间：2026-10-07 21:44:11</td></tr></table>
  <table class="author novel"><tr><td><a onclick="jump_book2(42,7)">作品一</a></td></tr><tr><td>发表时间：2026-03-30 19:00:00</td></tr></table>
  <table class="novel author"><tr><td><a onclick="jump_book2(43,7)">作品二</a></td></tr><tr><td>发表时间：2025-09-12 00:00:00</td></tr></table>
  <table class="author novel"><tr><td><a onclick="jump_book2(44,8)">其他作者</a></td></tr><tr><td>发表时间：2024-02-01 00:00:00</td></tr></table>
  <table class="author novel"><tr><td><a onclick="jump_book2(45,7)">无发表日期</a></td></tr><tr><td>最近更新时间：2026-10-01 00:00:00</td></tr></table>'''
  self.assertEqual(module.parse_author_publications(html,'7'),{'42':'2026-03-30','43':'2025-09-12','45':None})
 def test_publication_dates_and_cached_counts_keep_missing_values_distinct(self):
  for invalid in ('0000-00-00 00:00:00','2026-02-30','2026-1-2','昨天','2026-03-30T16:00:00Z',None):
   self.assertIsNone(module.platform_date(invalid))
  self.assertEqual(module.platform_date('2026-03-30 00:00:00'),'2026-03-30')
  self.assertEqual(module.metadata_classification({'classification':{'published_at':'2026-03-30','favorite_count':0,'_checked_version':2}}),{'published_at':'2026-03-30','favorite_count':0})
  self.assertEqual(module.metadata_classification({'classification':{'published_at':'未知','favorite_count':True}}),{})
 def test_app_classification_uses_the_explicit_platform_fields(self):
  value={'novelId':'42','novelClass':'原创-百合-近代现代-爱情-互攻','mainview':'互攻','novelStep':'2','novelSize':'874,059'}
  result=module.parse_app_metadata(value,'42')['classification']
  self.assertEqual(result,{'originality':'原创','orientation':'百合','era':'近代现代','genre':'爱情','perspective':'互攻','status':'完结','word_count':874059})
  value.update(novelStep='1',mainview='主受')
  self.assertEqual(module.parse_app_metadata(value,'42')['classification']['status'],'连载')
  self.assertEqual(module.parse_app_metadata(value,'42')['classification']['perspective'],'主受')
 def test_unknown_progress_and_nonpositive_size_are_not_assumed_complete(self):
  result=module.parse_app_metadata({'novelId':'42','novelClass':'未知','mainview':'未知','novelStep':'99','novelSize':'0'},'42')['classification']
  self.assertEqual(result,{})
  self.assertEqual(module.parse_classification(word_count=True),{})
  self.assertEqual(module.parse_classification(word_count='1.2万'),{})
 def test_explicit_unclear_viewpoint_is_a_filterable_platform_choice(self):
  result=module.parse_app_metadata({'novelId':'42','novelClass':'原创-无CP-近代现代-奇幻','mainview':'不明'},'42')['classification']
  self.assertEqual(result['perspective'],'不明')
  self.assertNotIn('status',result)
  self.assertNotIn('perspective',module.parse_classification(perspective='未知'))
  self.assertNotIn('perspective',module.parse_classification())
  self.assertNotIn('status',module.metadata_classification({'source':'jjwxc','genre':'原创-无CP-近代现代-奇幻','status':'未知'}))
 def test_old_cached_web_metadata_keeps_the_same_classification(self):
  result=module.metadata_classification({'source':'jjwxc','genre':'衍生-纯爱-幻想未来-奇幻','perspective':'主攻','status':'已完结','word_count':'123,456'})
  self.assertEqual(result,{'originality':'衍生','orientation':'纯爱','era':'幻想未来','genre':'奇幻','perspective':'主攻','status':'完结','word_count':123456})
  self.assertIsNone(module.metadata_classification({'description':'A synopsis only'}))
 def test_platform_relationships_are_preserved_without_guessing_roles(self):
  value={'novelId':'42','protagonist':'主角：A，B','mainview':'主受','characters':[{'novelid':'42','character_id':'1','character_name':'A','character_masked':'0','character_pic':'https://evil.invalid/photo'},{'novelid':'42','character_id':'2','character_name':'B','character_masked':'0'},{'novelid':'42','character_id':'3','character_name':'Hidden','character_masked':'1'}],'character_relations':[{'start':'1','end':'2','direction':'right_to_left'},{'start':'1','end':'3','direction':'left_to_right'}]}
  result=module.parse_app_metadata(value,'42');self.assertEqual(result['protagonists'],'A，B');self.assertEqual(len(result['characters']),2);self.assertIsNone(result['characters'][0]['picture']);self.assertEqual(len(result['relationships']),1);self.assertEqual(result['perspective'],'主受');self.assertNotIn('attack',str(result))
 def test_wrong_identity_and_malformed_optional_arrays(self):
  self.assertEqual(module.parse_app_metadata({'novelId':'43'},'42'),{})
  result=module.parse_app_metadata({'novelId':'42','characters':{},'character_relations':{}},'42');self.assertEqual(result['characters'],[])

class CatalogRouteTests(unittest.IsolatedAsyncioTestCase):
 async def test_unknown_book_and_client_supplied_url_are_not_fetched(self):
  with patch.object(server.workbench,'get_book',side_effect=HTTPException(404)),patch.object(server.workbench,'library_cache',{'books':[]}),patch.object(server.catalog,'cached',return_value=None),patch.object(server.catalog,'fetch',AsyncMock())as fetch:
   status,_,_=await http_json('/api/book-details',method='POST',data={'id':'42','url':'https://evil.invalid'});self.assertEqual(status,404);fetch.assert_not_awaited()
 async def test_reader_uses_catalog_chapter_instead_of_client_url(self):
  with patch.object(server.catalog,'cached',return_value=BOOK),patch.object(server.catalog,'read',AsyncMock(return_value={'id':'1','text':'fixture'}))as read:
   status,_,_=await http_json('/api/read-chapter',method='POST',data={'book_id':'42','chapter_id':'1','url':'https://evil.invalid'});self.assertEqual(status,200);read.assert_awaited_once_with(BOOK,'1')
 async def test_text_draft_preserves_original_and_does_not_start_translation(self):
  text='  A self-authored sample.\n\nSecond paragraph.  '
  with patch.object(server.workbench,'save_book')as save:
   status,_,result=await http_json('/api/translation/drafts',method='POST',data={'title':'Example','text':text});self.assertEqual(status,200);book=save.call_args.args[0];self.assertEqual(book['chapters'][0]['text'],text);self.assertEqual(book['chapters'][0]['raw_text'],text);self.assertEqual(result['progress']['done'],1)
 async def test_cross_origin_and_invalid_drafts_never_save(self):
  with patch.object(server.workbench,'save_book')as save:
   for text in ('','x'*20001,[]):
    status,_,_=await http_json('/api/translation/drafts',method='POST',data={'text':text});self.assertEqual(status,400)
   status,_,_=await http_json('/api/translation/drafts',method='POST',data={'text':'fixture'},origin='https://evil.invalid');self.assertEqual(status,403);save.assert_not_called()

class MetadataDOMTests(unittest.IsolatedAsyncioTestCase):
 async def test_public_metadata_and_chapter_summaries_are_plain_text(self):
  from playwright.async_api import async_playwright
  async with async_playwright()as pw:
   browser=await pw.chromium.launch(headless=True)
   try:
    page=await browser.new_page()
    await page.set_content('<div id="novelintro">A self-authored synopsis.<script>window.fixtureSecret="not-book-text";</script></div><span itemprop="genre">原创-百合</span><span itemprop="updataStatus">连载</span><span itemprop="wordCount">5000字</span><li>作品视角：<span>主受</span></li><span>一句话简介：A short description.</span><table id="oneboolt"><tbody><tr itemprop="chapter"><td>1</td><td>First</td><td>A self-authored chapter summary.</td></tr><tr itemprop="chapter newestChapter"><td>2</td><td>Second</td><td>Second summary.</td></tr></tbody></table>')
    meta=await page.evaluate(module.DETAILS_SCRIPT);summaries=await page.evaluate(module.TOC_SUMMARIES_SCRIPT)
    self.assertEqual(meta['description'],'A self-authored synopsis.');self.assertEqual(meta['perspective'],'主受');self.assertEqual(meta['word_count'],'5000')
    self.assertEqual(summaries,{'1':'A self-authored chapter summary.','2':'Second summary.'})
   finally:await browser.close()

class PublicBookMetadataTests(unittest.TestCase):
 def test_saved_chapters_receive_catalog_summaries_without_source_mutation(self):
  import copy
  original=copy.deepcopy(BOOK)
  with patch.object(server.catalog,'cached',return_value={**BOOK,'chapters':[{**BOOK['chapters'][0],'summary':'Public chapter summary.'}],'metadata':{'description':'Public synopsis.'}}):
   result=server.public_book(BOOK)
  self.assertEqual(result['chapters'][0]['summary'],'Public chapter summary.');self.assertEqual(BOOK,original)
