"""Durable, bounded public-history discovery, independent of WeRSS latest-item polling.

Only public album cursors and article identities are stored. Browser cookies belong
to the caller's in-memory transport, never to the queue or its reports.
"""
from __future__ import annotations

import base64
from collections import Counter
from contextlib import contextmanager
from datetime import date
import html
import json
import re
import time
from typing import Any, Callable, Iterator
from urllib.parse import urlencode
from uuid import uuid4

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.editorial import classify
from app.link_import import import_cached_link, literal_number, literal_biz
from app.material_dates import page_date, recent
from app.material_quality import inspect
from app.material_sources import sources
from app.materials import extract_material
from app.models import AppState, LearningMaterial, MaterialRevision
from app.public_albums import article_identity, parse_directory

PREFIX = 'material_history_v1:'
LEASE_SECONDS = 120
Fetch = Callable[[str], str]


class HistoryBusy(RuntimeError):
    pass


class HistoryRejected(ValueError):
    """Only stable, non-sensitive reason codes go into the durable manifest."""
    pass


def configured(key: str) -> dict[str, Any]:
    source = next((s for s in sources() if s['key'] == key and s['enabled'] and s['mp_id']), None)
    if not source:
        raise ValueError('Source must be bound and enabled')
    return source


def biz_for(source: dict[str, Any]) -> str:
    return base64.b64encode(source['mp_id'].removeprefix('MP_WXS_').encode()).decode()


def page_identity(raw: str, source: dict[str, Any]) -> str:
    soup = BeautifulSoup(raw, 'html.parser')
    name = soup.select_one('#js_name')
    biz = literal_biz(raw)
    mid, idx = literal_number(raw, 'mid|appmsgid'), literal_number(raw, 'idx')
    if not name or name.get_text(' ', strip=True) != source['name'] or not biz or biz != biz_for(source) or not mid or not idx:
        raise HistoryRejected('identity_unconfirmed')
    return ':'.join((biz, mid, idx))


def references(raw: str, biz: str) -> list[dict[str, str]]:
    """Short links are provisional; fetched account metadata must verify them."""
    root = BeautifulSoup(raw, 'html.parser').select_one('#js_content')
    items: dict[str, dict[str, str]] = {}
    if root:
        for anchor in root.select('a[href]'):
            url = html.unescape(str(anchor['href']))
            try:
                identity = article_identity(url)
            except ValueError:
                continue
            if not identity.startswith('/s/') and not identity.startswith(biz + ':'):
                continue
            items[identity] = {'url': 'https:' + url[5:] if url.startswith('http:') else url,
                               'identity': identity, 'title': anchor.get_text(' ', strip=True), 'create_time': ''}
    return list(items.values())


def read_state(key: str) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(AppState, PREFIX + key)
        return json.loads(row.value) if row else {}


def empty(source: dict[str, Any]) -> dict[str, Any]:
    return {'version': 1, 'mp_id': source['mp_id'], 'name': source['name'], 'items': {}, 'albums': {},
            'blocked': False, 'updated_at': None, 'lease': None}


def begin_write(db: Session) -> None:
    """Start on a fresh Session, clearing a leftover driver transaction if any.

    The queue never passes an active application transaction here. In particular,
    a connection returned by a failed earlier import must not nest SQLite BEGIN.
    """
    connection = db.connection()
    driver = connection.connection.driver_connection
    if driver is not None and getattr(driver, 'in_transaction', False):
        driver.rollback()
    connection.exec_driver_sql('BEGIN IMMEDIATE')


def save(key: str, state: dict[str, Any], token: str) -> None:
    """Compare-and-save the lease so a worker reclaimed after a crash cannot write."""
    with SessionLocal() as db:
        begin_write(db)
        row = db.get(AppState, PREFIX + key)
        current = json.loads(row.value) if row else {}
        lease = current.get('lease') or {}
        if lease.get('token') != token or lease.get('until', 0) < time.time():
            raise HistoryBusy('History lease expired or was reclaimed')
        state['lease'] = {'token': token, 'until': time.time() + LEASE_SECONDS}
        state['updated_at'] = time.time()
        assert row is not None
        row.value = json.dumps(state, ensure_ascii=False)
        db.commit()


@contextmanager
def leased(source: dict[str, Any]) -> Iterator[tuple[dict[str, Any], str]]:
    key, token = source['key'], uuid4().hex
    with SessionLocal() as db:
        begin_write(db)
        row = db.get(AppState, PREFIX + key)
        state = json.loads(row.value) if row else empty(source)
        if (state.get('lease') or {}).get('until', 0) > time.time():
            raise HistoryBusy('Another history batch is running for this account')
        if state['mp_id'] != source['mp_id'] or state['name'] != source['name']:
            raise ValueError('Source identity changed; review the old queue before collecting')
        state['lease'] = {'token': token, 'until': time.time() + LEASE_SECONDS}
        value = json.dumps(state, ensure_ascii=False)
        if row:
            row.value = value
        else:
            db.add(AppState(key=PREFIX + key, value=value))
        db.commit()
    try:
        yield state, token
    finally:
        with SessionLocal() as db:
            begin_write(db)
            row = db.get(AppState, PREFIX + key)
            current = json.loads(row.value) if row else {}
            if (current.get('lease') or {}).get('token') == token:
                current['lease'] = None
                assert row is not None
                row.value = json.dumps(current, ensure_ascii=False)
                db.commit()


def add_item(state: dict[str, Any], item: dict[str, Any], via: str, biz: str) -> None:
    try:
        identity = article_identity(item['url'])
    except (ValueError, KeyError):
        return
    if not identity.startswith('/s/') and not identity.startswith(biz + ':'):
        return
    # Already verified aliases should not reappear as new pending short links.
    existing = next((r for r in state['items'].values() if identity == r['identity'] or identity in r.get('aliases', [])), None)
    if existing is None:
        existing = {**item, 'identity': identity, 'status': 'pending', 'attempts': 0, 'aliases': [], 'via': []}
        state['items'][identity] = existing
    if not existing.get('title') and item.get('title'):
        existing['title'] = item['title']
    if not existing.get('day') and item.get('create_time'):
        existing['day'] = page_date('var ct = "' + str(item['create_time']) + '";')
    if via not in existing['via']:
        existing['via'].append(via)


def discover(state: dict[str, Any], raw: str, via: str, biz: str) -> None:
    for item in references(raw, biz):
        add_item(state, item, via, biz)
    # Album IDs are inert digits, not executable page expressions. Every returned
    # article still has to match this account before it is accepted.
    for album in set(re.findall(r'album_id[^0-9]{0,15}([0-9]{8,})', raw)):
        state['albums'].setdefault(album, {'status': 'pending', 'cursor': None, 'seen': [], 'pages': 0,
                                           'declared': None, 'attempts': 0})


def seed(state: dict[str, Any], source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    known: dict[str, dict[str, Any]] = {}
    biz = biz_for(source)
    with SessionLocal() as db:
        for material in db.query(LearningMaterial).filter_by(account=source['name']):
            revision = db.query(MaterialRevision).filter_by(material_id=material.id).order_by(MaterialRevision.revision.desc()).first()
            raw = revision.source_html if revision else ''
            raw = raw or ''
            add_item(state, {'url': material.url, 'title': material.title}, 'saved_article', biz)
            try:
                alias = article_identity(material.url)
            except ValueError:
                continue
            queued = next(r for r in state['items'].values() if r['identity'] == alias or alias in r.get('aliases', []))
            quality = inspect(material.payload or {}, raw)
            # Repair an existing imported fragment independently of whether its
            # content qualifies for a new current-affairs import.
            if quality['raw_kind'] != 'full_page':
                queued.update(material_id=material.id, needs_body=True, url=material.url)
                if queued['status'] in {'existing', 'imported', 'excluded'}:
                    queued['status'] = 'pending'
            try:
                identity = page_identity(raw, source)
            except ValueError:
                continue
            item = merge_identity(state, alias, identity)
            item.update(day=page_date(raw), category=classify(material.title, BeautifulSoup(raw, 'html.parser').get_text(' ', strip=True), source['name'], material.url).category)
            discover(state, raw, identity, biz)
            item['quality'] = quality['status']
            item['needs_body'] = quality['raw_kind'] != 'full_page'
            if item['quality'] == 'readable':
                item.update(status='existing', material_id=material.id)
                known[identity] = item
    return known


def merge_identity(state: dict[str, Any], alias: str, identity: str) -> dict[str, Any]:
    if alias not in state['items']:
        alias = next(k for k, r in state['items'].items() if alias in r.get('aliases', []))
    item: dict[str, Any] = state['items'][alias]
    if alias == identity:
        return item
    target: dict[str, Any] | None = state['items'].get(identity)
    if target:
        target['aliases'] = sorted(set(target['aliases'] + item.get('aliases', []) + [alias]))
        target['via'] = sorted(set(target['via'] + item['via']))
    else:
        target = item
        target['identity'] = identity
        target['aliases'] = sorted(set(item.get('aliases', []) + [alias]))
        state['items'][identity] = target
    del state['items'][alias]
    return target


def scope_reason(item: dict[str, Any], scope: str, today: date | None = None) -> str | None:
    category = item.get('category')
    learning = category in {'textbook', 'exam_advice'} or bool(re.search(r'带背|背诵|考点串讲', item.get('title', '')))
    if scope == 'learning':
        return None if learning else 'other_scope'
    if scope == 'all':
        return None
    if learning:
        return 'learning'
    if item.get('day') and not recent(item['day'], today):
        return 'outside_window'
    # A title alone cannot exclude an uncertain article. Body classification and
    # publication time are required; unknown dates never inherit import time.
    if item.get('fetched'):
        if not item.get('day'):
            return 'date_unknown'
        if category not in {'policy', 'analysis', 'report'}:
            return 'other_content'
    return None


def verification(raw: str) -> bool:
    soup = BeautifulSoup(raw, 'html.parser')
    return not soup.select_one('#js_content') and bool(
        re.search('环境异常|去验证|完成验证|请在微信客户端|验证后继续', soup.get_text())
        or re.search(r'secitptpage/template/verify\.js|seajs\.use\([\x22\x27]safety[^\x22\x27]*verify', raw))


def reconcile_cached_verification(key: str, cached: Callable[[str], str | None]) -> dict[str, Any]:
    """Reclassify already cached silent challenges without any network retry."""
    source = configured(key)
    with leased(source) as (state, token):
        for item in state['items'].values():
            if item['status'] not in {'rejected', 'failed'}:
                continue
            raw = cached(item['url'])
            if raw and verification(raw):
                item.update(status='verification_required', error='verification_required')
                state['blocked'] = True
        save(key, state, token)
        return summarize(state)


def fail(item: dict[str, Any], error: Exception) -> None:
    # Keep only exception class, never request headers, cookies or response dumps.
    item['error'] = type(error).__name__
    item['status'] = 'failed' if item['attempts'] >= 3 else 'retry'
    item['retry_at'] = time.time() + min(3600, 60 * 2 ** item['attempts'])


def run_batch(key: str, fetch: Fetch | None = None, *, max_articles: int = 20, max_pages: int = 3,
              scope: str = 'recent-affairs', resume: bool = False, pause: float = 1,
              check_history_entry: bool = False, refresh_directories: bool = False,
              retry_failed: bool = False) -> dict[str, Any]:
    if not 0 <= max_articles <= 300 or not 0 <= max_pages <= 30 or scope not in {'recent-affairs', 'learning', 'all'} or pause < 0:
        raise ValueError('Invalid batch limits or scope')
    source = configured(key)
    with leased(source) as (state, token):
        known = seed(state, source)
        if resume:
            state['blocked'] = False
            for collection in ('items', 'albums'):
                for item in state[collection].values():
                    if item['status'] == 'verification_required':
                        item.update(status='pending', attempts=0)
        for collection in ('items', 'albums'):
            for item in state[collection].values():
                if retry_failed and item['status'] in {'failed', 'rejected', 'stalled'}:
                    item.update(status='pending', attempts=0, retry_at=0)
        if refresh_directories:
            for directory in state['albums'].values():
                if directory['status'] == 'complete':
                    directory.update(status='pending', cursor=None)
        save(key, state, token)
        if not fetch or state['blocked']:
            return summarize(state)
        biz, pages, attempted, imported = biz_for(source), 0, 0, 0
        if check_history_entry:
            # History-home access is independent from article/album access.
            # A client-only home page cannot block otherwise accessible articles.
            try:
                raw = fetch('https://mp.weixin.qq.com/mp/profile_ext?' + urlencode({'action': 'home', '__biz': biz, 'scene': '124'}))
                state['history_entry'] = {'checked_at': time.time(), 'state': 'wechat_client_required' if verification(raw) else 'unparsed'}
            except Exception as error:
                state['history_entry'] = {'checked_at': time.time(), 'state': 'request_failed', 'error': type(error).__name__}
            save(key, state, token)
        # Article bodies can discover additional albums, so choose work again
        # after every committed page/article, rather than snapshotting a list.
        while not state['blocked']:
            album_pair = next(((a, r) for a, r in state['albums'].items() if r['status'] in {'pending', 'retry'} and r.get('retry_at', 0) <= time.time()), None)
            if album_pair and pages < max_pages:
                album, directory = album_pair
                params = {'action': 'getalbum', '__biz': biz, 'album_id': album, 'count': '100'}
                if directory['cursor']:
                    params.update(begin_msgid=directory['cursor'][0], begin_itemidx=directory['cursor'][1])
                directory['attempts'] += 1
                pages += 1
                save(key, state, token)
                try:
                    raw = fetch('https://mp.weixin.qq.com/mp/appmsgalbum?' + urlencode(params))
                    if verification(raw):
                        directory['status'] = 'verification_required'
                        state['blocked'] = True
                    else:
                        data = parse_directory(raw)
                        fresh = [x for x in data['items'] if x['identity'].startswith(biz + ':') and x['identity'] not in directory['seen']]
                        for entry in fresh:
                            add_item(state, entry, 'album:' + album, biz)
                        directory['seen'] = sorted(set(directory['seen'] + [x['identity'] for x in fresh]))
                        directory['declared'] = data['declared'] if data['declared'] is not None else directory['declared']
                        directory['pages'] += 1
                        directory['attempts'] = 0
                        if directory['declared'] is not None and len(directory['seen']) >= directory['declared']:
                            directory['status'] = 'complete'
                        elif data['more'] is False:
                            directory['status'] = 'count_mismatch' if directory['declared'] is not None else 'complete'
                        elif not fresh:
                            directory['status'] = 'stalled'
                        else:
                            last = fresh[-1]
                            cursor = [last['mid'], last['idx']]
                            if not all(cursor) or cursor == directory['cursor']:
                                directory['status'] = 'stalled'
                            else:
                                directory.update(cursor=cursor, status='pending')
                except Exception as error:
                    fail(directory, error)
            elif attempted < max_articles:
                candidate = next((r for r in state['items'].values() if r['status'] in {'pending', 'retry', 'excluded'} and r.get('retry_at', 0) <= time.time() and (r.get('needs_body') or not scope_reason(r, scope))), None)
                if candidate is None:
                    break
                attempted += 1
                candidate['attempts'] += 1
                save(key, state, token)
                try:
                    raw = fetch(candidate['url'])
                    if verification(raw):
                        candidate['status'] = 'verification_required'
                        state['blocked'] = True
                    else:
                        identity = page_identity(raw, source)
                        if not candidate['identity'].startswith('/s/') and identity != candidate['identity']:
                            raise HistoryRejected('url_identity_mismatch')
                        repair = {k: candidate[k] for k in ('material_id', 'url', 'needs_body') if k in candidate} if candidate.get('needs_body') else {}
                        candidate = merge_identity(state, candidate['identity'], identity)
                        candidate.update(repair)
                        soup = BeautifulSoup(raw, 'html.parser')
                        title_meta = soup.select_one('meta[property="og:title"]')
                        root = soup.select_one('#js_content')
                        if root is None or not title_meta or len(root.get_text(' ', strip=True)) < 100:
                            raise HistoryRejected('body_missing')
                        candidate.update(title=str(title_meta.get('content', '')), day=page_date(raw), fetched=True)
                        candidate['quality'] = inspect(extract_material(raw), raw)['status']
                        selection = classify(candidate['title'], root.get_text(' ', strip=True), source['name'], candidate['url'])
                        candidate['category'] = selection.category
                        discover(state, raw, identity, biz)
                        reason = scope_reason(candidate, scope)
                        if identity in known and not candidate.get('needs_body'):
                            candidate.update(status='existing', material_id=known[identity]['material_id'])
                        elif reason and not candidate.get('needs_body'):
                            candidate.update(status='excluded', exclusion=reason)
                        else:
                            save(key, state, token)
                            answer = import_cached_link(key, candidate['url'], raw)
                            candidate.update(status='imported', material_id=answer['id'])
                            candidate['needs_body'] = False
                            known[identity] = candidate
                            imported += int(answer['state'] != 'unchanged')
                except HistoryBusy:
                    raise
                except ValueError as error:
                    # Identity/body problems need review; retrying unchanged pages
                    # must not fill the queue with duplicates or pretend success.
                    candidate.update(status='rejected', error=str(error) if isinstance(error, HistoryRejected) else 'invalid_body')
                except Exception as error:
                    fail(candidate, error)
            else:
                break
            save(key, state, token)
            if pause:
                time.sleep(pause)
        save(key, state, token)
        return {**summarize(state), 'batch': {'article_requests': attempted, 'directory_requests': pages, 'imported': imported}}


def summarize(state: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    if not state:
        return {'initialized': False, 'account_total': None, 'account_coverage_rate': None}
    items = list(state['items'].values())
    statuses = Counter(r['status'] for r in items)
    bodies = [r for r in items if r['status'] in {'existing', 'imported'} and r.get('quality') == 'readable']
    return {'initialized': True, 'updated_at': state['updated_at'], 'discovered': len(items),
            'body_available': len(bodies), 'recent_affairs': sum(not scope_reason(r, 'recent-affairs', today) and bool(r.get('day')) and r.get('category') in {'policy', 'report', 'analysis'} for r in bodies),
            'learning': sum(r.get('category') in {'textbook', 'exam_advice'} or bool(re.search(r'带背|背诵', r.get('title', ''))) for r in items),
            'outside_window': sum(bool(r.get('day')) and not recent(r['day'], today) for r in items),
            'date_unknown': sum(not r.get('day') for r in items),
            'pending': sum(r['status'] in {'pending', 'retry', 'excluded'} and not scope_reason(r, 'recent-affairs', today) for r in items),
            'body_repair_pending': sum(r['status'] in {'pending', 'retry'} and bool(r.get('needs_body')) for r in items),
            'failed': statuses['failed'] + statuses['rejected'], 'retry': statuses['retry'],
            'verification_required': statuses['verification_required'], 'blocked': state['blocked'],
            'excluded': statuses['excluded'], 'account_total': None, 'account_coverage_rate': None,
            'body_needs_review': sum(r['status'] in {'existing', 'imported'} and r.get('quality') != 'readable' for r in items),
            'failure_reasons': dict(Counter(r.get('error', 'unknown') for r in items if r['status'] in {'failed', 'rejected'})),
            'history_entry': state.get('history_entry', {'state': 'not_checked'}),
            'directories': [{'album': a, 'declared': r['declared'], 'discovered': len(r['seen']),
                             'pages': r['pages'], 'status': r['status']} for a, r in state['albums'].items()]}
