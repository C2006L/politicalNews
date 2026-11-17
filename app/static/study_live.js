/* Calendar follows the system clock in the site's publication time zone.
 * Read-only polling observes scheduled ingestion; it never starts a crawler.
 */
(() => {
  'use strict';
  const formatter=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',weekday:'long'});
  function calendar(timestamp) {
    const parts=Object.fromEntries(formatter.formatToParts(new Date(timestamp)).map(p=>[p.type,p.value]));
    return {...parts,key:`${parts.year}-${parts.month}-${parts.day}`};
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={calendar};
  if(typeof document==='undefined')return;
  let root=document.querySelector('[data-live-study]');
  if(!root)return;
  let clockDay='',requestDay='',controller=null,generation=0,pending=null;
  const base=root.dataset.base;
  const message=text=>{root.querySelector('[data-live-message]').textContent=text;};
  function drawClock() {
    const value=calendar(Date.now());
    root.querySelector('[data-clock-month]').textContent=`${value.year} / ${value.month}`;
    root.querySelector('[data-clock-day]').textContent=value.day;
    root.querySelector('[data-clock-weekday]').textContent=value.weekday;
    return value.key;
  }
  function editing() {
    return root.contains(document.activeElement)&&document.activeElement.matches('input,textarea,select') ||
      Boolean(window.getSelection()?.toString()&&root.contains(window.getSelection().anchorNode));
  }
  function apply() {
    if(!pending||editing())return;
    const next=pending;
    if(next.dataset.renderedDay!==calendar(Date.now()).key){pending=null;return;}
    pending=null;
    const search=root.querySelector('#study-search')?.value||'';
    const anchor=[...root.querySelectorAll('.reading-card')].find(card=>card.getBoundingClientRect().bottom>0);
    const offset=anchor?.getBoundingClientRect().top;
    const focused=document.activeElement;
    const focusedCard=focused?.closest?.('.reading-card')?.id;
    const focusedAction=focused?.dataset?.action;
    const opened=[...root.querySelectorAll('details[open]')].map(detail=>{
      const card=detail.closest('.reading-card');
      return {card:card?.id,index:card?[...card.querySelectorAll('details')].indexOf(detail):-1,explainer:detail.classList.contains('list-explainer')};
    });
    root.replaceWith(next);root=next;
    root.querySelector('#study-search').value=search;
    for(const detail of opened){
      const card=detail.card&&document.getElementById(detail.card);
      const target=detail.explainer?root.querySelector('.list-explainer'):card?.querySelectorAll('details')[detail.index];
      if(target)target.open=true;
    }
    if(anchor&&offset!==undefined){const retained=document.getElementById(anchor.id);if(retained)window.scrollBy(0,retained.getBoundingClientRect().top-offset);}
    if(focusedCard&&focusedAction){const retained=document.getElementById(focusedCard);retained?.querySelector(`[data-action="${focusedAction}"]`)?.focus({preventScroll:true});}
    drawClock();
    document.dispatchEvent(new CustomEvent('study:updated'));
  }
  async function refresh() {
    if(document.hidden)return;
    const day=calendar(Date.now()).key;
    if(controller&&requestDay===day)return;
    if(controller)controller.abort();
    const current=++generation;
    requestDay=day;controller=new AbortController();
    const activeController=controller;
    const timeout=setTimeout(()=>activeController.abort(),15000);
    const params=new URLSearchParams();
    const currentQuery=new URLSearchParams(location.search);
    for(const key of ['page','track','topic','source','mode'])if(currentQuery.has(key))params.set(key,currentQuery.get(key));
    params.set('day',day);
    try {
      const response=await fetch(`${base}/api/study/live?${params}`,{cache:'no-store',signal:activeController.signal});
      if(!response.ok)throw Error('unavailable');
      const data=await response.json();
      if(current!==generation||data.day!==calendar(Date.now()).key)return;
      if(data.revision===root.dataset.liveRevision){
        // A queued update may have reverted on the server while the user typed.
        pending=null;
        const original=new DOMParser().parseFromString(data.html,'text/html').querySelector('[data-live-message]');
        if(original)message(original.textContent);
        return;
      }
      const parsed=new DOMParser().parseFromString(data.html,'text/html');
      const next=parsed.querySelector('[data-live-study]');
      if(!next||next.dataset.renderedDay!==data.day||next.dataset.liveRevision!==data.revision)throw Error('invalid');
      pending=next;
      apply();
      if(pending)message('新资料已就绪，完成当前输入后自动更新。');
      document.title=parsed.title;
    }catch(error){
      if(current===generation){message(navigator.onLine===false?'当前离线，保留已有资料；联网后自动检查更新。':'暂未取得更新，保留已有资料；稍后会自动重试。');}
    }finally{clearTimeout(timeout);if(current===generation)controller=null;}
  }
  function tick(force=false) {
    if(document.hidden)return;
    const day=calendar(Date.now()).key;
    if(day!==clockDay){
      clockDay=drawClock();
      if(root.dataset.renderedDay!==day)message('日期已切换，正在检查今天的阅读材料…');
      refresh();
    }else if(force)refresh();
  }
  document.addEventListener('focusout',()=>setTimeout(apply,0));
  document.addEventListener('selectionchange',()=>{if(pending)apply();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)tick(true);});
  window.addEventListener('pageshow',()=>tick(true));
  window.addEventListener('focus',()=>tick(true));
  window.addEventListener('online',()=>tick(true));
  setInterval(()=>tick(),1000);
  setInterval(refresh,60000);
  tick(true);
})();
