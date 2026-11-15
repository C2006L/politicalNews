"""Read only the local runtime identity and patch hashes, never container secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


def verify(deployment: Path, lock: dict[str, Any], docker: str) -> dict[str, Any]:
    patches = {}
    root = deployment.resolve()
    for name, expected in lock['patches'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Patch path must stay within deployment')
        patches[name] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected
    try:
        process = subprocess.run([docker, 'inspect', lock['container'], '--format', '{{.Image}}'],
                                 capture_output=True, text=True, timeout=10, check=False)
        image_matches = process.returncode == 0 and process.stdout.strip() == lock['image_id']
        available = process.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        available = image_matches = False
    snapshot_matches = True
    if lock.get('snapshot_container'):
        try:
            result = subprocess.run([docker, 'inspect', lock['snapshot_container'], '--format', '{{.Image}}'],
                                    capture_output=True, text=True, timeout=10, check=False)
            snapshot_matches = result.returncode == 0 and result.stdout.strip() == lock['image_id']
        except (OSError, subprocess.TimeoutExpired):
            snapshot_matches = False
    try:
        compose_pinned = lock['image'] in (root / 'compose.yaml').read_text(encoding='utf-8')
    except OSError:
        compose_pinned = False
    return {'container_available': available, 'running_image_matches': image_matches,
            'snapshot_exporter_matches': snapshot_matches,
            'compose_image_pinned': compose_pinned, 'patches': patches,
            'matches': available and image_matches and snapshot_matches and compose_pinned and all(patches.values())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, default=Path(__file__).resolve().parents[2] / 'werss-local/local-deploy')
    parser.add_argument('--lock', type=Path, default=Path(__file__).resolve().parents[1] / 'deploy/local-stack.lock.json')
    parser.add_argument('--docker', default=shutil.which('docker') or 'docker')
    args = parser.parse_args()
    report = verify(args.deployment, json.loads(args.lock.read_text(encoding='utf-8')), args.docker)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(not report['matches'])
