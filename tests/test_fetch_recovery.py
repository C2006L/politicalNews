import importlib
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

fetch = importlib.import_module('app.fetch_news')

class FetchRecoveryTests(unittest.TestCase):
    def test_404_is_not_retried(self):
        error = HTTPError('https://example.com', 404, 'missing', {}, None)
        with patch.object(fetch, 'urlopen', side_effect=error) as request, patch.object(fetch.time, 'sleep') as sleep:
            with self.assertRaises(HTTPError):
                fetch._fetch_url('https://example.com')
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def test_temporary_error_is_retried(self):
        with patch.object(fetch, 'urlopen', side_effect=URLError('temporary')) as request, patch.object(fetch.time, 'sleep'), patch.object(fetch, 'HTTP_RETRIES', 1):
            with self.assertRaises(URLError):
                fetch._fetch_url('https://example.com')
            self.assertEqual(request.call_count, 2)

    def test_people_archive_stops_after_missing_pages(self):
        source = dict(fetch.CURATED_HTML_SOURCES[0])
        error = HTTPError('https://example.com', 404, 'missing', {}, None)
        with patch.object(fetch, 'CURATED_HTML_SOURCES', [source]), patch.object(fetch, '_fetch_url', side_effect=error) as request:
            self.assertEqual(fetch._load_external_html_sources(), [])
            self.assertEqual(request.call_count, 3)

    def test_success_resets_missing_page_count(self):
        source = dict(fetch.CURATED_HTML_SOURCES[0])
        source['list_urls'] = source['list_urls'][:6]
        error = HTTPError('https://example.com', 404, 'missing', {}, None)
        with patch.object(fetch, 'CURATED_HTML_SOURCES', [source]), patch.object(fetch, '_fetch_url', side_effect=[error, error, '<html></html>', error, error, error]) as request, patch.object(fetch, '_parse_generic_list_page', return_value=[]):
            fetch._load_external_html_sources()
            self.assertEqual(request.call_count, 6)

if __name__ == '__main__':
    unittest.main()
