(() => {
  'use strict';
  const root=document.querySelector('[data-events]');if(!root)return;
  const base=root.dataset.base,find=key=>root.querySelector(`[data-event-${key}]`);
  const names={candidate:'待确认',confirmed:'已确认',rejected:'已拆开'};
  const make=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node;};
  const link=(text,path)=>{const node=make('a',text);node.href=base+path;return node;};
  const credential=()=> 'base64:'+btoa(String.fromCharCode(...new TextEncoder().encode(find('token').value)));
  let page=Number(root.dataset.page),generation=0;
  const dossier=make('section','');dossier.className='event-digest';find('list').before(dossier);
  function digest(data,visible){
    dossier.replaceChildren(make('h2','时政要点与报道对照'));
    dossier.append(make('p',data.mode==='confirmed'?'当前已确认报道的原文整理':'候选试读：关联尚未确认，以下仅供逐篇对照，不作为统一事件结论。'));
    const states={baseline:'对照起点',repeated:'已比较句均为重复表述',different:'存在近似表述差异',additional:'含补充表述',insufficient:'可比较文字不足'};
    const overview=make('details','');overview.append(make('summary','哪些是重复，哪些值得对照'));
    for(const row of data.articles){const p=make('p',`${row.publisher} · ${row.published_at.slice(0,10)} · ${states[row.state]}：重复 ${row.repeated} / 补充 ${row.additional} / 差异 ${row.different} 句${row.text_limited?'（已截取部分文字）':''} · `);p.append(link(row.title,'/news/'+row.news_id));overview.append(p);}
    dossier.append(overview);
    for(const point of data.points){
      const block=make('article','');block.className='digest-point';
      block.append(make('h3',point.label),make('blockquote',point.evidence.quote));
      const cite=make('p',`${point.evidence.publisher} · 刊载 ${point.evidence.published_at.slice(0,10)} · `);
      const inPage=visible.some(row=>row.id===point.evidence.article_id);
      const a=make('a',inPage?'查看这篇及原文依据 →':'阅读这篇原文 →');a.href=inPage?'#event-source-'+point.evidence.article_id:base+'/news/'+point.evidence.news_id;cite.append(a);block.append(cite);
      if(point.previous){const compare=make('details','');compare.append(make('summary','对照较早列出的表述（不能据此认定更正）'),make('blockquote',point.previous.quote));const source=make('p',point.previous.publisher+' · '+point.previous.published_at.slice(0,10)+' · ');source.append(link('阅读原文 →','/news/'+point.previous.news_id));compare.append(source);block.append(compare);}
      dossier.append(block);
    }
    if(!data.points.length)dossier.append(make('p','当前没有可摘录的要点，仍可直接阅读下方各篇原文。'));
    dossier.append(make('p',`展示 ${data.points.length} / ${data.total_points} 条可摘录表述；本次未纳入 ${data.excluded} 篇。${data.limits}`));
  }
  async function result(response){
    if(response.status===403)throw Error('请填写正确的维护口令，首次可到分类调整页设置。');
    if(response.status===409)throw Error('关联或原文已有变化，请刷新后核对当前稿。');
    if(!response.ok)throw Error('本次操作未完成，原记录保留。');
    return response.json();
  }
  function member(row){
    const article=make('article','');article.className='learning-entry event-member';article.dataset.eventArticle=row.id;article.id='event-source-'+row.id;
    const snapshot=row.snapshot;
    article.append(make('p',`${row.publisher} · 刊载 ${snapshot.published_at.slice(0,10)} · ${names[row.status]}${row.is_current?'':' · 旧版本，当前不可合并'} · ${row.selection?.label||'当前原文已不可用'}`));
    const h=make('h2','');h.append(link(snapshot.title,'/news/'+row.news_id));article.append(h);
    if(row.selection&&!row.selection.selected)article.append(make('p','当前未入选时政精选；确认事件关联不会改变分类决定。'));
    const timing={explicit_date:'原文提供完整日期',explicit_month_day:'原文提供月日，年份按刊载时间就近推定',explicit_day:'原文仅提供日数，月份与年份按刊载时间回推至最多 7 日内，仍需核对',publication_day:'没有确定的事件日期，仅在同刊载日窗口建立候选'};
    article.append(make('p',timing[row.proof.basis]||'日期依据待核对'));
    const evidence=make('details','');evidence.open=true;evidence.append(make('summary','为什么可能是同一件事'));
    row.proof.evidence.forEach(proof=>evidence.append(make('blockquote',`${proof.field==='title'?'标题':'原文'}：${proof.quote}`)));
    article.append(evidence);
    const original=make('details','');original.append(make('summary','匹配时保存的文字节选'),make('p',snapshot.text));article.append(original);
    article.append(link('查看当前原文 →','/news/'+row.news_id));
    try {const url=new URL(snapshot.url);if(['https:','http:'].includes(url.protocol)){const a=make('a',' 来源网页 ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';article.append(document.createTextNode(' · '),a);}}catch(_){}
    if(row.is_current){
      const save=make('button','收藏');save.type='button';save.dataset.saveId=String(row.news_id);save.dataset.title=snapshot.title;save.dataset.url=base+'/news/'+row.news_id;save.dataset.date=snapshot.published_at.slice(0,10);article.append(document.createTextNode(' '),save);
      const form=make('div','');form.className='event-review-actions';
      const note=make('input','');note.type='text';note.maxLength=500;note.placeholder='可选：记录确认或拆开理由';note.setAttribute('aria-label','事件关联维护备注');form.append(note);
      const message=make('p','');message.setAttribute('role','status');
      for(const [status,label] of Object.entries({confirmed:'确认同一事件',rejected:'拆开这篇',candidate:'恢复待确认'})){
        const button=make('button',label);button.type='button';button.disabled=row.status===status;button.dataset.eventStatus=status;
        button.addEventListener('click',async()=>{
          form.querySelectorAll('button').forEach(b=>b.disabled=true);
          try{
            await result(await fetch(`${base}/api/event-articles/${row.id}`,{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'study-ui','X-Sync-Token':credential()},body:JSON.stringify({status,expected_status:row.status,note:note.value})}));
            find('message').textContent=status==='rejected'?'已拆开；其他资料及已读、收藏保留。':'已保存；至少两篇当前稿确认后，首页在下次自动检查时归并。';
            await load();
          }catch(error){message.textContent=error.message;form.querySelectorAll('button').forEach(b=>b.disabled=b.dataset.eventStatus===row.status);}
        });form.append(button,document.createTextNode(' '));
      }
      form.append(message);article.append(form);
    }
    return article;
  }
  async function load(){
    const current=++generation;
    try{
      if(root.dataset.event){
        const params=new URLSearchParams({history:root.dataset.history,page});
        const data=await result(await fetch(`${base}/api/events/${root.dataset.event}?${params}`,{cache:'no-store'}));
        if(current!==generation)return;
        digest(data.digest,data.items);
        const scope=data.facts.time_scope==='publication_day'?'刊载日窗口（事件日期未确定）':'原文所述事件日期';
        find('summary').replaceChildren(make('p',`${scope}：${data.event_day} · ${data.facts.facts.phase==='planned'?'计划活动，不代表已经发生':'按报道建立关联，尚非事实核验'}。同来源转载不等于独立证据。`),link(root.dataset.history==='true'?'只看当前未排除稿 →':'查看已拆开与旧版本 →',`/events?event_id=${data.id}&history=${root.dataset.history!=='true'}`));
        find('list').replaceChildren(...data.items.map(member));
        if(!data.items.length)find('list').append(make('p','当前范围没有可归并的稿件，可查看已拆开与旧版本。'));
        find('next').disabled=page*20>=data.total;
        find('history').hidden=false;
      }else{
        const data=await result(await fetch(`${base}/api/events?scope=${root.dataset.scope}&page=${page}`,{cache:'no-store'}));
        if(current!==generation)return;
        find('summary').textContent=`当前范围 ${data.total} 组。刊载篇数与站点数不用于直接提高精选排序。`;
        find('list').replaceChildren(...data.items.map(row=>{
          const article=make('article','');article.className='learning-entry';
          article.append(make('p',`${row.event_day} · ${row.date_scope==='publication_day'?'同刊载日窗口':'原文日期'} · ${row.phase==='planned'?'计划活动':'报道关联'}`));
          const heading=make('h2','');heading.append(link(row.title,'/events?event_id='+row.id+(root.dataset.scope==='rejected'?'&history=true':'')));article.append(heading);
          article.append(make('p',`${row.article_count} 篇当前刊载 · ${row.publisher_count} 个站点 · 已确认 ${row.confirmed} / 待确认 ${row.candidate} / 已拆开 ${row.rejected}`));return article;
        }));
        if(!data.items.length)find('list').append(make('p','当前没有满足条件的事件组。没有候选不表示没有相关新闻，也不会强行按泛词凑组。'));
        find('next').disabled=page*20>=data.total;
      }
      find('prev').disabled=page<=1;find('page').textContent=`第 ${page} 页`;
      document.dispatchEvent(new CustomEvent('study:updated'));
    }catch(error){find('message').textContent=error.message;}
  }
  find('history').addEventListener('toggle',async()=>{
    if(!find('history').open||!root.dataset.event)return;
    try{const data=await result(await fetch(`${base}/api/events/${root.dataset.event}/reviews`,{headers:{'X-Sync-Token':credential()},cache:'no-store'}));find('reviews').replaceChildren(...data.items.map(row=>make('p',`${new Date(row.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} 北京时间 · 关联 ${row.article_id} · ${names[row.previous]} → ${names[row.status]} · ${row.note}`)));if(!data.items.length)find('reviews').append(make('p','暂无维护记录。'));}catch(error){find('reviews').replaceChildren(make('p',error.message));}
  });
  find('prev').addEventListener('click',()=>{page--;load();});find('next').addEventListener('click',()=>{page++;load();});load();
})();
