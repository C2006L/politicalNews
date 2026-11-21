"""Build a public reading edition. No database, credentials or raw pages leave the PC."""
from __future__ import annotations

import hashlib
import gzip
import json
import re
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString
from sqlalchemy.orm import Session

from app.chrome import bookmark, collection, header
from app.curated_materials import render_material_notes
from app.editorial import CATEGORIES, decision_map, document, select_document
from app.events import confirmed_event_map, event_hint_map
from app.exam_profiles import assess
from app.material_quality import latest_quality
from app.material_sources import route_material
from app.models import LearningMaterial, News
from app.news_data import source_label
from app.news_quality import capture_records, capture_status
from app.study import TRACKS, TOPICS, render_study

ASSETS = Path(__file__).parent / 'static'
PUBLIC_ASSETS = ('study.css', 'learning.css', 'material_library.css', 'study.js',
                 'collection_backup.js', 'static_edition.css', 'static_edition.js')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_url(value: str) -> str:
    """Allow only source web links, never script/data URLs embedded in imports."""
    parts = urlsplit(value)
    return value if parts.scheme in {'http', 'https'} and parts.netloc and not parts.username else '#'


def static_link(value: str, pages: set[str]) -> str | None:
    url = urlsplit(value)
    if url.scheme or url.netloc:
        return public_url(value)
    if not url.path:
        return value
    path = url.path.rstrip('/') or '/'
    query = parse_qs(url.query)
    if path in {'/', '/today', '/yesterday'}:
        track = query.get('track', ['all'])[0]
        if path == '/today' and query.get('mode', ['featured'])[0] == 'featured' and query.get('topic', ['all'])[0] == 'all' and not query.get('source'):
            return '/today/' + (track + '/' if track in TRACKS and track != 'all' else '')
        if path == '/':
            return '/'
        path = '/archive'
    if path.startswith(('/year/', '/source/', '/category/')) or path in {'/years', '/sources', '/events', '/reports'}:
        if path.startswith('/source/'):
            query['source'] = [path.split('/')[-1]]
        if path.startswith('/year/'):
            query['q'] = [path.split('/')[-1]]
        if path.startswith('/category/'):
            query['q'] = [path.split('/')[-1]]
        path = '/archive'
    if path in {'/archive', '/learning', '/search', '/categories'}:
        keep = {key: values[0] for key, values in query.items() if key in {'q', 'source', 'account', 'profile', 'track', 'purpose', 'topic'}}
        if 'track' in keep:
            keep['profile'] = keep.pop('track')
        if 'account' in keep:
            keep['source'] = keep.pop('account')
        if keep.get('profile') == 'general':
            keep['profile'] = 'all'
        return path + '/' + ('?' + urlencode(keep) if keep else '')
    if path + '/' in pages:
        return path + '/' + ('#' + url.fragment if url.fragment else '')
    return None


def finish_html(raw: str, pages: set[str], edition: str) -> bytes:
    soup = BeautifulSoup(raw, 'html.parser')
    for element in soup.select('script:not([src]), .report-save, .event-review-actions, .period-links, .auxiliary-tools, .curation-counts, .material-version, [data-practice-add], [data-add-practice]'):
        element.decompose()
    for script in list(soup.select('script[src]')):
        if urlsplit(str(script['src'])).path.split('/')[-1] not in PUBLIC_ASSETS:
            script.decompose()
    for node in soup.select('[data-live-study]'):
        del node['data-live-study']
    for node in soup.select('.edition-freshness'):
        node.string = '当前为已发布阅读版；新增内容在维护者下次发布后更新。'
    for link in list(soup.select('a[href]')):
        value = static_link(str(link['href']), pages)
        if value is None:
            previous = link.previous_sibling
            if isinstance(previous, NavigableString):
                previous.replace_with(re.sub(r'[\s·|]+$', '', str(previous)))
            link.decompose()
        else:
            link['href'] = value
    for node in soup.select('[data-url]'):
        value = static_link(str(node['data-url']), pages)
        if value:
            # The collection backup format intentionally uses extensionless URLs.
            node['data-url'] = value.rstrip('/')
    for form in list(soup.select('form')):
        if urlsplit(str(form.get('action', ''))).path == '/search':
            form['action'] = '/search/'
        elif not form.has_attr('data-static-filter'):
            form.decompose()
    for resource in soup.select('script[src], link[rel=stylesheet]'):
        key = 'src' if resource.name == 'script' else 'href'
        asset = urlsplit(str(resource[key])).path.split('/')[-1]
        if asset in PUBLIC_ASSETS:
            resource[key] = '/static/' + asset + '?v=' + digest((ASSETS / asset).read_bytes())[:12]
    if soup.head:
        extra = soup.new_tag('link', rel='stylesheet', href='/static/static_edition.css?v=' + digest((ASSETS / 'static_edition.css').read_bytes())[:12])
        soup.head.append(extra)
        robots = soup.new_tag('meta', attrs={'name': 'robots', 'content': 'noindex, nofollow'})
        soup.head.append(robots)
    if soup.body:
        note = soup.new_tag('div', attrs={'class': 'static-edition-note'})
        note.string = f'阅读版 · 内容更新于 {edition}（北京时间） · 收藏与已读保存在当前浏览器'
        soup.body.insert(0, note)
    return str(soup).encode('utf-8')


def layout(title: str, body: str, active: str) -> str:
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · 时政自习室</title><link rel="stylesheet" href="/static/study.css"><link rel="stylesheet" href="/static/learning.css">
<script src="/static/study.js" defer></script></head><body>{header('', active)}{body}{collection()}</body></html>'''


def catalog_page(title: str, kind: str, count: int, directory: str,
                 active: str = 'archive', topic_counts: dict[str, int] | None = None) -> str:
    options = ''.join(f'<option value="{key}">{label}</option>' for key, label in CATEGORIES.items())
    tracks = ''.join(f'<option value="{key}">{label}</option>' for key, label in TRACKS.items())
    topic_labels = {**{key: label for key, (label, _) in TOPICS.items()}, 'other': '其他主题'}
    topic_options = ''.join(f'<option value="{key}">{label}</option>' for key, label in topic_labels.items())
    topic_filter = (f'<label>时政主题<select name="topic"><option value="all">全部主题</option>{topic_options}</select></label>'
                    if kind != 'material' else '')
    topics = ''
    if topic_counts is not None:
        topics = '<nav class="static-topic-index" aria-label="专题分类">' + ''.join(
            f'<a href="/categories/?topic={key}" data-topic="{key}"><span>{label}</span><small>{topic_counts.get(key, 0)} 篇</small></a>'
            for key, label in {'all': '全部主题', **topic_labels}.items()) + '</nav>'
    body = f'''<div class="shell static-catalog" data-static-catalog data-kind="{kind}">
<aside class="rail"><div class="date-sheet"><div class="month">READING LIBRARY</div><div class="static-total">{count}</div><div class="date-foot">篇已发布资料</div></div>
<p class="rail-note">先选方向，再读材料。<br>政策、评论与带背分别呈现。</p><a href="/directory/">完整文章目录 →</a></aside>
<main class="workspace" id="reading"><section class="intro"><div><p class="eyebrow">THE READING ARCHIVE</p><h1>{escape(title)}</h1><p>阅读本机整理后发布的内容，按自己的节奏回看。</p></div></section>
{topics}<form class="static-filters" data-static-filter role="search"><label>备考方向<select name="profile">{tracks}</select></label>{topic_filter}
<label>内容类型<select name="purpose"><option value="all">全部类型</option>{options}</select></label>
<label>来源<select name="source"><option value="all">全部来源</option></select></label>
<label class="static-query">检索标题与正文<input name="q" type="search" maxlength="120" placeholder="政策、关键词或日期"></label>
<button type="submit">查看</button><button type="reset">重置</button></form>
<p data-static-status role="status" aria-live="polite">正在加载已发布目录…</p>
<div data-static-results></div><button type="button" data-static-more hidden>再看 20 篇</button>
<noscript><p>筛选和检索需要 JavaScript，文章正文可直接阅读。</p>{directory}</noscript></main></div>
<script src="/static/static_edition.js" defer></script>'''
    return layout(title, body, active)


def news_page(row: News, status: dict[str, Any]) -> str:
    text = (row.content or row.summary or '').strip()
    paragraphs = ''.join('<p>' + escape(line) + '</p>' for line in text.splitlines() if line.strip())
    source = escape(source_label(row.source))
    body = f'''<main class="learning-page static-article"><a href="/archive/">← 往期回顾</a><p class="eyebrow">{source} · {row.published_at:%Y-%m-%d}</p>
<h1 class="material-title">{escape(row.title)}</h1><div class="static-article-actions">{bookmark(str(row.id), row.title, '/news/' + str(row.id), row.published_at.strftime('%Y-%m-%d'))}
<a href="{escape(public_url(row.link), quote=True)}" target="_blank" rel="noopener noreferrer">原站原文 ↗</a></div>
<p class="report-scope">正文状态：{escape(status['label'])}。保留已采集文字，完整信息请对照原文。</p><section class="static-prose">{paragraphs or '<p>尚未取得文字正文，请查看原站原文。</p>'}</section></main>'''
    return layout(row.title, body, 'archive')


def build(db: Session, output: Path, days: int = 90, now: datetime | None = None) -> dict[str, Any]:
    """The destination must be new. Keep the previous successful export intact."""
    if output.exists():
        raise ValueError('导出目录已存在；请使用一个新目录，上一版不会被覆盖')
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    cutoff = now.date() - timedelta(days=days - 1)
    news = db.query(News).filter(News.published_at >= datetime.combine(cutoff, datetime.min.time()),
                                News.published_at < datetime.combine(now.date() + timedelta(days=1), datetime.min.time())).order_by(News.published_at.desc(), News.id.desc()).all()
    materials = db.query(LearningMaterial).order_by(LearningMaterial.imported_at.desc(), LearningMaterial.id.desc()).all()
    overrides, captures = decision_map(db), capture_records(db)
    updated = max([row.imported_at for row in materials] + [row.published_at for row in news] + [datetime.combine(now.date(), datetime.min.time())])
    # imported_at is UTC in the source database; never label it as a publication time.
    edition = max(now.date().isoformat(), updated.date().isoformat())
    pages = {'/', '/today/', '/archive/', '/learning/', '/search/', '/categories/', '/directory/'}
    pages.update('/today/' + track + '/' for track in TRACKS if track != 'all')
    pages.update('/news/' + str(row.id) + '/' for row in news)
    pages.update('/learning/' + str(row.id) + '/' for row in materials)
    output.mkdir(parents=True)
    (output / 'static').mkdir()
    for name in PUBLIC_ASSETS:
        (output / 'static' / name).write_bytes((ASSETS / name).read_bytes())
    records: list[dict[str, Any]] = []

    def write(route: str, raw: str) -> None:
        dest = output / route.strip('/') / 'index.html'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(finish_html(raw, pages, edition))

    for row in news:
        selection, captured = select_document(row, overrides), capture_status(row, captures)
        profiles = [key for key in ('postgrad', 'civil') if assess(row, key, overrides, captured)['eligible']]
        records.append({'id': str(row.id), 'kind': 'news', 'title': row.title, 'url': f'/news/{row.id}/',
                        'source': row.source, 'source_label': source_label(row.source), 'date': row.published_at.strftime('%Y-%m-%d'),
                        'purpose': selection.category, 'label': CATEGORIES[selection.category], 'profiles': profiles,
                        'topic': next((key for key, (_, words) in TOPICS.items() if any(word in row.title + ' ' + row.category for word in words)), 'other'),
                        'text': document(row)['text'], 'status': captured['label']})
        write(f'/news/{row.id}/', news_page(row, captured))
    from app.routers.learning import learning_detail
    for material in materials:
        routing, quality = route_material(material, overrides), latest_quality(db, material)
        payload = material.payload or {}
        published = payload.get('published_at') if payload.get('publication_time_verified') else None
        records.append({'id': 'learning:' + str(material.id), 'kind': 'material', 'title': material.title, 'url': f'/learning/{material.id}/',
                        'source': material.account, 'source_label': material.account, 'date': str(published or '')[:10],
                        'purpose': routing['purpose'], 'label': routing['label'], 'profiles': routing['directions'],
                        'topic': '', 'text': document(material)['text'], 'status': quality['label']})
        response = learning_detail(material.id, revision=None)
        write(f'/learning/{material.id}/', bytes(response.body).decode('utf-8'))
    members, hints = confirmed_event_map(db), event_hint_map(db)
    today = [row for row in news if row.published_at.date() == now.date()]
    for track in TRACKS:
        html = render_study(items=news, today_items=today, track=track, topic='all', page=1, base='', total=len(news), now=now,
                            mode='featured', overrides=overrides, captures=captures, confirmed_events=members, event_hints=hints,
                            learning_desk=render_material_notes(db, '', overrides, track))
        write('/today/' + (track + '/' if track != 'all' else ''), html)
        if track == 'all':
            write('/', html)
    directory = '<ul class="static-directory">' + ''.join(f'<li><a href="{row["url"]}">{escape(row["title"])}</a><small>{escape(row["source_label"])} · {escape(row["date"] or "发布日期待核对")}</small></li>' for row in records) + '</ul>'
    write('/directory/', layout('完整文章目录', '<main class="learning-page"><h1>完整文章目录</h1>' + directory + '</main>', 'archive'))
    for route, title, kind in [('/archive/', '往期回顾', 'news'), ('/learning/', '公众号研读', 'material'),
                                ('/search/', '检索资料', 'all'), ('/categories/', '专题资料', 'news')]:
        selected = [r for r in records if kind == 'all' or r['kind'] == kind]
        fallback = '<p><a href="/directory/">查看完整文章目录 →</a></p>'
        counts = {key: sum(r['topic'] == key for r in selected) for key in [*TOPICS, 'other']}
        counts['all'] = len(selected)
        active = 'learning' if kind == 'material' else 'categories' if route == '/categories/' else 'archive'
        write(route, catalog_page(title, kind, len(selected), fallback, active,
                                 counts if route == '/categories/' else None))
    (output / 'static' / 'catalog.json').write_text(json.dumps(records, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    # The full-text index is useful offline but large on mobile connections.
    # Precompress once on the PC instead of spending server CPU per request.
    (output / 'static' / 'catalog.json.gz').write_bytes(gzip.compress((output / 'static' / 'catalog.json').read_bytes(), mtime=0))
    (output / '404.html').write_text('<!doctype html><meta charset="utf-8"><title>未发布的页面</title><h1>这篇内容尚未发布到阅读版</h1><p><a href="/">返回时政自习室</a></p>', encoding='utf-8')
    files = {p.relative_to(output).as_posix(): digest(p.read_bytes()) for p in sorted(output.rglob('*')) if p.is_file()}
    manifest: dict[str, Any] = {'format': 'political-news-static-v1', 'built_at': now.isoformat(), 'days': days,
                               'news': len(news), 'materials': len(materials), 'files': files,
                               'bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file())}
    manifest['release'] = digest(json.dumps(files, sort_keys=True).encode())[:20]
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    validate(output)
    return manifest


def validate(output: Path) -> None:
    """Reject broken local links and accidental backend/file disclosure."""
    for path in output.rglob('*.html'):
        soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        if soup.select('script:not([src])'):
            raise ValueError('导出包含未经审核的内联脚本')
        for node in soup.select('[href], [src]'):
            value = urlsplit(str(node.get('href', node.get('src', ''))))
            if value.scheme or value.netloc or not value.path:
                continue
            if not value.path.startswith('/') or '..' in value.path.split('/'):
                raise ValueError(f'无效本地链接：{value.path}')
            target = output / value.path.lstrip('/')
            if target.is_dir():
                target /= 'index.html'
            if not target.is_file():
                raise ValueError(f'未导出的链接：{value.path}')
    for path in output.rglob('*'):
        if path.is_file() and path.suffix not in {'.html', '.css', '.js', '.json'} and path.name != 'catalog.json.gz':
            raise ValueError('导出包含非阅读文件')
