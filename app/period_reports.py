"""Read-only period drafts and explicit immutable saved editions."""
from datetime import date, datetime, time, timedelta
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from app.editorial import decision_map, select_document
from app.event_digest import build_digest
from app.events import confirmed_event_map, snapshot
from app.models import News
from app.news_data import source_label
from app.study import prepare_readings
from app.news_quality import capture_records,capture_status
from app.exam_profiles import assess,PROFILES


def build_report(db: Session, kind: str, day: date, profile: str='general') -> dict[str, Any]:
    if profile not in PROFILES:raise ValueError('未知备考方向')
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if day > today:
        raise ValueError('不能查看未来日期的整理稿')
    start = day if kind == 'daily' else day - timedelta(days=day.weekday())
    end = start if kind == 'daily' else start + timedelta(days=6)
    through = min(end, today)
    rows = db.query(News).filter(News.published_at >= datetime.combine(start,time.min),
                                News.published_at < datetime.combine(through+timedelta(days=1),time.min)).order_by(News.id).all()
    decisions = decision_map(db)
    captures = capture_records(db)
    confirmed = confirmed_event_map(db)
    readings = prepare_readings(rows, mode='featured', overrides=decisions, confirmed_events=confirmed,captures=captures)
    assessments={row.id:assess(row,profile,decisions,capture_status(row,captures)) for row in rows} if profile!='general' else {}
    # A confirmed event is one group across the week, while unconfirmed reports remain separate.
    groups: dict[str, dict[str, Any]] = {}
    for reading in readings:
        articles=[row for row in reading.articles if profile=='general' or assessments[row.id]['eligible']]
        if not articles:continue
        relevance=assessments[articles[0].id] if profile!='general' else None
        key = reading.event_id or 'same-title-day:'+reading.key
        if key not in groups:
            groups[key] = {'title':reading.item.title,'score':reading.score,'reasons':reading.reasons,
                           'event_id':reading.event_id,'sources':[],'relevance':relevance}
        group = groups[key]
        group['score'] = max(group['score'],max((assessments[row.id]['score'] for row in articles),default=reading.score)) if profile!='general' else max(group['score'],reading.score)
        for row in articles:
            group['sources'].append({'id':row.id,'news_id':row.id,'status':'confirmed' if reading.event_id else 'candidate',
                                     'is_current':True,'selection':select_document(row,decisions).as_dict(),
                                     'snapshot':snapshot(row),'publisher':source_label(row.source),
                                     'capture':capture_status(row,captures)})
    ranked = sorted(groups.values(),key=lambda g:(-g['score'], min(s['news_id'] for s in g['sources'])))
    # Preserve document > commentary > report priority; cap repeated themes rather than filling slots.
    if profile!='general':
        counts:dict[str,int]={};diverse=[]
        for group in ranked:
            topic=group['relevance']['topic']
            if counts.get(topic,0)>=(2 if kind=='daily' else 3):continue
            counts[topic]=counts.get(topic,0)+1;diverse.append(group)
        ranked=diverse
    items=[]
    limit=(5 if kind=='daily' else 10) if profile!='general' else (10 if kind=='daily' else 20)
    for group in ranked[:limit]:
        digest = build_digest(group['sources'])
        items.append({**group,'points':digest['points'][:3], 'comparison':digest['articles'],
                      'point_count':digest['total_points']})
    payload={'method':'period-extractive-4','kind':kind,'profile':profile,'profile_label':PROFILES[profile],
             'profile_excluded':len(rows)-sum(a['eligible'] for a in assessments.values()) if assessments else 0,
             'start':start.isoformat(),'end':end.isoformat(),
             'through':through.isoformat(),'period_complete':end<today,'collected':len(rows),
             'eligible_groups':len(groups),'items':items,
             'note':f'按刊载日期整理库内新闻，非完整资讯或命题预测。仅归并已确认事件及同日同标题稿，转载数量不加分。本方向本期最多{limit}组，每组最多3条原句。备考方向先检验正文提取状态、主题和具体行动，再按政策文件、评论、报道排序并兼顾主题分布；同主题仍可能含不同事件，不强行合并。文字比较仅覆盖每组前24篇、每篇前12000字中的40句；不推断统一观点或考查概率，空日期不借用其他日期资料。'}
    fingerprint=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {**payload,'fingerprint':fingerprint}
