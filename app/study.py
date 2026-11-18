"""每日阅读视图：保守合并同标题稿件，用可解释规则组织真实新闻。"""
from __future__ import annotations

import re
import os
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Sequence
from urllib.parse import urlencode, urlparse

from app.models import News
from app.chrome import header, collection
from app.news_data import source_label
from app.editorial import Selection, select_document

TRACKS = {"all": "综合阅读", "postgrad": "考研政治", "civil": "公考常识", "essay": "申论面试"}
TOPICS = {
    "policy": ("政策制度", ("政策", "条例", "规定", "意见", "方案", "改革", "法治", "决定")),
    "economy": ("经济民生", ("经济", "就业", "金融", "消费", "养老", "乡村", "医保", "民生")),
    "science": ("科技教育", ("科技", "航天", "卫星", "科研", "教育", "创新", "人工智能")),
    "world": ("国际外交", ("外交", "联合国", "峰会", "国际", "会晤", "访问")),
    "theory": ("理论党建", ("党建", "思想", "理论", "全会", "党史", "学习", "中国式现代化")),
}
PREFERENCES = {"all": (), "postgrad": ("theory", "policy", "world"), "civil": ("science", "policy", "economy"), "essay": ("economy", "policy")}
GUIDES = {
    "all": "先核对事件时间、发布主体和主要举措，再回到原文确认重要表述。",
    "postgrad": "阅读时区分事实与评论，记录重要表述，尝试联系已学理论；关联需自行核对教材。",
    "civil": "关注发布机构、实施日期、适用范围与关键数字。易混表述请对照原文记忆。",
    "essay": "按“问题—措施—目标”做笔记，思考案例的适用条件，避免脱离语境套用。",
}


@dataclass
class Reading:
    articles: list[News]
    topic: str
    score: int
    reasons: list[str]
    selection: Selection
    event_id: str | None = None

    @property
    def item(self) -> News:
        return self.articles[0]

    @property
    def key(self) -> str:
        if self.event_id:
            return str(self.item.id)
        return str(min(article.id for article in self.articles))


def prepare_readings(items: Sequence[News], track: str = "all", topic: str = "all",
                     mode: str = 'all', overrides: dict | None = None,
                     confirmed_events: dict[int,str] | None = None, captures: dict | None = None) -> list[Reading]:
    """Same-title groups or confirmed events within the current date/filter only."""
    track = track if track in TRACKS else "all"
    groups: dict[tuple[str, str, str, bool], list[News]] = {}
    selections = {}
    labels = {}
    relevance = {}
    for item in items:
        if mode=='featured' and captures is not None and (item.id in captures or os.getenv('STRICT_TEXT_QUALITY')=='1'):
            from app.news_quality import capture_status
            if capture_status(item,captures)['status']!='extracted':continue
        decision = select_document(item, overrides)
        selections[item.id] = decision
        if mode == 'featured' and not decision.selected:
            continue
        if mode == 'featured' and track in {'postgrad','civil','essay'}:
            from app.exam_profiles import assess
            from app.news_quality import capture_status
            match = assess(item, 'civil' if track=='essay' else track, overrides,
                           capture_status(item,captures) if captures is not None else None)
            if not match['eligible']:
                continue
            relevance[item.id] = match
        label = next((key for key, (_, words) in TOPICS.items() if any(word in item.title+' '+item.category for word in words)), 'other')
        labels[item.id] = label
        if topic!='all' and label!=topic:continue
        normalized = re.sub(r"[\W_]+", "", item.title).lower()
        event_id=(confirmed_events or {}).get(item.id)
        groups.setdefault((item.published_at.date().isoformat(), 'event:'+event_id if event_id else normalized,
                           '' if event_id else decision.category, decision.selected), []).append(item)
    result = []
    for articles in groups.values():
        articles.sort(key=lambda article: ({'policy':0,'report':1,'analysis':2}.get(selections[article.id].category,3),
                                          article.source != "gov_cn", article.id))
        item = articles[0]
        label = labels[item.id]
        decision = selections[item.id]
        score = max(selections[a.id].score + (3 if labels[a.id] in PREFERENCES[track] else 0) for a in articles)
        reasons = list(decision.reasons)
        if item.id in relevance:
            score = max(relevance[a.id]['score'] + (10 if selections[a.id].manual else 0) for a in articles)
            reasons.insert(0,relevance[item.id]['reason'])
        if label in PREFERENCES[track]:
            reasons.append("主题与所选备考方向相关")
        event_id=(confirmed_events or {}).get(item.id)
        if event_id:reasons.append('按已确认的事件关联归并，仍保留每篇原文与观点')
        result.append(Reading(articles, label, score, reasons, decision,event_id))
    result.sort(key=lambda entry: (-entry.score, -entry.item.published_at.timestamp(), entry.item.id))
    return result


def href(base: str, path: str, **params: object) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return escape(base + path + ("?" + query if query else ""), quote=True)


def article_card(reading: Reading, index: int, track: str, base: str, event_hints: dict | None = None) -> str:
    item = reading.item
    label = TOPICS.get(reading.topic, ("综合动态", ()))[0]
    excerpt = re.sub(r"\s+", " ", item.summary or item.content or "").strip()
    if excerpt == item.title or not excerpt:
        excerpt = "暂未提取到有效导读，请打开原文阅读。"
    excerpt = excerpt[:220] + ("…" if len(excerpt) > 220 else "")
    sources = []
    for article in reading.articles:
        if urlparse(article.link).scheme not in {"http", "https"}:
            continue
        source_title=source_label(article.source)+(f' · {article.title} · 刊载 {article.published_at:%m月%d日}' if reading.event_id else '')
        original=f'<a href="{escape(article.link, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(source_title)} ↗</a>'
        if reading.event_id:
            original=f'<div>{original} · <a href="{href(base, f"/news/{article.id}")}">站内阅读 →</a></div>'
        sources.append(original)
    detail = href(base, f"/news/{item.id}")
    from app.editorial import CATEGORIES
    decision = reading.selection
    classification = ('人工调整 · ' if decision.manual else '') + CATEGORIES[decision.category]
    evidence = ''.join(f'<p><small>{"标题" if e["field"] == "title" else "正文"}</small> {escape(e["quote"])}</p>' for e in decision.evidence)
    hint=(event_hints or {}).get(item.id)
    event_html=''
    if hint:
        note=f'已确认同一事件 · 当前筛选 {len(reading.articles)} 篇' if reading.event_id else f'同一事件候选 · {hint["articles"]} 篇刊载待核对'
        event_html=f'<p class="event-hint"><a href="{href(base,"/events",event_id=hint["event_id"])}">{note} →</a></p>'
    grouping='同事件关系已确认，按当前日期与筛选合并；各篇观点分别保留。' if reading.event_id else '同日同标题稿件合并展示。'
    return f'''<article class="reading-card story" id="reading-{reading.key}" data-id="{reading.key}" data-title="{escape(item.title, quote=True)}" data-url="{detail}" data-date="{item.published_at:%Y-%m-%d}">
      <span class="ordinal number" aria-label="第{index}条">{index:02d}</span><div class="card-content">
      <div class="card-top meta"><span class="tag">{escape(label)}</span><span class="source-name">{escape(source_label(item.source))}</span><span class="card-date">{item.published_at:%m月%d日}</span><span class="read-label" hidden>已读</span></div>
      <h3><a href="{detail}">{escape(item.title)}</a></h3>
      <p class="excerpt">{escape(excerpt)}</p>
      <p class="curation-reason"><span>{escape(classification)}</span> · {"入选" if decision.selected else "未入选"}：{escape(reading.reasons[0])}</p>
      {event_html}
      <details class="evidence"><summary>{len(set(a.source for a in reading.articles))} 个刊载来源 · 查看依据</summary><p>{escape('；'.join(reading.reasons))}。{grouping}来源数不等于独立报道数。</p><div class="source-links">{' '.join(sources)}</div></details>
      <details class="classification-evidence"><summary>分类依据与纠正</summary>{evidence}<p>规则分类，不代表事实核验或命题预测。</p><a href="{href(base, '/curation', kind='news', item=item.id)}">查看这篇的分类，可纠正 →</a></details>
      <div class="card-actions actions"><button type="button" data-action="read" aria-pressed="false">标为已读</button><button type="button" data-action="save" aria-pressed="false">收藏</button><a class="detail-link read-source" href="{detail}">阅读全文 ↗</a></div>
      </div>
    </article>'''



def render_study(*, items: Sequence[News], today_items: Sequence[News], track: str, topic: str,
                 page: int, base: str, total: int, now: datetime, source: str | None = None,
                 learning_desk: str = '', mode: str = 'all', overrides: dict | None = None,
                 confirmed_events: dict[int,str] | None = None, event_hints: dict | None = None, captures: dict | None = None) -> str:
    pool = list(today_items)
    fallback = not pool
    if not pool and items:
        latest = max(item.published_at.date() for item in items)
        pool = [item for item in items if item.published_at.date() == latest]
    readings = prepare_readings(pool, track, topic, mode, overrides, confirmed_events,captures)
    eligible_count = len(readings)
    if mode == 'featured':
        shortlist = []
        topic_counts: dict[str, int] = {}
        for reading in readings:
            if topic_counts.get(reading.topic, 0) >= 2:
                continue
            shortlist.append(reading)
            topic_counts[reading.topic] = topic_counts.get(reading.topic, 0) + 1
            if len(shortlist) == 5:
                break
        readings = shortlist
    pages = max(1, (len(readings) + 9) // 10)
    page = min(max(page, 1), pages)
    current = readings[(page - 1) * 10:page * 10]
    track_links = ''.join(f'<a class="track {"active" if key == track else ""}" {"aria-current=\"page\"" if key == track else ""} href="{href(base, "/today", track=key, topic=topic, source=source, mode=mode)}">{name}</a>' for key, name in TRACKS.items())
    topics = {"all": "全部主题", **{key: value[0] for key, value in TOPICS.items()}, "other": "综合动态"}
    topic_links = ''.join(f'<a class="topic {"active" if key == topic else ""}" href="{href(base, "/today", track=track, topic=key, source=source, mode=mode)}">{name}</a>' for key, name in topics.items())
    cards = ''.join(article_card(reading, (page - 1) * 10 + idx, track, base, event_hints) for idx, reading in enumerate(current, 1))
    if not cards:
        if total == 0:
            cards = ('<div class="empty-state"><h3>这里还没有阅读材料</h3>'
                     '<p>资料库尚未获取新闻。请先获取近期内容，再开始阅读。</p>'
                     f'<a href="{href(base, "/getting-started")}">获取第一批资料 →</a></div>')
        else:
            cards = ('<div class="empty-state"><h3>当前主题暂无阅读材料</h3>'
                     '<p>资料库已有内容，当前日期、分类或筛选条件没有匹配项；不会用背诵或推广凑满精选。</p>'
                     f'<a href="{href(base, "/today", track=track, topic="all", mode=mode)}">查看全部主题 →</a> · '
                     f'<a href="{href(base, "/today", track=track, topic=topic, source=source, mode="all")}">查看全部内容 →</a> · '
                     f'<a href="{href(base, "/archive")}">查看往期资料 →</a></div>')
    pager = ''.join(f'<a {"aria-current=\"page\"" if number == page else ""} href="{href(base, "/today", page=number, track=track, topic=topic, source=source, mode=mode)}">{number}</a>' for number in range(max(1, page - 2), min(pages, page + 2) + 1)) if pages > 1 else ''
    latest_text = max((item.published_at for item in pool), default=None)
    directory = ''.join(
        f'<li><a href="#reading-{reading.key}"><span>{(page - 1) * 10 + idx:02d}</span>'
        f'<em>{escape(reading.item.title)}</em></a></li>'
        for idx, reading in enumerate(current, 1)
    )
    substitutions = {
        'mode_links': ''.join(f'<a href="{href(base, "/today", track=track, topic=topic, source=source, mode=value)}" {"aria-current=page" if value == mode else ""}>{label}</a>' for value, label in [('featured', '时政初筛'), ('all', '全部收录')]),
        'selection_note': (f'从当前 {len(pool)} 篇收录中初筛出 {eligible_count} 组，本页保留 {len(readings)} 条；最多 5 条，同主题最多 2 条。初筛仅排除无关内容，备考重点请看方向简报。' if mode == 'featured' else '按原文查看全部收录，包含未通过初筛的条目；适合查漏和检索，不是必读清单。'),
        "learning_desk": learning_desk,
        "site_header": header(base, "today"), "site_library": collection(base),
        "base": escape(base, quote=True), "date": now.strftime("%Y.%m.%d"),
        "weekday": "星期" + "一二三四五六日"[now.weekday()], "tracks": track_links,
        "topics": topic_links, "cards": cards, "pager": pager,
        "track": TRACKS[track], "count": str(len(readings)), "total": str(total),
        "reading_date": latest_text.strftime("%Y.%m.%d") if latest_text else "等待采集",
        "section_title": "近期时政" if fallback else "今日时政",
        "notice": escape(("今日暂无更新，已显示最近时政。" if fallback else "") + (f"当前筛选来源：{source_label(source)}。" if source else "") + "同日同标题稿件合并展示；刊载来源数不等于独立报道数。"),
        "scope": "近期阅读" if fallback else "今日阅读",
        "guide": GUIDES[track],
        "edition_month": now.strftime("%Y / %m"),
        "edition_day": now.strftime("%d"),
        "edition_weekday": "星期" + "一二三四五六日"[now.weekday()],
        "edition_label": "北京时间",
        "calendar_day": now.strftime('%Y-%m-%d'),
        "freshness": (f'今日暂无入库内容，当前阅读 {latest_text:%m月%d日} 的最近资料。' if fallback and latest_text else
                      '资料库正在等待首次获取。' if not latest_text else '今日资料 · 内容入库后自动更新。'),
        "directory": directory or '<li class="directory-empty">暂无阅读材料</li>',
        "page_count": str(len(current)),
    }
    return Template((Path(__file__).parent / "templates" / "study.html").read_text(encoding="utf-8")).substitute(substitutions)
