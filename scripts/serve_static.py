"""Small read-only static server; no app imports, uploads, database or listing."""
import argparse
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class ReadingHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Keep the tiny server's log from growing on every public request.
        pass

    def list_directory(self, path):
        self.send_error(404, 'Not found')
        return None

    def send_head(self):
        path = Path(self.translate_path(self.path))
        encodings = [part.strip().lower() for part in self.headers.get('Accept-Encoding', '').split(',')]
        compressed = path.with_name(path.name + '.gz')
        if path.name == 'catalog.json' and 'gzip' in encodings and path.is_file() and compressed.is_file():
            stream = compressed.open('rb')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
            self.end_headers()
            return stream
        return super().send_head()

    def translate_path(self, path):
        result = Path(super().translate_path(path)).resolve()
        directory = Path(self.directory).resolve()
        if not result.is_relative_to(directory):
            return str(directory / '__not_found__')
        return str(result)

    def end_headers(self):
        self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Robots-Tag', 'noindex, nofollow')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=3011)
    args = parser.parse_args()
    directory = Path(args.directory).absolute()
    if not (directory / 'index.html').is_file():
        parser.error('No published reading edition')
    server = ThreadingHTTPServer((args.bind, args.port), partial(ReadingHandler, directory=str(directory)))
    server.daemon_threads = True
    print(f'Reading edition listening on {args.bind}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
