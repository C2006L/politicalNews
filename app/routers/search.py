from html import escape
from typing import Literal, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.routers.learning import layout
from app.unified_search import KINDS, search

router = APIRouter(tags=['统一检索'])
Kind = Literal['all', 'news', 'materials', 'knowledge']


@router.get('/api/search')
def search_api(q: str = Query('', max_length=120), kind: Kind = 'all', page: int = Query(1, ge=1, le=100000),
               year: int | None = Query(None, ge=1900, le=2200), source: str | None = Query(None, max_length=80)) -> dict[str, Any]:
    return search(q.strip(), kind, page, year, source)


def search_page(q: str, kind: Kind, page: int, year: int | None, source: str | None) -> HTMLResponse:
    if kind == 'all':
        page = 1
    data = search_api(q, kind, page, year, source)
    base = escape(get_settings().base_path, quote=True)
    options = ''.join(f'<option value="{key}" {"selected" if key == kind else ""}>{label}</option>'
                      for key, label in {'all': '全部资料', **KINDS}.items())
    body = f'''<div class="eyebrow">SEARCH THE READING ROOM</div><h1>从一处，找到学习线索。</h1>
<p class="learning-notice">检索新闻、公众号现有正文及考点名称。空格分隔的词需要全部命中；只查当前资料，不检索旧修订、个人复习记录或尚未采集的文章。</p>
<form class="unified-search" method="get" action="{base}/search">
<label>关键词 <input name="q" type="search" maxlength="120" value="{escape(q, quote=True)}" placeholder="如：法治 体系"></label>
<label>范围 <select name="kind">{options}</select></label>
<label>新闻年份 <input name="year" type="number" min="1900" max="2200" value="{year or ''}" placeholder="全部年份"></label>
<button type="submit">搜索</button></form>'''
    if source:
        from app.news_data import source_label
        body = body.replace('<button type="submit">搜索</button>',
                            f'<input type="hidden" name="source" value="{escape(source, quote=True)}"><button type="submit">搜索</button>')
        clear = urlencode({'q': q, 'kind': kind, **({'year': str(year)} if year else {})})
        body += f'<p>新闻来源：{escape(source_label(source))}（只约束新闻） · <a href="{base}/search?{escape(clear, quote=True)}">清除来源筛选</a></p>'
    body += f'<h2>搜索结果 · {data["total"]} 条</h2>'
    if not q and not year and not source:
        body += '<p>输入关键词开始检索，例如“法治体系”或“科技创新”。默认覆盖全部年份。</p>'
    else:
        if kind == 'all':
            body += '<p>每类先展示 5 条，进入该类可分页查看全部。</p>'
            body += '<p>' + ' · '.join(f'<a href="#results-{name}">{KINDS[name]} {group["total"]}</a>'
                                      for name, group in data['groups'].items()) + '</p>'
        for name, group in data['groups'].items():
            body += f'<section id="results-{name}"><h2>{KINDS[name]} · {group["total"]} 条</h2>'
            for item in (group['items'][:5] if kind == 'all' else group['items']):
                body += f'''<article class="learning-entry"><p class="meta">{escape(item['label'])} {escape(item['date'])}</p>
<h3><a href="{base}{escape(item['url'], quote=True)}">{escape(item['title'])}</a></h3><p>{escape(item['excerpt'])}</p></article>'''
            if not group['items']:
                body += '<p>本页没有匹配结果。可减少关键词或切换范围。</p>'
            if kind == 'all' and group['total']:
                args = {'q': q, 'kind': name}
                if year: args['year'] = str(year)
                if source: args['source'] = source
                body += f'<p><a href="{base}/search?{escape(urlencode(args), quote=True)}">查看全部{KINDS[name]} →</a></p>'
            body += '</section>'
        pages = max((max(1, (group['total'] + 19) // 20) for group in data['groups'].values()), default=1)
        def link(target: int, label: str) -> str:
            args: dict[str, str | int] = {'q': q, 'kind': kind, 'page': target}
            if year: args['year'] = str(year)
            if source: args['source'] = source
            return f'<a href="{base}/search?{escape(urlencode(args), quote=True)}">{label}</a>'
        if kind != 'all':
            body += f'<p>第 {page} / {pages} 页 · 每页最多 20 条。 '
            if page > 1: body += link(page - 1, '上一页') + ' '
            if page < pages: body += link(page + 1, '下一页')
            body += '</p>'
    return layout('统一检索', body, active='search')
