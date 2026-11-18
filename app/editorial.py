"""Explainable current-affairs selection. Classification never rewrites source text."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import News, LearningMaterial, ContentDecision

CATEGORIES = {'policy': '政策发布', 'report': '时政报道', 'analysis': '时政解读',
              'textbook': '教材背诵', 'exam_advice': '备考建议', 'promotion': '推广内容', 'uncertain': '待判断'}
METHOD = 'editorial-rules-2'


@dataclass
class Selection:
    category: str
    selected: bool
    reasons: list[str]
    evidence: list[dict[str, str]]
    score: int = 0
    manual: bool = False
    decision_id: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), 'label': CATEGORIES[self.category], 'method': METHOD}


def document(row: News | LearningMaterial) -> dict[str, str]:
    if isinstance(row, LearningMaterial):
        text = '\n'.join(line.get('text', '') for line in (row.payload or {}).get('evidence', []))
        return {'title': row.title, 'text': text, 'source': row.account, 'url': row.url}
    summary, content = (row.summary or '').strip(), (row.content or '').strip()
    if not content or content == row.title:
        text = summary
    elif not summary or summary == row.title or re.sub(r'\s+', '', summary) in re.sub(r'\s+', '', content):
        text = content
    else:
        text = summary + '\n' + content
    return {'title': row.title, 'text': text, 'source': row.source, 'url': row.link}


def fingerprint(row: News | LearningMaterial) -> str:
    return hashlib.sha256(json.dumps(document(row), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def classify(title: str, text: str, source: str = '', url: str = '') -> Selection:
    title = re.sub(r'\s+', ' ', title).strip()
    # The body is evidence, not executable instructions. Bound work on long pages.
    body = re.sub(r'\s+', ' ', text[:16000]).strip()
    evidence: list[dict[str, str]] = []
    def hit(pattern: str, value: str, field: str) -> bool:
        match = re.search(pattern, value, re.I)
        if match:
            evidence.append({'field': field, 'quote': value[max(0, match.start() - 25):match.end() + 70]})
        return match is not None
    # Strong teaching markers take precedence over a catchy current-affairs title.
    current_affairs_digest = bool(re.search(r'时政.{0,6}(?:总结|梳理|汇总|盘点)|(?:每月|每周|\d+月)时政', title))
    if (hit(r'背诵手册|带背\s*D?\d+|早鸟带背|考点(?:串讲|背诵)|教材(?:精讲|解读|背诵)', title, 'title') or
            not current_affairs_digest and hit(r'背诵知识点|点击(?:空白处|空格横线)查看答案|点击下方空白处获得答案|重温重要提法', body, 'text')):
        return Selection('textbook', False, ['包含明确的背诵或教材问答结构，保留在资料库'], evidence)
    journalistic = bool(re.search(r'调查|曝光|整治|查处|警惕.{0,15}诈骗', title))
    if not journalistic and hit(r'优惠码|限时抢购|课程(?:开售|优惠)|扫码(?:购买|领课)|免费领.*(?:真题|资料)|购课|直播带货', title, 'title'):
        return Selection('promotion', False, ['标题包含课程、资料领取或购买推广'], evidence)
    if hit(r'考研.*(?:订酒店|抢订|每天.*小时|复习安排|报名提醒)|考研人数|(?:8|八|4|四)套卷.*使用说明|考前心态|复习(?:计划|建议)|备考建议|弃考|想放弃', title, 'title'):
        return Selection('exam_advice', False, ['主要是备考方法或应试安排，不是时政事实整理'], evidence)
    if hit(r'研招网|考研报名|研究生报名|(?:考研|研招|报名|院校).{0,20}(?:材料审核|审核不通过|不合格名单|考场提醒)', title, 'title'):
        return Selection('exam_advice', False, ['研究生招考与报名提醒，不是时政报道'], evidence)
    if hit(r'(?:申论|国考|省考|公考|公务员).{0,25}(?:材料|答案|答题|备考|复习|技巧|真题)', title, 'title'):
        return Selection('exam_advice', False, ['主要是公考答题或备考方法，不是政策事实'], evidence)
    if hit(r'直播海报|先导片|视频预告|会议花絮', title, 'title'):
        return Selection('uncertain', False, ['属于海报或视频预告，暂不当作文字时政整理'], evidence)
    clean = re.sub(r'<[^>]*>', '', body)
    clean = clean.replace(title, '').strip()
    if len(clean) < 30:
        return Selection('uncertain', False, ['有效文字不足，暂不凭标题进入精选'], evidence)
    if url and (urlparse(url).scheme not in {'http', 'https'} or not urlparse(url).hostname):
        return Selection('uncertain', False, ['原文链接无法用于回查，暂不进入精选'], evidence)
    reasons = []
    score = 0
    official = source in {'gov_cn', 'mfa'}
    public_context = bool(re.search(r'APEC|亚太|政策|外交|国务院|改革|经济|两岸|联合国', title + ' ' + body[:500], re.I))
    lead = body[:650]
    # A publisher's name alone is never enough; require an explicit commentary
    # byline, a public issue, and an analytical judgement in the opening text.
    body_analysis = (bool(re.search(r'半月谈评论员|人民日报评论员|人民锐评|人民时评|新华社政论', lead)) and
                     bool(re.search(r'治理|法规|从严治党|政策|民生|乡村|教育|法治|改革|公共服务|亚太|APEC', lead)) and
                     hit(r'应当|必须|需要|不能|破除|关键|背道而驰|治理|坚持|推动', lead, 'text'))
    analysis = (hit(r'评论员|人民锐评|人民时评|半月谈评论|解读|述评|时政观察|时政.*(?:总结|梳理)|新闻分析|政策解读', title, 'title') or
                body_analysis or
                source in {'banyuetan_web','people_opinion_web'} and bool(re.search(r'政策|治理|民生|政绩|纪律|改革|乡村|经济|发展|干部',title)) or
                public_context and hit(r'【[^】]{0,10}观察】|东西问|瞩望', title, 'title'))
    policy = hit(r'(?:发布|印发|出台|实施|施行|审议通过|公布).{0,35}(?:政策|条例|办法|方案|意见|规定|法律|规划)|(?:条例|办法|规定).{0,12}(?:施行|实施)|补贴.{0,18}(?:品类|范围).{0,8}(?:扩容|新增|调整)', title, 'title')
    if not policy and not re.search(r'模拟|假设|如果|尚未|并未|建议.{0,20}(?:印发|发布)', lead[:180]):
        policy = hit(r'(?:国务院|中共中央|全国人大常委会|教育部|财政部|国家发展改革委).{0,35}(?:印发|发布|审议通过).{0,40}《[^》]{2,70}(?:条例|办法|方案|意见|规划|规定)》', lead, 'text')
    if analysis:
        category = 'analysis'
        score += 2
        reasons.append('属于时政解读或评论，按作者观点阅读')
    elif policy:
        category = 'policy'
        score += 5
        reasons.append('标题或正文开头包含发布主体、政策文件与明确动作，可查看原文举措')
    elif (hit(r'会晤|会见|出席|峰会|会议|发布|公布|启动|审议|同比|增长|投产|建成|签署|讲话|指示|全会|发射|调查|曝光|整治|查处|护航|开市.*周年|总市值|发表.{0,30}重要文章', title, 'title') or
          not re.search(r'模拟|假设|如果', lead[:180]) and
          hit(r'习近平.{0,80}(?:作出重要指示|考察时强调|重要讲话|出席)', lead, 'text') or
          source in {'gov_cn', 'people_cn', 'xinhuanet', 'cctv', 'mfa', 'chinanews', 'qstheory', 'gmw', 'ce', 'dangyuan'} and
          hit(r'国务院.{0,30}(?:印发|发布|会议)|新华社.{0,80}(?:出席|会议|签署|公布|发布)|(?:论坛|会议).{0,20}(?:召开|举行)', lead, 'text')):
        category = 'report'
        score += 3
        reasons.append('标题或正文开头包含公共事件、政策行动或公开进展')
    else:
        return Selection('uncertain', False, ['未识别到足够明确的时政信息，仍可查看全部原文'], evidence)
    if official:
        score += 1
        reasons.append('来自政府或部委刊载来源，便于核对发布主体')
    reasons.append('已有文字导读或正文可供回查；未生成综合摘要')
    return Selection(category, True, reasons, evidence, score)


def decision_map(db: Session) -> dict[tuple[str, int, str], ContentDecision]:
    result: dict[tuple[str, int, str], ContentDecision] = {}
    for row in db.query(ContentDecision).order_by(ContentDecision.id.desc()):
        result.setdefault((row.kind, row.content_id, row.fingerprint), row)
    return result


def select_document(row: News | LearningMaterial, overrides: dict | None = None) -> Selection:
    data = document(row)
    result = classify(data['title'], data['text'], data['source'], data['url'])
    kind = 'material' if isinstance(row, LearningMaterial) else 'news'
    saved = (overrides or {}).get((kind, row.id, fingerprint(row)))
    if saved:
        result.decision_id = saved.id
        if saved.category is not None:
            result.category = saved.category
            result.selected = saved.category in {'policy', 'report', 'analysis'}
            result.score = {'policy':5, 'report':3, 'analysis':2}.get(saved.category, 0)
            result.manual = True
        if saved.selection != 'auto':
            result.selected = saved.selection == 'include'
            if saved.selection == 'include':
                result.score += 10
            result.manual = True
        if result.manual:
            result.reasons.insert(0, '人工调整：' + (saved.note or '按本地维护决定展示'))
    return result
