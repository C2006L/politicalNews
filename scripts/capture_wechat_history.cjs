/* Reads public article pages in the user's manually verified Chrome session.
 * Stops at verification screens; never solves or bypasses platform challenges.
 * Outputs raw article-body snapshots to a caller-selected private directory.
 */
const fs = require('node:fs');
const path = require('node:path');
const ACCOUNTS = {
  MP_WXS_3243013434: {biz:'MzI0MzAxMzQzNA==',name:'肖秀荣教授'},
  MP_WXS_3080503014: {biz:'MzA4MDUwMzAxNA==',name:'考研政治徐涛'}
};
function validateItem(item) {
  const account=ACCOUNTS[item.account_id],url=new URL(item.url);
  if(!account || url.protocol!=='https:' || url.hostname!=='mp.weixin.qq.com' || url.username || url.password ||
      !['/s','/s/'].some(p=>url.pathname===p||url.pathname.startsWith('/s/')) || typeof item.title!=='string') throw Error('Invalid source identity');
  if(item.mid && (url.searchParams.get('__biz')!==account.biz || url.searchParams.get('mid')!==item.mid ||
      url.searchParams.get('idx')!==item.idx || !/^\d+$/.test(item.mid) || !/^\d+$/.test(item.idx))) throw Error('Album article identity mismatch');
  return account;
}
module.exports={validateItem};
async function main() {
  const [manifestPath,output,port='9242']=process.argv.slice(2);
  if(!manifestPath||!output||!/^\d{4,5}$/.test(port))throw Error('Usage: node capture_wechat_history.cjs manifest.json private-output-dir [port]');
  const manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
  if(!Array.isArray(manifest.items)||manifest.items.length>1000)throw Error('Invalid manifest');
  const legacy=manifest.existing_sources||[];
  [...manifest.items,...legacy].forEach(validateItem);
  fs.mkdirSync(output,{recursive:true});
  const targets=await(await fetch(`http://127.0.0.1:${port}/json`)).json();
  const target=targets.find(t=>t.type==='page'&&new URL(t.url).hostname==='mp.weixin.qq.com');
  if(!target)throw Error('Open the manually verified WeChat article in the dedicated Chrome window first');
  const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
  let serial=0;const pending=new Map();
  ws.onmessage=event=>{const message=JSON.parse(event.data);const task=pending.get(message.id);if(task){pending.delete(message.id);clearTimeout(task.timer);message.error?task.reject(Error('Browser command failed')):task.resolve(message.result)}};
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;const timer=setTimeout(()=>{pending.delete(id);reject(Error('Browser command timeout'))},30000);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method,params}))});
  const evaluate=async expression=>{const result=await send('Runtime.evaluate',{expression,returnByValue:true});if(result.exceptionDetails)throw Error('Article extraction failed');return result.result.value};
  const pause=ms=>new Promise(r=>setTimeout(r,ms));
  const atomic=(file,data)=>{const temp=file+'.tmp';fs.writeFileSync(temp,JSON.stringify(data,null,2));fs.renameSync(temp,file)};
  const report={scope:manifest.scope,started_at:new Date().toISOString(),captured:0,skipped:0,blocked:null};
  const tasks=[...manifest.items.slice(0,1),...legacy,...manifest.items.slice(1)];
  try {
    await send('Page.enable');
    for(const item of tasks){
      const account=validateItem(item),stem=item.mid?`${item.account_id}-${item.mid}-${item.idx}`:'existing-'+item.source_id;
      if(!/^[A-Za-z0-9_~-]+(?:[.-][A-Za-z0-9_~-]+)*$/.test(stem))throw Error('Invalid output name');
      const file=path.join(output,stem+'.json');
      if(fs.existsSync(file)){
        const saved=JSON.parse(fs.readFileSync(file,'utf8'));
        if(saved.account_id!==item.account_id || (item.mid && (saved.mid!==item.mid||saved.idx!==item.idx)) || typeof saved.html!=='string')throw Error('Existing snapshot identity mismatch');
        report.skipped++;continue;
      }
      if(item!==tasks[0]){await pause(3000);await send('Page.navigate',{url:item.url})}
      let row=null;
      for(let attempt=0;attempt<80;attempt++){
        await pause(300);
        const snapshot=await evaluate(`(()=>{const body=document.querySelector('#js_content');return {path:location.pathname,biz:window.biz,mid:String(window.mid||window.appmsgid||''),idx:String(window.idx||window.itemidx||''),title:document.querySelector('#activity-name')?.textContent.trim(),account:document.querySelector('#js_name')?.textContent.trim(),html:body?.innerHTML,text_chars:body?.textContent.trim().length||0,published_text:document.querySelector('#publish_time')?.textContent.trim()||'',ct:window.ct||null}})()`);
        if(snapshot.path.includes('captcha')){if(attempt<8)continue;throw Error('platform_verification_required')}
        if(snapshot.html && snapshot.biz===account.biz && snapshot.text_chars>=100 &&
           (!item.mid || (snapshot.mid===item.mid&&snapshot.idx===item.idx)) && snapshot.title===item.title){row=snapshot;break}
      }
      if(!row)throw Error('article_body_or_identity_unconfirmed');
      if(!/^\d+$/.test(row.mid)||!/^\d+$/.test(row.idx))throw Error('article_identity_unconfirmed');
      atomic(file,{...row,account_id:item.account_id,account:account.name,url:item.url,source_id:item.source_id||null,captured_at:new Date().toISOString()});
      report.captured++;atomic(path.join(output,'capture-report.json'),report);
      console.log(JSON.stringify({account:item.account_id,mid:row.mid,idx:row.idx,captured:report.captured,title:row.title}));
    }
  }catch(error){report.blocked=error.message;process.exitCode=2;console.log(JSON.stringify({stopped:true,reason:error.message,captured:report.captured,skipped:report.skipped}));}
  finally{report.finished_at=new Date().toISOString();atomic(path.join(output,'capture-report.json'),report);ws.close()}
}
if(require.main===module)main().catch(error=>{console.error(error.message);process.exitCode=1});
