(() => {
  const root = document.querySelector('[data-material-import]');
  if (!root) return;
  const base = root.dataset.base, find = key => root.querySelector(`[data-${key}]`);
  const states = {not_run:'尚未执行批量扫描；逐篇恢复结果见上方队列',running:'正在检查并导入',catching_up:'还有待扫描正文，可继续导入下一批',updated:'本批已导入新内容',partial:'本批有正文未通过检查，已有可用资料保留',no_new_materials:'本批没有新增或更正，不代表公众号没有更新',failed:'本次导入失败，修复后可以重试',interrupted:'上次导入中断，重新导入会继续已保存的进度'};
  const errors = {not_configured:'尚未配置资料源',source_missing:'文章库文件不存在，请检查 WeRSS 数据目录是否可用',source_busy:'文章库暂时被占用，请稍后重试',source_schema:'文章库结构不兼容，请核对 WeRSS 版本与数据文件',source_unreadable:'文章库不可读，请检查文件访问权限或数据库是否可用'};
  const when = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'}) + ' 北京时间' : '暂无记录';
  let busy = false;
  let checking = false;
  const probeStates = {not_configured:'尚未连接 WeRSS 主动检查接口',not_checked:'尚未主动检查',running:'正在检查两个公众号的最新文章入口',checked:'已完成上次通道检查',failed:'上次主动检查失败',interrupted:'上次主动检查中断，可以重试'};
  const probeErrors = {invalid_configuration:'本地接口地址配置不兼容',service_credential_missing:'WeRSS 接口凭据文件不可用',service_auth_required:'WeRSS 管理接口授权需要更新，这不等于微信读书登录失效',incompatible_service:'当前 WeRSS 版本不支持检查接口',incompatible_response:'检查响应无法识别',service_unavailable:'WeRSS 接口暂不可达，请检查本机服务',service_error:'WeRSS 服务未完成检查',check_failed:'检查未完成，请稍后重试'};
  const accountStates = {accessible:'检查时最新文章入口可访问（未检查正文）',empty_response:'未取得最新文章信息',login_required:'明确返回登录错误，请到 WeRSS 重新扫码后再检查',restricted_or_unsupported:'访问受限或接口不兼容，不能仅据此判断登录过期',network_error:'访问微信读书失败，登录状态未知',incompatible_response:'上游响应无法识别，状态未知'};
  async function refresh() {
    try {
      const response = await fetch(base + '/api/learning/status', {cache:'no-store'});
      if (!response.ok) throw new Error();
      const data = await response.json(), imported = data.import, source = data.acquisition, scan = data.scan;
      const probe = data.upstream_check || {state:'not_configured', fresh:false, retry_after_seconds:0};
      find('upstream-state').textContent = `${probeStates[probe.state] || '检查状态未知'}${probe.error ? '：'+(probeErrors[probe.error] || '请检查上游服务') : ''}${probe.state==='checked'&&!probe.fresh ? '；结果已过时，不能作为当前状态。' : ''}`;
      find('upstream-time').textContent = `最近检查结束：${when(probe.finished_at)}；最近两个入口均可访问：${when(probe.last_accessible_at)}。`;
      find('upstream-accounts').replaceChildren(...(probe.accounts || []).map(account => {
        const line = document.createElement('p'); line.textContent = `${account.name}：${accountStates[account.state] || '状态未知'}${account.latest_title ? ' · 最新返回：'+account.latest_title : ''}`; return line;
      }));
      find('upstream-button').disabled = checking || probe.state==='running' || probe.state==='not_configured' || probe.retry_after_seconds > 0;
      find('upstream-button').textContent = probe.retry_after_seconds ? `稍后再检查（${probe.retry_after_seconds} 秒）` : '主动检查上游通道';
      find('source-state').textContent = source.state === 'not_configured' ? '尚未配置 WeRSS 文章库。' : source.database_readable ? '文章库可读，采集实时状态与授权仍未确认。' : '上游文章库暂不可读，已有学习资料仍可使用。';
      find('source-accounts').replaceChildren(...source.accounts.map(account => {
        const line = document.createElement('p'); line.textContent = `${account.name}：已存 ${account.articles} 篇，有文字 ${account.with_text} 篇，待取正文 ${account.awaiting_text} 篇。`; return line;
      }));
      find('import-state').textContent = `${states[imported.state] || '状态未确认'}${imported.error_detail ? '：'+(errors[imported.error_detail] || '请检查资料源') : ''}`;
      const result = imported.result || {};
      find('import-result').textContent = `本批扫描 ${result.scanned || 0} 篇，新增 ${result.added || 0}，更正 ${result.updated || 0}，未变化 ${result.skipped || 0}，拒绝 ${result.rejected || 0}（验证页 ${result.verification_required || 0}，正文不足 ${result.insufficient_text || 0}）。`;
      find('import-scan').textContent = scan.remaining == null ? '还没有扫描进度。' : `上次扫描尚余 ${scan.remaining} 篇；最近一轮结束：${when(scan.last_cycle_finished_at)}，检查 ${scan.last_cycle_scanned || 0} 篇，拒绝 ${scan.last_cycle_rejected || 0} 篇。扫描结束仅针对库中已保存正文，不代表公众号采集完整。`;
      find('import-time').textContent = `最近成功检查：${when(imported.last_success_at)}；最近新增或更正：${when(imported.last_changed_at)}。`;
      find('import-button').disabled = busy || imported.state === 'running' || !source.database_readable;
      find('import-button').textContent = scan.remaining ? '继续导入下一批' : imported.state === 'failed' || imported.state === 'interrupted' ? '重新检查并导入' : '检查并导入一批';
    } catch (_) {
      find('import-state').textContent = '无法读取状态，请检查本地服务；稍后会重试。'; find('import-button').disabled = true;
    }
  }
  find('import-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return; busy = true; find('import-button').disabled = true;
    find('import-message').textContent = '正在导入，请稍候…';
    try {
      const credential = 'base64:' + btoa(String.fromCharCode(...new TextEncoder().encode(find('import-token').value)));
      const response = await fetch(base + '/api/learning/sync', {method:'POST',headers:{'X-Sync-Token':credential}});
      if (!response.ok) throw new Error(response.status===403 ? '管理口令不正确，请重试。' : response.status===409 ? '已有资料导入任务在运行，请稍候。' : response.status===503 ? '资料源未就绪或任务未能启动，请查看上方状态。' : '导入未完成，请稍后重试。');
      const result = await response.json();
      find('import-message').textContent = `本批已完成；${result.remaining ? `仍有 ${result.remaining} 篇待扫描，可继续下一批。` : '库中本轮正文已扫描。公众号上游覆盖仍需单独确认。'}`;
    } catch (error) { find('import-message').textContent = error.message; }
    finally { busy = false; refresh(); }
  });
  find('upstream-button').addEventListener('click', async () => {
    if (checking) return; checking = true; find('upstream-button').disabled = true;
    try {
      const credential = 'base64:' + btoa(String.fromCharCode(...new TextEncoder().encode(find('import-token').value)));
      const response = await fetch(base + '/api/upstream/check', {method:'POST',headers:{'X-Sync-Token':credential}});
      if (!response.ok) throw new Error(response.status===403 ? '管理口令不正确。' : response.status===429 ? '检查过于频繁，请至少间隔一分钟。' : response.status===409 ? '已有通道检查运行，请稍候。' : '未能启动检查，请核对本地接口配置。');
      find('upstream-message').textContent = '上游检查已启动，可离开本页，稍后回来查看结果。';
    } catch (error) {find('upstream-message').textContent = error.message;}
    finally {checking = false;refresh();}
  });
  refresh(); setInterval(refresh, 5000);
})();
