"""Import manually verified public-page snapshots into the local WeRSS source DB.

This explicit maintenance command writes upstream; the application bridge remains read-only.
Requires a pre-write backup path and preserves existing source IDs and content.
"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse
import base64
import json
import sqlite3
import time

from app.materials import ACCOUNTS, extract_material
from scripts.database_backup import backup


def import_snapshots(source: Path, snapshots: Path, backup_path: Path, source_stopped: bool = False) -> dict[str, int]:
    source = source.resolve(strict=True)
    if (source.parent / 'learning.snapshot.db').exists() and not source_stopped:
        raise ValueError('Stop WeRSS and its snapshot exporter before upstream maintenance; explicitly acknowledge source_stopped')
    records = {}
    aliases = {}
    alias_records = {}
    for path in snapshots.glob('*.json'):
        if path.name == 'capture-report.json':
            continue
        row = json.loads(path.read_text(encoding='utf-8'))
        account = row['account_id']
        biz = base64.b64encode(account.removeprefix('MP_WXS_').encode()).decode()
        if account not in ACCOUNTS or row['biz'] != biz or row['account'] != ACCOUNTS[account]:
            raise ValueError('Unrecognized article account')
        mid, idx = row['mid'], row['idx']
        if not isinstance(mid, str) or not mid.isascii() or not mid.isdigit() or not idx.isascii() or not idx.isdigit():
            raise ValueError('Invalid article identity')
        parsed = urlparse(row['url']); query = parse_qs(parsed.query)
        if parsed.scheme != 'https' or parsed.netloc != 'mp.weixin.qq.com' or parsed.username or parsed.password:
            raise ValueError('Invalid article URL')
        if parsed.path == '/s':
            if query.get('__biz') != [biz] or query.get('mid') != [mid] or query.get('idx') != [idx]:
                raise ValueError('Article URL identity mismatch')
        elif not parsed.path.startswith('/s/'):
            raise ValueError('Invalid article URL path')
        if not isinstance(row['title'], str) or not row['title']:
            raise ValueError('Missing title')
        extract_material(row['html'])
        identity = account, mid, idx
        if identity in records and records[identity]['title'] != row['title']:
            raise ValueError('Conflicting snapshot identity')
        records[identity] = row
        if row.get('source_id'):
            if identity in aliases and aliases[identity] != row['source_id']:
                raise ValueError('Conflicting existing source IDs')
            aliases[identity] = row['source_id']
            alias_records[identity] = row
    if not records:
        raise ValueError('No verified snapshots')
    backup(source, backup_path)
    report = {'added': 0, 'existing': 0, 'snapshots': len(records)}
    with closing(sqlite3.connect(source, timeout=10)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for identity, row in records.items():
            account, mid, idx = identity
            # Existing short URLs are mapped only through captured, identity-verified pages.
            existing_id = aliases.get(identity)
            if existing_id:
                saved = db.execute('SELECT mp_id,title,url FROM articles WHERE id=?', (existing_id,)).fetchone()
                alias_row = alias_records[identity]
                if not saved or saved != (account, alias_row['title'], alias_row['url']):
                    raise ValueError('Existing source changed; no articles imported')
            else:
                existing_id = account.removeprefix('MP_WXS_') + '-album_' + mid + '_' + idx
                saved = db.execute('SELECT mp_id FROM articles WHERE id=? OR url=?', (existing_id, row['url'])).fetchone()
                if saved and saved[0] != account:
                    raise ValueError('Source identity collision')
            metadata = {'public_album_identity': {'account_id': account, 'mid': mid, 'idx': idx},
                        'discovered_via': 'public_study_album', 'captured_at': row['captured_at']}
            if saved:
                report['existing'] += 1
                continue
            timestamp = int(row['ct']) if str(row.get('ct', '')).isdigit() else 0
            if timestamp < 0 or timestamp > time.time() + 86400:
                timestamp = 0
            db.execute('INSERT INTO articles '
                       '(id,mp_id,title,url,content,content_html,has_content,status,publish_time,create_time,'
                       'created_at,updated_at,updated_at_millis,extinfo,is_read,is_favorite) '
                       'VALUES (?,?,?,?,?,?,1,1,?,?,?,?,?,?,0,0)',
                       (existing_id, account, row['title'], row['url'], row['html'], row['html'], timestamp, timestamp,
                        datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S'),
                        int(time.time()), int(time.time()*1000), json.dumps(metadata, ensure_ascii=False)))
            report['added'] += 1
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('snapshots', type=Path)
    parser.add_argument('backup', type=Path)
    parser.add_argument('--source-stopped', action='store_true', help='Acknowledge that WeRSS and the snapshot exporter are stopped')
    args = parser.parse_args()
    print(json.dumps(import_snapshots(args.source, args.snapshots, args.backup, source_stopped=args.source_stopped)))
