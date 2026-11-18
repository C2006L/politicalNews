"""Run inside WeRSS: probe bound source lists without collecting article bodies.

Usage: docker exec -i ... /app/env_x86_64/bin/python3 - MP_WXS_...
with this script supplied on stdin. Credentials stay inside the running container.
"""
import contextlib
import io
import json
import re
import sys
import os
import time
from pathlib import Path

diagnostics = io.StringIO()
with contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
    from core.wx.model.weread_mp import MpsWereadMP, HistoryStore, ADAPTER_VERSION, parse_mp_articles
    collector = MpsWereadMP()
    collector._load_weread_auth()

for mp_id in sys.argv[1:]:
    if not re.fullmatch(r'MP_WXS_\d+', mp_id):
        raise SystemExit('Invalid bound source id')
    store = HistoryStore()
    with store.claim(mp_id) as (state, lease):
        result = {'source': mp_id, 'adapter_version': ADAPTER_VERSION}
        try:
            with contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
                payload = collector._get_mp_articles_page(mp_id, 0)
                articles, groups = parse_mp_articles(payload)
            result.update(status='list_ready', list_code=0, probe_articles=len(articles), probe_groups=groups)
        except Exception as error:
            code = getattr(error, 'code', type(error).__name__)
            result.update(status='authorization_or_limit' if code == -2012 else
                          'authorization_required' if code in (-2010,'missing_cookie') else
                          'access_restricted' if code == -2041 else 'history_error', list_code=code)
        state.update({k: v for k, v in result.items() if k != 'source'})
        if result['list_code'] in (-2012,-2010,-2041,499):
            auth=Path(os.getenv('WEREAD_LIC_PATH','./data/wx.lic'))
            state.update(retry_after=time.time()+1800,auth_epoch=auth.stat().st_mtime_ns if auth.exists() else 0)
        elif result['list_code']==0:
            state['retry_after']=0
            state['last_list_success_at']=int(time.time())
        store.save(mp_id, state, lease)
    print(json.dumps(result), flush=True)
