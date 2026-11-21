"""Portable static release packaging/installing; Python standard library only."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile

FORMAT = 'political-news-static-v1'
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_manifest(data):
    if data.get('format') != FORMAT or not re.fullmatch('[a-f0-9]{20}', str(data.get('release', ''))):
        raise ValueError('Invalid reading edition manifest')
    files = data.get('files')
    if not isinstance(files, dict) or not 1 <= len(files) <= 15000:
        raise ValueError('Invalid file list')
    for name, checksum in files.items():
        path = PurePosixPath(name)
        if (not re.fullmatch(r'[a-zA-Z0-9_/.-]+', name) or path.is_absolute() or
                any(part in {'.', '..'} for part in name.split('/')) or
                (path.suffix not in {'.html', '.css', '.js', '.json'} and name != 'static/catalog.json.gz') or
                not re.fullmatch('[a-f0-9]{64}', str(checksum))):
            raise ValueError('Invalid public file path or checksum')
    if 'index.html' not in files or 'static/catalog.json' not in files:
        raise ValueError('Missing reading entry or catalog')
    if sha(json.dumps(files, sort_keys=True).encode())[:20] != data['release']:
        raise ValueError('Release identity does not match contents')
    return data


def package(site, output, previous=None):
    site, output = Path(site).resolve(), Path(output).resolve()
    manifest = checked_manifest(json.loads((site / 'manifest.json').read_text(encoding='utf-8')))
    old = checked_manifest(previous)['files'] if previous and previous.get('format') else {}
    changed = []
    total = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    try:
        with tarfile.open(temporary, 'w:gz') as archive:
            raw = json.dumps(manifest, ensure_ascii=False).encode()
            info = tarfile.TarInfo('manifest.json'); info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
            for name, checksum in manifest['files'].items():
                path = site / name
                if path.is_symlink() or not path.resolve().is_relative_to(site):
                    raise ValueError('Public file escapes export directory')
                content = path.read_bytes()
                if sha(content) != checksum or len(content) > MAX_FILE:
                    raise ValueError('Changed or oversized export file: ' + name)
                total += len(content)
                if total > MAX_TOTAL:
                    raise ValueError('Export exceeds 128 MiB deployment limit')
                if old.get(name) == checksum:
                    continue
                info = tarfile.TarInfo('files/' + name); info.size = len(content); info.mode = 0o644
                archive.addfile(info, io.BytesIO(content)); changed.append(name)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {'changed_files': len(changed), 'total_files': len(manifest['files']), 'upload_bytes': output.stat().st_size,
            'release': manifest['release']}


def apply(root, bundle):
    root, bundle = Path(root).resolve(), Path(bundle).resolve()
    root.mkdir(parents=True, exist_ok=True)
    releases = root / 'releases'
    if releases.is_symlink():
        raise ValueError('Release directory cannot be a symlink')
    releases.mkdir(exist_ok=True)
    lock = root / '.publish.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    staging = None
    try:
        with tarfile.open(bundle, 'r:gz') as archive:
            entries = {}
            total = 0
            for member in archive.getmembers():
                if not member.isfile() or member.name in entries or member.size > MAX_FILE or member.size < 0:
                    raise ValueError('Invalid or oversized archive member')
                total += member.size
                if total > MAX_TOTAL + 4 * 1024 * 1024:
                    raise ValueError('Archive exceeds deployment limit')
                entries[member.name] = member
            if 'manifest.json' not in entries:
                raise ValueError('Archive has no manifest')
            with archive.extractfile(entries['manifest.json']) as stream:
                manifest = checked_manifest(json.load(stream))
            allowed = {'manifest.json'} | {'files/' + name for name in manifest['files']}
            if set(entries) - allowed:
                raise ValueError('Archive contains unexpected files')
            current = root / 'current'
            old = current.resolve() if current.exists() else None
            if old and (not current.is_symlink() or old.parent != releases.resolve()):
                raise ValueError('Current release is outside release directory')
            final = releases / manifest['release']
            if final.exists() and old == final:
                # Still verify the currently published files before claiming success.
                for name, checksum in manifest['files'].items():
                    if not (final / name).is_file() or sha((final / name).read_bytes()) != checksum:
                        raise ValueError('Current release was modified: ' + name)
                return {'release': manifest['release'], 'unchanged': True}
            if final.exists():
                raise ValueError('Release already exists; use rollback or a new export')
            required = sum(m.size for name, m in entries.items() if name != 'manifest.json') + 32 * 1024 * 1024
            if shutil.disk_usage(root).free < required:
                raise ValueError('Not enough free disk space; current release retained')
            staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=releases))
            for name, checksum in manifest['files'].items():
                destination = staging / name; destination.parent.mkdir(parents=True, exist_ok=True)
                member = entries.get('files/' + name)
                if member:
                    with archive.extractfile(member) as stream:
                        data = stream.read(MAX_FILE + 1)
                    if sha(data) != checksum:
                        raise ValueError('Upload checksum mismatch: ' + name)
                    destination.write_bytes(data)
                else:
                    source = old / name if old else None
                    if source is None or not source.is_file() or source.is_symlink() or sha(source.read_bytes()) != checksum:
                        raise ValueError('Incremental base missing or changed: ' + name)
                    os.link(source, destination)
            (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            staging.rename(final); staging = None
            temporary_link = root / '.next-current'
            previous = root / '.next-previous'
            if temporary_link.exists() or temporary_link.is_symlink() or previous.exists() or previous.is_symlink():
                raise ValueError('Pending release link already exists')
            temporary_link.symlink_to(final, target_is_directory=True)
            if old:
                previous.symlink_to(old, target_is_directory=True)
                os.replace(previous, root / 'previous')
            os.replace(temporary_link, current)
            # No recursive deletion of historical releases: show their disk use
            # for explicit cleanup instead of guessing which user data is disposable.
            return {'release': manifest['release'], 'previous': old.name if old else None, 'unchanged': False}
    finally:
        os.close(descriptor); lock.unlink(missing_ok=True)
        if staging and staging.resolve().parent == releases.resolve() and staging.name.startswith('.staging-'):
            shutil.rmtree(staging)


def rollback(root):
    root = Path(root).resolve()
    previous, current = root / 'previous', root / 'current'
    target = previous.resolve()
    if not previous.is_symlink() or target.parent != (root / 'releases').resolve() or not target.is_dir():
        raise ValueError('No previous release available')
    checked_manifest(json.loads((target / 'manifest.json').read_text(encoding='utf-8')))
    temporary = root / '.rollback-current'
    temporary.symlink_to(target, target_is_directory=True)
    os.replace(temporary, current)
    return {'release': target.name}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    pack = commands.add_parser('package'); pack.add_argument('--site', required=True); pack.add_argument('--output', required=True)
    pack.add_argument('--previous')
    install = commands.add_parser('apply'); install.add_argument('--root', required=True); install.add_argument('--bundle', required=True)
    undo = commands.add_parser('rollback'); undo.add_argument('--root', required=True)
    args = parser.parse_args()
    if args.command == 'package':
        previous = json.loads(Path(args.previous).read_text(encoding='utf-8-sig')) if args.previous else None
        result = package(args.site, args.output, previous)
    elif args.command == 'apply':
        result = apply(args.root, args.bundle)
    else:
        result = rollback(args.root)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
