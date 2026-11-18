"""Run on WeRSS stdin: compare saved/effective auth and probe shelf + one MP.

Only names, booleans, timestamps and numeric codes are printed, never secrets.
This explicit operator probe does not collect bodies, renew or rotate accounts.
"""
import contextlib,io,json,re
from pathlib import Path
from collections import Counter

out={}
quiet=io.StringIO()
with contextlib.redirect_stdout(quiet),contextlib.redirect_stderr(quiet):
 from core.config import Config,cfg
 from core.wx.model.weread_mp import MpsWereadMP
 import requests
 client=MpsWereadMP();client._load_weread_auth()
 lic=Path('/app/data/wx.lic');data=Config(str(lic)).get('weread_data',{})
 if isinstance(data,str):data=json.loads(data)
 accounts=data.get('accounts') or ([data] if data.get('cookie') else [])
 saved=accounts[0] if accounts else {}
 names=[p.split('=',1)[0].strip() for p in client._weread_cookies.split(';') if '=' in p]
 out['configuration']={'credential_file_exists':lic.exists(),'credential_mtime':lic.stat().st_mtime if lic.exists() else None,
 'account_count':len(accounts),'effective_matches_saved_cookie':client._weread_cookies==saved.get('cookie'),
 'config_cookie_override':bool(cfg.get('weread.cookie','')),'config_ticket_override':bool(cfg.get('weread.ticket','')),
 'cookie_names':sorted(set(names)),'duplicate_names':[n for n,c in Counter(names).items() if c>1],
 'has_ticket':bool(client._weread_ticket),'required_headers_present':all(client._request_headers().get(k) for k in ('Cookie','User-Agent','Origin','Referer'))}
 out['probes']=[]
 for path,params in [('/web/shelf/sync',{'userVid':'','synckey':0}),('/web/mp/articles',{'bookId':'MP_WXS_2399587742','offset':0})]:
  try:
   r=requests.get('https://weread.qq.com'+path,params=params,headers=client._request_headers(include_ticket=True),proxies=client._get_proxies(),timeout=(10,25),allow_redirects=False)
   try:p=r.json()
   except ValueError:p={}
   code=p.get('errCode',p.get('errcode',p.get('code'))) if isinstance(p,dict) else None
   msg=str(p.get('errMsg',p.get('errmsg',''))) if isinstance(p,dict) else ''
   out['probes'].append(dict(path=path,http_status=r.status_code,code=code,login_error_text=bool(re.search('登录|授权|认证',msg)),quota_error_text=bool(re.search('频率|配额|次数|频繁',msg)),list_schema=isinstance(p,dict) and isinstance(p.get('reviews'),list),shelf_schema=isinstance(p,dict) and any(k in p for k in ('books','synckey','bookCount'))))
  except Exception as e:out['probes'].append(dict(path=path,error_type=type(e).__name__))
print(json.dumps(out))
