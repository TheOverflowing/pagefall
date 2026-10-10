"""Serial translation orchestration with durable segment checkpoints."""
import copy
from .translation_store import digest, eligible_source, now


class TranslationPaused(Exception):
    pass


def public_config(settings):
    return {key: settings[key] for key in ('provider', 'model', 'base_url', 'source_language', 'target_language')}


async def run_translation(workbench, store, book, settings, api_key, scope):
    from .translation import VERSION, translate_chapter
    config = public_config(settings)
    index = store.create(book['id'], config)
    eligible = [chapter for chapter in book['chapters'] if eligible_source(chapter)]
    dependencies = store.source_dependencies(book)
    if scope == 'first':
        eligible = eligible[:1]
    workbench.translation = {'book_id': book['id'], 'running': True, 'done': 0, 'total': len(eligible), 'failed': 0,
                             'message': '正在准备翻译…', 'source_language': config['source_language'], 'target_language': config['target_language']}
    workbench.pause = False
    recent = []
    try:
        for position, chapter in enumerate(eligible, 1):
            if workbench.pause:
                raise TranslationPaused()
            prior = store.chapter(book, chapter, index=index, dependencies=dependencies)
            if prior and prior['status'] == 'done':
                recent.append({'chapter_id': chapter['id'], 'summary': index['chapters'][chapter['id']].get('summary', '')})
                workbench.translation['done'] += 1
                continue
            path_id = chapter['id']
            saved = store.read_work(book['id'], config['target_language'], path_id)
            plan = {'source_hash': digest(chapter['text']), 'source_title': chapter.get('title', ''),
                    'source_dependency': dependencies[path_id],
                    'translation_version': VERSION,
                    'config': config, 'glossary_revision': index['glossary_revision']}
            if saved.get('plan') != plan:
                # Keep the last successful translation until its replacement is
                # complete, even when source/terms/model changes invalidate it.
                saved = {'result': saved.get('result'), 'plan': plan,
                         'initial_glossary': copy.deepcopy(index['glossary']),
                         'initial_context': {'book_title': book.get('title', ''), 'author': book.get('author', ''),
                                             'previous_summaries': recent[-3:], 'continuity_summary': recent[-1]['summary'] if recent else ''}}
            store.write_work(book['id'], config['target_language'], path_id, saved)
            row = index['chapters'].get(path_id, {})
            row.update(status='translating', error='', updated_at=now())
            row.setdefault('source_hash', plan['source_hash'])
            row.setdefault('source_title', plan['source_title'])
            row.setdefault('source_dependency', plan['source_dependency'])
            row.setdefault('glossary_revision', index['glossary_revision'])
            index['chapters'][path_id] = row
            store.save(index)
            workbench.message = f'正在翻译第 {position}/{len(eligible)} 章 · {chapter["title"]}'
            workbench.translation['message'] = workbench.message

            async def checkpoint(payload):
                saved['checkpoint'] = payload
                store.write_work(book['id'], config['target_language'], path_id, saved)
                if workbench.pause:
                    raise TranslationPaused()

            try:
                result = await translate_chapter(chapter, config['source_language'], config['target_language'],
                    saved['initial_glossary'], saved['initial_context'], config, api_key,
                    checkpoint=saved.get('checkpoint'), on_checkpoint=checkpoint)
                text = result.get('translated_text') or result.get('text')
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('服务商没有返回完整译文。')
                store.merge_terms(index, result.get('glossary_updates') or [])
                saved['result'] = {'text': text, 'translated_title': result.get('translated_title') or chapter['title'],
                                   'summary': result.get('summary', ''), 'quality_issues': result.get('quality_issues') or [],
                                   'usage': result.get('usage') or {}, 'source_hash': plan['source_hash']}
                store.write_work(book['id'], config['target_language'], path_id, saved)
                index['chapters'][path_id] = {'status': 'done', 'source_hash': plan['source_hash'], 'source_title': plan['source_title'],
                    'source_dependency': plan['source_dependency'],
                    'glossary_revision': index['glossary_revision'], 'summary': result.get('summary', ''),
                    'translated_title': saved['result']['translated_title'], 'translated_characters': len(text),
                    'quality_issues_count': len(saved['result']['quality_issues']), 'updated_at': now(), 'error': ''}
                recent.append({'chapter_id': path_id, 'summary': result.get('summary', '')})
                store.save(index)
                workbench.translation['done'] += 1
            except TranslationPaused:
                row['status'] = 'paused'
                store.save(index)
                raise
            except Exception as exc:
                # Core provider failures expose a safe Chinese message, never
                # raw HTTP bodies or a credential-bearing request repr.
                from .translation import TranslationError
                message = str(exc) if isinstance(exc, TranslationError) else '翻译未完成，进度已保存，请稍后重试。'
                row.update(status='failed', error=message, updated_at=now())
                store.save(index)
                workbench.translation['failed'] += 1
                workbench.phase, workbench.message = 'error', message
                return
        workbench.phase = 'finished'
        workbench.message = f'翻译完成：已保存 {workbench.translation["done"]} 章译文。'
    except TranslationPaused:
        workbench.phase, workbench.message = 'paused', '翻译已暂停，已完成的段落已保存。'
    finally:
        workbench.translation['running'] = False
        workbench.translation['message'] = workbench.message
