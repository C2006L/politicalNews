import io
import json

import pytest

from scripts import setup_werss_access as helper


def test_integration_key_is_saved_privately_and_existing_file_is_not_overwritten(tmp_path, monkeypatch, capsys):
    admin = tmp_path/'admin'
    admin.write_text('admin-private')
    output = tmp_path/'integration'
    requests = []
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self, request, **kwargs):
            requests.append(request)
            return Response(json.dumps({'code':0,'data':{'id':'new-key','key':'ak','secret':'sk','expires_at':'2027-02-13'}}).encode())
    monkeypatch.setattr(helper,'build_opener',lambda *args:Opener())
    helper.setup('http://127.0.0.1:8003',admin,output)
    assert json.loads(output.read_text())['secret']=='sk'
    assert json.loads(requests[0].data)['expires_in_days']==90
    assert json.loads(requests[0].data)['permissions']==['article:read']
    assert capsys.readouterr().out==''
    with pytest.raises(FileExistsError):
        helper.setup('http://127.0.0.1:8003',admin,output)
    assert len(requests)==1


def test_failed_persistence_revokes_only_the_created_key(tmp_path, monkeypatch):
    admin=tmp_path/'admin'; admin.write_text('secret')
    output=tmp_path/'integration'; paths=[]
    class Opener:
        def open(self,request,**kwargs):
            paths.append((request.get_method(),request.full_url))
            return io.BytesIO(json.dumps({'code':0,'data':{'id':'created-id','key':'ak','secret':'sk','expires_at':'later'}}).encode())
    monkeypatch.setattr(helper,'build_opener',lambda *args:Opener())
    def fail(*args,**kwargs):raise OSError('disk full')
    monkeypatch.setattr(helper.os,'fdopen',fail)
    with pytest.raises(OSError):helper.setup('http://127.0.0.1:8003',admin,output)
    assert not output.exists()
    assert paths[-1]==('DELETE','http://127.0.0.1:8003/api/v1/wx/auth/ak/created-id')
