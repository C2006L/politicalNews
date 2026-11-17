"""Isolated, read-only sharing gateway. Never imports the main app or starts jobs."""
import asyncio
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request as URLRequest, HTTPRedirectHandler, ProxyHandler, build_opener

from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

UPSTREAM = 'http://127.0.0.1:8004'
MAX_BYTES = 12 * 1024 * 1024
PATHS = re.compile(
    r'/(?:|today|yesterday|reports|reports/saved/[a-f0-9]{64}|news/\d+|'
    r'learning|learning/\d+|knowledge|knowledge/[a-z0-9_-]+|practice|'
    r'material-quality|material-coverage|api/material-coverage|categories|category/[a-z_]+|archive|years|year/\d+|search|events|'
    r'sources|source/[a-z_]+|news-quality|'
    r'api/study/live|api/reports|api/events|api/events/[a-f0-9]+|'
    r'api/search|api/learning|api/learning/\d+|'
    r'static/[a-zA-Z0-9_.-]+\.(?:css|js|svg|png|woff2))'
)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def allowed(path: str) -> bool:
    return bool(PATHS.fullmatch(path)) and '..' not in path


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def fetch(path: str, query: str, accept: str) -> tuple[int, str, bytes, str | None]:
    url = UPSTREAM + path + ('?' + query if query else '')
    # No browser credentials or supplied proxy/auth headers are forwarded.
    request = URLRequest(url, headers={'Accept': accept, 'User-Agent': 'PoliticalNews-ReadOnlyPreview'})
    try:
        stream = build_opener(ProxyHandler({}), NoRedirect).open(request, timeout=20)
    except HTTPError as error:
        stream = error
    with stream:
        data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('response exceeds preview limit')
        return stream.code, stream.headers.get('Content-Type', 'application/octet-stream'), data, stream.headers.get('Location')


def reading_html(data: bytes) -> bytes:
    soup = BeautifulSoup(data.decode('utf-8'), 'html.parser')
    for element in soup.select('.report-save, .event-review-actions, [data-event-history]'):
        element.decompose()
    for link in soup.select('a[href]'):
        target = urlsplit(str(link['href']))
        if not target.scheme and target.path.startswith('/') and not allowed(target.path):
            link.replace_with(link.get_text())
    banner = soup.new_tag('div', attrs={'class': 'preview-banner'})
    banner.string = '同学试读 · 仅开放阅读 · 收藏和已读保存在当前浏览器'
    if soup.body:
        soup.body.insert(0, banner)
    if soup.head:
        style = soup.new_tag('style')
        style.string = '.preview-banner{padding:8px 20px;background:#223b34;color:#f3f0e7;font:12px/1.8 sans-serif;text-align:center}'
        soup.head.append(style)
    return str(soup).encode('utf-8')


@app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'])
async def preview(request: Request, path: str) -> Response:
    route = '/' + path
    if request.method not in {'GET', 'HEAD'} or not allowed(route):
        return HTMLResponse('<h1>试读入口仅开放阅读</h1><p><a href="/today">返回阅读页</a></p>', status_code=403)
    try:
        status, content_type, body, location = await asyncio.to_thread(
            fetch, route, request.url.query, request.headers.get('accept', '*/*'))
    except (OSError, URLError, ValueError):
        return HTMLResponse('<h1>本机阅读服务暂未就绪</h1><p>请稍后重试。</p>', status_code=503)
    headers = {'Content-Type': content_type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow'}
    if location:
        target = urlsplit(location)
        if target.netloc and target.netloc != '127.0.0.1:8004' or not allowed(target.path):
            return Response(status_code=403)
        headers['Location'] = target.path + ('?' + target.query if target.query else '')
    if content_type.startswith('text/html'):
        body = reading_html(body)
    return Response(b'' if request.method == 'HEAD' else body, status_code=status, headers=headers)
