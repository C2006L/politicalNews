"""History correctness: real DB imports with offline, deterministic transports."""
from datetime import date, datetime, time as day_time, timezone
import json
import time

import pytest

from app.database import SessionLocal
from app.material_history import (HistoryBusy, add_item, biz_for, configured, discover, leased,
                                  read_state, run_batch, save, summarize, PREFIX)
from app.models import AppState, LearningMaterial


def url(mid):
    return f'https://mp.weixin.qq.com/s?__biz=MzI0MzAxMzQzNA==&mid={mid}&idx=1'


def page(mid, links='', title='国务院印发乡村振兴实施方案', day=None, dated=True):
    timestamp = int(datetime.combine(day or date.today(), day_time(1), timezone.utc).timestamp())
    return (f'<html><meta property="og:title" content="{title}"><span id="js_name">肖秀荣教授</span>'
            f'<script>var biz="MzI0MzAxMzQzNA==";\nvar mid="" || "{mid}";\nvar idx="1";\n'
            + (f'var ct="{timestamp}";' if dated else '')
            + '</script><div id="js_content"><p>' + '政策明确推进乡村振兴，加强基层治理，落实民生保障与公共服务。' * 8
            + '</p>' + links + '</div></html>')


def enqueue(mid='1', raw=None):
    source = configured('xiao')
    with leased(source) as (state, token):
        add_item(state, {'url': url(mid), 'title': ''}, 'test_seed', biz_for(source))
        if raw:
            discover(state, raw, 'test_seed', biz_for(source))
        save('xiao', state, token)


def test_recursive_discovery_survives_restart_and_short_long_aliases_do_not_duplicate():
    enqueue()
    requests = []
    def fetch(address):
        requests.append(address)
        if address == url('1'):
            return page('1', f'<a href="{url(2)}">政策二</a><a href="https://mp.weixin.qq.com/s/alias2">政策二短链</a>'
                        '<a href="https://mp.weixin.qq.com/s?__biz=foreign&mid=5&idx=1">外号</a>')
        return page('2')
    first = run_batch('xiao', fetch, max_articles=1, max_pages=0, pause=0)
    assert first['batch']['imported'] == 1 and first['pending'] == 2
    second = run_batch('xiao', fetch, max_articles=5, max_pages=0, pause=0)
    assert second['discovered'] == second['body_available'] == 2
    assert second['batch']['imported'] == 1
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 2
    run_batch('xiao', fetch, max_articles=5, max_pages=0, pause=0)
    assert len(requests) == 3  # Two actual articles, one provisional alias check.
    assert second['account_coverage_rate'] is None


def test_album_cursor_committed_between_batches_and_repeated_page_is_not_complete():
    enqueue(raw='<div album_id="1234567890"></div>')
    calls = []
    def fetch(address):
        calls.append(address)
        mid = '2' if 'begin_msgid=1' in address else '1'
        return json.dumps({'base_resp': {'ret': 0}, 'getalbum_resp': {'article_count': 3, 'continue_flag': 1,
                          'article_list': [{'url': url(mid), 'msgid': mid, 'itemidx': '1'}]}})
    first = run_batch('xiao', fetch, max_articles=0, max_pages=1, pause=0)
    assert first['directories'][0]['pages'] == 1
    second = run_batch('xiao', fetch, max_articles=0, max_pages=1, pause=0)
    assert 'begin_msgid=1' in calls[1]
    assert second['directories'][0]['discovered'] == 2
    third = run_batch('xiao', fetch, max_articles=0, max_pages=1, pause=0)
    assert third['directories'][0]['status'] == 'stalled'
    assert third['account_total'] is None


def test_verification_pauses_only_source_and_requires_explicit_resume():
    enqueue()
    blocked = run_batch('xiao', lambda _: '<p>环境异常，去验证</p>', max_pages=0, pause=0)
    assert blocked['blocked'] and blocked['verification_required'] == 1
    def forbidden(_):
        pytest.fail('Paused source must not make HTTP requests')
    assert run_batch('xiao', forbidden, pause=0)['blocked']
    assert not run_batch('xu', forbidden, pause=0)['blocked']
    good = run_batch('xiao', lambda _: page('1'), max_pages=0, resume=True, pause=0)
    assert not good['blocked'] and good['body_available'] == 1


def test_lease_prevents_concurrent_workers_and_stale_write_after_reclaim():
    source = configured('xiao')
    with leased(source) as (state, token):
        with pytest.raises(HistoryBusy):
            run_batch('xiao')
        with SessionLocal() as db:
            row = db.get(AppState, PREFIX + 'xiao')
            current = json.loads(row.value)
            current['lease']['until'] = time.time() - 1
            row.value = json.dumps(current)
            db.commit()
        run_batch('xiao')
        with pytest.raises(HistoryBusy):
            save('xiao', state, token)


def test_scope_exclusions_keep_links_and_unknown_dates_are_not_import_dates():
    enqueue()
    unknown = page('1', f'<a href="{url(2)}">下一篇</a>', dated=False)
    result = run_batch('xiao', lambda _: unknown, max_articles=1, max_pages=0, pause=0)
    assert result['body_available'] == 0 and result['date_unknown'] == 2 and result['pending'] == 1
    # Unknown-time body still yielded its outgoing link, without claiming a 90-day match.
    assert len(read_state('xiao')['items']) == 2
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 0
    learning = page('2', title='早鸟带背D5 乡村振兴')
    result = run_batch('xiao', lambda _: learning, max_articles=1, max_pages=0, pause=0)
    assert result['learning'] == 1 and result['body_available'] == 0
    # Explicit learning scope imports the teaching article separately.
    result = run_batch('xiao', lambda _: learning, max_articles=1, max_pages=0, scope='learning', pause=0)
    assert result['body_available'] == 1 and result['recent_affairs'] == 0


def test_wrong_account_and_wrong_article_are_never_imported():
    enqueue()
    result = run_batch('xiao', lambda _: page('99'), max_articles=1, max_pages=0, pause=0)
    assert result['failed'] == 1 and result['body_available'] == 0
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 0


def test_transport_errors_back_off_without_recording_sensitive_error_messages():
    enqueue()
    def failing(_):
        raise TimeoutError('Cookie: secret must never be recorded')
    first = run_batch('xiao', failing, max_pages=0, pause=0)
    assert first['retry'] == 1
    second = run_batch('xiao', failing, max_pages=0, pause=0)
    assert second['batch']['article_requests'] == 0
    assert 'secret' not in json.dumps(read_state('xiao'))


def test_coverage_page_uses_live_queue_and_never_claims_full_history(client):
    enqueue()
    run_batch('xiao')
    report = client.get('/api/material-coverage').json()
    xiao = next(r for r in report['items'] if r['key'] == 'xiao')
    assert xiao['history']['discovered'] == 1 and xiao['account_total'] is None
    html = client.get('/material-coverage').text
    assert '持续补采清单' in html and '尚未取得完整的全号历史分母' in html


def test_outside_window_is_separate_from_learning_and_window_moves():
    state = {'items': {'a': {'status': 'existing', 'quality': 'readable', 'title': '带背D5', 'category': 'textbook', 'day': '2026-08-21'}},
             'albums': {}, 'updated_at': 0, 'blocked': False}
    assert summarize(state, date(2026, 11, 18))['outside_window'] == 0
    assert summarize(state, date(2026, 11, 19))['outside_window'] == 1
    assert summarize(state, date(2026, 11, 19))['learning'] == 1


def test_literal_biz_fallback_is_verified_without_running_expressions():
    from app.link_import import literal_biz
    enqueue()
    raw = page('1').replace('var biz="MzI0MzAxMzQzNA==";', 'var biz="" || "MzI0MzAxMzQzNA==";')
    result = run_batch('xiao', lambda _: raw, max_articles=1, max_pages=0, pause=0)
    assert result['body_available'] == 1
    assert literal_biz('var biz="" || dangerous();') is None


def test_history_home_client_requirement_does_not_block_public_article():
    enqueue()
    def fetch(address):
        return '<p>请在微信客户端打开链接</p>' if 'profile_ext' in address else page('1')
    result = run_batch('xiao', fetch, max_articles=1, max_pages=0, check_history_entry=True, pause=0)
    assert result['body_available'] == 1 and not result['blocked']
    assert result['history_entry']['state'] == 'wechat_client_required'


def test_partial_body_is_saved_without_counting_as_readable():
    enqueue()
    raw = page('1', '<a href="https://mp.weixin.qq.com/s/next">下一页</a>')
    result = run_batch('xiao', lambda _: raw, max_articles=1, max_pages=0, pause=0)
    assert result['body_available'] == 0 and result['body_needs_review'] == 1
    assert result['batch']['imported'] == 1


def test_reviewed_failed_items_require_explicit_retry():
    enqueue()
    result = run_batch('xiao', lambda _: page('99'), max_articles=1, max_pages=0, pause=0)
    assert result['failure_reasons'] == {'url_identity_mismatch': 1}
    result = run_batch('xiao', lambda _: page('1'), max_articles=1, max_pages=0, retry_failed=True, pause=0)
    assert result['body_available'] == 1 and not result['failed']


def test_silent_javascript_challenge_pauses_without_a_false_body_failure():
    enqueue()
    raw = '<html><script>seajs.use("secitptpage/template/verify.js");</script></html>'
    result = run_batch('xiao', lambda _: raw, max_articles=5, max_pages=0, pause=0)
    assert result['blocked'] and result['verification_required'] == 1
    assert result['batch']['article_requests'] == 1 and result['failed'] == 0


def test_old_silent_challenges_are_reclassified_offline():
    from app.material_history import reconcile_cached_verification
    enqueue()
    run_batch('xiao', lambda _: '<p>unrecognized page</p>', max_articles=1, max_pages=0, pause=0)
    result = reconcile_cached_verification('xiao', lambda _: '<script>seajs.use("secitptpage/template/verify.js")</script>')
    assert result['blocked'] and result['verification_required'] == 1 and result['failed'] == 0


def test_fresh_queue_write_handles_leftover_driver_transaction():
    from sqlalchemy import event
    from app.database import engine
    source = configured('xiao')
    with leased(source) as (state, token):
        def stale_transaction(connection, record, proxy):
            connection.execute('BEGIN')
        event.listen(engine, 'checkout', stale_transaction, once=True)
        save('xiao', state, token)
    assert read_state('xiao')['lease'] is None


def test_album_terminal_page_with_count_gap_is_not_reported_complete():
    enqueue(raw='<div album_id="1234567890"></div>')
    raw = json.dumps({'base_resp': {'ret': 0}, 'getalbum_resp': {'article_count': 3, 'continue_flag': 0,
                     'article_list': [{'url': url('1'), 'msgid': '1', 'itemidx': '1'}]}})
    result = run_batch('xiao', lambda _: raw, max_articles=0, max_pages=1, pause=0)
    assert result['directories'][0]['status'] == 'count_mismatch'


def test_existing_non_affairs_fragment_is_repaired_without_new_scope_imports():
    from app.revisions import append_revision
    from app.materials import extract_material
    address = url('1')
    with SessionLocal() as db:
        material = LearningMaterial(source_id='existing-fragment', title='带背D5', url=address,
                                    account='肖秀荣教授', fingerprint='old', imported_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                    payload=extract_material('<p>'+'原有的教材片段。'*30+'</p>'))
        db.add(material)
        db.flush()
        append_revision(db, material, '<p>'+'原有的教材片段。'*30+'</p>')
        original_id = material.id
        db.commit()
    result = run_batch('xiao', lambda _: page('1', title='带背D5'), max_articles=1, max_pages=0, pause=0)
    assert result['body_available'] == 1 and result['recent_affairs'] == 0
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 1
        assert db.query(LearningMaterial).one().id == original_id


def test_existing_alias_fragment_is_repaired_even_when_canonical_body_already_exists():
    from app.revisions import append_revision
    from app.materials import extract_material
    from app.material_quality import latest_quality
    enqueue()
    run_batch('xiao', lambda _: page('1'), max_articles=1, max_pages=0, pause=0)
    with SessionLocal() as db:
        material = LearningMaterial(source_id='upstream-alias', title='国务院印发乡村振兴实施方案',
                                    url='https://mp.weixin.qq.com/s/newalias', account='肖秀荣教授', fingerprint='old',
                                    imported_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                    payload=extract_material('<p>'+'原来的导入片段。'*30+'</p>'))
        db.add(material)
        db.flush()
        append_revision(db, material, '<p>'+'原来的导入片段。'*30+'</p>')
        original_id = material.id
        db.commit()
    result = run_batch('xiao', lambda _: page('1'), max_articles=1, max_pages=0, pause=0)
    assert result['discovered'] == result['body_available'] == 1
    with SessionLocal() as db:
        assert db.query(LearningMaterial).count() == 2  # Preserve already issued IDs and references.
        assert latest_quality(db, db.get(LearningMaterial, original_id))['status'] == 'readable'
