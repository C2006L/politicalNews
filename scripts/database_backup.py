"""SQLite backup/restore without overwriting an existing destination.

Run: python scripts/database_backup.py SOURCE DESTINATION
For recovery, stop the app and restore to a NEW path, then change DATABASE_URL.
"""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def backup(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if not destination.parent.is_dir():
        raise ValueError("Destination directory must already exist")
    # Reserve the path exclusively, including when another backup runs concurrently.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Source database integrity check failed")
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity check failed")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(args.source, args.destination)
    print("Backup verified. Existing destination paths are never overwritten.")
