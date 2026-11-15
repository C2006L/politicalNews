(() => {
  const panel = document.querySelector('[data-onboarding]');
  if (!panel) return;
  const base = panel.dataset.base;
  const button = panel.querySelector('[data-setup-start]');
  const status = panel.querySelector('[data-setup-state]');
  const messages = {
    empty: '还没有可阅读资料。可以获取第一批新闻，或接入公众号资料。',
    running: '正在获取新闻，请稍候。你可以离开本页，任务仍会继续。',
    interrupted: '上次任务未正常结束，请检查同步状态或重新获取。',
    ready: '已有资料可以学习，也可以继续更新。',
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
      const read=panel.querySelector('[data-setup-read]');
      read.hidden = data.news_count === 0 && data.material_count === 0;
      read.href=base+(data.news_count?'/today':'/learning');read.textContent=data.news_count?'开始阅读新闻 →':'开始研读公众号 →';
      const readyMessage=data.state==='ready'&&!data.news_count?'已有公众号资料可以研读，暂未取得新闻；不用等待新闻获取完成。':messages[data.state];
      status.textContent = data.state === 'running' ? messages.running : actionError || readyMessage || '状态待确认，请查看同步状态。';
      if(data.state==='ready'&&data.news_state==='no_content')status.textContent+=' 上次新闻获取没有取得内容，可单独重试。';
      document.querySelector('[data-path-reading]').textContent=data.news_count||data.material_count?`可阅读 ${data.news_count} 条新闻、${data.material_count} 篇公众号资料。任选一处进入，按标题及原文依据核对。`:'资料尚未取得。先获取新闻或导入公众号正文，再开始阅读。';
      document.querySelector('[data-path-knowledge]').textContent=data.knowledge_count?`当前有 ${data.knowledge_count} 个考点目录。查看关联来源及证据，候选关系仍需核对。`:'暂未建立考点目录，可以先直接阅读材料；没有匹配结果不代表这份材料没有价值。';
      document.querySelector('[data-path-recall]').textContent=data.recall_entry_count?`公众号当前识别到 ${data.recall_entry_count} 条问答或提法。原文依据有效的条目可加入复习；这不是已学数量。`:'暂无已识别的公众号问答或提法。已有复习记录仍可从下方入口继续。';
      button.disabled = data.state === 'running';
      button.textContent = data.news_state === 'no_content' ? '重新获取新闻' : data.news_count ? '更新近期新闻' : '获取第一批新闻';
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
