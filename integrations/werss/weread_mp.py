"""Collect WeChat Official Account articles through WeRead Web."""

import time
import json
import os
import re
import sqlite3
import uuid
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from core.log import logger
from core.models.feed import Feed
from core.print import print_info
from core.wx.model.weread import MpsWeread


WEREAD_WEB_BASE = "https://weread.qq.com"
ADAPTER_VERSION = 'history-90-v1'


class HistoryStore:
    """Separate local progress DB; never store authorization or change WeRSS schema."""
    def __init__(self):
        self.path = Path(os.getenv('POLITICAL_HISTORY_STATE_PATH', './data/political-news-history.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS progress(mp_id TEXT PRIMARY KEY,payload TEXT NOT NULL,lease TEXT,expires REAL)')

    @contextmanager
    def claim(self, mp_id):
        token = uuid.uuid4().hex
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload,lease,expires FROM progress WHERE mp_id=?', (mp_id,)).fetchone()
            if row and row[1] and row[2] > time.time():
                raise WereadMPAPIError('history_busy', 'Another batch holds this source')
            state = json.loads(row[0]) if row else {'version': 1, 'offset': 0, 'complete': False, 'anchor': [], 'discovered_ids': [], 'body_pending': []}
            db.execute('INSERT INTO progress VALUES(?,?,?,?) ON CONFLICT(mp_id) DO UPDATE SET lease=excluded.lease,expires=excluded.expires',
                       (mp_id, json.dumps(state), token, time.time() + 180))
        try:
            stopped = threading.Event()
            def heartbeat():
                while not stopped.wait(30):
                    try:
                        if not self.renew(mp_id, token):
                            return
                    except sqlite3.Error:
                        continue
            worker = threading.Thread(target=heartbeat, daemon=True)
            worker.start()
            yield state, token
        finally:
            stopped.set()
            worker.join(timeout=6)
            with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
                db.execute('UPDATE progress SET lease=NULL,expires=NULL WHERE mp_id=? AND lease=?', (mp_id, token))

    def renew(self, mp_id, token):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            cursor=db.execute('UPDATE progress SET expires=? WHERE mp_id=? AND lease=? AND expires>=?',
                              (time.time()+180,mp_id,token,time.time()))
            return cursor.rowcount==1

    def save(self, mp_id, state, token):
        state['checked_at'] = int(time.time())
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            cursor = db.execute('UPDATE progress SET payload=?,expires=? WHERE mp_id=? AND lease=? AND expires>=?',
                                (json.dumps(state), time.time() + 180, mp_id, token, time.time()))
            if cursor.rowcount != 1:
                raise WereadMPAPIError('history_lease_lost', 'Source batch lease expired')


class WereadMPAPIError(Exception):
    def __init__(self, code, message, retriable=True):
        super().__init__(f"WeRead MP API error {code}: {message}")
        self.code = code
        self.message = message
        self.retriable = retriable


def build_mp_url(original_id: str) -> str:
    original_id = str(original_id or "").strip()
    if not original_id:
        return ""
    # WeRead represents URL-safe underscore with tilde in its originalId.
    article_token = quote(original_id.replace("~", "_"), safe="")
    return f"https://mp.weixin.qq.com/s/{article_token}"


def build_mp_link_from_review_id(review_id: str, book_id: str = "") -> str:
    """从 WeRead 的 reviewId 推导公众号原文链接。

    reviewId 形如 ``MP_WXS_<bookId>_<articleToken>``，末尾段即为
    mp.weixin.qq.com 原文短链的 token。token 中可能含 ``~``（如
    ``4OcS7~rrtk2Lwe4P0YPiGg``），须原样保留。新版接口 ``/api/mp/cover``
    不返回 originalId，只能由此推导。
    """
    review_id = str(review_id or "").strip()
    if not review_id:
        return ""
    token = review_id
    prefix = f"{book_id}_" if book_id else ""
    if prefix and review_id.startswith(prefix):
        token = review_id[len(prefix):]
    else:
        match = re.fullmatch(r'MP_WXS_\d+_(.+)', token)
        if not match:
            return ''
        token = match[1]
    token = token.replace("~", "_")
    return f"https://mp.weixin.qq.com/s/{quote(token, safe='')}"


def _raise_response_error(payload: dict):
    code = payload.get("errCode", payload.get("errcode", 0))
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        code = 0
    if code == 0:
        return
    message = payload.get("errMsg") or payload.get("errmsg") or str(code)
    raise WereadMPAPIError(
        code,
        message,
        retriable=code not in (-2041, -2012, -2010),
    )


def parse_mp_articles(payload: dict):
    if not isinstance(payload, dict):
        raise WereadMPAPIError("invalid_response", "response is not an object")
    _raise_response_error(payload)

    groups = payload.get("reviews")
    if not isinstance(groups, list):
        raise WereadMPAPIError('invalid_list', 'Expected a review-group list')
    articles = []
    for group in groups:
        group_time = group.get("createTime", 0)
        for sub_review in group.get("subReviews", []) or []:
            review = sub_review.get("review") or {}
            mp_info = review.get("mpInfo") or {}
            review_id = review.get("reviewId") or sub_review.get("reviewId")
            if not review_id:
                continue
            create_time = review.get("createTime") or group_time or 0
            update_time = mp_info.get("time") or create_time
            articles.append({
                "aid": review_id,
                "id": review_id,
                "title": mp_info.get("title", ""),
                "link": build_mp_url(mp_info.get("originalId", "")) or build_mp_link_from_review_id(review_id),
                "cover": mp_info.get("pic_url", ""),
                "digest": mp_info.get("content") or review.get("content", ""),
                "content": "",
                "create_time": create_time,
                "update_time": update_time,
                "read_num": mp_info.get("readNum", 0),
                "like_num": mp_info.get("likeNum", 0),
                "item_show_type": 0,
            })
    return articles, len(groups)


def extract_mp_content(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    content = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    if content is None:
        raise WereadMPAPIError("invalid_content", "article body was not found")
    for element in content.select("script, style"):
        element.decompose()
    return content.decode_contents().strip()


def original_snapshot(raw, soup, root):
    """Keep the complete article body and literal metadata, discard site bundles.

    WeRead pages contain several MB of tracking/runtime JS. Those scripts are
    not article text and make upstream markdown conversion needlessly expensive.
    """
    assignments=[]
    for name, alphabet in [('biz',r'A-Za-z0-9+/='),('mid',r'0-9'),('appmsgid',r'0-9'),('idx',r'0-9'),('ct',r'0-9')]:
        match=re.search(r'var\s+'+name+r'\s*=\s*([^;\n]+)',raw)
        if match and re.fullmatch(r'''["']['''+alphabet+r''']*["'](?:\s*\|\|\s*["']['''+alphabet+r''']*["'])*''',match[1].strip()):
            assignments.append('var '+name+' = '+match[1].strip()+';')
    meta=soup.select_one('meta[property="og:title"]')
    author=soup.select_one('#js_name')
    title=soup.select_one('#activity-name')
    return ('<html><head><meta name="werss-snapshot" content="original-body-and-metadata">'+(str(meta) if meta else '')+
            '</head><body>'+(str(author) if author else '')+(str(title) if title else '')+str(root)+
            '<script>'+'\n'.join(assignments)+'</script></body></html>')


class MpsWereadMP(MpsWeread):
    """WeRead-backed collector for existing ``MP_WXS_*`` feeds."""

    def _get_feed_update_time(self, mp_id: str) -> int:
        from core.db import DB

        session = DB.get_session()
        try:
            row = session.query(Feed.update_time).filter(Feed.id == mp_id).first()
            return int(row[0] or 0) if row else 0
        finally:
            session.close()

    def _get_catchup_page_limit(self, requested_pages: int) -> int:
        from core.config import cfg

        configured_limit = int(cfg.get("weread.mp_max_pages", 20) or 20)
        return max(requested_pages, configured_limit, 1)

    def _get_content_interval(self) -> float:
        from core.config import cfg

        return max(float(cfg.get("weread.content_interval", 2) or 0), 0)

    def _get_page_interval(self) -> float:
        from core.config import cfg

        return max(float(cfg.get("weread.page_interval", 1) or 0), 0)

    def _request_headers(self, include_ticket=False):
        headers = {
            "Cookie": self._weread_cookies,
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://weread.qq.com",
            "Referer": "https://weread.qq.com/",
        }
        if include_ticket and self._weread_ticket:
            # 新版微信读书已弃用 x-wr-ticket，仅需有效 Cookie 即可拉取文章列表；
            # 保留旧逻辑以便兼容旧版微信读书。无 ticket 时不拦截请求。
            headers["x-wr-ticket"] = self._weread_ticket
        return headers

    def _get_mp_articles_page(self, book_id: str, offset=0):
        # A failed authorization is not evidence that this endpoint is obsolete.
        try:
            response = requests.get(
                f"{WEREAD_WEB_BASE}/web/mp/articles",
                params={"bookId": book_id, "offset": offset},
                headers=self._request_headers(include_ticket=True),
                proxies=self._get_proxies(),
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise WereadMPAPIError("network_error", str(exc)) from exc
        if response.status_code != 200:
            raise WereadMPAPIError(
                response.status_code,
                f"article list returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WereadMPAPIError("invalid_json", "article list is not JSON") from exc
        if not isinstance(payload, dict):
            raise WereadMPAPIError("invalid_response", "article list is not an object")
        _raise_response_error(payload)
        return payload

    def _get_mp_cover(self, book_id: str) -> dict:
        """获取最新一篇文章，仅用作历史列表失败时的降级入口。

        返回: {"name", "title", "pic", "reviewId", ...}；无文章或接口异常时抛错。
        """
        try:
            response = requests.get(
                f"{WEREAD_WEB_BASE}/api/mp/cover",
                params={"bookId": book_id},
                headers=self._request_headers(),
                proxies=self._get_proxies(),
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise WereadMPAPIError("network_error", str(exc)) from exc
        if response.status_code != 200:
            raise WereadMPAPIError(
                response.status_code,
                f"mp cover returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WereadMPAPIError("invalid_json", "mp cover is not JSON") from exc
        if not isinstance(payload, dict):
            raise WereadMPAPIError("invalid_response", "mp cover is not an object")
        if not payload.get("reviewId"):
            _raise_response_error(payload)
            raise WereadMPAPIError(
                "empty_cover",
                "公众号未返回最新文章（可能从未在微信读书内发布过文章）",
                retriable=False,
            )
        return payload

    def _is_article_gathered(self, mp_id: str, review_id: str) -> bool:
        """判断 reviewId 是否已入库（跨会话增量，避免重复推送）。

        Article.id 存储格式为 ``{mp_id}-{aid}`` 并去掉 ``MP_WXS_`` 前缀，
        与 ``Db.add_article`` 的归一化规则保持一致。
        """
        if not mp_id or not review_id:
            return False
        from core.db import DB
        from core.models.article import Article

        session = DB.get_session()
        try:
            stored_id = f"{mp_id}-{review_id}".replace("MP_WXS_", "")
            row = session.query(Article.id).filter(
                Article.mp_id == mp_id,
                Article.id == stored_id,
                Article.has_content == 1,
            ).first()
            return row is not None
        except Exception:
            return False
        finally:
            session.close()

    def _get_mp_content(self, review_id: str):
        headers = self._request_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,*/*"
        try:
            response = requests.get(
                f"{WEREAD_WEB_BASE}/web/mp/content",
                params={"reviewId": review_id},
                headers=headers,
                proxies=self._get_proxies(),
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise WereadMPAPIError("network_error", str(exc)) from exc
        if response.status_code != 200:
            raise WereadMPAPIError(
                response.status_code,
                f"article content returned HTTP {response.status_code}",
            )
        raw = response.text
        if raw.lstrip().startswith('{'):
            try:
                payload=json.loads(raw)
            except ValueError:
                payload=None
            if isinstance(payload,dict):
                _raise_response_error(payload)
        soup = BeautifulSoup(raw, 'html.parser')
        root = soup.select_one('#js_content')
        if root is None and (re.search('环境异常|去验证|完成验证|请在微信客户端', soup.get_text()) or 'secitptpage/template/verify.js' in raw):
            raise WereadMPAPIError('verification_required', 'The source requires manual platform verification', False)
        if root is not None and len(root.get_text(' ', strip=True)) < 100:
            raise WereadMPAPIError('media_or_short_body', 'The page has no sufficiently long text body', False)
        if root is None:
            raise WereadMPAPIError('content_unconfirmed', 'Original text body is missing or access-restricted')
        # Preserve original metadata, dates and body boundaries instead of
        # turning a recovered original back into a converted body fragment.
        return original_snapshot(raw, soup, root)

    def _publish_item(self, item, mp_id, title, callback, item_callback):
        item['mp_id'] = mp_id
        if callback is not None:
            super().FillBack(CallBack=callback, data=item, Ext_Data={'mp_title': title, 'mp_id': mp_id})
        if item_callback is not None:
            item_callback(item)

    def _collect_latest_only(self, book_id, mp_id, title, gather, callback, item_callback):
        cover = self._get_mp_cover(book_id)
        review_id = str(cover.get('reviewId') or '')
        if not review_id.startswith(book_id + '_'):
            raise WereadMPAPIError('identity_mismatch', 'Latest article does not match source')
        if self._is_article_gathered(mp_id, review_id):
            return 0
        item = {'id': review_id, 'aid': review_id, 'mp_id': mp_id,
                'title': str(cover.get('title') or ''), 'link': build_mp_link_from_review_id(review_id, book_id),
                'cover': cover.get('pic') or '', 'digest': cover.get('digest') or '', 'content': '',
                'create_time': 0, 'update_time': 0, 'read_num': 0, 'like_num': 0, 'item_show_type': 0}
        if gather:
            item['content'] = self._get_mp_content(review_id)
        self._publish_item(item, mp_id, title, callback, item_callback)
        return 1

    def _collect_history(self, book_id, mp_id, title, gather, pages, state, store, lease, callback, item_callback):
        zone = ZoneInfo('Asia/Shanghai')
        start = datetime.now(zone).date() - timedelta(days=89)
        cutoff = int(datetime.combine(start, datetime.min.time(), tzinfo=zone).timestamp())
        state.update(window_start=start.isoformat(), requested_bodies=gather)
        if gather and state.get('completed_metadata_only'):
            state.update(offset=0, complete=False, anchor=[], completed_metadata_only=False)
        initial_offset = int(state.get('offset', 0))
        incremental = bool(state.get('complete'))
        # Every batch first checks the newest page. A partial historical scan
        # then overlaps its last group so new publications do not create gaps.
        offset = 0
        resuming = False
        resumed = initial_offset == 0 or incremental
        seen = set()
        max_publish = 0
        known_count = 0
        requests_left = pages + int(not incremental and initial_offset > 0)
        while requests_left:
            requests_left -= 1
            store.save(mp_id, state, lease)
            payload = self._get_mp_articles_page(book_id, offset)
            articles, groups = parse_mp_articles(payload)
            self.response_valid = True
            if resuming and state.get('anchor') and not any(item['id'] in state['anchor'] for item in articles):
                # A deletion/reorder or a large insertion moved the page; do
                # not claim contiguous coverage across a missing anchor.
                state.update(offset=0, anchor=[], complete=False, status='cursor_reset')
                store.save(mp_id, state, lease)
                break
            dated = []
            known_on_page = False
            for item in articles:
                review_id = item['id']
                if not review_id.startswith(book_id + '_') or not item['link'] or not item['title']:
                    raise WereadMPAPIError('identity_mismatch', 'List article metadata does not match source')
                stamp = int(item.get('update_time') or item.get('create_time') or 0)
                if stamp < 1262304000 or stamp > time.time() + 300:
                    stamp = 0
                    item['create_time'] = item['update_time'] = 0
                dated.append(stamp)
                if review_id not in state['discovered_ids']:
                    state['discovered_ids'].append(review_id)
                if stamp and stamp < cutoff:
                    continue
                if review_id in seen:
                    continue
                seen.add(review_id)
                if self._is_article_gathered(mp_id, review_id):
                    known_on_page = True
                    known_count += 1
                    if review_id in state['body_pending']:
                        state['body_pending'].remove(review_id)
                    continue
                max_publish = max(max_publish, stamp)
                if gather:
                    if review_id not in state['body_pending']:
                        state['body_pending'].append(review_id)
                    store.save(mp_id, state, lease)
                    try:
                        item['content'] = self._get_mp_content(review_id)
                    except WereadMPAPIError as error:
                        if error.code != 'media_or_short_body':
                            raise
                        # Explicit media/short-text gaps must not prevent later
                        # policy documents from being discovered in this list.
                        state.setdefault('body_reasons', {})[review_id] = error.code
                        store.save(mp_id, state, lease)
                        continue
                self._publish_item(item, mp_id, title, callback, item_callback)
                if callback is not None and gather and not self._is_article_gathered(mp_id, review_id):
                    raise WereadMPAPIError('write_unconfirmed', 'Article persistence was not confirmed')
                if review_id in state['body_pending']:
                    state['body_pending'].remove(review_id)
                state['saved_this_batch'] += 1
                store.save(mp_id, state, lease)
                if self._get_content_interval():
                    time.sleep(self._get_content_interval())
            old_page = bool(dated) and all(stamp and stamp < cutoff for stamp in dated)
            if not groups or old_page:
                state.update(complete=True, offset=0, anchor=[], status='window_scanned',
                             completed_metadata_only=not gather, end_reason='empty_page' if not groups else 'date_boundary')
                store.save(mp_id, state, lease)
                break
            if incremental and known_on_page:
                state.update(status='incremental_checked', complete=True)
                store.save(mp_id, state, lease)
                break
            if not resumed:
                offset = max(0, initial_offset - 1)
                resumed = resuming = True
            else:
                next_offset = offset + groups
                # If the endpoint returned only the overlap group, continue
                # once at the forward cursor rather than stalling forever.
                if resuming and next_offset <= initial_offset and not requests_left:
                    requests_left = 1
                offset = next_offset
                resuming = False
                last_items, _ = parse_mp_articles({'reviews': payload['reviews'][-1:]})
                state.update(offset=offset, anchor=[item['id'] for item in last_items], complete=False, status='history_partial')
                store.save(mp_id, state, lease)
            if requests_left and self._get_page_interval():
                time.sleep(self._get_page_interval())
        state['known_this_batch'] = known_count
        return max_publish

    def get_Articles(self, faker_id=None, Mps_id=None, Mps_title='', CallBack=None,
                     start_page=0, MaxPage=1, interval=10, Gather_Content=False,
                     Item_Over_CallBack=None, Over_CallBack=None):
        """History first, resumable 90-day scan; latest-only is an explicit degraded result."""
        self.articles, self.aids = [], []
        self.get_token()
        self.start_time = time.time()
        self.response_valid, self.last_error = False, None
        self._load_weread_auth()
        book_id = Mps_id if str(Mps_id or '').startswith('MP_WXS_') else faker_id
        if not re.fullmatch(r'MP_WXS_\d+', str(book_id or '')) or not self._weread_cookies:
            raise WereadMPAPIError('authorization_or_source_missing', 'A bound source and authorization are required', False)
        mp_id = Mps_id or book_id
        pages = min(max(int(MaxPage or 1), 1), 6)
        gather = bool(Gather_Content or self.Gather_Content)
        store = HistoryStore()
        try:
            with store.claim(mp_id) as (state, lease):
                state.update(adapter_version=ADAPTER_VERSION, saved_this_batch=0, known_this_batch=0,
                             latest_fallback=False, latest_saved=0)
                auth_path=Path(os.getenv('WEREAD_LIC_PATH','./data/wx.lic'))
                auth_epoch=auth_path.stat().st_mtime_ns if auth_path.exists() else 0
                if state.get('retry_after',0)>time.time() and state.get('auth_epoch')==auth_epoch:
                    raise WereadMPAPIError(state.get('list_code','channel_cooldown'),'Channel cooldown; progress preserved',False)
                try:
                    latest = self._collect_history(book_id, mp_id, Mps_title, gather, pages, state, store, lease, CallBack, Item_Over_CallBack)
                    state.update(list_code=0,retry_after=0)
                    store.save(mp_id, state, lease)
                    self.update_mps(mp_id, Feed(sync_time=int(time.time()), update_time=latest or None))
                except WereadMPAPIError as error:
                    state.update(list_code=error.code, status='authorization_or_limit' if error.code == -2012 else
                                 'authorization_required' if error.code == -2010 else
                                 'access_restricted' if error.code in (-2041,499) else 'history_error')
                    if error.code in (-2012,-2010,-2041,499):
                        state.update(retry_after=time.time()+1800,auth_epoch=auth_epoch)
                    if not self.response_valid:
                        # This may preserve a new latest item, but never turns
                        # a failed historical scan into a successful job.
                        try:
                            state['latest_saved'] = self._collect_latest_only(book_id, mp_id, Mps_title, gather, CallBack, Item_Over_CallBack)
                            state['latest_fallback'] = True
                        except Exception as fallback_error:
                            state['latest_fallback'] = False
                            state['latest_error'] = getattr(fallback_error, 'code', type(fallback_error).__name__)
                    store.save(mp_id, state, lease)
                    raise
        except Exception as error:
            self.last_error = {'code': getattr(error, 'code', type(error).__name__),
                               'message': '公众号历史补采未完成，请检查授权、访问限制或正文状态',
                               'retriable': getattr(error, 'retriable', True)}
            raise
        finally:
            super().Over(CallBack=Over_CallBack)
