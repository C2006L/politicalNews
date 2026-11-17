(() => {
  'use strict';
  const root = document.querySelector('[data-curation]');
  if (!root) return;
  const base = root.dataset.base, find = key => root.querySelector(`[data-curation-${key}]`);
  const categories = {policy:'政策发布',report:'时政报道',analysis:'时政解读',textbook:'教材背诵',exam_advice:'备考建议',promotion:'推广内容',uncertain:'待判断'};
  const make = (tag,text) => {const node=document.createElement(tag);node.textContent=text;return node;};
  let page=Number(root.dataset.page), generation=0;
  const credential = () => 'base64:' + btoa(String.fromCharCode(...new TextEncoder().encode(find('token').value)));
  function select(values,current) {
    const field=make('select','');
    for(const [value,label] of Object.entries(values)) {const option=make('option',label);option.value=value;field.append(option);}
    field.value=current;return field;
  }
  async function responseData(response) {
    if(response.status===403)throw Error('维护口令不正确，或尚未设置。');
    if(response.status===409)throw Error('原文或分类已有变化，请重新加载后再调整。');
    if(!response.ok)throw Error('本次操作未完成，原内容保留。');
    return response.json();
  }
  async function access() {
    try {
      const response=await fetch(base+'/api/review/access',{cache:'no-store'});
      if(!response.ok)throw Error();
      const data=await response.json();
      find('setup').hidden=!data.can_setup;
      find('access').textContent=data.configured?'需要调整时填写已有维护口令。':data.can_setup?'首次可在这里设置至少 10 个字符的本机维护口令。':'首次维护设置需要在运行程序的本机打开此页面。';
    }catch(_){find('access').textContent='暂时无法读取维护设置，已有内容仍可浏览。';}
  }
  find('setup').addEventListener('click',async()=>{
    find('setup').disabled=true;
    try {
      if(find('token').value.length<10)throw Error('首次口令至少需要 10 个字符。');
      const response=await fetch(base+'/api/review/access',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'study-ui'},body:JSON.stringify({password:find('token').value})});
      if(!response.ok)throw Error(response.status===409?'口令已设置，请填写已有口令。':'未能完成首次设置，请在本机重试。');
      find('message').textContent='已设置本机维护口令，现在可以保存分类调整。';
      await access();
    }catch(error){find('message').textContent=error.message;}
    finally{find('setup').disabled=false;}
  });
  function card(item) {
    const article=make('article','');article.className='learning-entry';
    article.append(make('p',`${item.source} · ${item.label} · ${item.selected?'符合精选规则':'未入选'}${item.manual?' · 人工调整':''}`));
    const heading=make('h2',''), link=make('a',item.title);link.href=base+item.url;heading.append(link);article.append(heading);
    article.append(make('p',item.excerpt));
    const reasons=make('ul','');item.reasons.forEach(reason=>reasons.append(make('li',reason)));article.append(reasons);
    const evidence=make('details','');evidence.append(make('summary','规则命中的原文片段'));
    item.evidence.forEach(line=>evidence.append(make('blockquote',`${line.field==='title'?'标题':'正文'}：${line.quote}`)));
    if(!item.evidence.length)evidence.append(make('p','未命中明确分类片段，可结合原文纠正。'));
    article.append(evidence);
    const edit=make('details','');edit.append(make('summary','纠正分类或精选范围'));
    const kind=select({auto:'按规则分类',...categories},item.manual?item.category:'auto');
    const selection=select({auto:'按类别与规则决定',include:'优先纳入精选候选',exclude:'明确排除精选'},'auto');
    const note=make('textarea','');note.maxLength=500;note.rows=2;note.placeholder='调整理由（会显示在入选依据中）';note.setAttribute('aria-label','调整理由');
    const label1=make('label','内容类别 '), label2=make('label','精选范围 ');label1.append(kind);label2.append(selection);edit.append(label1,document.createTextNode(' '),label2,make('p',''),note);
    const save=make('button','保存调整'),reset=make('button','恢复规则判断');save.type=reset.type='button';
    const message=make('p','');message.setAttribute('role','status');
    let decision=item.decision_id;
    async function write(restore) {
      save.disabled=reset.disabled=true;
      try {
        const payload={fingerprint:item.fingerprint,expected_decision:decision,category:restore||kind.value==='auto'?null:kind.value,selection:restore?'auto':selection.value,note:restore?'':note.value};
        const result=await responseData(await fetch(`${base}/api/curation/${item.kind}/${item.id}`,{method:'PUT',headers:{'Content-Type':'application/json','X-Sync-Token':credential(),'X-Requested-With':'study-ui'},body:JSON.stringify(payload)}));
        decision=result.decision_id;
        message.textContent=restore?'已恢复规则判断。':'已保存；首页会在下次自动检查时更新。';
        find('message').textContent=message.textContent;
        await load();
      } catch(error) {message.textContent=error.message;}
      finally {save.disabled=reset.disabled=false;}
    }
    save.addEventListener('click',()=>write(false));reset.addEventListener('click',()=>write(true));
    edit.append(make('p',''),save,document.createTextNode(' '),reset,message);article.append(edit);
    const history=make('details',''),list=make('div','');history.append(make('summary','查看最近 20 条调整记录'),list);
    history.addEventListener('toggle',async()=>{
      if(!history.open)return;
      try {
        const data=await responseData(await fetch(`${base}/api/curation/${item.kind}/${item.id}/history`,{headers:{'X-Sync-Token':credential()},cache:'no-store'}));
        list.replaceChildren(...data.items.map(row=>make('p',`${new Date(row.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} 北京时间 · ${row.is_current?'当前正文':'旧正文，未沿用'} · ${categories[row.category]||'规则分类'} / ${{auto:'规则决定',include:'纳入',exclude:'排除'}[row.selection]} · ${row.note}`)));
        if(!data.items.length)list.append(make('p','暂无人工调整记录。'));
      }catch(error){list.replaceChildren(make('p',error.message));}
    });article.append(history);return article;
  }
  async function load() {
    const current=++generation;
    try {
      const params=new URLSearchParams({kind:root.dataset.kind,scope:root.dataset.scope,page});
      for(const key of ['item','category'])if(root.dataset[key])params.set(key,root.dataset[key]);
      const data=await responseData(await fetch(`${base}/api/curation?${params}`,{cache:'no-store'}));
      if(current!==generation)return;
      find('count').textContent=`当前范围 ${data.total} 篇；全部分类：`+Object.entries(data.counts).filter(([,n])=>n).map(([key,n])=>`${categories[key]} ${n}`).join(' · ');
      find('list').replaceChildren(...data.items.map(card));
      if(!data.items.length)find('list').append(make('p','当前范围暂无内容，可以切换全部类别与全部内容。'));
      find('page').textContent=`第 ${page} 页`;find('prev').disabled=page<=1;find('next').disabled=page*20>=data.total;
    }catch(error){find('message').textContent=error.message;}
  }
  find('prev').addEventListener('click',()=>{page--;load();});find('next').addEventListener('click',()=>{page++;load();});access();load();
})();
