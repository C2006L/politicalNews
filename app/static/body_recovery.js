(() => {
  const root = document.querySelector('[data-body-recovery]');
  if (!root) return;
  const base = root.dataset.base, find = key => root.querySelector(`[data-recovery-${key}]`);
  const states = {pending:'本机排队中，尚未提交',submitting:'正在提交，结果待确认',queued:'WeRSS 已接收任务，尚未确认正文',running:'WeRSS 正在处理，尚未确认正文',verifying:'仍在等待有效正文或主项目导入',unknown:'结果未确认，不会盲目重发',completed:'有效正文已验证并导入',invalid_content:'正文未通过校验，原有资料保留',failed:'本次恢复未完成',superseded:'已由后续手动重试接替'};
  const errors = {source_unavailable:'上游文章库不可读',source_identity_changed:'文章或所属账号已变化',source_no_longer_missing:'正文已变化，请先核对恢复结果',body_not_visible:'未在上游库中确认有效正文',content_rejected:'正文不足或仍是验证页面',import_busy:'正在等待已有资料导入任务结束',submission_unconfirmed:'无法确认上游是否已接收',task_unconfirmed:'上游任务记录缺失或不匹配',task_timeout:'等待结果超时',upstream_failed:'WeRSS 报告任务失败',local_observation_failed:'本机结果记录未完成，将继续核对',service_auth_required:'WeRSS 恢复接口凭据需要更新，不等于微信登录过期',service_credential_missing:'恢复接口凭据文件不可用',service_unavailable:'WeRSS 接口暂不可达',service_error:'WeRSS 未明确返回任务结果',resource_missing:'上游文章或接口不存在',incompatible_service:'上游接口不兼容',invalid_configuration:'本地接口配置有误'};
  const make = (tag, text) => {const el = document.createElement(tag);el.textContent = text;return el;};
  let page = 1, busy = false, generation = 0;
  async function request(path) {
    const password = document.querySelector('[data-import-token]').value;
    if (!password) throw new Error('请先在上方填写管理口令。');
    const credential = 'base64:' + btoa(String.fromCharCode(...new TextEncoder().encode(password)));
    const response = await fetch(base + path, {method:'POST',headers:{'X-Sync-Token':credential}});
    if (!response.ok) throw new Error(response.status===403 ? '管理口令不正确。' : response.status===409 ? '已有任务或状态已变化，请查看队列，稍后重试。' : response.status===429 ? '请间隔至少 10 秒再检查恢复结果。' : '恢复接口未就绪，请检查本机配置。');
    return response.json();
  }
  async function action(path, enqueue) {
    if (busy) return; busy = true;
    root.querySelectorAll('button').forEach(button => button.disabled = true);
    try {
      const result = await request(path);
      find('message').textContent = enqueue ? result.accepted ? `已将 ${result.accepted} 篇加入本机队列，等待逐篇处理；这不代表已恢复正文。` : '没有可加入的缺正文文章，或相关记录暂在冷却期。' : '已开始核对恢复结果，请稍候查看队列。';
    } catch (error) {find('message').textContent = error.message;}
    finally {busy = false;refresh();}
  }
  async function refresh() {
    if (busy) return;
    const current = ++generation;
    try {
      const response = await fetch(base + '/api/body-recovery/status?page=' + page, {cache:'no-store'});
      if (!response.ok) throw new Error();
      const data = await response.json(); if (current !== generation) return;
      if (!data.items.length && page > 1 && data.total <= (page - 1) * 20) {page=Math.max(1,Math.ceil(data.total/20));return refresh();}
      find('summary').textContent = `${data.configured ? '' : '恢复接口尚未配置。'}${data.source_error ? '文章库暂不可读。' : `当前有 ${data.missing_count} 篇符合条件的已知缺正文。`} 队列中 ${data.active_count} 项。${data.automatic ? '自动任务开启时每分钟核对。' : '当前请使用“检查恢复结果”继续处理。'}`;
      find('start').disabled = !data.configured || data.source_error || data.worker_running || data.active_count>=3 || !data.missing_count;
      find('check').disabled = !data.configured || data.worker_running || !data.active_count || data.check_after_seconds>0;
      find('check').textContent = data.check_after_seconds ? `稍后核对（${data.check_after_seconds} 秒）` : '检查恢复结果';
      find('list').replaceChildren(...data.items.map(item => {
        const article = make('article','');article.className='learning-entry';
        article.append(make('p',item.account),make('h3',item.title),make('p',states[item.state]||'状态未确认'));
        if(item.error)article.append(make('p',errors[item.error]||'请查看上游服务与本机状态。'));
        article.append(make('p','最近核对：'+new Date(item.updated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+' 北京时间'));
        if(['failed','invalid_content','unknown'].includes(item.state)){
          const button=make('button',item.can_retry?'明确重试此文章':`可重试前需等待 ${item.retry_after_seconds} 秒`);button.type='button';button.disabled=!item.can_retry||data.worker_running||!data.configured;
          button.addEventListener('click',()=>action('/api/body-recovery/'+encodeURIComponent(item.id)+'/retry',true));article.append(button);
        }
        return article;
      }));
      if(!data.items.length)find('list').append(make('p','还没有恢复任务。没有已知缺正文，不代表上游文章列表完整。'));
      find('prev').disabled=page<=1;find('next').disabled=page*20>=data.total;find('page').textContent=`${page} / ${Math.max(1,Math.ceil(data.total/20))}`;
    } catch(_) {find('summary').textContent='无法读取恢复队列，请检查本地服务。';find('start').disabled=true;find('check').disabled=true;}
  }
  find('start').addEventListener('click',()=>action('/api/body-recovery/start',true));
  find('check').addEventListener('click',()=>action('/api/body-recovery/check',false));
  find('prev').addEventListener('click',()=>{page--;refresh();});find('next').addEventListener('click',()=>{page++;refresh();});
  refresh();setInterval(refresh,5000);
})();
