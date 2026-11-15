from hashlib import sha256
from types import SimpleNamespace

from scripts.verify_local_stack import verify


def test_locked_runtime_checks_only_identity_and_patch_files(tmp_path, monkeypatch):
    (tmp_path / 'patch.py').write_bytes(b'fixed patch')
    (tmp_path / 'compose.yaml').write_text('image: service@sha256:abc', encoding='utf-8')
    lock = {'patches': {'patch.py': sha256(b'fixed patch').hexdigest()},
            'image': 'service@sha256:abc', 'container': 'local-test', 'image_id': 'sha256:abc'}
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='sha256:abc\n')
    monkeypatch.setattr('scripts.verify_local_stack.subprocess.run', run)
    assert verify(tmp_path, lock, 'docker')['matches']
    assert calls == [['docker', 'inspect', 'local-test', '--format', '{{.Image}}']]
    (tmp_path / 'patch.py').write_bytes(b'changed')
    assert not verify(tmp_path, lock, 'docker')['matches']
    (tmp_path / 'compose.yaml').write_text('image: service:latest', encoding='utf-8')
    assert not verify(tmp_path, lock, 'docker')['compose_image_pinned']


def test_missing_docker_and_files_are_reported_without_raw_errors(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('private path and token must not escape')
    monkeypatch.setattr('scripts.verify_local_stack.subprocess.run', fail)
    report = verify(tmp_path, {'patches': {'missing.py': 'abc'}, 'image': 'x', 'container': 'x', 'image_id': 'x'}, 'docker')
    assert not report['matches'] and not report['container_available']
    assert 'private' not in str(report)
