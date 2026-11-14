"""Create a revocable 90-day WeRSS channel-check key, outside the project.

Only sanitized completion messages are printed. Existing destinations are kept.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.parse import urlparse


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def setup(base: str, admin_file: Path, output: Path, recovery: bool = False) -> None:
    parsed = urlparse(base)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.path not in ('', '/') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Only a local WeRSS service is supported')
    # Reserve before remote creation; never overwrite another integration key.
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    created = None
    token = ''
    opener = build_opener(ProxyHandler({}), NoRedirect())
    def call(path, payload=None, method='POST'):
        request = Request(base.rstrip('/') + '/api/v1/wx' + path, method=method,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(request, timeout=15) as response:
            return json.load(response)
    try:
        token = admin_file.read_text(encoding='utf-8').strip()
        result = call('/auth/ak/create', {'name': 'Political News body recovery' if recovery else 'Political News channel checks',
            'description': 'Controlled missing-body recovery for two selected feeds.' if recovery else
                           'Local target-channel connectivity checks; no article updates or external notifications.',
            'permissions': ['article:read','article:sync'] if recovery else ['article:read'], 'expires_in_days': 90})
        if result.get('code') != 0:
            raise ValueError('Integration key creation failed')
        created = result['data']['id']
        data = {key: result['data'][key] for key in ('id', 'key', 'secret', 'expires_at')}
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            fd = None
            json.dump(data, stream)
    except Exception:
        if fd is not None:
            os.close(fd)
        if created:
            try:
                call('/auth/ak/' + created, method='DELETE')
            except Exception:
                pass
        output.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8003')
    parser.add_argument('--admin-token-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recovery', action='store_true')
    args = parser.parse_args()
    try:
        setup(args.base, args.admin_token_file, args.output, args.recovery)
    except FileExistsError:
        print('Existing integration credential kept; no new key created.')
    except Exception:
        raise SystemExit('Could not configure the local integration; check the WeRSS administrator login and output directory.') from None
    else:
        print('Local integration configured for 90 days. Credentials were saved only to the selected private file.')
