"""Run beside WeRSS in Linux: export a closed, rollback-journal SQLite snapshot."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def export_snapshot(source: Path, destination: Path, timeout_seconds: float = 120) -> None:
    if not 1 <= timeout_seconds <= 300:
        raise ValueError('Snapshot time budget must be 1..300 seconds')
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination:
        raise ValueError('Snapshot must not replace the source')
    fd, temporary = tempfile.mkstemp(prefix='snapshot-', suffix='.db', dir=destination.parent)
    os.close(fd)
    temp_path = Path(temporary)
    started = time.monotonic()
    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError('Snapshot export exceeded time budget')
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as src:
            with closing(sqlite3.connect(temp_path, timeout=5)) as target:
                src.backup(target, pages=128, progress=progress, sleep=0.05)
                target.execute('PRAGMA journal_mode=DELETE')
                if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Snapshot integrity check failed')
        # Never expose a half-written file. Busy Windows readers leave the old
        # snapshot in place; the next export retries instead of truncating it.
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(temp_path) + suffix).unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if args.interval < 30:
        parser.error('interval must be at least 30 seconds')
    while True:
        try:
            export_snapshot(args.source, args.destination, args.timeout)
            print('Snapshot exported.', flush=True)
        except Exception:
            print('Snapshot export failed; previous snapshot retained.', flush=True)
        time.sleep(args.interval)
