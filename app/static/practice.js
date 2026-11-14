(() => {
  'use strict';
  const MINUTE = 60000, DAY = 86400000;
  function schedule(stage, result, now) {
    if (!['forgot', 'partial', 'remembered'].includes(result)) throw new Error('未知回想结果');
    if (!Number.isInteger(stage) || stage < 0 || stage > 5 || !Number.isFinite(now)) throw new Error('复习记录无效');
    const next = result === 'remembered' ? Math.min(5, stage + 1) : 0;
    const delay = result === 'forgot' ? 10 * MINUTE : result === 'partial' ? DAY : [1, 3, 7, 14, 30][next - 1] * DAY;
    return {stage: next, due_at: new Date(now + delay).toISOString()};
  }
  function validCard(card) {
    return card && /^material:\d+:r\d+:(questions|points):\d+$/.test(card.key) &&
      typeof card.question === 'string' && typeof card.answer === 'string' && typeof card.title === 'string' &&
      typeof card.url === 'string' && /^\/learning\/\d+\?revision=\d+#e-L\d+$/.test(card.url) &&
      Array.isArray(card.evidence) && card.evidence.length > 0 && card.evidence.every(line => typeof line.text === 'string');
  }
  const keyPattern = /^material:(\d+):r(\d+):(questions|points):(\d+)$/;
  const dateOK = v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$/.test(v) &&
    Number.isFinite(Date.parse(v)) && new Date(v).toISOString() === (v.includes('.') ? v : v.replace('Z','.000Z'));
  const textOK = (v, max = 100000) => typeof v === 'string' && v.length <= max;
  function backupCard(card) {
    const match = card && typeof card.key === 'string' && card.key.match(keyPattern);
    if (!match || !validCard(card) || ![card.material_id, card.revision, card.index].every(Number.isSafeInteger) ||
        card.material_id < 1 || card.revision < 1 || card.index < 0 ||
        card.key !== `material:${card.material_id}:r${card.revision}:${card.kind}:${card.index}` ||
        !textOK(card.title,1000) || !textOK(card.account,1000) || !textOK(card.question) || !textOK(card.answer) ||
        card.evidence.length > 1000 || !card.evidence.every(e => e && /^L\d+$/.test(e.id) && textOK(e.text)) ||
        new Set(card.evidence.map(e=>e.id)).size !== card.evidence.length ||
        !card.evidence.some(e=>card.url===`/learning/${card.material_id}?revision=${card.revision}#e-${e.id}`) ||
        typeof card.needs_review !== 'boolean' || typeof card.starred_by_author !== 'boolean') throw Error('备份卡片格式或固定版本依据无效。');
    return Object.fromEntries(['key','material_id','revision','kind','index','title','account','question','answer','needs_review','starred_by_author','url'].map(k=>[k,card[k]]).concat([
      ['evidence',card.evidence.map(e=>({id:e.id,text:e.text}))]
    ]));
  }
  function validateBackup(data) {
    if (!data || data.format!=='political-news-practice' || data.version!==1 || !dateOK(data.exported_at) ||
        !Array.isArray(data.cards) || !Array.isArray(data.events) || data.cards.length>5000 || data.events.length>50000) throw Error('不是支持的复习备份，或备份数量超过上限。');
    const keys = new Set();
    const cards=data.cards.map(row=>{
      if (!row || row.key!==row.card?.key || keys.has(row.key) || !Number.isInteger(row.stage) || row.stage<0 || row.stage>5 ||
          !Number.isSafeInteger(row.review_count) || row.review_count<0 || !dateOK(row.due_at) ||
          !(row.last_at===null || dateOK(row.last_at)) || ![null,'forgot','partial','remembered'].includes(row.last_result) ||
          ((row.last_at===null)!==(row.last_result===null))) throw Error('备份进度格式无效或有重复卡片。');
      keys.add(row.key);
      return {key:row.key,card:backupCard(row.card),stage:row.stage,due_at:row.due_at,review_count:row.review_count,last_result:row.last_result,last_at:row.last_at};
    });
    const identities=new Set();
    const events=data.events.map(event=>{
      if (!event || !textOK(event.key,200) || !keyPattern.test(event.key) || !['grade','undo','remove'].includes(event.type) || !dateOK(event.at) || !textOK(event.question) ||
          (event.type==='grade' && (!['forgot','partial','remembered'].includes(event.result) || !dateOK(event.due_at) || !textOK(event.event_id,100) || !event.event_id)) ||
          (event.type==='undo' && (!textOK(event.undo_of,100) || !event.undo_of))) throw Error('备份回想历史格式无效。');
      const result={key:event.key,type:event.type,question:event.question,at:event.at};
      if(event.type==='grade')Object.assign(result,{event_id:event.event_id,result:event.result,due_at:event.due_at});
      if(event.type==='undo')Object.assign(result,{undo_of:event.undo_of});
      const identity=eventIdentity(result);if(identities.has(identity))throw Error('备份包含重复回想历史。');identities.add(identity);
      return result;
    });
    return {format:data.format,version:1,exported_at:data.exported_at,cards,events};
  }
  function eventIdentity(event) {
    return JSON.stringify([event.type,event.key,event.type==='grade'?event.event_id:event.at,event.undo_of||'']);
  }
  const core = {schedule, validCard, validateBackup, eventIdentity};
  if (typeof module !== 'undefined' && module.exports) module.exports = core;
  if (typeof document === 'undefined') return;

  let connection;
  function openDB() {
    if (!connection) connection = new Promise((resolve, reject) => {
      const request = indexedDB.open('political-news-practice-v1', 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('cards', {keyPath: 'key'});
        request.result.createObjectStore('events', {keyPath: 'id', autoIncrement: true});
      };
      request.onsuccess = () => { request.result.onversionchange = () => request.result.close(); resolve(request.result); };
      request.onerror = () => reject(new Error('浏览器未能打开复习记录，请检查是否允许本地存储。'));
      request.onblocked = () => reject(new Error('复习记录升级受阻，请关闭其他本网站页面后重试。'));
    });
    return connection;
  }
  async function all(store) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readonly');
      const request = tx.objectStore(store).getAll();
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = () => reject(new Error('未能读取复习记录。'));
    });
  }
  async function change(key, operation) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(['cards', 'events'], 'readwrite');
      const cards = tx.objectStore('cards'), events = tx.objectStore('events');
      let value, failure;
      const request = cards.get(key);
      request.onsuccess = () => {
        try { value = operation(request.result, cards, events); }
        catch (error) { failure = error; tx.abort(); }
      };
      tx.oncomplete = () => resolve(value);
      tx.onabort = () => reject(failure || new Error('记录未保存，可能存储空间不足；请重试。'));
    });
  }
  function check(row, token) {
    if (!row || row.token !== token) throw new Error('这张卡已在其他页面更新，请刷新后再操作。');
  }
  const store = {
    rows: () => all('cards'), events: () => all('events'),
    add: (card, now = Date.now()) => {
      if (!validCard(card)) return Promise.reject(new Error('资料格式或依据不足，未加入复习。'));
      return change(card.key, (current, cards) => {
        if (current) return current;
        const row = {key: card.key, card, stage: 0, due_at: new Date(now).toISOString(), review_count: 0,
          last_result: null, last_at: null, token: crypto.randomUUID(), undo: null};
        cards.add(row); return row;
      });
    },
    grade: (key, result, token, now = Date.now()) => change(key, (row, cards, events) => {
      check(row, token);
      const next = schedule(row.stage, result, now);
      const previous = {stage: row.stage, due_at: row.due_at, review_count: row.review_count, last_result: row.last_result, last_at: row.last_at};
      const eventId = crypto.randomUUID();
      const updated = {...row, ...next, review_count: row.review_count + 1, last_result: result,
        last_at: new Date(now).toISOString(), token: crypto.randomUUID(), undo: {previous, event_id: eventId}};
      cards.put(updated);
      events.add({key, type: 'grade', event_id: eventId, result, question: row.card.question, at: updated.last_at, due_at: next.due_at});
      return updated;
    }),
    undo: (key, token) => change(key, (row, cards, events) => {
      check(row, token);
      if (!row.undo) throw new Error('没有可撤销的回想记录。');
      const updated = {...row, ...row.undo.previous, token: crypto.randomUUID(), undo: null};
      cards.put(updated);
      events.add({key, type: 'undo', undo_of: row.undo.event_id, question: row.card.question, at: new Date().toISOString()});
      return updated;
    }),
    remove: (key, token) => change(key, (row, cards, events) => {
      check(row, token); cards.delete(key);
      events.add({key, type: 'remove', question: row.card.question, at: new Date().toISOString()});
    })
  };
  async function snapshot() {
    const db=await openDB();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction(['cards','events'],'readonly');
      const cards=tx.objectStore('cards').getAll(),events=tx.objectStore('events').getAll();
      tx.oncomplete=()=>{try{resolve(validateBackup({format:'political-news-practice',version:1,exported_at:new Date().toISOString(),cards:cards.result,events:events.result}));}catch(e){reject(e)}};
      tx.onabort=()=>reject(Error('未能完整读取备份，原记录保留。'));
    });
  }
  async function restore(input) {
    const data=validateBackup(input),db=await openDB();
    return new Promise((resolve,reject)=>{
      const tx=db.transaction(['cards','events'],'readwrite'),cards=tx.objectStore('cards'),events=tx.objectStore('events');
      const currentCards=cards.getAll(),currentEvents=events.getAll();let read=0,failure;
      const report={added:0,kept:0,conflicts:0,events:0};
      const merge=()=>{if(++read!==2)return;try{
        const existing=new Map(currentCards.result.map(row=>[row.key,row]));
        for(const row of data.cards){
          if(existing.has(row.key)){
            report.kept++;
            try{if(JSON.stringify(backupCard(existing.get(row.key).card))!==JSON.stringify(row.card))report.conflicts++;}catch(_){report.conflicts++;}
          }else{cards.add({...row,token:crypto.randomUUID(),undo:null,restored:true});report.added++;}
        }
        const seen=new Set(currentEvents.result.map(eventIdentity));
        for(const event of data.events){const id=eventIdentity(event);if(!seen.has(id)){events.add(event);seen.add(id);report.events++;}}
      }catch(e){failure=e;tx.abort();}};
      currentCards.onsuccess=merge;currentEvents.onsuccess=merge;
      tx.oncomplete=()=>resolve(report);
      tx.onabort=()=>reject(failure||Error('恢复未保存，所有改动已回滚；原记录保留。'));
    });
  }
  store.snapshot=snapshot;store.restore=restore;
  core.store = store;
  globalThis.PracticeCore = core;
  const script = document.querySelector('[data-practice-script]');
  const base = script ? script.dataset.base : '';
  const page = document.querySelector('[data-practice-page]');
  const find = selector => page.querySelector(`[data-practice-${selector}]`);
  const make = (tag, text) => { const node = document.createElement(tag); node.textContent = text; return node; };
  const labels = {forgot: '没想起', partial: '不熟悉', remembered: '想起来了'};
  const when = value => new Date(value).toLocaleString('zh-CN');
  let visible = 20, historyVisible = 20, versions = null, generation = 0, prepared = null, fileGeneration = 0;
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('political-news-practice') : null;
  function notify() { if (channel) channel.postMessage('changed'); }
  async function updateAddButtons() {
    const keys = new Set((await store.rows()).map(row => row.key));
    document.querySelectorAll('[data-practice-add]').forEach(button => {
      const key = `material:${button.dataset.material}:r${button.dataset.revision}:${button.dataset.kind}:${button.dataset.index}`;
      button.disabled = keys.has(key);
      button.textContent = keys.has(key) ? '已加入复习' : '加入复习';
    });
  }
  document.querySelectorAll('[data-practice-add]').forEach(button => {
    const message = make('span', ''); message.setAttribute('role', 'status'); button.after(message);
    button.addEventListener('click', async () => {
      button.disabled = true; message.textContent = '';
      try {
        const {material, revision, kind, index} = button.dataset;
        const response = await fetch(`${base}/api/practice/card/${material}/${revision}/${kind}/${index}`);
        if (!response.ok) throw new Error('未能读取这条资料的版本与依据，请刷新重试。');
        await store.add(await response.json());
        message.append(make('span', ' 已加入。'));
        const link = make('a', '去复习 →'); link.href = base + '/practice'; message.append(link);
        await updateAddButtons(); notify();
      } catch (error) { message.textContent = error.message; button.disabled = false; }
    });
  });
  if (document.querySelector('[data-practice-add]')) updateAddButtons().catch(error => {
    document.querySelectorAll('[data-practice-add]').forEach(button => {button.disabled = true; button.after(make('p', error.message));});
  });

  function renderCard(row, isOpen) {
    const card = row.card, article = make('article', ''); article.className = 'learning-entry';
    article.dataset.practiceKey = row.key;
    article.append(make('p', `${card.account} · 固定版本 ${card.revision} · 已回想 ${row.review_count} 次`), make('h2', card.question));
    if (card.kind === 'points') article.append(make('p', '提法回想提示由系统生成，不是原文试题。'));
    if (card.needs_review) article.append(make('p', '原文答案仍待核对；回想记录不表示答案已核对教材。'));
    if (card.starred_by_author) article.append(make('p', '原作者重点标记'));
    if (!versions) article.append(make('p', '暂时无法核对来源最新版本，当前显示已保存的版本。'));
    else if (versions[String(card.material_id)] !== card.revision) article.append(make('p', '来源已有更正或已不可用。当前回顾保留的旧版本，复习进度不会自动转移到新版。'));
    if(row.restored) article.append(make('p','从备份恢复的固定版本快照，尚未核对本机原文。换资料库后同编号可能对应不同文章，请按标题、作者及原文依据核对。'));
    const source = make('a', row.restored?'查看本机同编号资料并核对 →':'核对固定版本原文 →'); source.href = base + card.url; article.append(source);
    if(!row.restored){const latest = make('a', '查看最新资料 →'); latest.href = `${base}/learning/${card.material_id}`; article.append(document.createTextNode(' · '), latest);}
    const answer = make('details', ''); answer.className = 'learning-question';
    answer.open = isOpen;
    answer.append(make('summary', '我已尝试回想，展开答案与依据'), make('p', card.answer));
    card.evidence.forEach(line => answer.append(make('blockquote', `${line.id} · ${line.text}`)));
    const actions = make('p', '');
    Object.entries(labels).forEach(([result, label]) => {
      const button = make('button', label); button.type = 'button';
      button.addEventListener('click', () => act(async () => {
        const updated = await store.grade(row.key, result, row.token);
        return `${label}，已保存。下次复习：${when(updated.due_at)}。可切换全部复习卡撤销本次记录。`;
      }, actions));
      actions.append(button, document.createTextNode(' '));
    });
    answer.append(actions); article.append(answer, make('p', `下次复习：${when(row.due_at)}`));
    if (row.undo) {
      const undo = make('button', '撤销最近一次回想'); undo.type = 'button';
      undo.addEventListener('click', () => act(async () => {await store.undo(row.key, row.token); return '已撤销最近一次回想，并恢复之前的复习时间。';}, article));
      article.append(undo);
    }
    const remove = make('button', '移出复习'); remove.type = 'button';
    remove.addEventListener('click', () => {
      if (confirm('移出后这张卡的复习进度将清除，原始资料和回想历史仍保留。确定移出？')) act(async () => {await store.remove(row.key, row.token); return '已移出复习。';}, article);
    });
    article.append(document.createTextNode(' '), remove); return article;
  }
  async function act(action, container) {
    container.querySelectorAll('button').forEach(button => button.disabled = true);
    const backup=container.hasAttribute('data-practice-backup');
    if(backup)find('file').disabled=true;
    try { find('message').textContent = await action(); notify(); }
    catch (error) { find('message').textContent = error.message; }
    await render();
    if(backup){find('file').disabled=false;find('export').disabled=false;find('restore').disabled=!prepared;}
  }
  async function render() {
    if (!page) return;
    const id = ++generation;
    try {
      const [allRows, events] = await Promise.all([store.rows(), store.events()]);
      if (id !== generation) return;
      const rows = allRows.filter(row => validCard(row.card));
      const due = rows.filter(row => Date.parse(row.due_at) <= Date.now());
      find('summary').textContent = `复习清单 ${rows.length} 张 · 现在到期 ${due.length} 张。`;
      if (rows.length !== allRows.length) find('message').textContent = '部分本地记录格式异常，已保留原数据，暂不显示。';
      const selected = (find('filter').value === 'due' ? due : rows).sort((a,b) => Date.parse(a.due_at) - Date.parse(b.due_at) || a.key.localeCompare(b.key));
      const openKeys = new Set(Array.from(find('list').querySelectorAll('article')).filter(article => article.querySelector('details[open]')).map(article => article.dataset.practiceKey));
      find('list').replaceChildren(...selected.slice(0, visible).map(row => renderCard(row, openKeys.has(row.key))));
      if (!selected.length) find('list').append(make('p', rows.length ? '当前没有到期卡片，可以切换“全部复习卡”查看安排。' : '还没有复习卡。从公众号问答或提法旁点击“加入复习”。'));
      find('more').hidden = selected.length <= visible;
      const undone = new Set(events.filter(event => event.type === 'undo').map(event => event.undo_of));
      const history = [...events].sort((a,b) => b.id - a.id);
      find('history').replaceChildren(...history.slice(0, historyVisible).map(event => make('p', `${when(event.at)} · ${event.question} · ${event.type === 'grade' ? labels[event.result] + (undone.has(event.event_id) ? '（已撤销）' : '') : event.type === 'undo' ? '撤销回想' : '移出复习'}`)));
      if (!history.length) find('history').append(make('p', '还没有回想记录。'));
      find('more-history').hidden = history.length <= historyVisible;
    } catch (error) { find('summary').textContent = '复习记录暂不可读'; find('message').textContent = error.message; }
  }
  if (channel) channel.onmessage = () => {render(); updateAddButtons().catch(() => {});};
  function refreshVersions() {
    return fetch(base + '/api/practice/versions', {cache: 'no-store'}).then(response => {if (!response.ok) throw new Error();return response.json();}).then(data => {versions = data.versions; render();}).catch(() => {versions = null; render();});
  }
  if (page) {
    find('export').addEventListener('click',()=>act(async()=>{
      const data=await store.snapshot();
      const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
      if(blob.size>25*1024*1024)throw Error('备份超过 25 MB 上限，暂不能导出。原记录保留。');
      const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`study-room-practice-${new Date().toISOString().slice(0,10)}.json`;a.hidden=true;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
      return `已生成 ${data.cards.length} 张卡片及 ${data.events.length} 条历史的备份，请保管下载文件。`;
    },find('backup')));
    find('file').addEventListener('change',async()=>{
      const fileVersion=++fileGeneration;
      prepared=null;find('restore').disabled=true;
      try{
        const file=find('file').files[0];if(!file)return;
        if(file.size>25*1024*1024)throw Error('文件超过 25 MB，未读取。');
        const checked=validateBackup(JSON.parse(await file.text()));
        if(fileVersion!==fileGeneration)return;
        prepared=checked;
        find('preview').textContent=`已校验：${prepared.cards.length} 张卡片，${prepared.events.length} 条历史；备份时间 ${when(prepared.exported_at)}。确认后合并，已有卡片及进度保留，历史去重。`;
        find('restore').disabled=false;
      }catch(e){if(fileVersion===fileGeneration)find('preview').textContent=e.message;}
    });
    find('restore').addEventListener('click',()=>act(async()=>{
      if(!prepared)throw Error('请先选择并校验备份文件。');
      const result=await store.restore(prepared);prepared=null;find('file').value='';find('restore').disabled=true;
      find('preview').textContent='';
      return `恢复完成：补入 ${result.added} 张卡片，保留本机 ${result.kept} 张，新增历史 ${result.events} 条。${result.conflicts?`${result.conflicts} 张同编号内容不同，已保留本机卡片，请核对资料库。`:''}`;
    },find('backup')));
    find('filter').addEventListener('change', () => {visible = 20; render();});
    find('more').addEventListener('click', () => {visible += 20; render();});
    find('more-history').addEventListener('click', () => {historyVisible += 20; render();});
    refreshVersions();
    render();
    document.addEventListener('visibilitychange', () => {if (!document.hidden) {render(); refreshVersions();}});
    setInterval(refreshVersions, 60000);
  }
})();
