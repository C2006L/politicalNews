"""Exercise the deployed adapter without importing or mutating a real WeRSS DB."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
import types
from contextlib import closing
from unittest.mock import Mock

import pytest


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    class Base:
        Gather_Content = True
        user_agent = 'offline-test'
        def get_token(self):
            pass
        def _load_weread_auth(self):
            self._weread_cookies = 'offline-placeholder'
            self._weread_ticket = ''
        def _get_proxies(self):
            return None
        def FillBack(self, CallBack, data, Ext_Data):
            return CallBack(data)
        def Over(self, CallBack=None):
            if CallBack:
                CallBack()
    for name in ['core', 'core.log', 'core.models', 'core.models.feed', 'core.print', 'core.wx', 'core.wx.model', 'core.wx.model.weread']:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules['core.log'].logger = types.SimpleNamespace(warning=lambda *a: None)
    sys.modules['core.models.feed'].Feed = types.SimpleNamespace
    sys.modules['core.print'].print_info = lambda *a: None
    sys.modules['core.wx.model.weread'].MpsWeread = Base
    transport = types.ModuleType('requests')
    transport.RequestException = type('RequestException', (Exception,), {})
    transport.get = Mock(side_effect=AssertionError('A real WeRSS HTTP transport is not used in offline tests'))
    monkeypatch.setitem(sys.modules, 'requests', transport)
    monkeypatch.setenv('POLITICAL_HISTORY_STATE_PATH', str(tmp_path / 'history.sqlite3'))
    spec = importlib.util.spec_from_file_location('isolated_werss_adapter', Path(__file__).resolve().parents[1] / 'integrations/werss/weread_mp.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def group(name, stamp=None):
    stamp = int(time.time()) - 60 if stamp is None else stamp
    return {'createTime': stamp, 'subReviews': [{'review': {
        'reviewId': 'MP_WXS_1_' + name, 'createTime': stamp,
        'mpInfo': {'title': '政策 ' + name, 'time': stamp, 'originalId': name}}}]}


def test_reinitializing_collector_preserves_qr_client_identity(adapter):
    client=adapter.MpsWereadMP()
    client._load_weread_auth()
    client.get_token();before=client._request_headers(True)
    client.get_token();after=client._request_headers(True)
    assert before==after
    assert after['User-Agent']==adapter.WEREAD_USER_AGENT
    assert client.headers['User-Agent']==after['User-Agent']


def client_for(module, rows, known=None):
    client = module.MpsWereadMP()
    known = known if known is not None else set()
    calls, saved = [], []
    def fetch(book, offset):
        calls.append(offset)
        return {'reviews': rows[offset:offset + 2]}
    def save(item):
        known.add(item['id'])
        saved.append(item)
        return True
    client._get_mp_articles_page = fetch
    client._get_mp_content = lambda _: '<div id="js_content">' + '已核对的原文。' * 30 + '</div>'
    client._is_article_gathered = lambda mp, review: review in known
    client._get_content_interval = lambda: 0
    client._get_page_interval = lambda: 0
    client.update_mps = Mock()
    return client, calls, saved, save


def read_state(module):
    with module.HistoryStore().claim('MP_WXS_1') as (state, _):
        return state.copy()


def collect(client, callback, pages=1):
    return client.get_Articles(Mps_id='MP_WXS_1', Mps_title='测试号', MaxPage=pages, CallBack=callback, Gather_Content=True)


def test_latest_already_saved_does_not_prevent_90_day_backfill_and_resume(adapter):
    rows = [group('A'), group('B'), group('C'), group('old', int(time.time()) - 100 * 86400)]
    client, calls, saved, callback = client_for(adapter, rows, {'MP_WXS_1_A'})
    collect(client, callback)
    assert [x['id'] for x in saved] == ['MP_WXS_1_B']
    assert read_state(adapter)['offset'] == 2 and not read_state(adapter)['complete']
    collect(client, callback)
    assert calls == [0, 0, 1]  # Fresh head and overlapping historical group.
    for _ in range(3):
        collect(client, callback)
    assert {x['id'] for x in saved} == {'MP_WXS_1_B', 'MP_WXS_1_C'}
    assert read_state(adapter)['complete']
    assert read_state(adapter)['end_reason'] == 'date_boundary'


def test_expired_history_authorization_is_not_hidden_by_successful_latest_fallback(adapter):
    client, _, _, callback = client_for(adapter, [])
    client._get_mp_articles_page = Mock(side_effect=adapter.WereadMPAPIError(-2012, 'login unavailable'))
    client._get_mp_cover = Mock(return_value={'reviewId': 'MP_WXS_1_L', 'title': '最新一篇'})
    with pytest.raises(adapter.WereadMPAPIError) as caught:
        collect(client, callback)
    assert caught.value.code == -2012
    state = read_state(adapter)
    assert state['status'] == 'authorization_or_limit' and state['latest_fallback']
    assert not state['complete'] and state['latest_saved'] == 1
    assert not state.get('last_list_success_at')
    assert state['last_body_success_at'] and not state.get('last_batch_success_at')
    assert state['run_finished_at'] >= state['run_started_at']
    client.update_mps.assert_not_called()


def test_success_times_survive_later_channel_failure_and_resume(adapter):
    client,calls,saved,callback=client_for(adapter,[group('A'),group('B'),group('C')])
    collect(client,callback)
    before=read_state(adapter)
    assert before['last_list_success_at'] and before['last_body_success_at']
    assert before['last_batch_success_at'] and before['offset']==2
    original=client._get_mp_articles_page
    client._get_mp_articles_page=Mock(side_effect=adapter.WereadMPAPIError(-2012,'restricted'))
    client._get_mp_cover=Mock(side_effect=adapter.WereadMPAPIError(-2012,'restricted'))
    with pytest.raises(adapter.WereadMPAPIError):collect(client,callback)
    failed=read_state(adapter)
    for key in ('last_list_success_at','last_body_success_at','last_batch_success_at'):
        assert failed[key]==before[key]
    with adapter.HistoryStore().claim('MP_WXS_1') as (state,lease):
        state['retry_after']=0
        adapter.HistoryStore().save('MP_WXS_1',state,lease)
    client._get_mp_articles_page=original
    collect(client,callback)
    assert calls[-2:]==[0,1]
    assert len({x['id'] for x in saved})==len(saved)==3


def test_verification_remains_paused_until_explicit_source_confirmation(adapter,monkeypatch):
    client,_,_,callback=client_for(adapter,[group('A')])
    original=client._get_mp_content
    client._get_mp_content=Mock(side_effect=adapter.WereadMPAPIError('verification_required','verify',False))
    with pytest.raises(adapter.WereadMPAPIError):collect(client,callback)
    assert read_state(adapter)['status']=='verification_required'
    client._get_mp_content=original
    client._get_mp_articles_page=Mock(return_value={'reviews':[group('A')]})
    with pytest.raises(adapter.WereadMPAPIError):collect(client,callback)
    client._get_mp_articles_page.assert_not_called()
    monkeypatch.setenv('POLITICAL_HISTORY_VERIFICATION_CONFIRMED','MP_WXS_1')
    collect(client,callback)
    assert read_state(adapter)['last_body_success_at']


def test_cover_fallback_does_not_fabricate_publication_time(adapter):
    client, _, saved, callback = client_for(adapter, [])
    client._get_mp_cover = lambda _: {'reviewId': 'MP_WXS_1_A', 'title': '最新一篇'}
    client._collect_latest_only('MP_WXS_1', 'MP_WXS_1', '测试号', True, callback, None)
    assert saved[0]['create_time'] == saved[0]['update_time'] == 0


def test_body_failure_keeps_page_cursor_and_resumes_without_duplicate_writes(adapter):
    client, _, saved, callback = client_for(adapter, [group('A'), group('B')])
    original = client._get_mp_content
    client._get_mp_content = lambda name: original(name) if name.endswith('_A') else (_ for _ in ()).throw(adapter.WereadMPAPIError('content_unconfirmed', 'restricted'))
    with pytest.raises(adapter.WereadMPAPIError):
        collect(client, callback)
    state = read_state(adapter)
    assert state['offset'] == 0 and state['body_pending'] == ['MP_WXS_1_B']
    client._get_mp_content = original
    collect(client, callback)
    assert [x['id'] for x in saved] == ['MP_WXS_1_A', 'MP_WXS_1_B']
    assert read_state(adapter)['body_pending'] == []


def test_callback_success_without_persistence_cannot_advance_cursor(adapter):
    client, _, _, _ = client_for(adapter, [group('A')])
    with pytest.raises(adapter.WereadMPAPIError) as caught:
        collect(client, lambda _: True)
    assert caught.value.code == 'write_unconfirmed'
    assert read_state(adapter)['offset'] == 0 and not read_state(adapter)['complete']


def test_changed_page_anchor_resets_instead_of_claiming_contiguous_history(adapter):
    rows = [group('A'), group('B'), group('C'), group('D')]
    client, _, _, callback = client_for(adapter, rows)
    collect(client, callback)
    rows[:] = [group('new1'), group('new2'), group('new3'), group('A'), group('B')]
    collect(client, callback)
    state = read_state(adapter)
    assert state['status'] == 'cursor_reset' and state['offset'] == 0 and not state['complete']


def test_api_error_after_a_valid_page_never_switches_to_cover(adapter):
    client, _, _, callback = client_for(adapter, [])
    client._get_mp_articles_page = Mock(side_effect=[{'reviews': [group('A')]}, adapter.WereadMPAPIError(-2041, 'restricted')])
    client._get_mp_cover = Mock()
    with pytest.raises(adapter.WereadMPAPIError):
        collect(client, callback, pages=2)
    client._get_mp_cover.assert_not_called()
    assert read_state(adapter)['status'] == 'access_restricted'


def test_history_lease_rejects_concurrent_and_stale_workers(adapter):
    store = adapter.HistoryStore()
    with store.claim('MP_WXS_1') as (state, token):
        with pytest.raises(adapter.WereadMPAPIError):
            with store.claim('MP_WXS_1'):
                pass
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute('UPDATE progress SET expires=0')
        with store.claim('MP_WXS_1') as (new_state, new_token):
            store.save('MP_WXS_1', new_state, new_token)
            with pytest.raises(adapter.WereadMPAPIError):
                store.save('MP_WXS_1', state, token)


def test_missing_list_schema_is_not_interpreted_as_empty_history(adapter):
    with pytest.raises(adapter.WereadMPAPIError):
        adapter.parse_mp_articles({'base_resp': {'ret': 0}})
    assert adapter.build_mp_link_from_review_id('MP_WXS_1_token_with_under') == 'https://mp.weixin.qq.com/s/token_with_under'
    assert adapter.build_mp_link_from_review_id('invalid') == ''


def test_content_keeps_original_container_and_metadata(adapter, monkeypatch):
    client, _, _, _ = client_for(adapter, [])
    # Use the real implementation, not client_for's deterministic body transport.
    raw = '<html><script>var ct="1778580000";</script><div id="js_content">' + '原文保留限定和来源。' * 20 + '</div></html>'
    monkeypatch.setattr(adapter.requests, 'get', lambda *a, **kw: types.SimpleNamespace(status_code=200, text=raw))
    client._load_weread_auth()
    captured=adapter.MpsWereadMP._get_mp_content(client, 'MP_WXS_1_A')
    assert 'var ct = "1778580000"' in captured and 'id="js_content"' in captured
    assert '原文保留限定和来源。'*20 in captured
    monkeypatch.setattr(adapter.requests, 'get', lambda *a, **kw: types.SimpleNamespace(status_code=200, text='<p>去验证</p>'))
    with pytest.raises(adapter.WereadMPAPIError):
        adapter.MpsWereadMP._get_mp_content(client, 'MP_WXS_1_A')
    monkeypatch.setattr(adapter.requests, 'get', lambda *a, **kw: types.SimpleNamespace(status_code=200, text='{"errCode":-2012}'))
    with pytest.raises(adapter.WereadMPAPIError) as caught:
        adapter.MpsWereadMP._get_mp_content(client, 'MP_WXS_1_A')
    assert caught.value.code==-2012  # Preserve the channel error rather than calling it a missing body.


def test_public_progress_reports_explicit_history_failure_without_ids_or_credentials(adapter, monkeypatch, client):
    from app.werss_history import progress
    store = adapter.HistoryStore()
    # The deployed reader deliberately uses the known filename beside the snapshot.
    deployed = store.path.parent / 'political-news-history.sqlite3'
    with store.claim('MP_WXS_1') as (state, lease):
        state.update(status='authorization_required', list_code=-2012, latest_fallback=True,
                     adapter_version='history-90-v1', discovered_ids=['private-id'])
        store.save('MP_WXS_1', state, lease)
    deployed.write_bytes(store.path.read_bytes())
    monkeypatch.setenv('WERSS_DATABASE_PATH', str(store.path.parent / 'learning.snapshot.db'))
    result = progress()['MP_WXS_1']
    assert result['status'] == 'authorization_or_limit' and result['discovered'] == 1
    assert 'private-id' not in json.dumps(result) and 'lease' not in result


def test_progress_detects_new_authorization_without_exposing_credentials(adapter,monkeypatch):
    from app.werss_history import progress
    store=adapter.HistoryStore()
    auth=store.path.parent/'wx.lic';auth.write_text('{}')
    with store.claim('MP_WXS_1') as (state,lease):
        state.update(list_code=-2012,auth_epoch=auth.stat().st_mtime_ns-1)
        store.save('MP_WXS_1',state,lease)
    deployed=store.path.parent/'political-news-history.sqlite3'
    deployed.write_bytes(store.path.read_bytes())
    monkeypatch.setenv('WERSS_DATABASE_PATH',str(store.path.parent/'learning.snapshot.db'))
    result=progress()['MP_WXS_1']
    assert result['authorization_changed']
    assert 'auth_epoch' not in result and 'cookie' not in result


def test_media_gap_does_not_block_later_policy_article_or_pretend_text_complete(adapter):
    client, _, saved, callback = client_for(adapter, [group('video'), group('policy')])
    original=client._get_mp_content
    def body(name):
        if name.endswith('_video'):
            raise adapter.WereadMPAPIError('media_or_short_body','image primary',False)
        return original(name)
    client._get_mp_content=body
    collect(client,callback)
    assert [r['id'] for r in saved]==['MP_WXS_1_policy']
    assert read_state(adapter)['body_pending']==['MP_WXS_1_video']


def test_coverage_page_distinguishes_latest_only_from_history_success(adapter, monkeypatch, client):
    store=adapter.HistoryStore()
    with store.claim('MP_WXS_3243013434') as (state,lease):
        state.update(status='authorization_required',list_code=-2012,latest_fallback=True)
        store.save('MP_WXS_3243013434',state,lease)
    deployed=store.path.parent/'political-news-history.sqlite3'
    deployed.write_bytes(store.path.read_bytes())
    monkeypatch.setenv('WERSS_DATABASE_PATH',str(store.path.parent/'learning.snapshot.db'))
    report=client.get('/api/material-coverage').json()
    row=next(r for r in report['items'] if r['key']=='xiao')
    assert row['collector']['status']=='authorization_or_limit'
    assert not row['collector']['complete']
    html=client.get('/material-coverage').text
    assert '历史通道受限' in html and '最新文章抓取成功不代表近90天补齐' in html


def test_original_snapshot_preserves_text_links_media_and_literal_metadata_without_site_bundles(adapter):
    from bs4 import BeautifulSoup
    raw=('<html><meta property="og:title" content="政策标题"><span id="js_name">半月谈</span>'
         '<div id="js_content"><p>适用条件与范围。</p><a href="https://mp.weixin.qq.com/s/next">原文链接</a><img src="https://example.test/photo.jpg"></div>'
         '<script>var biz="" || "MjM5OTU4Nzc0Mg==";\nvar mid="" || "123";\nvar idx="1";\nvar ct="1778580000";</script>'
         '<script>'+('bundled_runtime_'*40000)+'</script></html>')
    original=BeautifulSoup(raw,'html.parser');root=original.select_one('#js_content')
    captured=adapter.original_snapshot(raw,original,root)
    saved=BeautifulSoup(captured,'html.parser')
    assert saved.select_one('#js_content').get_text()==root.get_text()
    assert str(saved.select_one('#js_content'))==str(root)
    assert saved.select_one('meta[property="og:title"]')['content']=='政策标题'
    assert 'var mid = "" || "123";' in captured and 'var biz = "" || "MjM5OTU4Nzc0Mg==";' in captured
    assert len(captured)<len(raw)/100 and 'bundled_runtime_' not in captured


def test_snapshot_does_not_copy_executable_metadata_expressions(adapter):
    from bs4 import BeautifulSoup
    raw='<script>var ct=externalCall();\nvar biz="" || externalCall();</script><div id="js_content">文字</div>'
    soup=BeautifulSoup(raw,'html.parser')
    assert 'externalCall' not in adapter.original_snapshot(raw,soup,soup.select_one('#js_content'))


def test_limit_error_uses_cooldown_until_authorization_file_changes(adapter,monkeypatch,tmp_path):
    import os
    credential=tmp_path/'authorization.lic';credential.write_text('{}')
    monkeypatch.setenv('WEREAD_LIC_PATH',str(credential))
    client,_,_,callback=client_for(adapter,[])
    client._get_mp_articles_page=Mock(side_effect=adapter.WereadMPAPIError(-2012,'ambiguous authorization/limit'))
    client._get_mp_cover=lambda _: {'reviewId':'MP_WXS_1_A','title':'最新'}
    for _ in range(2):
        with pytest.raises(adapter.WereadMPAPIError):collect(client,callback)
    assert client._get_mp_articles_page.call_count==1
    assert read_state(adapter)['retry_after']>time.time()
    changed=credential.stat().st_mtime_ns+2_000_000_000
    os.utime(credential,ns=(changed,changed))
    client._get_mp_articles_page=Mock(return_value={'reviews':[]})
    collect(client,callback)
    assert read_state(adapter)['complete'] and not read_state(adapter)['latest_fallback']


def test_heartbeat_can_extend_a_live_lease_without_reviving_a_stale_owner(adapter,monkeypatch):
    clock=[1000.0]
    monkeypatch.setattr(adapter.time,'time',lambda:clock[0])
    store=adapter.HistoryStore()
    with store.claim('MP_WXS_1') as (state,token):
        clock[0]=1100
        assert store.renew('MP_WXS_1',token)
        clock[0]=1200  # The original, unrenewed lease would have expired at 1180.
        store.save('MP_WXS_1',state,token)
        assert not store.renew('MP_WXS_1','wrong-owner')
