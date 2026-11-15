/* Pure validation/merge helpers, shared by UI and offline tests. */
(() => {
  'use strict';
  const keyOK = key => typeof key === 'string' && /^(?:learning:)?[1-9]\d*$/.test(key);
  function entryOK(key, entry) {
    if (!entry || typeof entry !== 'object' || typeof entry.title !== 'string' || entry.title.length > 2000 ||
        typeof entry.url !== 'string' || entry.url.length > 2048 || !entry.url.startsWith('/') ||
        /[\\\u0000-\u0020]/.test(entry.url) || typeof entry.date !== 'string' || entry.date.length > 100) return false;
    try {
      const url = new URL(entry.url, 'https://local.invalid');
      const id = key.replace('learning:', ''), type = key.startsWith('learning:') ? 'learning' : 'news';
      // News keys may be the group's smallest ID while its displayed article is
      // the preferred government source. Learning IDs must match exactly.
      const pathOK = type === 'learning' ? url.pathname.endsWith(`/learning/${id}`) : /\/news\/[1-9]\d*$/.test(url.pathname);
      return url.origin === 'https://local.invalid' && pathOK && !url.hash && !url.search;
    } catch (_) { return false; }
  }
  function validate(data) {
    if (!data || data.format !== 'political-news-collection' || data.version !== 1 ||
        typeof data.exported_at !== 'string' || !Number.isFinite(Date.parse(data.exported_at)) ||
        !Array.isArray(data.read) || data.read.length > 100000 || !data.read.every(keyOK) ||
        !data.saved || typeof data.saved !== 'object' || Array.isArray(data.saved) || Object.keys(data.saved).length > 10000)
      throw Error('不是支持的收藏备份，或记录超过上限。');
    const saved = {};
    for (const [key, entry] of Object.entries(data.saved)) {
      if (!keyOK(key) || !entryOK(key, entry)) throw Error('收藏的编号或链接不匹配，未恢复。');
      saved[key] = {title:entry.title, url:entry.url, date:entry.date};
    }
    return {format:data.format, version:1, exported_at:data.exported_at, read:[...new Set(data.read)], saved};
  }
  function merge(current, backup) {
    const data = validate(backup), saved = {...current.saved};
    let added = 0, conflicts = 0;
    for (const [key, entry] of Object.entries(data.saved)) {
      if (!Object.hasOwn(saved, key)) {saved[key] = entry; added++;}
      else if (saved[key].url !== entry.url || saved[key].title !== entry.title) conflicts++;
    }
    const read = [...new Set([...current.read, ...data.read])];
    if (read.length > 100000 || Object.keys(saved).length > 10000) throw Error('合并后记录超过上限，原记录保留。');
    return {state:{read, saved}, added, conflicts, read_added:read.length - current.read.length};
  }
  const core = {validate, merge};
  if (typeof module !== 'undefined' && module.exports) module.exports = core;
  else globalThis.CollectionBackup = core;
})();
