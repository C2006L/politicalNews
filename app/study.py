"""每日阅读视图：保守合并同标题稿件，用可解释规则组织真实新闻。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Sequence
from urllib.parse import urlencode, urlparse

from app.models import News
from app.news_data import source_label

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

    @property
    def item(self) -> News:
        return self.articles[0]

    @property
    def key(self) -> str:
        return str(min(article.id for article in self.articles))


def prepare_readings(items: Sequence[News], track: str = "all", topic: str = "all") -> list[Reading]:
    """仅合并同一天、去标点后标题相同的文章，不宣称识别了所有同事件报道。"""
    track = track if track in TRACKS else "all"
    groups: dict[tuple[str, str], list[News]] = {}
    for item in items:
        normalized = re.sub(r"[\W_]+", "", item.title).lower()
        groups.setdefault((item.published_at.date().isoformat(), normalized), []).append(item)
    result = []
    for articles in groups.values():
        articles.sort(key=lambda article: (article.source != "gov_cn", article.id))
        item = articles[0]
        # 只使用标题和已有分类，避免正文关键词偶然命中造成过度推断。
        text = item.title + " " + item.category
        label = next((key for key, (_, words) in TOPICS.items() if any(word in text for word in words)), "other")
        if topic != "all" and label != topic:
            continue
        score = 0
        reasons = []
        if label in PREFERENCES[track]:
            score += 3
            reasons.append("主题与所选备考方向相关")
        if label in {"policy", "theory"}:
            score += 2
            reasons.append("标题涉及政策制度或理论学习")
        if any(article.source in {"gov_cn", "mfa"} for article in articles):
            score += 1
            reasons.append("包含政府或部委网站刊载内容")
        if len({article.source for article in articles}) > 1:
            score += 1
            reasons.append("多个来源刊载同标题稿件")
        result.append(Reading(articles, label, score, reasons or ["近期公开报道，供拓展阅读"]))
    result.sort(key=lambda entry: (-entry.score, -entry.item.published_at.timestamp(), entry.item.id))
    return result


def href(base: str, path: str, **params: object) -> str:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return escape(base + path + ("?" + query if query else ""), quote=True)


def article_card(reading: Reading, index: int, track: str, base: str) -> str:
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
        sources.append(f'<a href="{escape(article.link, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(source_label(article.source))} ↗</a>')
    detail = href(base, f"/news/{item.id}")
    return f'''<article class="reading-card" data-id="{reading.key}" data-title="{escape(item.title, quote=True)}" data-url="{detail}" data-date="{item.published_at:%Y-%m-%d}">
      <span class="ordinal" aria-label="第{index}条">{index:02d}</span><div class="card-content">
      <div class="card-top"><span class="tag">{escape(label)}</span><span class="source-name">{escape(source_label(item.source))}</span><span class="card-date">{item.published_at:%m月%d日}</span><span class="read-label" hidden>已读</span></div>
      <h3><a href="{detail}">{escape(item.title)}</a></h3>
      <p class="excerpt">{escape(excerpt)}</p>
      <div class="card-bottom"><details class="evidence"><summary>{len(set(a.source for a in reading.articles))} 个刊载来源 · 查看依据</summary><p>{escape('；'.join(reading.reasons))}。同标题稿件合并，来源数不等于独立报道数。</p><div class="source-links">{' '.join(sources)}</div></details>
      <div class="card-actions"><button type="button" data-action="read" aria-pressed="false">标为已读</button><button type="button" data-action="save" aria-pressed="false">收藏</button><a class="detail-link" href="{detail}">阅读全文 →</a></div></div>
      </div>
    </article>'''



def render_study(*, items: Sequence[News], today_items: Sequence[News], track: str, topic: str,
                 page: int, base: str, total: int, now: datetime, source: str | None = None) -> str:
    pool = list(today_items)
    fallback = not pool
    if not pool and items:
        latest = max(item.published_at.date() for item in items)
        pool = [item for item in items if item.published_at.date() == latest]
    readings = prepare_readings(pool, track, topic)
    pages = max(1, (len(readings) + 9) // 10)
    page = min(max(page, 1), pages)
    current = readings[(page - 1) * 10:page * 10]
    track_links = ''.join(f'<a class="track {"active" if key == track else ""}" {"aria-current=\"page\"" if key == track else ""} href="{href(base, "/today", track=key, topic=topic, source=source)}">{name}</a>' for key, name in TRACKS.items())
    topics = {"all": "全部主题", **{key: value[0] for key, value in TOPICS.items()}, "other": "综合动态"}
    topic_links = ''.join(f'<a class="topic {"active" if key == topic else ""}" href="{href(base, "/today", track=track, topic=key, source=source)}">{name}</a>' for key, name in topics.items())
    cards = ''.join(article_card(reading, (page - 1) * 10 + idx, track, base) for idx, reading in enumerate(current, 1))
    if not cards:
        cards = '<div class="empty-state"><h3>这里还没有阅读材料</h3><p>可切换主题或备考方向。若资料库为空，请稍后刷新，或前往同步状态查看采集进度。</p></div>'
    pager = ''.join(f'<a {"aria-current=\"page\"" if number == page else ""} href="{href(base, "/today", page=number, track=track, topic=topic, source=source)}">{number}</a>' for number in range(max(1, page - 2), min(pages, page + 2) + 1)) if pages > 1 else ''
    latest_text = max((item.published_at for item in pool), default=None)
    substitutions = {
        "base": escape(base, quote=True), "date": now.strftime("%Y.%m.%d"),
        "weekday": "星期" + "一二三四五六日"[now.weekday()], "tracks": track_links,
        "topics": topic_links, "cards": cards, "pager": pager,
        "track": TRACKS[track], "count": str(len(readings)), "total": str(total),
        "reading_date": latest_text.strftime("%Y.%m.%d") if latest_text else "等待采集",
        "section_title": "近期时政" if fallback else "今日时政",
        "notice": escape(("今日暂无更新，已显示最近时政。" if fallback else "") + (f"当前筛选来源：{source_label(source)}。" if source else "") + "同日同标题稿件合并展示；刊载来源数不等于独立报道数。"),
        "scope": "近期阅读" if fallback else "今日阅读",
        "guide": GUIDES[track],
    }
    return Template((Path(__file__).parent / "templates" / "study.html").read_text(encoding="utf-8")).substitute(substitutions)
