/* Reuse an explicitly logged-in local Chrome profile. Credentials stay in memory. */
const { spawn } = require('node:child_process');
const { existsSync } = require('node:fs');
const path = require('node:path');

async function main() {
  const args = process.argv.slice(2);
  const index = args.indexOf('--cdp-port');
  const port = index < 0 ? 9262 : Number(args.splice(index, 2)[1]);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw Error('Invalid local CDP port');
  const base = `http://127.0.0.1:${port}`;
  async function request(suffix, options = {}) {
    const response = await fetch(base + suffix, { ...options, signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw Error('Local authenticated browser is unavailable');
    return response;
  }
  const version = await (await request('/json/version')).json();
  const target = await (await request('/json/new?about:blank', { method: 'PUT' })).json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  let timer;
  let cookies;
  try {
    cookies = await new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(Error('Local browser cookie request timed out')), 15000);
      socket.onerror = () => reject(Error('Local browser connection failed'));
      socket.onopen = () => socket.send(JSON.stringify({ id: 1, method: 'Network.getCookies', params: { urls: ['https://mp.weixin.qq.com/'] } }));
      socket.onmessage = event => {
        const message = JSON.parse(event.data);
        if (message.id !== 1) return;
        if (message.error) reject(Error('Local browser cookie request failed'));
        else resolve(message.result.cookies.filter(cookie => ['mp.weixin.qq.com', '.mp.weixin.qq.com'].includes(cookie.domain))
          .map(cookie => cookie.name + '=' + cookie.value).join('; '));
      };
    });
  } finally {
    clearTimeout(timer);
    socket.close();
    await request('/json/close/' + target.id);
  }
  const workspace = path.resolve(__dirname, '../..');
  const localPython = path.join(workspace, '.conda', 'python.exe');
  const python = existsSync(localPython) ? localPython : 'python';
  const child = spawn(python, [path.join(__dirname, 'backfill_public_albums.py'), '--session-stdin', ...args], {
    cwd: path.resolve(__dirname, '..'), stdio: ['pipe', 'inherit', 'inherit'], env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
  });
  child.stdin.on('error', () => {});
  child.on('error', () => { console.error('Python could not start'); process.exitCode = 1; });
  child.stdin.end(JSON.stringify({ cookies, agent: version['User-Agent'] || 'Mozilla/5.0' }));
  child.on('exit', code => { process.exitCode = code || 0; });
}

main().catch(() => { console.error('历史补采未启动：请检查本机专用浏览器、登录状态及参数。未输出或保存登录凭据。'); process.exitCode = 1; });
