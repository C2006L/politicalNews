"""User-run Linux installer. All project files stay below /home/ubuntu/lcz."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

from static_release import apply, rollback

ALLOWED = Path('/home/ubuntu/lcz')


def checked_root(value):
    root = Path(value).resolve()
    if not root.is_relative_to(ALLOWED.resolve()) or root == ALLOWED.resolve():
        raise ValueError('Project directory must be a child of /home/ubuntu/lcz')
    return root


def process_identity(pid):
    try:
        # comm can contain spaces and parentheses; split after its final ')'.
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return stat[19], [x.decode() for x in args if x]
    except (OSError, IndexError):
        return None, []


def running(root):
    record = root / 'server.json'
    if not record.exists():
        return None
    info = json.loads(record.read_text())
    started, args = process_identity(info['pid'])
    expected = str(root / 'bin' / 'serve_static.py')
    if started == info['started'] and expected in args and str(root / 'current') in args:
        return info
    return None


def main():
    parser = argparse.ArgumentParser(description='Install the static reader inside lcz only')
    parser.add_argument('--root', default='/home/ubuntu/lcz/political-news-static')
    parser.add_argument('--bundle', type=Path, default=Path(__file__).parent / 'site.tar.gz')
    parser.add_argument('--port', type=int, default=3011)
    parser.add_argument('--action', choices=['publish', 'stop', 'rollback', 'status'], default='publish')
    args = parser.parse_args()
    root = checked_root(args.root)
    if not 1024 <= args.port <= 65535:
        parser.error('Use a nonprivileged port from 1024 to 65535')
    live = running(root)
    if args.action == 'status':
        print(json.dumps({'server': live, 'current': str((root / 'current').resolve())})); return
    if args.action == 'stop':
        if live:
            os.kill(live['pid'], signal.SIGTERM)
        print('Owned reading service stopped' if live else 'No matching reading service'); return
    if args.action == 'rollback':
        print(json.dumps(rollback(root))); return
    if live and live['port'] != args.port:
        raise ValueError('Existing reader uses a different port; stop it explicitly before changing the port')
    if not live:
        # Never stop whichever other service owns the requested port.
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('0.0.0.0', args.port))
    result = apply(root, args.bundle)
    print(json.dumps(result), flush=True)
    if live:
        print(f'Published; existing service continues on port {args.port}'); return
    tools = root / 'bin'; tools.mkdir(exist_ok=True)
    source = Path(__file__).resolve().parent
    for filename in ['serve_static.py', 'static_release.py', 'install_static.py']:
        target = tools / filename
        if target.resolve() != (source / filename).resolve():
            shutil.copyfile(source / filename, target)
    log = root / 'server.log'
    if log.exists() and log.stat().st_size > 256 * 1024:
        os.replace(log, root / 'server.previous.log')
    with log.open('ab') as stream:
        process = subprocess.Popen([sys.executable, '-u', str(tools / 'serve_static.py'), '--directory', str(root / 'current'),
                                    '--bind', '0.0.0.0', '--port', str(args.port)],
                                   cwd=root, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError('Reader failed to start; inspect ' + str(log))
        try:
            with socket.create_connection(('127.0.0.1', args.port), timeout=.3):
                break
        except OSError:
            time.sleep(.2)
    else:
        process.terminate(); raise RuntimeError('Reader startup timed out')
    started, _ = process_identity(process.pid)
    if not started:
        raise RuntimeError('Could not verify reader identity')
    (root / 'server.json').write_text(json.dumps({'pid': process.pid, 'started': started, 'port': args.port}), encoding='utf-8')
    print(f'Ready: http://YOUR_SERVER_IP:{args.port}/')
    print('No system directories or firewall settings were changed. Restart after a server reboot with the same publish command.')


if __name__ == '__main__':
    main()
