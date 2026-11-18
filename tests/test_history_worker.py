import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import history_worker as worker
from app.tasks import setup_scheduler

SOURCE = dict(key='banyuetan', mp_id='MP_WXS_1', enabled=True)


@pytest.fixture
def runtime(monkeypatch):
    observed = {}
    monkeypatch.setenv('LOCAL_WERSS_HISTORY_WORKER_ENABLED', '1')
    monkeypatch.setenv('WERSS_DATABASE_PATH', 'D:/local/learning.snapshot.db')
    monkeypatch.setattr(worker, 'progress', lambda: observed)
    monkeypatch.setattr(worker, 'sources', lambda: [SOURCE])
    monkeypatch.setattr(worker.shutil, 'which', lambda _: 'docker.exe')
    return observed


def test_disabled_worker_cannot_dispatch(monkeypatch):
    transport=Mock(side_effect=AssertionError('must not dispatch'))
    monkeypatch.setattr(worker.subprocess,'run',transport)
    assert worker.run()['state']=='disabled'
    transport.assert_not_called()


def test_missing_docker_is_visible_without_dispatch(runtime,monkeypatch):
    monkeypatch.setattr(worker.shutil,'which',lambda _:None)
    assert worker.run()['state']=='docker_unavailable'
    assert worker.status()['error']=='docker_cli_missing'


def test_cooldown_verification_running_and_unregistered_sources_are_skipped():
    entries=[dict(SOURCE,mp_id=f'MP_WXS_{i}') for i in range(1,6)]
    entries += [dict(SOURCE,mp_id='injected; command'),dict(SOURCE,mp_id='MP_WXS_6',enabled=False)]
    observed={'MP_WXS_1':{'retry_after':2000},'MP_WXS_2':{'status':'verification_required'},
              'MP_WXS_3':{'running':True},'MP_WXS_4':{'complete':True,'checked_at':500}}
    assert [s['mp_id'] for s in worker.eligible(entries,observed,{},1000)]==['MP_WXS_5']


def test_recovers_from_saved_cursor_and_rotates_without_exposing_logs(runtime,monkeypatch):
    runtime['MP_WXS_1']={'offset':26,'retry_after':1,'status':'authorization_or_limit'}
    calls=[]
    def dispatch(command,**kwargs):
        calls.append(command)
        assert kwargs['timeout']==300 and kwargs['stdout']==subprocess.DEVNULL
        assert 'MaxPage=1' in kwargs['input']
        run_id=command[command.index('-e')+1].split('=',1)[1]
        runtime['MP_WXS_1']={'offset':52,'status':'history_partial','run_id':run_id,'run_finished_at':5000}
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(worker.subprocess,'run',dispatch)
    assert worker.run()['state']=='observed_complete'
    assert worker.run()['state']=='waiting'
    assert len(calls)==1 and calls[0][-1]=='MP_WXS_1'
    assert runtime['MP_WXS_1']['offset']==52
    assert 'run_id' not in worker.status()


def test_timeout_survives_restart_without_redispatch_and_resolves_only_matching_run(runtime,monkeypatch):
    transport=Mock(side_effect=subprocess.TimeoutExpired('docker',300))
    monkeypatch.setattr(worker.subprocess,'run',transport)
    assert worker.run()['state']=='awaiting_confirmation'
    receipt=worker.read()
    runtime['MP_WXS_1']={'run_id':'different-worker','run_finished_at':5000}
    assert worker.run()['state']=='awaiting_confirmation'
    assert transport.call_count==1
    runtime['MP_WXS_1'].update(run_id=receipt['run_id'])
    assert worker.run()['state']=='waiting'
    assert worker.status()['state']=='observed_complete'


def test_cli_success_without_adapter_receipt_is_never_reported_complete(runtime,monkeypatch):
    transport=Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(worker.subprocess,'run',transport)
    assert worker.run()['state']=='awaiting_confirmation'
    assert worker.run()['state']=='awaiting_confirmation'
    assert transport.call_count==1


def test_preflight_failure_receipt_allows_other_sources_to_continue(runtime,monkeypatch):
    receipt=worker._claim('MP_WXS_1',1000)
    monkeypatch.setattr(worker,'execution_receipt',lambda run_id: dict(source='MP_WXS_1',finished_at=1001)
                        if run_id==receipt['run_id'] else {})
    assert worker._finished(receipt,{})
    assert not worker._finished(dict(receipt,source='MP_WXS_2'),{})


def test_durable_claim_blocks_other_app_processes_and_stale_finish(runtime):
    receipt=worker._claim('MP_WXS_1',1000)
    assert receipt and not worker._claim('MP_WXS_2',1001)
    worker._write(dict(receipt,state='observed_complete'))
    later=worker._claim('MP_WXS_2',1002)
    assert later
    worker._write(dict(receipt,state='awaiting_confirmation'))
    assert worker.read()['run_id']==later['run_id']


def test_scheduler_registers_one_opt_in_history_job(runtime):
    jobs=[j for j in setup_scheduler().get_jobs() if j.id=='local_werss_history']
    assert len(jobs)==1 and jobs[0].max_instances==1
    assert jobs[0].trigger.interval.total_seconds()==300
