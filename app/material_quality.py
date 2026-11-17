"""Conservative text availability, separate from factual or full-media verification."""
import re
from typing import Any
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session
from app.models import LearningMaterial

LABELS={'readable':'文字正文可读','image_primary':'主要内容可能在图片','partial':'疑似截断或分页未齐','missing':'未取得文字正文','blocked':'验证页或访问受限','unverified':'正文范围待核对'}


def inspect(payload: dict[str,Any], raw: str | None) -> dict[str,Any]:
    text='\n'.join(x.get('text','') for x in payload.get('evidence',[]))
    soup=BeautifulSoup(raw or '', 'html.parser')
    root=soup.select_one('#js_content')
    raw_kind='full_page' if root else 'converted_fragment' if raw and soup.find(['p','section']) and not soup.find('html') else 'unknown_page' if raw else 'missing'
    images=len(root.select('img,svg[data-lazy-bgimg]')) if root else 0
    status='unverified'
    if not root and re.search('环境异常|完成验证|请在微信客户端打开',soup.get_text()):status='blocked'
    elif images>=2 and len(text)<350:status='image_primary'
    elif not text.strip():status='missing'
    elif root and (re.search('余下全文|全文请.{0,10}(?:点击|阅读)|点击.{0,8}阅读剩余',text[-200:]) or any(re.search('下一页|下页',a.get_text()) for a in root.select('a[href]'))):status='partial'
    elif root and len(text)>=100:status='readable'
    reasons={'converted_fragment':'已保存上游转换的正文片段，尚缺完整原页的范围依据','missing':'当前版本缺少原始页面，不能确认正文范围','unknown_page':'未识别到可靠的原文正文容器','full_page':'已检查保存页面的文字容器，媒体内容另行核对'}
    return {'status':status,'label':LABELS[status],'characters':len(text),'images':images,'raw_kind':raw_kind,'reason':reasons[raw_kind],
            'featured_eligible':status=='readable','scope':'仅检查已保存文字，图片、音频及事实准确性未核验'}


def latest_quality(db: Session, material: LearningMaterial) -> dict[str,Any]:
    from app.models import MaterialRevision
    revision=db.query(MaterialRevision).filter_by(material_id=material.id).order_by(MaterialRevision.revision.desc()).first()
    return inspect(revision.snapshot['payload'] if revision else material.payload, revision.source_html if revision else None)
