"""Windows desktop entry point. Configure private storage BEFORE importing app."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import queue
import secrets
import socket
import sys
import threading
import time
import webbrowser


def prepare(data: Path) -> dict:
    data.mkdir(parents=True, exist_ok=True)
    config_file = data / 'settings.json'
    settings = json.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else {}
    # Never inherit a developer database, deployment prefix, or admin credential.
    os.environ.update(DATABASE_URL='sqlite:///' + (data / 'study.db').as_posix(), BASE_PATH='',
                      AUTO_SYNC_ON_STARTUP='0', BOOTSTRAP_RECENT_NEWS_ON_STARTUP='0',
                      SCHEDULER_ENABLED='1', EXPOSE_API_DOCS='0',
                      SYNC_ADMIN_TOKEN=secrets.token_urlsafe(32),
                      WERSS_API_URL=str(settings.get('werss_api_url', '')),
                      WERSS_API_TOKEN_FILE=str(settings.get('werss_api_token_file', '')),
                      WERSS_RECOVERY_TOKEN_FILE=str(settings.get('werss_recovery_token_file', '')),
                      WERSS_DATABASE_PATH=str(settings.get('werss_database_path', '')))
    stream = (data / 'application.log').open('a', encoding='utf-8', buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(stream)], force=True)
    return settings


def acquire_lock(data: Path):
    import msvcrt
    handle = (data / 'instance.lock').open('a+b')
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def backup_before_upgrade(data: Path):
    import sqlite3
    from app.migrations import LATEST_VERSION
    from scripts.database_backup import backup
    path = data / 'study.db'
    if not path.exists():
        return
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        exists = db.execute("select 1 from sqlite_master where name='schema_migrations'").fetchone()
        version = db.execute('select max(version) from schema_migrations').fetchone()[0] if exists else 0
    if (version or 0) < LATEST_VERSION:
        folder = data / 'backups'
        folder.mkdir(exist_ok=True)
        backup(path, folder / f'before-v{LATEST_VERSION}-{time.time_ns()}.db')


class LocalService:
    def __init__(self, port: int = 0):
        self.server = None
        self.error = None
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', port))
        self.url = f'http://127.0.0.1:{self.sock.getsockname()[1]}'
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            import uvicorn
            from app.main import app
            self.server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False,
                                                      loop='asyncio', http='h11', ws='none'))
            self.server.run(sockets=[self.sock])
        except BaseException as exc:
            logging.exception('Local service failed')
            self.error = str(exc)

    def stop(self):
        if self.server is not None:
            self.server.should_exit = True
        self.thread.join(timeout=8)
        self.sock.close()


def smoke(service: LocalService, data: Path):
    from urllib.request import urlopen
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    deadline = time.monotonic() + 90
    while not (service.server and service.server.started):
        if service.error or time.monotonic() > deadline:
            raise RuntimeError(service.error or 'Startup timed out')
        time.sleep(.1)
    results = {}
    for route in ('/learning', '/today', '/knowledge', '/api/knowledge', '/static/study.css', '/static/learning.css'):
        with urlopen(service.url + route, timeout=10) as response:
            results[route] = response.status
    (data / 'smoke-result.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


def show_window(service: LocalService, data: Path, settings: dict):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('时政自习室')
    root.geometry('580x390')
    root.configure(bg='#f3f0e7')
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='时政自习室', font=('Microsoft YaHei', 22)).pack(anchor='w')
    status = tk.StringVar(value='正在启动本地阅读服务…')
    ttk.Label(frame, textvariable=status, wraplength=520).pack(anchor='w', pady=14)
    events = queue.Queue()
    opened = False

    def job(action):
        def run():
            try:
                events.put(action())
            except Exception:
                logging.exception('Desktop action failed')
                events.put('操作未完成，请查看阅读页的同步状态或应用日志。')
        threading.Thread(target=run, daemon=True).start()

    def update_news():
        from app.sync_service import start_background_sync
        started = start_background_sync('本地应用手动更新', months=1, max_pages=2, max_items=40)
        return '已启动新闻更新，可在网页的同步状态查看进度。' if started else '已有新闻更新任务在运行。'

    def import_materials():
        from app.materials import sync_materials
        result = sync_materials()
        return f'公众号资料：新增 {result["added"]}，更新 {result["updated"]}，跳过 {result["skipped"]}。'

    def configure_source():
        path = filedialog.askopenfilename(title='选择 WeRSS 的文章数据库 db.db', filetypes=[('SQLite 数据库', '*.db'), ('全部文件', '*.*')])
        if path:
            settings['werss_database_path'] = path
            (data / 'settings.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
            status.set('公众号资料源已保存。请关闭应用后重新启动，以启用定时导入。')

    buttons = []
    for text, command in [('打开阅读页面', lambda: webbrowser.open(service.url + '/learning')),
                          ('更新近期新闻', lambda: job(update_news)),
                          ('导入公众号资料', lambda: job(import_materials))]:
        button = ttk.Button(frame, text=text, command=command, state='disabled')
        button.pack(fill='x', pady=3)
        buttons.append(button)
    ttk.Button(frame, text='设置公众号资料源', command=configure_source).pack(fill='x', pady=3)
    ttk.Button(frame, text='打开数据与日志目录', command=lambda: os.startfile(data)).pack(fill='x', pady=3)
    ttk.Label(frame, text='阅读无需 Docker；公众号自动采集仍需本机 WeRSS 和微信授权。\n关闭此窗口将停止本地服务。', wraplength=520).pack(anchor='w', pady=12)

    def poll():
        nonlocal opened
        if service.error:
            status.set('启动失败，请查看数据目录中的 application.log。')
        elif service.server and service.server.started and not opened:
            opened = True
            (data / 'instance.json').write_text(json.dumps({'url': service.url}), encoding='utf-8')
            status.set('已启动。资料保存在本机，应用升级不会覆盖数据库。')
            for button in buttons:
                button.configure(state='normal')
            webbrowser.open(service.url + '/learning')
        while not events.empty():
            status.set(events.get_nowait())
        root.after(250, poll)

    def close():
        if messagebox.askokcancel('退出时政自习室', '退出后，本机采集与阅读服务将停止。确定退出？'):
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, poll)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    data = (args.data_dir or Path(os.environ['LOCALAPPDATA']) / 'PoliticalNewsStudy').resolve()
    settings = prepare(data)
    lock = acquire_lock(data)
    if lock is None:
        if not args.smoke_test:
            from tkinter import messagebox
            messagebox.showinfo('时政自习室', '应用已经运行，请打开现有启动窗口。')
        return
    service = None
    try:
        backup_before_upgrade(data)
        # Stable browser origin preserves localStorage favorites across launches.
        service = LocalService(0 if args.smoke_test else 18764)
        service.thread.start()
        if args.smoke_test:
            smoke(service, data)
        else:
            show_window(service, data, settings)
    finally:
        if service is not None:
            service.stop()
        lock.close()


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    try:
        main()
    except Exception:
        logging.exception('Desktop startup failed')
        if '--smoke-test' not in sys.argv:
            from tkinter import messagebox
            messagebox.showerror('启动失败', '无法启动应用。请查看数据目录的 application.log。')
        raise
