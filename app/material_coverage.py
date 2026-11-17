"""Observed collection coverage. Unknown account totals stay unknown."""
from collections import Counter
from datetime import datetime,timedelta
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from app.database import SessionLocal
from app.material_sources import sources
from app.models import LearningMaterial, MaterialRevision
from app.material_dates import page_date,recent
from app.editorial import select_document,decision_map

REPORT_ROOT=Path(__file__).resolve().parents[2]/'preview'

def coverage() -> dict[str, Any]:
    path=REPORT_ROOT/'public-coverage-20261117'/'report.json'
    try:report=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):report={}
    observations={r['key']:r for r in report.get('sources',[])}
    recent_path=path.parents[1]/'recent-affairs-20261117'/'report.json'
    try:recent_report=json.loads(recent_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):recent_report={}
    recent_observations={r['key']:r for r in recent_report.get('sources',[])}
    try:link_report=json.loads((REPORT_ROOT/'body-repair-20261117'/'linked-batch'/'report.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):link_report={}
    link_observations={r['key']:r for r in link_report.get('sources',[])}
    try:history_entries=json.loads((recent_path.parent/'history-entry-check.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):history_entries=[]
    restricted={row['key'] for row in history_entries if row.get('verification')}
    with SessionLocal() as db:
        materials=db.query(LearningMaterial).all()
        counts=Counter(row.account for row in materials)
        dates: dict[int,str]={}
        for revision in db.query(MaterialRevision).order_by(MaterialRevision.revision):
            parsed=page_date(revision.source_html)
            if parsed:dates.setdefault(revision.material_id,parsed)
        overrides=decision_map(db)
        dated=Counter(row.account for row in materials if row.id in dates)
        recent_counts=Counter(row.account for row in materials if recent(dates.get(row.id)))
        affairs=Counter(row.account for row in materials if recent(dates.get(row.id)) and select_document(row,overrides).selected)
    entries=[]
    for source in sources():
        observed=observations.get(source['key'])
        recent_batch=recent_observations.get(source['key'])
        link_batch=link_observations.get(source['key'])
        entries.append({'key':source['key'],'name':source['name'],'enabled':source['enabled'],
                        'imported':counts[source['name']],'account_total':None,
                        'dated':dated[source['name']], 'recent_90':recent_counts[source['name']],
                        'recent_affairs':affairs[source['name']], 'date_unknown':counts[source['name']]-dated[source['name']],
                        'recent_batch':{key:recent_batch.get(key) for key in ['discovered','scope_candidates','existing','imported','deferred_learning','deferred_non_affairs','outside_window','date_unknown','remaining']} if recent_batch else None,
                        'linked_batch':{key:link_batch.get(key) for key in ['linked_discovered','imported','existing','remaining','blocked']} if link_batch else None,
                        'directories':observed.get('directories',[]) if observed else [],
                        'discovered':observed.get('discovered') if observed else None,
                        'covered_in_batch':observed['existing']+observed['imported'] if observed else None,
                        'new_in_batch':observed.get('imported') if observed else None,
                        'failures':len(observed.get('failed',[])) if observed else None,
                        'remaining':observed.get('remaining') if observed else None,
                        'blocked':bool((observed or {}).get('blocked') or (link_batch or {}).get('blocked')),
                        'history_access':'wechat_client_required' if source['key'] in restricted else 'not_established',
                        'state':'verification_required' if (observed or {}).get('blocked') or (link_batch or {}).get('blocked') else
                                'partial' if observed and (observed.get('remaining') or observed.get('failed')) else
                                'directory_checked' if observed and observed.get('directories') else 'unknown_directory'})
    end=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    return {'total':sum(counts.values()),'at':report.get('at'),'items':entries,
            'window':{'start':(end-timedelta(days=89)).isoformat(),'end':end.isoformat(),'days':90,'account_coverage_rate':None},
            'scope':'只统计已知公开目录，不代表全号历史。未知总量不填0；近90天覆盖率尚待核实刊载时间。'}
