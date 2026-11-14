(() => {
  const root = document.querySelector('[data-review]');
  if (!root) return;
  const base = root.dataset.base;
  const find = key => root.querySelector(`[data-${key}]`);
  const states = {candidate: '待确认', confirmed: '已确认', rejected: '已排除'};
  let page = 1, eventPage = 1, requestId = 0;
  let setupRequired = false;
  const node = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
  async function api(path, options = {}) {
    const credential = 'base64:' + btoa(String.fromCharCode(...new TextEncoder().encode(find('review-token').value)));
    const response = await fetch(base + path, {...options, headers: {'Content-Type': 'application/json', 'X-Sync-Token': credential}});
    if (!response.ok) throw new Error(response.status === 403 ? '无权审核，请检查管理口令。' : response.status === 409 ? '记录或管理设置已变更，请刷新页面后重试。' : '请求失败，请稍后重试。');
    return response.json();
  }
  function pager(prefix, current, total) {
    find(prefix + '-prev').disabled = current <= 1;
    find(prefix + '-next').disabled = current * 20 >= total;
    find(prefix + '-page').textContent = `${current} / ${Math.max(1, Math.ceil(total / 20))}`;
  }
  function card(row) {
    const el = node('article', ''); el.className = 'learning-entry';
    el.append(node('p', `${row.kind === 'news' ? '新闻' : '公众号资料'} · ${row.source} · ${states[row.status]}`), node('h2', row.point_title), node('h3', row.title));
    const link = node('a', '打开对应考点 →'); link.href = base + '/knowledge/' + encodeURIComponent(row.point_id); el.append(link);
    if (row.revision) el.append(node('p', `固定资料版本 ${row.revision}`));
    if (row.reason) el.append(node('p', row.reason));
    if (row.question) el.append(node('p', `问题：${row.question}`), node('p', `原文答案：${row.answer}`));
    if (row.needs_review) el.append(node('p', '原文答案仍有待核对提示，确认关联不会消除该提示。'));
    row.evidence.forEach(line => el.append(node('blockquote', `${line.id} · ${line.text}`)));
    const context = node('details', ''); const body = node('p', row.context || '暂无可用正文'); body.style.whiteSpace = 'pre-wrap';
    context.append(node('summary', '展开匹配时的全文，核对上下文'), body); el.append(context);
    const note = document.createElement('textarea'); note.maxLength = 500; note.rows = 2;
    note.placeholder = '审核备注（可选，最多 500 字）'; note.setAttribute('aria-label', '审核备注'); el.append(note);
    const actions = node('p', '');
    Object.entries(states).forEach(([status, label]) => {
      if (status === row.status) return;
      const button = node('button', status === 'candidate' ? '恢复待确认' : label === '已排除' ? '排除关联' : '确认关联');
      button.type = 'button';
      button.addEventListener('click', async () => {
        const buttons = actions.querySelectorAll('button'); buttons.forEach(b => b.disabled = true);
        try {
          const path = row.kind === 'news' ? '/api/knowledge-news-links/' : '/api/knowledge-links/';
          await api(path + row.id + '/review', {method: 'PUT', body: JSON.stringify({status, expected_status: row.status, note: note.value})});
          find('review-message').textContent = '已保存审核决定，并记录操作。';
          await load();
        } catch (error) { find('review-message').textContent = error.message; buttons.forEach(b => b.disabled = false); }
      });
      actions.append(button, document.createTextNode(' '));
    });
    el.append(actions); return el;
  }
  async function load() {
    if (!find('review-token').value) return;
    const id = ++requestId;
    try {
      const [queue, events] = await Promise.all([
        api(`/api/review/queue?kind=${find('review-kind').value}&status=${find('review-status').value}&page=${page}`),
        api(`/api/review/events?page=${eventPage}`)
      ]);
      if (id !== requestId) return;
      if (!queue.items.length && page > 1 && queue.total <= (page - 1) * 20) { page = Math.max(1, Math.ceil(queue.total / 20)); return load(); }
      find('review-count').textContent = `当前版本共 ${queue.total} 条关联。`;
      find('review-list').replaceChildren(...queue.items.map(card));
      if (!queue.items.length) find('review-list').append(node('p', '当前条件下没有关联。可以切换状态查看已确认或已排除的记录。'));
      pager('review', page, queue.total); pager('events', eventPage, events.total);
      find('review-events').replaceChildren(...events.items.map(event => node('p', `${new Date(event.created_at).toLocaleString('zh-CN')} · ${event.kind === 'news' ? '新闻' : '资料'} #${event.link_id} · ${event.point_id} · ${states[event.previous]} → ${states[event.status]}${event.note ? ' · ' + event.note : ''}`)));
    } catch (error) {
      if (id !== requestId) return;
      find('review-message').textContent = error.message;
      find('review-list').replaceChildren(); find('review-events').replaceChildren();
    }
  }
  find('review-login').addEventListener('submit', async event => {
    event.preventDefault(); page = 1; find('review-message').textContent = '';
    if (setupRequired) {
      const password = find('review-token').value;
      if (password.length < 10) {find('review-message').textContent = '请设置至少 10 个字符的管理口令。'; return;}
      find('review-submit').disabled = true;
      try {
        const response = await fetch(base + '/api/review/access', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'study-ui'}, body: JSON.stringify({password})});
        if (!response.ok) throw new Error('未能初始化管理设置，请刷新页面检查是否已经配置。');
        setupRequired = false;
        find('review-submit').textContent = '加载审核队列';
        find('review-access-note').textContent = '管理口令已设置。口令仅保留在当前输入框，离开后需重新填写。';
      } catch (error) {find('review-message').textContent = error.message; return;}
      finally {find('review-submit').disabled = false;}
    }
    load();
  });
  ['review-kind', 'review-status'].forEach(key => find(key).addEventListener('change', () => {page = 1; load();}));
  find('review-prev').addEventListener('click', () => {page--; load();});
  find('review-next').addEventListener('click', () => {page++; load();});
  find('events-prev').addEventListener('click', () => {eventPage--; load();});
  find('events-next').addEventListener('click', () => {eventPage++; load();});
  fetch(base + '/api/review/access').then(response => {if (!response.ok) throw new Error();return response.json();}).then(access => {
    setupRequired = !access.configured;
    find('review-submit').disabled = setupRequired && !access.can_setup;
    find('review-submit').textContent = setupRequired ? '设置管理口令并进入' : '加载审核队列';
    if (setupRequired) find('review-access-note').textContent = access.can_setup ? '首次使用：请设置至少 10 个字符的管理口令。保存的是口令校验值，不保存明文；以后输入此口令审核。' : '管理口令尚未设置，请在运行本程序的电脑上完成首次设置。';
  }).catch(() => {find('review-message').textContent = '无法读取管理设置，请刷新重试。';});
})();
