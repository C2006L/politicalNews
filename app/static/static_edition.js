/* Published reading edition: local filtering only, no backend calls. */
(() => {
  'use strict';
  function filterRecords(rows, filters, kind) {
    const words = (filters.q || '').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return rows.filter(row => (kind === 'all' || row.kind === kind) &&
      (!filters.profile || filters.profile === 'all' || row.profiles.includes(filters.profile === 'essay' ? 'civil' : filters.profile)) &&
      (!filters.purpose || filters.purpose === 'all' || row.purpose === filters.purpose) &&
      (!filters.source || filters.source === 'all' || row.source === filters.source) &&
      (!filters.topic || filters.topic === 'all' || row.topic === filters.topic) &&
      words.every(word => `${row.title}\n${row.text}\n${row.source_label}\n${row.date}`.toLocaleLowerCase().includes(word)));
  }
  globalThis.StaticEdition = {filterRecords};
  if (typeof document === 'undefined') return;
  const root = document.querySelector('[data-static-catalog]');
  if (!root) return;
  const form = root.querySelector('form'), results = root.querySelector('[data-static-results]');
  const status = root.querySelector('[data-static-status]'), more = root.querySelector('[data-static-more]');
  let rows = [], visible = 20;
  const field = name => form.elements.namedItem(name);
  const fieldNames = ['profile', 'purpose', 'source', 'topic', 'q'].filter(name => field(name));
  const params = () => Object.fromEntries(new URLSearchParams(location.search));
  function node(tag, text, className) {
    const el = document.createElement(tag); if (text) el.textContent = text;
    if (className) el.className = className; return el;
  }
  function hydrate() {
    const filters = params();
    for (const name of fieldNames) {
      field(name).value = filters[name] || (name === 'q' ? '' : 'all');
      if (name !== 'q' && !field(name).value) field(name).value = 'all';
    }
  }
  function render() {
    root.querySelectorAll('[data-topic]').forEach(link => {
      const active = link.dataset.topic === (params().topic || 'all');
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'true'); else link.removeAttribute('aria-current');
    });
    const matching = filterRecords(rows, params(), root.dataset.kind);
    results.replaceChildren();
    matching.slice(0, visible).forEach((row, index) => {
      const card = node('article', '', 'story reading-card');
      card.dataset.id = row.id; card.dataset.title = row.title; card.dataset.url = row.url.replace(/\/$/, ''); card.dataset.date = row.date;
      const body = node('div'), meta = node('p', `${row.label} · ${row.source_label} · ${row.date || '发布日期待核对'}`, 'meta');
      const title = node('h2'), link = node('a', row.title); link.href = row.url; title.append(link);
      const excerpt = node('p', row.text.slice(0, 160) || '尚未取得文字正文，请对照原文。', 'excerpt');
      const scope = node('p', row.status, 'meta'), actions = node('div', '', 'actions');
      const read = node('a', '阅读全文 →', 'read-source'); read.href = row.url;
      const save = node('button', '收藏'), seen = node('button', '标为已读');
      save.type = seen.type = 'button'; save.dataset.action = 'save'; seen.dataset.action = 'read';
      actions.append(read, save, seen); body.append(meta, title, excerpt, scope, actions);
      card.append(node('span', String(index + 1).padStart(2, '0'), 'number'), body); results.append(card);
    });
    status.textContent = `${matching.length} 篇匹配 · 已展示 ${Math.min(visible, matching.length)} 篇`;
    if (!matching.length) results.append(node('p', '没有符合当前条件的资料，可以重置筛选再试。', 'empty'));
    more.hidden = visible >= matching.length;
    document.dispatchEvent(new Event('study:updated'));
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    const next = new URLSearchParams();
    for (const name of fieldNames) {
      const value = field(name).value.trim(); if (value && value !== 'all') next.set(name, value);
    }
    history.pushState(null, '', location.pathname + (next.size ? '?' + next : ''));
    visible = 20; render();
  });
  form.addEventListener('reset', () => {
    history.pushState(null, '', location.pathname); visible = 20; setTimeout(() => {hydrate(); render();}, 0);
  });
  for (const name of fieldNames.filter(name => name !== 'q')) field(name).addEventListener('change', () => form.requestSubmit());
  root.querySelectorAll('[data-topic]').forEach(link => link.addEventListener('click', event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const next = new URLSearchParams(location.search);
    if (link.dataset.topic === 'all') next.delete('topic'); else next.set('topic', link.dataset.topic);
    history.pushState(null, '', location.pathname + (next.size ? '?' + next : ''));
    visible = 20; hydrate(); render();
  }));
  more.addEventListener('click', () => {visible += 20; render();});
  window.addEventListener('popstate', () => {visible = 20; hydrate(); render();});
  fetch('/static/catalog.json', {cache: 'no-cache'}).then(response => {
    if (!response.ok) throw Error('目录未加载'); return response.json();
  }).then(data => {
    rows = data;
    const sources = new Map(rows.filter(row => root.dataset.kind === 'all' || row.kind === root.dataset.kind).map(row => [row.source, row.source_label]));
    for (const [value, label] of [...sources].sort((a,b) => a[1].localeCompare(b[1], 'zh-CN'))) {
      const option = node('option', label); option.value = value; field('source').append(option);
    }
    hydrate(); render();
  }).catch(() => {
    status.textContent = '目录暂时没有加载成功，请刷新重试；也可以直接打开完整文章目录。';
    const link = node('a', '打开完整文章目录 →'); link.href = '/directory/'; results.append(link);
  });
})();
