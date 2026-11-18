import importlib
import json


def test_collector_success_times_and_backoff_are_distinct_from_attempts(client,monkeypatch):
    from app import material_coverage
    import time
    monkeypatch.setattr(material_coverage,'collector_progress',lambda: {'MP_WXS_3243013434':{
        'status':'authorization_or_limit','discovered':20,'pending_bodies':4,'checked_at':time.time(),
        'last_list_success_at':time.time()-7200,'last_body_success_at':None,'running':False,
        'retry_after':time.time()+1800}})
    data=client.get('/api/material-coverage').json()
    entry=next(r for r in data['items'] if r['key']=='xiao')
    assert entry['collector']['last_body_success_at'] is None
    page=client.get('/material-coverage')
    assert page.status_code==200
    assert '最近列表成功' in page.text and '最近正文入库：尚无可核查记录' in page.text
    assert '正文待补 4 篇' in page.text and '最早重试' in page.text


def test_unknown_totals_and_restricted_history_are_not_reported_as_zero_or_complete(client,tmp_path,monkeypatch):
    module=importlib.import_module('app.material_coverage')
    monkeypatch.setattr(module,'REPORT_ROOT',tmp_path)
    report=tmp_path/'recent-affairs-20261117';report.mkdir()
    (report/'history-entry-check.json').write_text(json.dumps([{'key':'banyuetan','verification':True}]),encoding='utf-8')
    data=client.get('/api/material-coverage').json()
    row=next(r for r in data['items'] if r['key']=='banyuetan')
    assert row['account_total'] is None and row['discovered'] is None
    assert row['history_access']=='wechat_client_required'
    assert row['state']=='unknown_directory'
    assert '近90天覆盖率尚不能计算' in client.get('/material-coverage').text


def test_linked_batch_verification_does_not_claim_new_body(client,tmp_path,monkeypatch):
    module=importlib.import_module('app.material_coverage')
    monkeypatch.setattr(module,'REPORT_ROOT',tmp_path)
    report=tmp_path/'body-repair-20261117'/'linked-batch';report.mkdir(parents=True)
    (report/'report.json').write_text(json.dumps({'sources':[{'key':'dengta','linked_discovered':30,'imported':0,'existing':0,'remaining':29,'blocked':True}]}),encoding='utf-8')
    row=next(r for r in client.get('/api/material-coverage').json()['items'] if r['key']=='dengta')
    assert row['state']=='verification_required' and row['imported']==0
    assert row['linked_batch']['linked_discovered']==30
    assert '待采链接不计为已收录正文' in client.get('/material-coverage').text
