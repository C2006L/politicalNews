document.querySelectorAll('[data-source-config]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const message = form.querySelector('[data-source-message]');
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      const fields = new FormData(form);
      const payload = Object.fromEntries(['name', 'mp_id', 'group', 'region'].map(key => [key, String(fields.get(key) || '').trim()]));
      payload.mp_id ||= null;
      payload.enabled = fields.has('enabled');
      payload.identity_checked = fields.has('identity_checked');
      const response = await fetch(`${form.dataset.base}/api/material-sources/${encodeURIComponent(form.dataset.key)}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'study-ui',
          'X-Sync-Token': document.querySelector('[data-source-token]').value}, body: JSON.stringify(payload)
      });
      const result = await response.json();
      message.textContent = response.ok ? result.note + ' 刷新页面查看最新状态。' : typeof result.detail === 'string' ? result.detail : '配置不合法，请检查账号ID与必填项。';
    } catch { message.textContent = '未能保存，请检查本机服务后重试。'; }
    finally { button.disabled = false; }
  });
});
