(() => {
  'use strict';
  const key = 'political-news-study-v1';
  let state = {read: [], saved: {}};
  const toast = document.querySelector('.toast');
  let timer;
  function notify(message) {
    toast.textContent = message; toast.hidden = false;
    clearTimeout(timer); timer = setTimeout(() => { toast.hidden = true; }, 3200);
  }
  try {
    const stored = JSON.parse(localStorage.getItem(key));
    if (stored && Array.isArray(stored.read) && stored.saved && typeof stored.saved === 'object' && !Array.isArray(stored.saved)) {
      state = {read: stored.read.filter(id => typeof id === 'string'), saved: stored.saved};
    }
  } catch (_) { /* A fresh state also works when browser storage is unavailable. */ }
  const cards = [...document.querySelectorAll('.reading-card')];
  const library = document.getElementById('library');
  const toggle = document.querySelector('.library-toggle');
  const list = document.querySelector('[data-saved-list]');
  function persist() {
    try { localStorage.setItem(key, JSON.stringify(state)); }
    catch (_) { notify('浏览器未允许保存，当前操作仅在本页有效。'); }
  }
  function update() {
    let count = 0;
    for (const card of cards) {
      const read = state.read.includes(card.dataset.id), saved = Boolean(state.saved[card.dataset.id]);
      count += Number(read);
      card.querySelector('.read-label').hidden = !read;
      const readButton = card.querySelector('[data-action=read]'), saveButton = card.querySelector('[data-action=save]');
      readButton.setAttribute('aria-pressed', String(read)); readButton.textContent = read ? '取消已读' : '标为已读';
      saveButton.setAttribute('aria-pressed', String(saved)); saveButton.textContent = saved ? '已收藏' : '收藏';
    }
    document.querySelector('[data-read-count]').textContent = String(count);
    document.querySelector('[data-visible-count]').textContent = String(cards.length);
    const percent = cards.length ? Math.round(count / cards.length * 100) : 0;
    document.querySelector('[data-progress]').style.width = percent + '%';
    document.querySelector('[role=progressbar]').setAttribute('aria-valuenow', String(percent));
    document.querySelector('[data-save-count]').textContent = String(Object.keys(state.saved).length);
    list.replaceChildren();
    for (const [id, entry] of Object.entries(state.saved)) {
      if (!entry || typeof entry.url !== 'string' || !entry.url.startsWith('/') || entry.url.startsWith('//')) continue;
      const li = document.createElement('li'), a = document.createElement('a'), date = document.createElement('small'), button = document.createElement('button');
      a.href = entry.url; a.textContent = entry.title; date.textContent = entry.date;
      button.type = 'button'; button.textContent = '移除'; button.setAttribute('aria-label', '移除收藏：' + entry.title);
      button.addEventListener('click', () => { delete state.saved[id]; persist(); update(); });
      li.append(a, date, button); list.append(li);
    }
    if (!list.children.length) { const li = document.createElement('li'); li.textContent = '还没有收藏。在新闻卡片上点击“收藏”，留给下次回看。'; list.append(li); }
  }
  for (const card of cards) {
    card.addEventListener('click', event => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const id = card.dataset.id;
      if (button.dataset.action === 'read') {
        state.read = state.read.includes(id) ? state.read.filter(value => value !== id) : [...state.read, id];
      } else if (state.saved[id]) { delete state.saved[id]; }
      else { state.saved[id] = {title: card.dataset.title, url: card.dataset.url, date: card.dataset.date}; }
      persist(); update();
    });
  }
  toggle.addEventListener('click', () => {
    library.hidden = !library.hidden; toggle.setAttribute('aria-expanded', String(!library.hidden));
    if (!library.hidden) { library.scrollIntoView({block: 'start'}); document.getElementById('library-title').setAttribute('tabindex', '-1'); document.getElementById('library-title').focus({preventScroll: true}); }
  });
  document.querySelector('[data-close-library]').addEventListener('click', () => { library.hidden = true; toggle.setAttribute('aria-expanded', 'false'); toggle.focus(); });
  update();
})();
