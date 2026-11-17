"""Parse public directory data without evaluating page JavaScript."""
import html
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit
from bs4 import BeautifulSoup


def article_identity(url: str) -> str:
    parts=urlsplit(html.unescape(url));query=parse_qs(parts.query)
    if parts.hostname!='mp.weixin.qq.com' or parts.username or parts.password or parts.scheme not in {'http','https'}:
        raise ValueError('Not a public WeChat article')
    if parts.path=='/s' and all(query.get(k) for k in ['__biz','mid','idx']):
        return ':'.join(query[k][0] for k in ['__biz','mid','idx'])
    if re.fullmatch(r'/s/[A-Za-z0-9_~\-]+',parts.path):return parts.path
    raise ValueError('Not an article URL')


def linked_articles(raw: str, biz: str) -> list[dict[str, str]]:
    """Only same-account article links in the saved body, never suggested offsite content."""
    root=BeautifulSoup(raw,'html.parser').select_one('#js_content')
    if root is None:return []
    items={}
    for anchor in root.select('a[href]'):
        url=html.unescape(str(anchor['href']))
        try:identity=article_identity(url)
        except ValueError:continue
        if not identity.startswith(biz+':'):continue
        parts=urlsplit(url);query=parse_qs(parts.query)
        if not query['mid'][0].isdigit() or not query['idx'][0].isdigit():continue
        items[identity]={'url':urlunsplit(('https',parts.netloc,parts.path,parts.query,'')),
                         'identity':identity,'mid':query['mid'][0],'idx':query['idx'][0],
                         'title':anchor.get_text(' ',strip=True),'create_time':''}
    return list(items.values())


def parse_directory(raw: str) -> dict[str, Any]:
    try:
        obj=json.loads(raw)
    except ValueError:obj=None
    if isinstance(obj,dict):
        if (obj.get('base_resp') or {}).get('ret')!=0:raise ValueError('Platform returned an error')
        body=obj.get('getalbum_resp') or {};rows=body.get('article_list') or []
        if isinstance(rows,dict):rows=[rows]
        declared=body.get('article_count')
        more=str(body.get('continue_flag'))=='1'
    else:
        start=raw.find('articleList:')
        if start<0:raise ValueError('No public directory data; verification may be required')
        section=raw[start:]; rows=[]
        for match in re.finditer(r"url:\s*['\"]([^'\"]+)['\"][\s\S]{0,400}?msgid:\s*['\"](\d+)['\"][\s\S]{0,80}?itemidx:\s*['\"](\d+)['\"]",section):
            prefix=section[max(0,match.start()-1600):match.start()]
            titles=re.findall(r"title:\s*['\"]([^'\"]*)['\"]",prefix)
            times=re.findall(r"create_time:\s*['\"](\d+)['\"]",prefix)
            rows.append({'url':match[1],'msgid':match[2],'itemidx':match[3],
                         'title':html.unescape(titles[-1]) if titles else '',
                         'create_time':times[-1] if times else ''})
        count=re.search(r"article_count:\s*['\"](\d+)['\"]",raw)
        declared=int(count[1]) if count else None;more=None
    items=[];seen=set()
    for row in rows:
        url=html.unescape(row.get('url',''));p=urlsplit(url)
        try:key=article_identity(url)
        except ValueError:continue
        if key in seen:continue
        seen.add(key)
        items.append({'url':urlunsplit(('https',p.netloc,p.path,p.query,'')),
                      'mid':str(row.get('msgid','')),'idx':str(row.get('itemidx','')),'identity':key,
                      'title':str(row.get('title','')),'create_time':str(row.get('create_time',''))})
    return {'items':items,'declared':int(str(declared)) if str(declared).isdigit() else None,'more':more}
