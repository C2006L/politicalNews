from functools import partial
import gzip
from http.server import ThreadingHTTPServer
import importlib.util
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler


def test_port_only_reader_serves_pages_but_no_directory_listing_or_writes(tmp_path):
    spec = importlib.util.spec_from_file_location('serve_static', Path(__file__).parents[1] / 'scripts/serve_static.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    site = tmp_path / 'site'; site.mkdir()
    (site / 'index.html').write_text('<h1>Reading</h1>')
    (site / 'catalog.json').write_bytes(b'[]')
    (site / 'catalog.json.gz').write_bytes(gzip.compress(b'[]'))
    (site / 'private-looking-empty-directory').mkdir()
    (tmp_path / 'not-public.txt').write_text('OUTSIDE')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(module.ReadingHandler, directory=str(site)))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    opener = build_opener(ProxyHandler({})); base = f'http://127.0.0.1:{server.server_port}'
    try:
        with opener.open(base + '/') as response:
            assert b'Reading' in response.read()
            assert response.headers['Cache-Control'] == 'no-cache'
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
            assert "object-src 'none'" in response.headers['Content-Security-Policy']
        with opener.open(Request(base + '/catalog.json', headers={'Accept-Encoding': 'gzip'})) as response:
            assert response.headers['Content-Encoding'] == 'gzip'
            assert gzip.decompress(response.read()) == b'[]'
        for path in ['/private-looking-empty-directory/', '/../not-public.txt', '/api/learning/sync']:
            try:
                opener.open(base + path)
            except HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError('Unexpected public path: ' + path)
        try:
            opener.open(Request(base + '/', data=b'x', method='POST'))
        except HTTPError as error:
            assert error.code == 501
        else:
            raise AssertionError('Static server accepted a write')
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=3)
