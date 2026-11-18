"""Bounded local operator run inside WeRSS; never invokes notification tasks.

Supply this file on docker exec stdin and bound MP_WXS ids as arguments.
The normal WeRSS callback persists articles; only counts and error codes leave
the container. Cursor/body gaps remain in the adapter's separate progress DB.
"""
import contextlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import os
from contextlib import closing


class Quiet:
    def write(self, value):
        return len(value)
    def flush(self):
        pass


quiet = Quiet()
with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
    from core.wx.model.weread_mp import MpsWereadMP, HistoryStore
    from jobs.article import UpdateArticle
    from core.db import DB

report = []
for mp_id in sys.argv[1:]:
    if not re.fullmatch(r'MP_WXS_\d+', mp_id):
        raise SystemExit('Invalid bound source id')
    result = {'source': mp_id}
    run_id=os.getenv('POLITICAL_HISTORY_RUN_ID','')
    started=time.time()
    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        feed = DB.get_mps(mp_id)
    if feed is None:
        result['state'] = 'source_not_subscribed'
    else:
        try:
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                collector = MpsWereadMP()
                collector.get_Articles(Mps_id=mp_id, Mps_title=feed.mp_name, MaxPage=1,
                                      Gather_Content=True, CallBack=UpdateArticle)
            result['state'] = 'batch_complete'
        except Exception as error:
            result.update(state='paused', code=getattr(error, 'code', type(error).__name__))
        with closing(sqlite3.connect(HistoryStore().path)) as db:
            row = db.execute('SELECT payload FROM progress WHERE mp_id=?', (mp_id,)).fetchone()
        if row:
            state = json.loads(row[0])
            result.update({key: state.get(key) for key in ['status', 'complete', 'offset', 'saved_this_batch', 'known_this_batch', 'list_code']})
            result.update(discovered=len(state.get('discovered_ids', [])), pending_bodies=len(state.get('body_pending', [])))
    report.append(result)
    if re.fullmatch(r'[0-9a-f]{32}',run_id):
        # Also acknowledge preflight failures (e.g. unsubscribed feed or missing
        # authorization), which may occur before the adapter claims a source.
        with closing(sqlite3.connect(HistoryStore().path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS operator_runs(run_id TEXT PRIMARY KEY,mp_id TEXT,started REAL,finished REAL,state TEXT,code TEXT)')
            db.execute('INSERT OR REPLACE INTO operator_runs VALUES(?,?,?,?,?,?)',
                       (run_id,mp_id,started,time.time(),result['state'],str(result.get('code',''))[:80]))
            db.execute('DELETE FROM operator_runs WHERE finished<?',(time.time()-30*86400,))
    Path('/app/data/political-news-history-batch.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
