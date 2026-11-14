"""Literal, bounded search over current news, material evidence and point names."""
from typing import Any

from sqlalchemy import text

from app.database import SessionLocal
from app.news_data import source_label, SOURCE_LABELS

KINDS = {'news': '新闻', 'materials': '公众号资料', 'knowledge': '考点'}


def excerpt(value: str, tokens: list[str]) -> str:
    value = ' '.join(value.split())
    positions = [value.casefold().find(token.casefold()) for token in tokens]
    start = max(0, min((p for p in positions if p >= 0), default=0) - 35)
    return ('…' if start else '') + value[start:start + 180] + ('…' if len(value) > start + 180 else '')


def search(q: str, kind: str = 'all', page: int = 1, year: int | None = None,
           source: str | None = None) -> dict[str, Any]:
    tokens = q.split()
    groups: dict[str, Any] = {}
    with SessionLocal() as db:
        for name in KINDS:
            if kind not in ('all', name):
                continue
            params: dict[str, Any] = {'offset': (page - 1) * 20}
            clauses = []
            if name == 'news':
                table, fields = 'news', ['title', 'summary', 'content', 'source', 'published']
                columns, order = 'id,title,source,summary,content,published', 'published_at DESC,id DESC'
                if year:
                    clauses.append('year=:year'); params['year'] = year
                if source:
                    clauses.append('source=:source'); params['source'] = source
            elif name == 'materials':
                table, fields = 'learning_materials', ['title', 'account']
                columns, order = 'id,title,account,payload', 'imported_at DESC,id DESC'
            else:
                table, fields = 'knowledge_points', ['title', 'subject']
                columns, order = 'id,title,subject', 'subject,id'
            for index, token in enumerate(tokens):
                key = f'q{index}'
                params[key] = '%' + token.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
                matches = [f"{field} LIKE :{key} ESCAPE '!'" for field in fields]
                if name == 'news':
                    for alias_index, (code, label) in enumerate(SOURCE_LABELS.items()):
                        if token.casefold() in label.casefold():
                            alias_key = f'a{index}_{alias_index}'
                            params[alias_key] = code
                            matches.append(f'source=:{alias_key}')
                if name == 'materials':
                    matches.append("EXISTS (SELECT 1 FROM json_each(payload,'$.evidence') e "
                                   f"WHERE json_extract(e.value,'$.text') LIKE :{key} ESCAPE '!')")
                clauses.append('(' + ' OR '.join(matches) + ')')
            # An empty query never silently dumps the full library.
            where = ' AND '.join(clauses) if tokens or (name == 'news' and (year or source)) else '0'
            total = db.execute(text(f'SELECT COUNT(*) FROM {table} WHERE {where}'), params).scalar_one()
            rows = db.execute(text(f'SELECT {columns} FROM {table} WHERE {where} ORDER BY {order} LIMIT 20 OFFSET :offset'), params).mappings()
            items = []
            for row in rows:
                if name == 'news':
                    body = row['content'] or row['summary'] or row['title']
                    item = dict(id=row['id'], title=row['title'], label=source_label(row['source']),
                                date=row['published'], url=f"/news/{row['id']}", excerpt=excerpt(body, tokens))
                elif name == 'materials':
                    import json
                    payload = json.loads(row['payload'])
                    evidence = payload.get('evidence', [])
                    body = ' '.join(line.get('text', '') for line in evidence)
                    item = dict(id=row['id'], title=row['title'], label=row['account'], date='',
                                url=f"/learning/{row['id']}", excerpt=excerpt(body, tokens))
                else:
                    from urllib.parse import quote
                    item = dict(id=row['id'], title=row['title'], label=row['subject'], date='',
                                url='/knowledge/' + quote(row['id'], safe=''), excerpt='进入考点查看原文依据及相关资料。')
                items.append(item)
            groups[name] = {'total': total, 'items': items}
    return {'q': q, 'kind': kind, 'page': page, 'year': year, 'source': source, 'groups': groups,
            'total': sum(group['total'] for group in groups.values())}
