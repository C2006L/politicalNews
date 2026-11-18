"""Optional WeRSS target-channel checks. Never persist or return credentials."""
from __future__ import annotations
from app.material_sources import accounts as get_accounts

from datetime import datetime, timezone
import json
import hashlib
import math
import re
from pathlib import Path
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from app.config import get_settings
from app.database import SessionLocal
from app.models import AppState

KEY = 'werss_channel_check_v1'
LOCK = threading.Lock()
MAX_AGE_SECONDS = 7200


def source_key() -> str:
    settings = get_settings()
    return hashlib.sha256((settings.werss_api_url + '\n' + settings.werss_api_token_file + '\n' +
                           json.dumps(get_accounts(), sort_keys=True)).encode()).hexdigest()


class ProbeFailure(Exception):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def configured() -> bool:
    settings = get_settings()
    return bool(settings.werss_api_url and settings.werss_api_token_file)


def service_request(path: str, method: str = 'POST', payload: dict[str, Any] | None = None,
                    recovery: bool = False) -> dict[str, Any]:
    allowed = ((path == '/weread/mp/test' and method == 'POST' and not recovery) or
               (recovery and method == 'POST' and re.fullmatch(r'/articles/[A-Za-z0-9][A-Za-z0-9_.~-]{0,249}/refresh', path)) or
               (recovery and method == 'GET' and re.fullmatch(r'/articles/refresh/tasks/[0-9a-f-]{36}', path)))
    if not allowed:
        raise ProbeFailure('invalid_operation')
    settings = get_settings()
    try:
        parsed = urlparse(settings.werss_api_url)
    except ValueError:
        raise ProbeFailure('invalid_configuration') from None
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ProbeFailure('invalid_configuration')
    try:
        token_file = settings.werss_recovery_token_file if recovery else settings.werss_api_token_file
        if not token_file:
            raise ProbeFailure('service_credential_missing')
        token = Path(token_file).read_text(encoding='utf-8').strip()
        if token.startswith('{'):
            credentials = json.loads(token)
            token = credentials['key'] + ':' + credentials['secret']
            scheme = 'AK-SK '
        else:
            scheme = 'Bearer '
    except (OSError, ValueError, KeyError, TypeError):
        raise ProbeFailure('service_credential_missing') from None
    if not token or len(token) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ProbeFailure('service_credential_missing')
    request = Request(settings.werss_api_url.rstrip('/') + '/api/v1/wx' + path,
                      data=json.dumps(payload).encode() if payload is not None else None, method=method,
                      headers={'Content-Type': 'application/json', 'Authorization': scheme + token})
    try:
        # Credentials cannot follow redirects or be sent through a host proxy.
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=50) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ProbeFailure('incompatible_response')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ProbeFailure('incompatible_response')
        return payload
    except HTTPError as exc:
        raise ProbeFailure('service_auth_required' if exc.code in (401, 403) else
                           'resource_missing' if exc.code == 404 and recovery else
                           'incompatible_service' if exc.code in (301,302,303,307,308,404,405) else 'service_error') from None
    except (URLError, OSError, TimeoutError):
        raise ProbeFailure('service_unavailable') from None
    except (ValueError, UnicodeError):
        raise ProbeFailure('incompatible_response') from None


def api_request(account_id: str) -> dict[str, Any]:
    return service_request('/weread/mp/test', payload={'mp_id': account_id})


def normalize(payload: dict[str, Any], account_id: str) -> dict[str, Any]:
    # Only explicit fields survive; raw API messages may contain session data.
    data = payload.get('data')
    if type(payload.get('code')) is int and payload.get('code') == 0 and isinstance(data, dict) and data.get('mp_id') == account_id:
        count = data.get('article_count')
        title = data.get('latest_title')
        if type(count) is int and count in (0, 1) and isinstance(title, str):
            return {'state': 'accessible' if count else 'empty_response',
                    'authorization': 'not_rejected_at_check' if count else 'unknown',
                    'latest_title': title[:300], 'article_count': count}
    code = data.get('code') if isinstance(data, dict) else None
    if str(code) == '-2012':
        return {'state': 'authorization_or_limit', 'authorization': 'unknown'}
    if str(code) in {'-2013', '401', 'missing_cookie'}:
        return {'state': 'login_required', 'authorization': 'rejected'}
    if str(code) in {'-2010', '-2014', '403', '429', '499', '-2041'}:
        return {'state': 'restricted_or_unsupported', 'authorization': 'unknown'}
    if str(code) in {'network_error', 'timeout'}:
        return {'state': 'network_error', 'authorization': 'unknown'}
    return {'state': 'incompatible_response', 'authorization': 'unknown'}


def read_raw() -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(AppState, KEY)
        try:
            value = json.loads(row.value) if row else {'state': 'not_checked'}
        except ValueError:
            value = {'state': 'interrupted'}
        return value if isinstance(value, dict) else {'state': 'interrupted'}


def save(reset: bool = False, **changes: Any) -> None:
    with SessionLocal() as db:
        row = db.get(AppState, KEY)
        try:
            value = json.loads(row.value) if row else {}
        except ValueError:
            value = {}
        if not isinstance(value, dict):
            value = {}
        if reset:
            value = {}
        value.update(changes)
        if row is None:
            db.add(AppState(key=KEY, value=json.dumps(value, ensure_ascii=False)))
        else:
            row.value = json.dumps(value, ensure_ascii=False)
        db.commit()


def status() -> dict[str, Any]:
    value = read_raw()
    if value.get('source_key') != source_key():
        value = {'state': 'not_checked'}
    value = {key: value[key] for key in ('state', 'started_at', 'finished_at', 'last_accessible_at', 'error', 'accounts') if key in value}
    value['retry_after_seconds'] = 0
    if not configured():
        return {**value, 'state': 'not_configured', 'fresh': False, 'scope': 'target_latest_article',
                'coverage': 'latest_only_not_complete'}
    if value.get('state') == 'running' and not LOCK.locked():
        value = {**value, 'state': 'interrupted'}
    if value.get('state') != 'interrupted':
        try:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(value.get('started_at', ''))).total_seconds()
            value['retry_after_seconds'] = max(0, min(60, math.ceil(60 - elapsed)))
        except (TypeError, ValueError):
            pass
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value.get('finished_at', ''))).total_seconds()
        fresh = 0 <= age < MAX_AGE_SECONDS and value.get('state') == 'checked'
    except (ValueError, TypeError):
        fresh = False
    return {**value, 'fresh': fresh, 'scope': 'target_latest_article', 'coverage': 'latest_only_not_complete'}


def run_check(_lock_owned: bool = False) -> None:
    if not _lock_owned and not LOCK.acquire(blocking=False):
        return
    try:
        save(reset=read_raw().get('source_key') != source_key(),
             state='running', started_at=stamp(), finished_at=None, accounts=[], error=None, source_key=source_key())
        accounts = []
        try:
            if not configured():
                raise ProbeFailure('not_configured')
            for account_id, name in get_accounts().items():
                result = normalize(api_request(account_id), account_id)
                accounts.append({'id': account_id, 'name': name, **result, 'body_verified': False})
        except ProbeFailure as exc:
            save(state='failed', finished_at=stamp(), error=exc.category, accounts=accounts)
            return
        finished = stamp()
        changes: dict[str, Any] = dict(state='checked', finished_at=finished, error=None, accounts=accounts)
        if accounts and all(account['state'] == 'accessible' for account in accounts):
            changes['last_accessible_at'] = finished
        save(**changes)
    except Exception:
        # Never persist exception strings, URLs or request headers.
        try:
            save(state='failed', finished_at=stamp(), accounts=[], error='check_failed')
        except Exception:
            pass
    finally:
        LOCK.release()


def start_check() -> bool:
    if not LOCK.acquire(blocking=False):
        return False
    try:
        thread = threading.Thread(target=run_check, kwargs={'_lock_owned': True}, daemon=True)
        thread.start()
        return True
    except Exception:
        LOCK.release()
        raise
