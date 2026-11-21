"""Create an isolated SQLite snapshot, then export a public static edition."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description='导出时政自习室静态阅读版，不上传服务器')
    parser.add_argument('--database', type=Path, default=ROOT / 'test.db')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--days', type=int, default=90)
    args = parser.parse_args()
    if not 1 <= args.days <= 3660:
        parser.error('days 必须在 1 至 3660 之间')
    if args.output.exists():
        parser.error('输出目录已存在，请使用新目录保留上一版')
    source = args.database.resolve(strict=True)
    args.output = args.output.resolve()
    if source == args.output or source in args.output.parents:
        parser.error('输出目录不能覆盖数据库')
    # Temporary full DB is kept outside the public export, then removed.
    private = ROOT / 'dist'
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.static-snapshot-', dir=private) as folder:
        snapshot = Path(folder) / 'snapshot.db'
        src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        os.environ.update(DATABASE_URL='sqlite:///' + snapshot.as_uri() + '?mode=ro&uri=true',
                          SCHEDULER_ENABLED='0', AUTO_SYNC_ON_STARTUP='0', BOOTSTRAP_RECENT_NEWS_ON_STARTUP='0',
                          BASE_PATH='', STRICT_TEXT_QUALITY='1', WERSS_DATABASE_PATH='', WERSS_API_URL='',
                          WERSS_API_TOKEN_FILE='', WERSS_RECOVERY_TOKEN_FILE='')
        from app.database import SessionLocal, engine
        from app.static_export import build
        try:
            with SessionLocal() as db:
                manifest = build(db, args.output, args.days)
            print(json.dumps({k: v for k, v in manifest.items() if k != 'files'}, ensure_ascii=False, indent=2))
            print('本机预览：python -m http.server 8018 --bind 127.0.0.1 --directory "' + str(args.output) + '"')
        finally:
            engine.dispose()


if __name__ == '__main__':
    main()
