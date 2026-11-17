"""Conservative event candidates, explicit evidence and revision-bound membership."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from typing import Any
import unicodedata

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.editorial import document, select_document
from app.models import News, NewsEvent, EventArticle

METHOD = 'event-facts-1'
DATE = re.compile(r'(?:(20\d{2})年\s*)?(\d{1,2})月\s*(\d{1,2})日')
DAY_ONLY = re.compile(r'(?<![月\d])(\d{1,2})日')
NUM = r'[一二三四五六七八九十百零〇两\d]{1,8}'
MEETINGS = [
    re.compile(rf'(?P<organ>亚太经合组织(?:[（(]APEC[）)])?工商咨询理事会|ABAC)(?:[（(]ABAC[）)])?\s*第(?P<number>{NUM})次会议(?P<stage>开幕式|闭幕式|新闻发布会)?', re.I),
    re.compile(rf'(?P<organ>亚太经合组织)(?:[（(]APEC[）)])?\s*第(?P<number>{NUM})次(?P<body>领导人非正式会议)(?P<stage>开幕式|闭幕式|新闻发布会)?', re.I),
    re.compile(rf'第(?P<term>{NUM})届(?P<organ>全国人民代表大会)第(?P<number>{NUM})次会议(?P<stage>开幕式|闭幕式|新闻发布会)?'),
    re.compile(rf'第(?P<number>{NUM})届(?P<organ>联合国气候变化大会)(?P<stage>开幕式|闭幕式|新闻发布会)?'),
]
POLICY = re.compile(r'《(?P<name>[^《》\n]{4,70}(?:条例|办法|意见|方案|规划|规定|法律))》')
ISSUER = re.compile(r'国务院办公厅|中共中央办公厅|全国人大常委会|国务院|中共中央|教育部|财政部|国家发展改革委|商务部|工业和信息化部')
BILATERAL = re.compile(r'(?P<actor>习近平|李强|何立峰|王毅)(?:主席|总理|副总理|外长)?(?:于?(?:\d{1,2}月)?\d{1,2}日)?(?:在(?P<venue>[^，。！？\n]{1,16}?))?(?P<action>会见|会晤|通话)(?P<target>[^，。！？；：:\n]{3,50})(?=[，。！？；：:\n]|$)')
VENUE = re.compile(r'在(北京|上海|深圳|广州|香港|澳门|天津|南京|杭州|成都|武汉|重庆|厦门|福州|西安|济南|南宁|曼谷|贝伦|日内瓦|纽约|巴黎)')


def number(value: str) -> str:
    value = unicodedata.normalize('NFKC', value)
    if value.isdecimal():
        return str(int(value))
    digits = {'零':0, '〇':0, '一':1, '二':2, '两':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9}
    total = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in {'十', '百'}:
            total += (current or 1) * (10 if char == '十' else 100)
            current = 0
        else:
            return value
    return str(total + current)


def snapshot(row: News) -> dict[str, Any]:
    return {**document(row), 'published_at': row.published_at.isoformat(), 'method': METHOD}


def content_fingerprint(row: News) -> str:
    return hashlib.sha256(json.dumps(snapshot(row), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def evidence(field: str, text: str, start: int, end: int) -> dict[str, Any]:
    return {'field': field, 'start': start, 'end': end, 'quote': text[start:end]}


def event_date(text: str, start: int, end: int, published: date, field: str,
               allow_after: bool=True) -> tuple[str, str, dict | None] | None:
    # Choose a date close to this event mention, never the webpage's first date.
    def usable(match: re.Match) -> bool:
        return not (re.match(r'\s*(?:电|讯|消息|\d{1,2}:\d{2})',text[match.end():match.end()+12]) or
                    re.search(r'发布时间|更新时间|刊载时间',text[max(0,match.start()-15):match.start()]))
    before = [m for m in DATE.finditer(text, max(0, start - 160), start) if usable(m)]
    after = [m for m in DATE.finditer(text, start, min(len(text), end + 65)) if usable(m)] if allow_after else []
    scheduled = [m for m in after if re.search(r'将于|拟于|计划于',text[end:m.start()])]
    match = scheduled[0] if scheduled else before[-1] if before else after[0] if after else None
    short = [m for m in DAY_ONLY.finditer(text,max(0,start-35),start) if usable(m)]
    if short and not scheduled and (not before or short[-1].start()>before[-1].start()):
        item=short[-1]
        candidates=[]
        for offset in (-1,0):
            month_index=published.year*12+published.month-1+offset
            try:
                day=date(month_index//12,month_index%12+1,int(item[1]))
                if 0<=(published-day).days<=7:candidates.append(day)
            except ValueError:pass
        if not candidates:return None
        day=max(candidates)
        return day.isoformat(),'explicit_day',evidence(field,text,item.start(),item.end())
    if match is None:
        return published.isoformat(), 'publication_day', None
    years = [int(match[1])] if match[1] else [published.year - 1, published.year, published.year + 1]
    possible = []
    for year in years:
        try:
            candidate = date(year, int(match[2]), int(match[3]))
            if abs((candidate - published).days) <= 45:
                possible.append(candidate)
        except ValueError:
            pass
    if not possible:
        return None  # An old/invalid explicit date must not become "today".
    day = min(possible, key=lambda d: abs((d - published).days))
    basis = 'explicit_date' if match[1] else 'explicit_month_day'
    return day.isoformat(), basis, evidence(field, text, match.start(), match.end())


def extract_event(row: News) -> dict[str, Any] | None:
    if not select_document(row).selected:
        return None
    doc = document(row)
    if re.search(r'辟谣|不实消息|并未|尚未',doc['title']):
        return None  # Do not turn a denied claim into an event that happened.
    # Title establishes the primary subject; body gives a better local date.
    mentions: list[tuple[str, str, re.Match, dict[str, str]]] = []
    for field in ('title', 'text'):
        text = doc[field][:1600]
        for pattern in MEETINGS:
            for match in pattern.finditer(text):
                groups = match.groupdict()
                organ = groups['organ'].upper()
                organ = '亚太经合组织工商咨询理事会' if organ == 'ABAC' else re.sub(r'[（(]APEC[）)]','',groups['organ'],flags=re.I)
                body = groups.get('body') or '会议'
                stage = groups.get('stage') or '会议'
                locations=list(VENUE.finditer(text,max(0,match.start()-80),min(len(text),match.end()+100)))
                location=min(locations,key=lambda m: min(abs(m.start()-match.start()),abs(m.start()-match.end()))) if locations else None
                facts = {'kind':'meeting', 'organ':organ, 'body':body, 'session':number(groups['number']),
                         'term':number(groups['term']) if groups.get('term') else '', 'stage':stage,
                         'venue':location[1] if location else ''}
                mentions.append((field, text, match, facts))
        for match in POLICY.finditer(text):
            prefix = text[max(0,match.start()-70):match.start()]
            suffix = text[match.end():match.end()+30]
            issuers = sorted(set(ISSUER.findall(prefix)))
            actions=[(len(prefix)-m.end(),m[0],prefix[max(0,m.start()-6):m.start()]) for m in re.finditer(r'印发|发布|公布|出台|施行|实施',prefix)]
            actions += [(m.start(),m[0],suffix[max(0,m.start()-6):m.start()]) for m in re.finditer(r'印发|发布|公布|出台|施行|实施',suffix)]
            action=min(actions,key=lambda item:item[0]) if actions else None
            if issuers and action and not re.search(r'未|没有|不会',action[2]) and not re.search(r'不实|谣言|假消息',suffix):
                stage = '实施' if action[1] in {'施行','实施'} else '发布'
                mentions.append((field,text,match,{'kind':'policy','issuer':'、'.join(issuers),
                                                 'name':match['name'], 'stage':stage}))
        for match in BILATERAL.finditer(text):
            target = re.sub(r'(?:一行|来访代表团)$', '', match['target']).strip()
            if (len(target) < 3 or re.search(r'会议|峰会|论坛|活动|讲话|仪式|举行',target) or
                    re.search(r'出席|参加|主持|发表|举行|会见|会晤|通话',match['venue'] or '')):
                continue
            mentions.append((field,text,match,{'kind':'bilateral','actor':match['actor'],
                                              'action':match['action'],'target':target,'venue':match['venue'] or ''}))
    if not mentions:
        return None
    # Prefer body mentions agreeing with a title signature. Otherwise use only
    # the lead; later background paragraphs cannot redefine the main event.
    title_facts = [f for field,_,_,f in mentions if field == 'title']
    body_matches = [entry for entry in mentions if entry[0] == 'text' and
                    (any(all(entry[3].get(key)==value for key,value in facts.items() if value!='') for facts in title_facts) or
                     not title_facts and entry[2].start() < 450)]
    chosen = body_matches or [entry for entry in mentions if entry[0] == 'title']
    if not chosen:
        return None
    field, text, match, facts = min(chosen, key=lambda entry:entry[2].start())
    start=match.start('action') if facts['kind']=='bilateral' else match.start()
    allow_after=facts['kind']=='meeting'
    if facts['kind']=='policy':
        suffix=text[match.end():match.end()+45]
        allow_after=bool(re.search(r'印发|发布|公布|出台',suffix)) and not re.search(r'施行|实施|生效',suffix)
    timing = event_date(text,start,match.end(),row.published_at.date(),field,allow_after=bool(allow_after))
    if timing is None:
        return None
    day, basis, date_proof = timing
    if facts['kind'] == 'meeting':
        # A press conference, opening and full meeting are distinct stages.
        if facts['term']:
            name = f'第{facts["term"]}届{facts["organ"]}第{facts["session"]}次{facts["body"]}'
        elif facts['organ']=='联合国气候变化大会':
            name = f'第{facts["session"]}届{facts["organ"]}'
        else:
            name = f'{facts["organ"]}第{facts["session"]}次{facts["body"]}'
        if facts['stage'] != '会议': name += ' · ' + facts['stage']
        if facts['venue']:name += '（'+facts['venue']+'）'
    elif facts['kind'] == 'policy':
        name = f'{facts["issuer"]}{facts["stage"]}《{facts["name"]}》'
    else:
        name = facts['actor'] + ('在'+facts['venue'] if facts['venue'] else '') + facts['action'] + facts['target']
    planned=date.fromisoformat(day) > row.published_at.date()
    if facts['kind']=='meeting':
        context=text[max(0,match.start()-60):match.end()+60]
        planned=planned or bool(re.search(r'将于|拟于|计划于',context))
    facts = {**facts, 'phase':'planned' if planned else 'reported'}
    key = {'facts':facts, 'day':day, 'time_scope':'publication_day' if basis=='publication_day' else 'event_day', 'method':METHOD}
    identity = hashlib.sha256(json.dumps(key,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    proof = [evidence(field,text,match.start(),match.end())]
    if facts['kind']=='meeting' and facts['venue']:
        location=next((m for m in VENUE.finditer(text,max(0,match.start()-80),min(len(text),match.end()+100)) if m[1]==facts['venue']),None)
        if location:proof.append(evidence(field,text,location.start(),location.end()))
    if facts['kind']=='policy':
        proof.append(evidence(field,text,max(0,match.start()-70),min(len(text),match.end()+30)))
    if date_proof:proof.append(date_proof)
    return {'id':identity,'title':name,'event_day':day,'facts':key,'proof':{'basis':basis,'evidence':proof}}


def link_event(db: Session, row: News) -> int:
    parsed = extract_event(row)
    if parsed is None:
        return 0
    fp = content_fingerprint(row)
    existing = db.query(EventArticle).filter_by(event_id=parsed['id'],news_id=row.id,fingerprint=fp).first()
    if existing:
        return 0
    if db.get(NewsEvent, parsed['id']) is None:
        db.add(NewsEvent(id=parsed['id'],title=parsed['title'],event_day=parsed['event_day'],facts=parsed['facts'],method=METHOD))
        db.flush()
    db.add(EventArticle(event_id=parsed['id'],news_id=row.id,fingerprint=fp,snapshot=snapshot(row),
                        proof=parsed['proof'],status='candidate'))
    db.flush()
    return 1


def rebuild_events() -> dict[str, int]:
    with SessionLocal() as db:
        added = sum(link_event(db,row) for row in db.query(News).order_by(News.id))
        db.commit()
    return {'added':added}


def current_members(db: Session) -> list[tuple[EventArticle, News]]:
    return [(link,row) for link,row in db.query(EventArticle,News).join(News,News.id==EventArticle.news_id)
            if link.fingerprint==content_fingerprint(row)]


def needs_event_review(members: list[tuple[EventArticle,News]]) -> bool:
    titles={re.sub(r'[\W_]+','',row.title).lower() for _,row in members}
    days={row.published_at.date() for _,row in members}
    return len(titles)>1 or len(days)>1


def confirmed_event_map(db: Session) -> dict[int, str]:
    groups: dict[str, list[int]] = {}
    for link,row in current_members(db):
        if link.status=='confirmed':groups.setdefault(link.event_id,[]).append(row.id)
    result = {}
    for event_id, identities in groups.items():
        if len(set(identities))>=2:
            for identity in identities:result[identity]=event_id
    return result


def event_hint_map(db: Session) -> dict[int, dict[str, Any]]:
    groups: dict[str,list[tuple[EventArticle,News]]] = {}
    for link,row in current_members(db):
        if link.status!='rejected':groups.setdefault(link.event_id,[]).append((link,row))
    result={}
    for event_id,members in groups.items():
        if len(members)<2:continue
        confirmed=sum(link.status=='confirmed' for link,_ in members)
        if confirmed<2 and not needs_event_review(members):continue
        for _,row in members:
            result[row.id]={'event_id':event_id,'articles':len(members),'confirmed':confirmed}
    return result
