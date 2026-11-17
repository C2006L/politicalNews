"""Typed extractive reading hints; exact source lines, no generated conclusions."""
import re
from typing import Any
from app.editorial import classify


def readout(material: dict[str, Any]) -> list[dict[str, str]]:
    lines = material.get('evidence') or []
    text = '\n'.join(line['text'] for line in lines)
    category = classify(material['title'], text, material.get('account',''), material['url']).category
    if category not in {'policy','report','analysis'}:
        return []
    patterns = ([('适用对象与条件',r'适用于|适用范围|个人消费者|对象为|须在|范围内'),
                 ('措施与关键数字',r'补贴|明确|要求|印发|措施|\d+(?:\.\d+)?[%％亿元万]'),
                 ('时间与安排',r'施行|实施日期|活动时间|截止|截至|\d+月\d+日')]
                if category=='policy' else [('作者观点（原文）',r'认为|应当|应该|关键|必须|需要'),
                 ('论据与案例（原文）',r'例如|比如|案例|调查|据|\d+[%％]')]
                if category=='analysis' else [('事件与进展（原文）',r'发布|印发|召开|举行|完成|取得|成功'),
                 ('时间与数字（原文）',r'\d+月\d+日|\d+(?:\.\d+)?[%％亿元万]')])
    result=[];used=set()
    for label,pattern in patterns:
        line=next((x for x in lines if x['id'] not in used and len(x['text'])>=15 and re.search(pattern,x['text'])),None)
        if line:
            used.add(line['id']);result.append({'label':label,'quote':line['text'],'evidence_id':line['id']})
    return result