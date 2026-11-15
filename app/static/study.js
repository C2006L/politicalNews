(() => {
  'use strict';
  const key = 'political-news-study-v1';
  let state = {read: [], saved: {}};
  const library = document.getElementById('library');
  const toggle = document.querySelector('.library-toggle');
  const list = document.querySelector('[data-saved-list]');
  if (!library || !toggle || !list) return;
  function validEntry(entry) {
    if (!entry || typeof entry.title !== 'string' || typeof entry.url !== 'string' || !entry.url.startsWith('/')) return false;
    try { return new URL(entry.url, location.origin).origin === location.origin; } catch (_) { return false; }
  }
  function load() {
    try {
      const stored = JSON.parse(localStorage.getItem(key));
      if (stored && Array.isArray(stored.read) && stored.saved && typeof stored.saved === 'object' && !Array.isArray(stored.saved)) {
        state = {read: stored.read.filter(id => typeof id === 'string'), saved: Object.fromEntries(Object.entries(stored.saved).filter(([, entry]) => validEntry(entry)))};
      }
    } catch (_) { /* Storage may be unavailable; current-page actions still work. */ }
  }
  function persist() {
    try { localStorage.setItem(key, JSON.stringify(state)); }
    catch (_) {
      const toast = document.querySelector('.toast');
      if (toast) { toast.textContent = '浏览器未允许保存，当前操作仅在本页有效。'; toast.hidden = false; setTimeout(() => { toast.hidden = true; }, 3200); }
    }
  }
  function update() {
    document.querySelectorAll('.reading-card').forEach(card => {
      const read = state.read.includes(card.dataset.id);
      card.classList.toggle('is-read', read);
      const label = card.querySelector('.read-label');
      if (label) label.hidden = !read;
      const readButton = card.querySelector('[data-action=read]');
      if (readButton) { readButton.setAttribute('aria-pressed', String(read)); readButton.textContent = read ? '取消已读' : '标为已读'; }
      const directory = document.querySelector('.directory a[href="#' + card.id + '"]');
      if (directory) directory.parentElement.classList.toggle('is-read', read);
    });
    document.querySelectorAll('[data-action=save], [data-save-id]').forEach(button => {
      const card = button.closest('.reading-card');
      const id = button.dataset.saveId || card?.dataset.id;
      const saved = Boolean(state.saved[id]);
      button.setAttribute('aria-pressed', String(saved)); button.textContent = saved ? '已收藏' : '收藏';
    });
    document.querySelectorAll('[data-save-count]').forEach(el => { el.textContent = String(Object.keys(state.saved).length); });
    list.replaceChildren();
    for (const [id, entry] of Object.entries(state.saved)) {
      const li = document.createElement('li'), a = document.createElement('a'), date = document.createElement('small'), button = document.createElement('button');
      a.href = entry.url; a.textContent = entry.title; date.textContent = typeof entry.date === 'string' ? entry.date : '';
      button.type = 'button'; button.textContent = '移除'; button.setAttribute('aria-label', '移除收藏：' + entry.title);
      button.addEventListener('click', () => { delete state.saved[id]; persist(); update(); const next = list.querySelector('button'); (next || library.querySelector('[data-close-library]')).focus(); });
      li.append(a, date, button); list.append(li);
    }
    if (!list.children.length) { const li = document.createElement('li'); li.className = 'library-empty'; li.textContent = '还没有收藏。阅读时点击“收藏”，留给下次回看。'; list.append(li); }
  }
  document.addEventListener('click', event => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest('button[data-action], button[data-save-id]');
    if (!button) return;
    const card = button.closest('.reading-card');
    const id = button.dataset.saveId || card?.dataset.id;
    if (!id) return;
    if (button.dataset.action === 'read') {
      state.read = state.read.includes(id) ? state.read.filter(value => value !== id) : [...state.read, id];
    } else if (state.saved[id]) { delete state.saved[id]; }
    else {
      const source = button.dataset.saveId ? button : card;
      const entry = {title: source.dataset.title, url: source.dataset.url, date: source.dataset.date || ''};
      if (!validEntry(entry)) return;
      state.saved[id] = entry;
    }
    persist(); update();
  });
  toggle.addEventListener('click', () => { library.showModal(); toggle.setAttribute('aria-expanded', 'true'); });
  library.querySelector('[data-close-library]').addEventListener('click', () => library.close());
  library.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); library.close(); }
  });
  library.addEventListener('close', () => { toggle.setAttribute('aria-expanded', 'false'); toggle.focus(); });
  library.addEventListener('click', event => { if (event.target === library && event.clientX < library.getBoundingClientRect().left) library.close(); });
  window.addEventListener('storage', event => { if (event.key === key || event.key === null) { state = {read: [], saved: {}}; load(); update(); } });
  document.addEventListener('study:updated', update);
  const backupFile = library.querySelector('[data-collection-file]');
  const backupRestore = library.querySelector('[data-collection-restore]');
  const backupPreview = library.querySelector('[data-collection-preview]');
  const backupMessage = library.querySelector('[data-collection-message]');
  let prepared = null, fileVersion = 0;
  library.querySelector('[data-collection-export]').addEventListener('click', () => {
    try {
      const data = CollectionBackup.validate({format:'political-news-collection', version:1, exported_at:new Date().toISOString(), ...state});
      const blob = new Blob([JSON.stringify(data,null,2)], {type:'application/json'});
      if (blob.size > 5*1024*1024) throw Error('备份超过 5 MB，原记录保留。');
      const url=URL.createObjectURL(blob), a=document.createElement('a');
      a.href=url; a.download='study-room-collection-'+new Date().toISOString().slice(0,10)+'.json';
      library.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
      backupMessage.textContent='已请求下载备份，请确认浏览器已保存文件。';
    } catch (error) { backupMessage.textContent=error.message; }
  });
  backupFile.addEventListener('change', async () => {
    const version=++fileVersion;prepared=null;backupRestore.disabled=true;backupPreview.textContent='';
    try {
      const file=backupFile.files[0];if(!file)return;
      if(file.size>5*1024*1024)throw Error('文件超过 5 MB，未读取。');
      const data=CollectionBackup.validate(JSON.parse(await file.text()));
      if(version!==fileVersion)return;
      prepared=data;backupRestore.disabled=false;
      backupPreview.textContent=`已校验 ${Object.keys(data.saved).length} 条收藏、${data.read.length} 条已读；确认后才写入，已有收藏优先保留。`;
    }catch(error){if(version===fileVersion)backupPreview.textContent=error.message;}
  });
  backupRestore.addEventListener('click', () => {
    if(!prepared)return;
    try {
      // Reload just before merging; don't replace changes made in another tab.
      state={read:[],saved:{}};load();
      const result=CollectionBackup.merge(state,prepared);
      // One atomic localStorage write. On quota/security failure keep old state.
      localStorage.setItem(key,JSON.stringify(result.state));state=result.state;
      prepared=null;backupFile.value='';backupRestore.disabled=true;backupPreview.textContent='';
      backupMessage.textContent=`已补入 ${result.added} 条收藏、${result.read_added} 条已读。${result.conflicts} 条同编号内容不同，已保留本机收藏；请核对资料库。`;
      update();
    }catch(error){backupMessage.textContent='恢复未保存，原记录保留。'+error.message;}
  });
  load(); update();
})();
