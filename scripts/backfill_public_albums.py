"""Resume bounded public history discovery; this is not whole-account coverage."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import engine, init_db
from app.material_history import HistoryBusy, run_batch
from app.material_sources import sources


class SameHost(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname != 'mp.weixin.qq.com' or parsed.username or parsed.password:
            raise ValueError('Offsite redirect rejected')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-articles', type=int, default=20, help='Total article requests across this batch')
    parser.add_argument('--max-pages', type=int, default=3, help='Album pages per source; cursors survive later runs')
    parser.add_argument('--scope', choices=['recent-affairs', 'learning', 'all'], default='recent-affairs')
    parser.add_argument('--source', action='append')
    parser.add_argument('--linked-only', action='store_true')
    parser.add_argument('--discover-only', action='store_true', help='Seed from saved pages without network')
    parser.add_argument('--session-stdin', action='store_true', help='Read {cookies, agent} through stdin; never store cookies')
    parser.add_argument('--resume', action='store_true', help='Resume after manually completing platform verification')
    parser.add_argument('--check-history-entry', action='store_true', help='Probe the public account home once per source')
    parser.add_argument('--refresh-directories', action='store_true', help='Recheck completed albums for new entries')
    parser.add_argument('--retry-failed', action='store_true', help='Retry failed/rejected items after manual review')
    args = parser.parse_args()
    if not 0 <= args.max_articles <= 300 or not 0 <= args.max_pages <= 30:
        parser.error('Article limit must be 0..300 and page limit 0..30')
    selected = [s for s in sources() if s['enabled'] and s['mp_id'] and (not args.source or s['key'] in args.source)]
    if args.source and set(args.source) - {s['key'] for s in selected}:
        parser.error('Requested source is unknown, disabled or not bound')
    priority = ['banyuetan', 'people_comment', 'dengta']
    selected.sort(key=lambda s: priority.index(s['key']) if s['key'] in priority else len(priority))
    args.output.mkdir(parents=True, exist_ok=True)
    if engine.url.get_backend_name() == 'sqlite' and engine.url.database:
        backup = args.output / 'before.db'
        if not backup.exists():
            with closing(sqlite3.connect(engine.url.database)) as src, closing(sqlite3.connect(backup)) as target:
                src.backup(target)
    init_db()
    session = json.load(sys.stdin) if args.session_stdin else {}
    opener = build_opener(SameHost())

    def fetch(url):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'mp.weixin.qq.com' or parsed.username or parsed.password:
            raise ValueError('Invalid public WeChat URL')
        request = Request(url, headers={'User-Agent': session.get('agent', 'Mozilla/5.0'), 'Cookie': session.get('cookies', '')})
        with opener.open(request, timeout=20) as result:
            data = result.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError('Response too large')
        raw = data.decode('utf-8')
        (args.output / (hashlib.sha256(url.encode()).hexdigest() + '.html')).write_text(raw, encoding='utf-8')
        return raw

    report = {'scope': args.scope, 'sources': [], 'account_coverage_rate': None}
    remaining = args.max_articles
    for source in selected:
        try:
            result = run_batch(source['key'], None if args.discover_only else fetch,
                               max_articles=remaining, max_pages=0 if args.linked_only else args.max_pages,
                               scope=args.scope, resume=args.resume, check_history_entry=args.check_history_entry,
                               refresh_directories=args.refresh_directories, retry_failed=args.retry_failed)
            remaining -= result.get('batch', {}).get('article_requests', 0)
            result.update(key=source['key'], name=source['name'])
        except HistoryBusy:
            result = {'key': source['key'], 'state': 'busy'}
        report['sources'].append(result)
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
