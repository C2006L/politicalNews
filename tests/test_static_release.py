import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location('static_release', Path(__file__).parents[1] / 'scripts/static_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def edition(path, text='first'):
    path.mkdir()
    (path / 'static').mkdir()
    (path / 'index.html').write_text(text)
    (path / 'static/catalog.json').write_text('[]')
    files = {name: hashlib.sha256((path / name).read_bytes()).hexdigest() for name in ['index.html', 'static/catalog.json']}
    manifest = {'format': release.FORMAT, 'files': files,
                'release': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:20]}
    (path / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


def test_package_sends_only_changed_files_and_checks_local_integrity(tmp_path):
    old = edition(tmp_path / 'old')
    edition(tmp_path / 'new', 'second')
    bundle = tmp_path / 'delta.tar.gz'
    result = release.package(tmp_path / 'new', bundle, old)
    assert result['changed_files'] == 1
    with tarfile.open(bundle) as archive:
        assert archive.getnames() == ['manifest.json', 'files/index.html']
    (tmp_path / 'new/index.html').write_text('unexpected edit')
    with pytest.raises(ValueError, match='Changed'):
        release.package(tmp_path / 'new', tmp_path / 'bad.tar.gz')


def test_malicious_archive_cannot_escape_or_change_current(tmp_path):
    site = tmp_path / 'site'; edition(site)
    bundle = tmp_path / 'bad.tar.gz'; release.package(site, bundle)
    with tarfile.open(bundle, 'w:gz') as archive:
        for name, data in [('manifest.json', (site / 'manifest.json').read_bytes()), ('../../outside', b'bad')]:
            info = tarfile.TarInfo(name); info.size = len(data); archive.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match='unexpected'):
        release.apply(tmp_path / 'remote', bundle)
    assert not (tmp_path / 'outside').exists()
    assert not (tmp_path / 'remote/current').exists()


def test_failed_delta_retains_current_and_removes_incomplete_stage(tmp_path):
    old = edition(tmp_path / 'old')
    edition(tmp_path / 'new', 'second')
    bundle = tmp_path / 'delta.tar.gz'; release.package(tmp_path / 'new', bundle, old)
    with pytest.raises(ValueError, match='base missing'):
        release.apply(tmp_path / 'remote', bundle)
    assert not (tmp_path / 'remote/current').exists()
    assert not list((tmp_path / 'remote/releases').glob('.staging-*'))


def test_atomic_full_delta_and_rollback(tmp_path):
    # The deployment target is Linux; Windows developer-mode symlinks may also work.
    try:
        (tmp_path / 'probe').symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / 'probe').unlink()
    except OSError:
        pytest.skip('Directory symlinks unavailable on this Windows host')
    old = edition(tmp_path / 'old')
    edition(tmp_path / 'new', 'second')
    root = tmp_path / 'remote'
    first = tmp_path / 'first.tar.gz'; release.package(tmp_path / 'old', first)
    release.apply(root, first)
    second = tmp_path / 'second.tar.gz'; release.package(tmp_path / 'new', second, old)
    release.apply(root, second)
    assert (root / 'current/index.html').read_text() == 'second'
    assert (root / 'previous/index.html').read_text() == 'first'
    release.rollback(root)
    assert (root / 'current/index.html').read_text() == 'first'
