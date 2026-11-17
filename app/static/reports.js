(() => {
 const button=document.querySelector('[data-save-report]');if(!button)return;
 button.addEventListener('click',async()=>{
  const message=document.querySelector('[data-report-message]');button.disabled=true;
  try{
   const value=document.querySelector('[data-report-token]').value;
   const token='base64:'+btoa(String.fromCharCode(...new TextEncoder().encode(value)));
   const response=await fetch(button.dataset.base+'/api/reports/snapshots',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'study-ui','X-Sync-Token':token},body:JSON.stringify({kind:button.dataset.kind,profile:button.dataset.profile||'general',day:button.dataset.day,fingerprint:button.dataset.fingerprint})});
   if(!response.ok)throw Error(response.status===409?'资料已有变化，请重新查看后保存。':response.status===403?'请填写正确的维护口令。':'保存未完成，原版本保留。');
   const data=await response.json();location.href=button.dataset.base+'/reports/saved/'+data.id;
  }catch(error){message.textContent=error.message;button.disabled=false;}
 });
})();
