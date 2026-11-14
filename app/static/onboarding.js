(() => {
  const panel = document.querySelector('[data-onboarding]');
  if (!panel) return;
  const base = panel.dataset.base;
  const button = panel.querySelector('[data-setup-start]');
  const status = panel.querySelector('[data-setup-state]');
  const messages = {
    empty: '还没有新闻。点击下方按钮获取第一批内容。',
    running: '正在获取新闻，请稍候。你可以离开本页，任务仍会继续。',
    interrupted: '上次任务未正常结束，请检查同步状态或重新获取。',
    ready: '已有新闻可以阅读，也可以继续更新。',
    no_content: '本次未取得可阅读新闻。请检查网络和同步状态，再重试。'
  };
  let busy = false;
  let actionError = '';
  async function refresh() {
    if (busy) return;
    try {
      const response = await fetch(base + '/api/getting-started');
      if (!response.ok) throw new Error();
      const data = await response.json();
      panel.querySelector('[data-setup-count]').textContent = `现有 ${data.news_count} 条新闻，${data.material_count} 篇公众号资料。`;
      panel.querySelector('[data-setup-auth]').hidden = !data.token_required;
      panel.querySelector('[data-setup-read]').hidden = data.news_count === 0;
      status.textContent = data.state === 'running' ? messages.running : actionError || messages[data.state] || '状态待确认，请查看同步状态。';
      button.disabled = data.state === 'running';
      button.textContent = data.state === 'no_content' ? '重新获取新闻' : data.news_count ? '更新近期新闻' : '获取第一批新闻';
    } catch (_) {
      status.textContent = '无法读取状态，请检查本地服务是否运行。稍后将重试。';
      button.disabled = true;
    }
  }
  button.addEventListener('click', async () => {
    actionError = '';
    busy = true;
    button.disabled = true;
    try {
      const response = await fetch(base + '/api/getting-started/news', {method: 'POST', headers: {
        'X-Requested-With': 'study-ui', 'X-Sync-Token': panel.querySelector('[data-setup-token]').value
      }});
      if (!response.ok) {
        const messages = {403: '当前操作未获授权，请检查管理令牌。', 409: '已有任务运行，请稍候。'};
        actionError = messages[response.status] || '未能启动获取任务，请稍后重试。';
        status.textContent = actionError;
        return;
      }
      status.textContent = '获取任务已启动，正在检查进度…';
    } catch (_) {
      actionError = '连接失败，请检查本地服务后重试。';
      status.textContent = actionError;
    } finally {
      busy = false;
      button.disabled = false;
    }
  });
  refresh();
  setInterval(refresh, 2500);
})();
