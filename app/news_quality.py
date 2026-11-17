"""Content-bound capture status; absence is never treated as completeness."""
import hashlib,json
from typing import Any
from sqlalchemy.orm import Session
from app.models import AppState,News


def capture_records(db: Session) -> dict[int,dict[str,Any]]:
    result={}
    for row in db.query(AppState).filter(AppState.key.like('news_body:%')):
        if not row.key.startswith('news_body:'):continue
        try:
            identity=int(row.key.split(':',1)[1]);data=json.loads(row.value)
            if isinstance(data,dict):result[identity]=data
        except (ValueError,TypeError):continue
    return result


def capture_status(row: News, records: dict[int,dict[str,Any]]) -> dict[str,Any]:
    record=records.get(row.id,{})
    valid=record.get('fingerprint')==hashlib.sha256((row.content or '').encode()).hexdigest()
    if not valid:return {'status':'unverified','label':'待核对，可能只有节选','characters':len(row.content or ''),'paragraphs':None,'last_attempt':None}
    status=record.get('status','unverified')
    labels={'extracted':'已提取网页正文','partial':'疑似分页未齐','blocked':'原站验证或访问受限',
            'missing':'未取得有效正文','unverified':'未识别到可靠正文范围'}
    return {'status':status,'label':labels.get(status,'待核对'),'characters':len(row.content or ''),
            'paragraphs':record.get('paragraphs'),'last_attempt':record.get('last_attempt_status')}
