"""Extract body text without the old paragraph/character truncation."""
import re
from typing import Any
from bs4 import BeautifulSoup

METHOD='body-extract-2'
CONTAINERS=('#UCAP-CONTENT','.pages_content','.rm_txt_con','#articleText','#article_p',
            '.article-content','.content_area','.left_zw','.news-article','.TRS_Editor','#ozoom','.article','.content','main')


def extract_body(html: str) -> tuple[str,str,dict[str,Any]]:
    soup=BeautifulSoup(html,'html.parser')
    for discard in soup.select('script,style,noscript,nav,footer,aside'):
        discard.decompose()
    container=None; selector='fallback-p'
    for candidate in CONTAINERS:
        node=soup.select_one(candidate)
        if node and node.find('p'):
            container=node;selector=candidate;break
    blocks=(container or soup).find_all('p')
    paragraphs=[]
    for block in blocks:
        value=re.sub(r'\s+',' ',block.get_text(' ',strip=True)).strip()
        if not value or re.match(r'^(?:责任编辑|编辑[:：]|扫一扫|打印|关闭窗口|相关阅读|推荐阅读|分享让更多人看到|版权|Copyright)',value):
            continue
        paragraphs.append(value)
    content='\n'.join(paragraphs)
    status='extracted' if container and len(content)>=10 and '\ufffd' not in content else 'unverified' if content else 'missing'
    # Follow-up pages are never silently declared complete.
    page_links=list((container or soup).select('a[href]'))+list(soup.select('.pagination a[href],.pages a[href],#pages a[href]'))
    if any(re.search(r'下一页|下页',a.get_text(' ',strip=True)) for a in page_links):
        status='partial'
    if re.search(r'验证码|访问过于频繁|请完成安全验证|访问异常',content[:200]):
        status='blocked';content='';paragraphs=[]
    return paragraphs[0] if paragraphs else '',content,{'method':METHOD,'status':status,'selector':selector,
            'paragraphs':len(paragraphs),'characters':len(content)}
