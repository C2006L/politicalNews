"""时政资料库 Web 页面。"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, date
import hashlib
from html import escape
import json
import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from app.config import get_settings
from app.models import News
from app.study import render_study
from app.database import SessionLocal
from app.editorial import decision_map
from app.curated_materials import render_material_notes
from app.events import confirmed_event_map, event_hint_map
from app.chrome import header, collection, bookmark
from app.news_knowledge import render_news_points

# 子路径部署前缀（如 /news），由环境变量 BASE_PATH 提供
BASE_PATH = get_settings().base_path
from app.news_data import (
    CATEGORY_DEFINITIONS,
    category_from_slug,
    category_label,
    count_news_records,
    get_news_by_id,
    get_year_counts,
    group_by_month,
    query_news,
    source_label,
    source_trust_label,
    source_trust_note,
    today_news,
    yesterday_news,
)
from app.search_index import get_hot_keywords
from app.sync_service import get_app_state, get_sync_status

router = APIRouter(tags=["页面"])

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
MIN_FILTER_YEAR = 2025
ITEMS_PER_PAGE = 8
MONTHS_PER_PAGE = 3
TRUSTED_SOURCE_ORDER = (
    "gov_cn",
    "people_cn",
    "xinhuanet",
    "cctv",
    "mfa",
    "chinanews",
    "qstheory",
    "gmw",
    "ce",
    "dangyuan",
)


def _filter_items_by_source(items: Sequence, selected_source: Optional[str]) -> List:
    if not selected_source:
        return list(items)
    return [item for item in items if item.source == selected_source]


def _source_counts(items: Sequence) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        source = item.source or "unknown"
        counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items(), key=lambda entry: (-entry[1], source_label(entry[0]))))


def _category_counts(items: Sequence) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        label = category_label(getattr(item, "category", None))
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda entry: (-entry[1], entry[0])))


def _filter_items_by_category(items: Sequence, selected_category: Optional[str]) -> List:
    if not selected_category:
        return list(items)
    normalized = category_label(selected_category)
    return [item for item in items if category_label(getattr(item, "category", None)) == normalized]


def _highlight_text(text: str, keyword: Optional[str]) -> str:
    safe_text = escape(text or "")
    needle = (keyword or "").strip()
    if not needle:
        return safe_text

    escaped_needle = escape(needle)
    pattern = re.compile(re.escape(escaped_needle), re.IGNORECASE)
    return pattern.sub(lambda match: f"<mark>{match.group(0)}</mark>", safe_text)


def _source_hostname(link: str) -> str:
    hostname = urlparse(link).hostname or ""
    return hostname.removeprefix("www.")


def _render_source_signature(item: News) -> str:
    source = escape(source_label(item.source))
    category = escape(category_label(getattr(item, "category", None)))
    trust = escape(source_trust_label(item.source))
    hostname = escape(_source_hostname(item.link))
    return (
        "<div class='meta-cluster'>"
        f"<span class='meta-pill source'>{source}</span>"
        f"<span class='meta-pill category'>{category}</span>"
        f"<span class='meta-pill trust'>{trust}</span>"
        f"<span class='meta-pill domain'>{hostname}</span>"
        "</div>"
    )


def _paginate_sequence(items: Sequence, page: int, page_size: int) -> Tuple[Sequence, int, int]:
    if not items:
        return items, 1, 1

    total_pages = max(1, math.ceil(len(items) / page_size))
    current_page = min(max(page, 1), total_pages)
    start = (current_page - 1) * page_size
    end = start + page_size
    return items[start:end], current_page, total_pages


def _build_href(path: str, **params: Optional[object]) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "", False)}
    prefixed = f"{BASE_PATH}{path}"
    if not clean:
        return prefixed
    return f"{prefixed}?{urlencode(clean)}"


def _render_pager(path: str, current_page: int, total_pages: int, **params: Optional[object]) -> str:
    if total_pages <= 1:
        return ""

    links = []
    if current_page > 1:
        links.append(
            f"<a class='pager-link' href='{_build_href(path, page=current_page - 1, **params)}'>上一页</a>"
        )

    start = max(1, current_page - 2)
    end = min(total_pages, current_page + 2)
    for page in range(start, end + 1):
        class_name = "pager-link current" if page == current_page else "pager-link"
        links.append(
            f"<a class='{class_name}' href='{_build_href(path, page=page, **params)}'>{page}</a>"
        )

    if current_page < total_pages:
        links.append(
            f"<a class='pager-link' href='{_build_href(path, page=current_page + 1, **params)}'>下一页</a>"
        )
    return "<nav class='pager'>" + "".join(links) + "</nav>"


def _render_news_card(item: News, keyword: Optional[str] = None) -> str:
    title = _highlight_text(item.title, keyword)
    link = escape(item.link)
    published = escape(item.published or item.published_at.strftime("%Y-%m-%d"))
    excerpt = _highlight_text((item.summary or item.content or item.title or "")[:180], keyword)
    return (
        "<article class='news-card'>"
        f"<div class='news-meta'><span>{published}</span><span>站内阅读</span></div>"
        f"{_render_source_signature(item)}"
        f"<h4><a href='{BASE_PATH}/news/{item.id}'>{title}</a></h4>"
        f"<p>{excerpt}</p>"
        "<div class='card-actions'>"
        f"<a class='inline-link' href='{BASE_PATH}/news/{item.id}'>站内阅读</a>"
        f"{bookmark(str(item.id), item.title, f'{BASE_PATH}/news/{item.id}', item.published_at.strftime('%Y-%m-%d'))}"
        f"<a class='inline-link' href='{link}' target='_blank' rel='noreferrer'>原站原文</a>"
        "</div>"
        "</article>"
    )


def _render_news_stream(items: Sequence[News], empty_text: str, keyword: Optional[str] = None) -> str:
    if not items:
        return f"<div class='empty-state'>{escape(empty_text)}</div>"
    return "".join(_render_news_card(item, keyword=keyword) for item in items)


def _render_scroll_shell(content: str, variant: str = "stream") -> str:
    return f"<div class='scroll-shell {variant}'>{content}</div>"


def _render_category_shelves(items: Sequence, limit_categories: int = 4, per_category: int = 4) -> str:
    grouped: Dict[str, List] = {}
    for item in items:
        label = category_label(getattr(item, "category", None))
        grouped.setdefault(label, []).append(item)

    sections = []
    for label, slug in ((label, slug) for slug, label in CATEGORY_DEFINITIONS.items()):
        bucket = grouped.get(label, [])
        if not bucket:
            continue
        sections.append(
            "<section class='panel'>"
            "<div class='panel-head'>"
            f"<div><h2>{escape(label)}</h2><div class='panel-subtitle'>按专题聚合近两年内容，减少用户在不同站点之间来回跳。</div></div>"
            f"<a class='inline-link' href='{BASE_PATH}/category/{slug}'>进入专题</a>"
            "</div>"
            + _render_scroll_shell(
                "".join(_render_news_card(item) for item in bucket[:per_category]),
                variant="shelf",
            )
            + "</section>"
        )
        if len(sections) >= limit_categories:
            break
    return "".join(sections)


def _render_recent_updates(items: Sequence[News], empty_text: str) -> str:
    if not items:
        return f"<div class='empty-state'>{escape(empty_text)}</div>"

    blocks = []
    for item in items[:6]:
        published = escape(item.published or item.published_at.strftime("%Y-%m-%d"))
        blocks.append(
            "<article class='mini-card'>"
            f"<div class='mini-date'>{published} · {escape(source_label(item.source))}</div>"
            f"<h4><a href='{BASE_PATH}/news/{item.id}'>{escape(item.title)}</a></h4>"
            f"<p>{escape((item.summary or item.content or item.title or '')[:110])}</p>"
            "</article>"
        )
    return "".join(blocks)


def _render_month_groups(groups: "OrderedDict[str, List]", keyword: Optional[str] = None) -> str:
    if not groups:
        return "<div class='empty-state'>当前时间范围内没有可展示的按月归档内容。</div>"

    sections = []
    for month_label, items in groups.items():
        sections.append(
            "<details class='month-block'>"
            f"<summary class='month-title'><span>{escape(month_label)}</span><strong>{len(items)} 条</strong></summary>"
            f"<div class='month-body'>{''.join(_render_news_card(item, keyword=keyword) for item in items)}</div>"
            "</details>"
        )
    return "".join(sections)


def _visible_years(year_counts: Dict[int, int], current_year: int, selected_year: Optional[int] = None) -> List[int]:
    years = set(range(current_year, MIN_FILTER_YEAR - 1, -1))
    years.update(year for year in year_counts if year >= MIN_FILTER_YEAR)
    if selected_year:
        years.add(selected_year)
    return sorted((year for year in years if year >= MIN_FILTER_YEAR), reverse=True)


def _render_year_select(year_counts: Dict[int, int], current_year: int, selected_year: Optional[int]) -> str:
    effective_year = selected_year or current_year
    options = []
    for year in _visible_years(year_counts, current_year, effective_year):
        selected = " selected" if year == effective_year else ""
        count_text = f" ({year_counts.get(year, 0)})" if year_counts.get(year, 0) else ""
        options.append(f"<option value='{year}'{selected}>{year}年{count_text}</option>")
    return "".join(options)


def _render_source_select(source_counts: Dict[str, int], selected_source: Optional[str]) -> str:
    options = ['<option value="">全部来源</option>']
    sources = list(source_counts.keys())
    if selected_source and selected_source not in source_counts:
        sources.append(selected_source)

    for source in sources:
        selected = " selected" if source == selected_source else ""
        count_text = f" ({source_counts.get(source, 0)})"
        options.append(
            f"<option value='{escape(source)}'{selected}>{escape(source_label(source))}{count_text}</option>"
        )
    return "".join(options)


def _render_category_overview(category_counts: Dict[str, int]) -> str:
    if not category_counts:
        return "<div class='empty-state'>当前还没有形成可用的分类数据。</div>"

    cards = []
    for slug, label in CATEGORY_DEFINITIONS.items():
        count = category_counts.get(label, 0)
        cards.append(
            "<a class='category-card' href='{}'>"
            "<strong>{}</strong>"
            "<span>{} 条</span>"
            "</a>".format(_build_href(f"/category/{slug}"), escape(label), count)
        )
    return "<div class='category-grid'>" + "".join(cards) + "</div>"


def _render_source_overview(source_counts: Dict[str, int]) -> str:
    if not source_counts:
        return "<div class='empty-state'>当前还没有可展示的数据源概况。</div>"

    cards = []
    for source in TRUSTED_SOURCE_ORDER:
        count = source_counts.get(source, 0)
        cards.append(
            "<a class='category-card source-card' href='{}'>"
            "<strong>{}</strong>"
            "<span>{} 条</span>"
            "</a>".format(_build_href(f"/source/{source}"), escape(source_label(source)), count)
        )
    return "<div class='category-grid'>" + "".join(cards) + "</div>"


def _render_year_grid(year_counts: Dict[int, int], current_year: int) -> str:
    years = _visible_years(year_counts, current_year)
    if not years:
        return "<div class='empty-state'>当前还没有可展示的年份数据。</div>"

    cards = []
    for year in years:
        count = year_counts.get(year, 0)
        cards.append(
            "<a class='year-card' href='{}'>"
            "<strong>{}</strong>"
            "<span>{} 条时政</span>"
            "</a>".format(_build_href(f"/year/{year}"), year, count)
        )
    return "<div class='year-grid'>" + "".join(cards) + "</div>"


def _render_source_grid(source_counts: Dict[str, int], active_source: Optional[str], path: str, **params: object) -> str:
    if not source_counts and not active_source:
        return "<div class='empty-state'>当前还没有可展示的来源分布。</div>"

    chips = [
        "<a class='source-chip{}' href='{}'>全部来源</a>".format(
            " active" if not active_source else "",
            _build_href(path, source=None, **params),
        )
    ]

    for source, count in source_counts.items():
        chips.append(
            "<a class='source-chip{}' href='{}'>{} <span>{}</span></a>".format(
                " active" if source == active_source else "",
                _build_href(path, source=source, **params),
                escape(source_label(source)),
                count,
            )
        )
    return "<div class='source-grid'>" + "".join(chips) + "</div>"


def _render_sync_panel(task_status: Dict[str, object], last_sync_at: str, last_sync_result: str) -> str:
    status_label = "进行中" if task_status["in_progress"] else "空闲"
    scope = task_status["scope"] or "最近一次任务"
    message = task_status["message"] or "当前没有运行中的同步任务。"
    started_at = task_status["started_at"] or "暂无记录"
    finished_at = task_status["finished_at"] or "暂无记录"
    last_result = last_sync_result or "尚未有成功同步记录。"
    source_alerts = task_status.get("source_alerts") or []
    if isinstance(source_alerts, list):
        source_alerts = [str(item) for item in source_alerts if str(item).strip()]
    else:
        source_alerts = []
    source_alert_text = "；".join(source_alerts)
    critical_sources = task_status.get("critical_sources") or []
    if isinstance(critical_sources, list):
        critical_sources = [str(item) for item in critical_sources if str(item).strip()]
    else:
        critical_sources = []
    critical_text = "、".join(source_label(source) for source in critical_sources)
    busy_class = "status-badge busy" if task_status["in_progress"] else "status-badge"
    sync_admin_token = get_settings().sync_admin_token
    action_html = (
        """
      <div class="actions compact-actions">
        <form method="get" action="{BASE_PATH}/sync-view">
          <input type="hidden" name="months" value="24" />
          <button type="submit">同步近两年到数据库</button>
        </form>
        <form method="get" action="{BASE_PATH}/sync-view">
          <input type="hidden" name="year" value="{current_year}" />
          <button type="submit">同步本年</button>
        </form>
      </div>
        """.format(BASE_PATH=BASE_PATH, current_year=datetime.now(LOCAL_TZ).year)
        if not sync_admin_token
        else """
      <div class="notice compact">
        <strong>管理员提示：</strong>当前已启用同步令牌保护，公开页面只展示状态。手动同步请在服务器侧携带令牌调用接口。
      </div>
        """
    )

    return f"""
    <section class="panel" id="sync-panel">
      <div class="panel-head">
        <div>
          <h2>同步状态</h2>
          <div class="panel-subtitle">页面会自动刷新这里的状态，不需要再跳去纯 JSON 接口。</div>
        </div>
        <span class="{busy_class}" id="sync-badge">{escape(status_label)}</span>
      </div>
      <div class="sync-grid">
        <div class="sync-item"><strong>任务范围</strong><span id="sync-scope">{escape(str(scope))}</span></div>
        <div class="sync-item"><strong>最近同步</strong><span id="sync-last-at">{escape(last_sync_at)}</span></div>
        <div class="sync-item"><strong>开始时间</strong><span id="sync-started-at">{escape(str(started_at))}</span></div>
        <div class="sync-item"><strong>结束时间</strong><span id="sync-finished-at">{escape(str(finished_at))}</span></div>
      </div>
      <div class="notice compact" id="sync-message"><strong>当前状态：</strong>{escape(str(message))}</div>
      <div class="notice compact" id="sync-last-result"><strong>最近结果：</strong>{escape(last_result)}</div>
      <div class="notice compact" id="sync-source-alerts"><strong>来源告警：</strong>{escape(source_alert_text or "无")}</div>
      <div class="notice compact" id="sync-critical-sources"><strong>连续异常来源：</strong>{escape(critical_text or "无")}</div>
      {action_html}
    </section>
    """


def _render_learning_status() -> str:
    from app.material_status import pipeline_status
    data = pipeline_status()
    imported = data["import"]
    acquired = data["acquisition"]
    labels = {"not_run": "尚未导入", "running": "导入中", "interrupted": "上次导入中断",
              "catching_up": "分批导入中，仍有待检查正文",
              "failed": "导入失败", "partial": "部分正文被拒绝", "updated": "已导入新内容",
              "no_new_materials": "本次无新增资料"}
    def display_time(value: object) -> str:
        if not value:
            return "暂无记录"
        try:
            return datetime.fromisoformat(str(value)).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S 北京时间")
        except ValueError:
            return "时间未确认"
    source_label_text = {"unknown": "采集实时状态未确认", "not_configured": "未配置资料源",
                         "source_unavailable": "资料源暂不可读"}.get(acquired["state"], "未确认")
    counts = imported.get("result", {})
    probe = data['upstream_check']
    probe_label = {'not_configured':'未连接检查接口', 'not_checked':'尚未主动检查', 'running':'正在检查',
                   'checked':'已完成上次检查', 'failed':'上次检查失败', 'interrupted':'上次检查中断'}.get(probe['state'], '未确认')
    if probe['state'] == 'checked' and not probe['fresh']:
        probe_label += '（结果已过时）'
    account_rows = ''.join(
        f'<div class="sync-item"><strong>{escape(a["name"])}</strong><span>已存 {a["articles"]} 篇 · 有文字 {a["with_text"]} 篇 · 待取正文 {a["awaiting_text"]} 篇</span></div>'
        for a in acquired["accounts"])
    return f'''<section class="panel" id="learning-pipeline"><h2>公众号采集与资料整理</h2>
<div class="sync-grid"><div class="sync-item"><strong>上游采集</strong><span>{source_label_text}</span></div>
<div class="sync-item"><strong>主项目导入</strong><span>{labels.get(imported["state"], "未知")}</span></div>
<div class="sync-item"><strong>最近导入检查</strong><span>{display_time(imported.get("finished_at"))}</span></div>
<div class="sync-item"><strong>最近成功检查</strong><span>{display_time(imported.get("last_success_at"))}</span></div>
<div class="sync-item"><strong>最近新增或更正</strong><span>{display_time(imported.get("last_changed_at"))}</span></div>
<div class="sync-item"><strong>主动通道检查</strong><span>{probe_label} · {display_time(probe.get('finished_at'))}</span></div>{account_rows}</div><p class="notice">采集通道目前仅保证尝试获取最新一篇，多篇更新和历史文章可能缺失。库文件可读不能证明授权有效，导入无新增不能证明公众号没有更新。主动检查仅针对最新文章入口，不验证正文和历史覆盖。</p>
<p class="panel-subtitle">本次新增 {counts.get("added", 0)} · 更新 {counts.get("updated", 0)} · 跳过 {counts.get("skipped", 0)} · 拒绝 {counts.get("rejected", 0)}（验证页 {counts.get("verification_required", 0)}，正文不足 {counts.get("insufficient_text", 0)}）</p>
<p class="panel-subtitle">文章来源时间尚未核实，导入时间不作为发布时间；图片与音频未完整处理。刷新页面可查看最新状态。</p><p><a href="{_build_href('/learning-import')}">检查导入进度或重试 →</a></p></section>'''


def _render_article_body(item: News) -> str:
    content = (item.content or "").strip()
    summary = (item.summary or "").strip()
    body_text = content or summary
    if not body_text:
        return "<div class='empty-state'>这条内容暂时没有抓到正文，建议点击原文查看。</div>"

    paragraphs = [segment.strip() for segment in re.split(r"[\r\n]+", body_text) if segment.strip()]
    if not paragraphs:
        paragraphs = [body_text]
    return "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)


def _render_quality_panel() -> str:
    chips = "".join(
        f"<span class='helper-chip'>{escape(source_label(source))} · {escape(source_trust_label(source))}</span>"
        for source in TRUSTED_SOURCE_ORDER
    )
    return (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>数据说明</h2><div class='panel-subtitle'>把可信度说清楚，比堆更多按钮更重要。</div></div></div>"
        "<div class='helper-list'>"
        "<div class='notice compact'><strong>白名单来源：</strong>当前纳入中国政府网、人民网、新华网、外交部、中国新闻网等公开权威来源。</div>"
        "<div class='notice compact'><strong>同步规则：</strong>链接域名必须匹配来源白名单，日期异常或正文过短的内容会被过滤。</div>"
        "<div class='notice compact'><strong>展示方式：</strong>站内只做原文摘录，不做 AI 改写，并始终保留原文链接。</div>"
        f"<div class='source-reference'>{chips}</div>"
        "</div>"
        "</section>"
    )


def _source_health_rows(items: Sequence) -> List[Dict[str, str]]:
    now = datetime.now(LOCAL_TZ)
    rows: List[Dict[str, str]] = []

    for source in TRUSTED_SOURCE_ORDER:
        source_items = [item for item in items if item.source == source]
        latest = max((item.published_at for item in source_items), default=None)
        count = len(source_items)

        if latest is None:
            freshness = "缺失"
            freshness_class = "stale"
            latest_text = "暂无数据"
        else:
            age_days = (now.date() - latest.date()).days
            if age_days <= 2:
                freshness = "正常"
                freshness_class = "healthy"
            elif age_days <= 7:
                freshness = "轻微延迟"
                freshness_class = "warm"
            else:
                freshness = "偏旧"
                freshness_class = "stale"
            latest_text = latest.strftime("%Y-%m-%d")

        rows.append(
            {
                "source": source_label(source),
                "trust": source_trust_label(source),
                "latest": latest_text,
                "count": str(count),
                "freshness": freshness,
                "freshness_class": freshness_class,
            }
        )
    return rows


def _render_source_health_panel(items: Sequence) -> str:
    rows = _source_health_rows(items)
    cards = []
    for row in rows:
        cards.append(
            "<article class='health-card'>"
            f"<div class='health-head'><strong>{escape(row['source'])}</strong><span class='health-badge {escape(row['freshness_class'])}'>{escape(row['freshness'])}</span></div>"
            f"<div class='health-meta'>{escape(row['trust'])}</div>"
            f"<div class='health-grid'><span>最近日期：{escape(row['latest'])}</span><span>收录条数：{escape(row['count'])}</span></div>"
            "</article>"
        )
    return (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>来源覆盖</h2><div class='panel-subtitle'>这里帮助你快速判断哪一路数据断流了、哪一路还是新鲜的。</div></div></div>"
        + "".join(cards)
        + "</section>"
    )


def _render_hot_keywords() -> str:
    """搜索页侧边栏：近期高频热词，点击即搜索。"""
    try:
        keywords = get_hot_keywords(limit=20)
    except Exception:
        keywords = []
    if not keywords:
        return ""
    chips = "".join(
        f"<a class='helper-chip' href='{_build_href('/search', q=kw)}'>{escape(kw)}</a>"
        for kw in keywords
    )
    return (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>近期热词</h2><div class='panel-subtitle'>最近 60 天标题里出现最多的话题，点击即可搜索。</div></div></div>"
        f"<div class='helper-list'>{chips}</div>"
        "</section>"
    )


def _render_nav(active_tab: str) -> str:
    tabs = [
        ("today", "/", "今日时政"),
        ("yesterday", "/yesterday", "昨日时政"),
        ("categories", "/categories", "分类专题"),
        ("sources", "/sources", "数据源"),
        ("archive", "/archive", "按月归档"),
        ("status", "/status", "同步状态"),
    ]

    links = []
    for key, href, label in tabs:
        class_name = "nav-link active" if key == active_tab else "nav-link"
        links.append(f"<a class='{class_name}' {"aria-current=\"page\"" if key == active_tab else ""} href='{BASE_PATH}{href}'>{label}</a>")
    return "".join(links)


def _render_layout(
    *,
    active_tab: str,
    hero_title: str,
    hero_text: str,
    stats: Iterable[Tuple[str, str]],
    main_html: str,
    side_html: str,
    year_counts: Dict[int, int],
    source_counts: Dict[str, int],
    current_year: int,
    selected_year: Optional[int] = None,
    selected_source: Optional[str] = None,
    search_query: str = "",
    page_title: Optional[str] = None,
    focused: bool = False,
) -> HTMLResponse:
    stats_html = "".join(
        f"<div class='stat'><strong>{escape(label)}</strong><span>{escape(value)}</span></div>"
        for label, value in stats
    )

    html = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <meta http-equiv="refresh" content="3600" />
      <title>{escape(page_title or hero_title)}</title>
      <link rel="stylesheet" href="{BASE_PATH}/static/study.css?v=4" />
      <link rel="stylesheet" href="{BASE_PATH}/static/reference.css?v=1" />
      <script src="{BASE_PATH}/static/study.js?v=4" defer></script>
    </head>
    <body class="reference-page">
      <a class="skip-link" href="#reference-content">跳到页面内容</a>
      {header(BASE_PATH, "categories" if active_tab == "categories" else "archive" if active_tab in {"archive", "years", "year"} else "today" if active_tab in {"today", "yesterday"} else "")}
      <main class="reference-shell" id="reference-content">
        <section class="hero">
          <div class="eyebrow">THE READING ARCHIVE</div>
          <h1>{escape(hero_title)}</h1>
          <p>{escape(hero_text)}</p>

          {'' if focused else f'<div class="stats">{stats_html}</div>'}
        </section>

        <section class="layout {'focused-layout' if focused else ''}">
          <div class="main-stack">{main_html}</div>
          <aside class="side-stack">{side_html}</aside>
        </section>
        <footer class="site-footer">
          <details class="maintenance-links"><summary>采集与来源管理</summary><a href="{BASE_PATH}/news-quality">正文质量</a> · <a href="{BASE_PATH}/sources">来源覆盖</a> · <a href="{BASE_PATH}/status">同步状态</a> · <a href="{BASE_PATH}/getting-started">资料准备</a></details>
          本网站内容仅供学习与信息参考，不构成任何官方立场或决策依据。所有时政信息均来自公开来源聚合展示，最终请以原始来源及官方发布为准。如有侵权或内容异议，请联系站点维护者处理。
        </footer>
      </main>

      {collection(BASE_PATH)}
      <script>
        async function refreshSyncStatus() {{
          const badge = document.getElementById('sync-badge');
          if (!badge) return;
          try {{
            const response = await fetch('/sync-status', {{ cache: 'no-store' }});
            if (!response.ok) return;
            const data = await response.json();
            const scope = document.getElementById('sync-scope');
            const lastAt = document.getElementById('sync-last-at');
            const startedAt = document.getElementById('sync-started-at');
            const finishedAt = document.getElementById('sync-finished-at');
            const message = document.getElementById('sync-message');
            const lastResult = document.getElementById('sync-last-result');
            const sourceAlerts = document.getElementById('sync-source-alerts');
            const criticalSources = document.getElementById('sync-critical-sources');

            const busy = Boolean(data.in_progress);
            badge.textContent = busy ? '进行中' : '空闲';
            badge.classList.toggle('busy', busy);
            if (scope) scope.textContent = data.scope || '最近一次任务';
            if (lastAt) lastAt.textContent = data.last_sync_at || {json.dumps(get_app_state("last_sync_at", "尚未同步"), ensure_ascii=False)};
            if (startedAt) startedAt.textContent = data.started_at || '暂无记录';
            if (finishedAt) finishedAt.textContent = data.finished_at || '暂无记录';
            if (message) message.innerHTML = '<strong>当前状态：</strong>' + (data.message || '当前没有运行中的同步任务。');
            if (lastResult) lastResult.innerHTML = '<strong>最近结果：</strong>' + (data.last_result || '尚未有成功同步记录。');
            if (sourceAlerts) {{
              const alerts = Array.isArray(data.source_alerts) ? data.source_alerts : [];
              sourceAlerts.innerHTML = '<strong>来源告警：</strong>' + (alerts.length ? alerts.join('；') : '无');
            }}
            if (criticalSources) {{
              const critical = Array.isArray(data.critical_sources) ? data.critical_sources : [];
              criticalSources.innerHTML = '<strong>连续异常来源：</strong>' + (critical.length ? critical.join('、') : '无');
            }}
          }} catch (error) {{
            console.warn('sync status refresh failed', error);
          }}
        }}

        refreshSyncStatus();
        setInterval(refreshSyncStatus, 10000);
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)


def _shared_sidebar(
    year_counts: Dict[int, int],
    current_year: int,
    recent_items: Sequence[News],
    source_counts: Dict[str, int],
    source_path: str,
    active_source: Optional[str] = None,
    **source_params: object,
) -> str:
    return (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>来源筛选</h2><div class='panel-subtitle'>只保留一个必要筛选，不再重复堆专题入口和来源入口。</div></div></div>"
        + _render_source_grid(source_counts, active_source, source_path, **source_params)
        + "</section>"
    )


@router.get("/latest")
async def latest_page(
    page: int = Query(default=1, ge=1),
    source: Optional[str] = Query(default=None),
) -> RedirectResponse:
    return RedirectResponse(url=_build_href("/archive", page=page, source=source), status_code=302)


@router.get("/", response_class=HTMLResponse)
@router.get("/today", response_class=HTMLResponse)
async def today_page(
    page: int = Query(default=1, ge=1),
    source: Optional[str] = Query(default=None),
    track: str = Query(default="all", pattern="^(all|postgrad|civil|essay)$"),
    topic: str = Query(default="all", pattern="^(all|policy|economy|science|world|theory|other)$"),
    mode: str = Query(default='featured', pattern='^(featured|all)$'),
) -> HTMLResponse:
    snapshot = study_snapshot(page, source, track, topic, mode=mode)
    return HTMLResponse(snapshot['html'], headers={'Cache-Control': 'no-store'})


def study_snapshot(page: int, source: Optional[str], track: str, topic: str, day: date | None = None,
                   mode: str = 'featured') -> dict[str, str]:
    now = datetime.now(LOCAL_TZ)
    if day is not None:
        now = now.replace(year=day.year, month=day.month, day=day.day)
    all_recent_items, _ = query_news(year=None, search=None, months=24)
    filtered = [item for item in _filter_items_by_source(all_recent_items, source) if item.published_at.date() <= now.date()]
    items = [item for item in filtered if item.published_at.date() == now.date()]
    with SessionLocal() as db:
        overrides = decision_map(db)
        notes = render_material_notes(db, BASE_PATH, overrides)
        confirmed_events = confirmed_event_map(db)
        event_hints = event_hint_map(db)
    html = render_study(
        items=filtered, today_items=items, track=track, topic=topic,
        page=page, base=BASE_PATH, total=count_news_records(),
        now=now, source=source, learning_desk=notes, mode=mode, overrides=overrides,
        confirmed_events=confirmed_events,event_hints=event_hints,
    )
    revision = hashlib.sha256(html.encode()).hexdigest()
    return {'day': now.date().isoformat(), 'revision': revision,
            'html': html.replace('data-live-revision=""', f'data-live-revision="{revision}"', 1)}


@router.get('/api/study/live')
def live_study(page: int = Query(1, ge=1, le=100000), source: str | None = Query(None, max_length=80),
               track: str = Query('all', pattern='^(all|postgrad|civil|essay)$'),
               topic: str = Query('all', pattern='^(all|policy|economy|science|world|theory|other)$'),
               day: date | None = None, mode: str = Query('featured', pattern='^(featured|all)$')) -> JSONResponse:
    return JSONResponse(study_snapshot(page, source, track, topic, day, mode), headers={'Cache-Control': 'no-store'})


@router.get("/yesterday", response_class=HTMLResponse)
async def yesterday_page(
    page: int = Query(default=1, ge=1),
    source: Optional[str] = Query(default=None),
) -> HTMLResponse:
    current_year = datetime.now(LOCAL_TZ).year
    all_recent_items, _ = query_news(year=None, search=None, months=24)
    source_counts = _source_counts(all_recent_items)
    filtered_recent_items = _filter_items_by_source(all_recent_items, source)
    items, title = yesterday_news(filtered_recent_items, limit=None)
    page_items, current_page, total_pages = _paginate_sequence(items, page, ITEMS_PER_PAGE)
    year_counts = get_year_counts(min_year=MIN_FILTER_YEAR)

    main_html = (
        "<section class='panel'>"
        f"<div class='panel-head'><div><h2>{escape(title)}</h2><div class='panel-subtitle'>单独拎出来做一页，方便回看昨天的重要信息。</div></div><span>{len(items)} 条</span></div>"
        + _render_scroll_shell(_render_news_stream(page_items, "昨天还没有抓取到时政内容。"))
        + _render_pager("/yesterday", current_page, total_pages, source=source)
        + "</section>"
    )

    return _render_layout(
        active_tab="yesterday",
        hero_title="昨日时政",
        hero_text="如果你是隔天集中看新闻，这页会比长首页更顺手。",
        stats=[
            ("昨日条数", str(len(items))),
            ("数据库总条数", str(count_news_records())),
            ("年份覆盖", f"{min(_visible_years(year_counts, current_year), default=current_year)}-{current_year}"),
            ("当前来源", source_label(source) if source else "全部来源"),
        ],
        main_html=main_html,
        side_html=_shared_sidebar(
            year_counts,
            current_year,
            all_recent_items,
            source_counts,
            "/yesterday",
            active_source=source,
        ),
        year_counts=year_counts,
        source_counts=source_counts,
        current_year=current_year,
        selected_year=current_year,
        selected_source=source,
        page_title="昨日时政",
    )


def _browse_source_form(path: str, counts: Dict[str, int], source: Optional[str], **params: object) -> str:
    hidden = ''.join(f'<input type="hidden" name="{escape(key)}" value="{escape(str(value), quote=True)}">'
                     for key,value in params.items() if value is not None)
    return (f'<form class="browse-source" action="{BASE_PATH}{path}" method="get">{hidden}'
            f'<label>来源 <select name="source">{_render_source_select(counts,source)}</select></label>'
            '<button type="submit">应用筛选</button>'
            f'<a href="{BASE_PATH}{path}">重置筛选</a></form>')


@router.get("/categories", response_class=HTMLResponse)
async def categories_page(
    page: int = Query(default=1, ge=1),
    source: Optional[str] = Query(default=None),
    category: str = Query(default="shizheng"),
) -> HTMLResponse:
    current_year = datetime.now(LOCAL_TZ).year
    all_items, _ = query_news(year=None, search=None, months=24)
    scoped = _filter_items_by_source(all_items, source)
    category_counts = _category_counts(scoped)
    selected_category = category_from_slug(category)
    selected_items = _filter_items_by_category(scoped, selected_category)
    category_sources = _source_counts(_filter_items_by_category(all_items,selected_category))
    source_counts = {key:category_sources.get(key,0) for key in _source_counts(all_items)}
    page_items, current_page, total_pages = _paginate_sequence(selected_items,page,ITEMS_PER_PAGE)
    choices = ''.join(
        f'<a href="{_build_href("/categories",category=slug,source=source)}" '
        f'{"aria-current=\"page\"" if label==selected_category else ""}>'
        f'<span>{escape(label)}</span><small>{category_counts.get(label,0)}</small></a>'
        for slug,label in CATEGORY_DEFINITIONS.items())
    controls = ('<section class="browse-controls" aria-label="专题与来源筛选">'
                '<p class="browse-label">按栏目浏览</p>'
                f'<nav class="browse-categories" aria-label="专题切换">{choices}</nav>'
                + _browse_source_form('/categories',source_counts,source,category=category) + '</section>')
    main_html = controls + (
        '<section class="panel" id="browse-results" tabindex="-1">'
        f'<div class="panel-head"><div><h2>{escape(selected_category)}专题</h2>'
        f'<div class="panel-subtitle">{escape(source_label(source) if source else "全部来源")} · 最近 24 个月 · 按刊载时间倒序</div></div>'
        f'<span>{len(selected_items)} 条</span></div>'
        + _render_news_stream(page_items,'当前专题和来源下没有文章。可切换专题或重置筛选。')
        + _render_pager('/categories',current_page,total_pages,source=source,category=category) + '</section>')
    return _render_layout(
        active_tab='categories',hero_title='专题资料',
        hero_text='按栏目找到文章，再按来源缩小范围。切换栏目时保留来源选择。',
        stats=[],main_html=main_html,side_html='',year_counts={},source_counts=source_counts,
        current_year=current_year,selected_source=source,page_title='专题资料',focused=True)


@router.get("/category/{slug}", response_class=HTMLResponse)
async def category_detail_page(
    slug: str,
    page: int = Query(default=1, ge=1),
    source: Optional[str] = Query(default=None),
) -> HTMLResponse:
    # Existing bookmarks open the same complete browsing interface.
    return await categories_page(page=page,source=source,category=slug)


@router.get("/sources", response_class=HTMLResponse)
async def sources_page() -> HTMLResponse:
    current_year = datetime.now(LOCAL_TZ).year
    recent_items, _ = query_news(year=None, search=None, months=24)
    source_counts = _source_counts(recent_items)
    year_counts = get_year_counts(min_year=MIN_FILTER_YEAR)

    main_html = (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>数据源</h2><div class='panel-subtitle'>来源页只负责展示站点覆盖和可信说明，不再混入专题入口。</div></div></div>"
        + _render_source_overview(source_counts)
        + "</section>"
        + _render_source_health_panel(recent_items)
    )

    return _render_layout(
        active_tab="sources",
        hero_title="数据源",
        hero_text="这里专门看来源覆盖、来源新鲜度和站点可信说明，不再和首页、专题页混在一起。",
        stats=[
            ("来源数量", str(len([count for count in source_counts.values() if count > 0]))),
            ("数据库总条数", str(count_news_records())),
            ("当前年份", str(current_year)),
            ("当前收录", str(len(recent_items))),
        ],
        main_html=main_html,
        side_html=_render_quality_panel(),
        year_counts=year_counts,
        source_counts=source_counts,
        current_year=current_year,
        selected_year=current_year,
        page_title="数据源",
    )


@router.get("/source/{source}", response_class=HTMLResponse)
async def source_detail_page(
    source: str,
    page: int = Query(default=1, ge=1),
) -> HTMLResponse:
    current_year = datetime.now(LOCAL_TZ).year
    all_items, _ = query_news(year=None, search=None, months=24, source=source)
    page_items, current_page, total_pages = _paginate_sequence(all_items, page, ITEMS_PER_PAGE)
    source_counts = _source_counts(all_items)
    recent_items, _ = query_news(year=None, search=None, months=24)
    year_counts = get_year_counts(min_year=MIN_FILTER_YEAR)

    main_html = (
        "<section class='panel'>"
        f"<div class='panel-head'><div><h2>{escape(source_label(source))}</h2><div class='panel-subtitle'>{escape(source_trust_note(source))}</div></div><span>{len(all_items)} 条</span></div>"
        + _render_scroll_shell(_render_news_stream(page_items, f"{source_label(source)} 暂时还没有收录内容。"))
        + _render_pager(f"/source/{source}", current_page, total_pages)
        + "</section>"
    )

    return _render_layout(
        active_tab="sources",
        hero_title=f"{source_label(source)}",
        hero_text="来源详情页帮助你判断单一权威来源的覆盖面和更新质量，也方便回看该站点的全部收录。",
        stats=[
            ("来源", source_label(source)),
            ("来源级别", source_trust_label(source)),
            ("收录条数", str(len(all_items))),
            ("数据库总条数", str(count_news_records())),
        ],
        main_html=main_html,
        side_html=_shared_sidebar(
            year_counts,
            current_year,
            recent_items,
            _source_counts(recent_items),
            f"/source/{source}",
            active_source=source,
        ),
        year_counts=year_counts,
        source_counts=source_counts if source_counts else _source_counts(recent_items),
        current_year=current_year,
        selected_year=current_year,
        selected_source=source,
        page_title=source_label(source),
    )


@router.get("/archive", response_class=HTMLResponse)
async def archive_page(
    page: int = Query(default=1, ge=1),
    source: Optional[str] = Query(default=None),
    year: Optional[int] = Query(default=None, ge=0, le=9999),
) -> HTMLResponse:
    year = year or None
    current_year = datetime.now(LOCAL_TZ).year
    all_recent_items, _ = query_news(year=None, search=None, months=24)
    source_counts = _source_counts(all_recent_items)
    recent_items = _filter_items_by_source(all_recent_items, source)
    if year is not None:
        recent_items = [item for item in recent_items if item.published_at.year == year]
        year_counts_by_source = _source_counts([item for item in all_recent_items if item.published_at.year == year])
        source_counts = {key:year_counts_by_source.get(key,0) for key in source_counts}
    groups = group_by_month(recent_items)
    entries = list(groups.items())
    page_entries, current_page, total_pages = _paginate_sequence(entries, page, MONTHS_PER_PAGE)
    visible_groups = OrderedDict(page_entries)
    year_counts = get_year_counts(min_year=MIN_FILTER_YEAR)

    year_options = '<option value="0">全部年份</option>' + ''.join(
        f'<option value="{value}"{" selected" if value == year else ""}>{value}年</option>'
        for value in sorted({item.published_at.year for item in all_recent_items} | ({year} if year else set()), reverse=True))
    controls = (f'<form class="browse-source archive-controls" action="{BASE_PATH}/archive" method="get">'
                f'<label>年份 <select name="year">{year_options}</select></label>'
                f'<label>来源 <select name="source">{_render_source_select(source_counts,source)}</select></label>'
                f'<button type="submit">应用筛选</button><a href="{BASE_PATH}/archive">重置筛选</a></form>')
    main_html = f'<p class="period-links"><a href="{BASE_PATH}/reports">每日整理稿 →</a> · <a href="{BASE_PATH}/reports?kind=weekly">每周整理稿 →</a></p>' + controls + (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>按月归档</h2><div class='panel-subtitle'>默认折叠显示，每页只看少量月份，避免页面过长。</div></div>"
        f"<span>{len(groups)} 个月</span></div>"
        + f'<p class="panel-subtitle">当前：{str(year)+"年" if year else "全部年份"} · {escape(source_label(source) if source else "全部来源")} · {len(recent_items)} 条</p>'
        + (_render_month_groups(visible_groups) if visible_groups else '<p class="empty-state">当前年份和来源下没有文章，可重置筛选。</p>')
        + _render_pager("/archive", current_page, total_pages, source=source,year=year)
        + "</section>"
    )

    return _render_layout(
        active_tab="archive",
        hero_title="往期回顾",
        hero_text="按时间找回已收录的文章。先选年份与来源，再展开月份阅读；范围为最近 24 个月。",
        stats=[
            ("归档月份", str(len(groups))),
            ("最近两年条数", str(len(all_recent_items))),
            ("数据库总条数", str(count_news_records())),
            ("当前来源", source_label(source) if source else "全部来源"),
        ],
        main_html=main_html,
        side_html='',
        year_counts=year_counts,
        source_counts=source_counts,
        current_year=current_year,
        selected_year=current_year,
        selected_source=source,
        page_title="按月归档",
        focused=True,
    )


@router.get("/years", response_class=HTMLResponse)
async def years_page() -> HTMLResponse:
    current_year = datetime.now(LOCAL_TZ).year
    year_counts = get_year_counts(min_year=MIN_FILTER_YEAR)
    recent_items, _ = query_news(year=None, search=None, months=24)
    source_counts = _source_counts(recent_items)

    main_html = (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>按年份浏览</h2><div class='panel-subtitle'>如果你想单独复习某一年，可以直接从这里进入对应页面。</div></div></div>"
        + _render_year_grid(year_counts, current_year)
        + "</section>"
    )

    return _render_layout(
        active_tab="archive",
        hero_title="按年份浏览",
        hero_text="年份入口保留为兼容页面，但主流程已经统一收敛到顶部搜索和归档页，减少重复按钮。",
        stats=[
            ("可查看年份", str(len(_visible_years(year_counts, current_year)))),
            ("最早年份", str(min(_visible_years(year_counts, current_year), default=current_year))),
            ("数据库总条数", str(count_news_records())),
            ("已收录条数", str(len(recent_items))),
        ],
        main_html=main_html,
        side_html=_shared_sidebar(
            year_counts,
            current_year,
            recent_items,
            source_counts,
            "/years",
        ),
        year_counts=year_counts,
        source_counts=source_counts,
        current_year=current_year,
        selected_year=current_year,
        page_title="按年份浏览",
    )


@router.get("/year/{year}", response_class=HTMLResponse)
async def year_detail_page(
    year: int,
    page: int = Query(default=1, ge=1),
    source: Optional[str] = Query(default=None),
) -> HTMLResponse:
    current_year = datetime.now(LOCAL_TZ).year
    all_items, _ = query_news(year=year, search=None, months=None)
    source_counts = _source_counts(all_items)
    items = _filter_items_by_source(all_items, source)
    page_items, current_page, total_pages = _paginate_sequence(items, page, ITEMS_PER_PAGE)
    year_counts = get_year_counts(min_year=MIN_FILTER_YEAR)
    recent_items, _ = query_news(year=None, search=None, months=24)

    main_html = (
        "<section class='panel'>"
        f"<div class='panel-head'><div><h2>{year} 年时政</h2><div class='panel-subtitle'>该页面只展示 {year} 年的数据库内容。</div></div><span>{len(items)} 条</span></div>"
        + _render_scroll_shell(_render_news_stream(page_items, f"{year} 年还没有同步到数据库。"))
        + _render_pager(f"/year/{year}", current_page, total_pages, source=source)
        + "</section>"
    )

    return _render_layout(
        active_tab="archive",
        hero_title=f"{year} 年时政",
        hero_text="这是年份独立页面。顶部搜索可以直接限定年份，所以这里更像按年复习时的直达页。",
        stats=[
            ("年份", str(year)),
            ("当年条数", str(len(all_items))),
            ("数据库总条数", str(count_news_records())),
            ("当前来源", source_label(source) if source else "全部来源"),
        ],
        main_html=main_html,
        side_html=_shared_sidebar(
            year_counts,
            current_year,
            recent_items,
            source_counts,
            f"/year/{year}",
            active_source=source,
        ),
        year_counts=year_counts,
        source_counts=source_counts,
        current_year=current_year,
        selected_year=year,
        selected_source=source,
        page_title=f"{year} 年时政",
    )


@router.get("/search", response_class=HTMLResponse)
def search_page(
    q: str = Query(default="", max_length=120),
    year: str = Query(default="", pattern=r"^(?:19[0-9]{2}|20[0-9]{2}|21[0-9]{2}|2200)?$"),
    source: Optional[str] = Query(default=None, max_length=80),
    page: int = Query(default=1, ge=1, le=100000),
    kind: str = Query(default="all", pattern="^(all|news|materials|knowledge)$"),
) -> HTMLResponse:
    from app.routers.search import search_page as unified_page, Kind
    from typing import cast
    return unified_page(q.strip(), cast(Kind, kind), page, int(year) if year else None, source)


@router.get("/news/{news_id}", response_class=HTMLResponse)
async def news_detail_page(news_id: int) -> HTMLResponse:
    item = get_news_by_id(news_id)
    if item is None:
        return HTMLResponse(
            content="<h1>未找到内容</h1><p>这条时政可能尚未同步，或者已经不存在。</p>",
            status_code=404,
        )

    current_year = datetime.now(LOCAL_TZ).year
    year_counts = get_year_counts(min_year=MIN_FILTER_YEAR)
    recent_items, _ = query_news(year=None, search=None, months=24)
    source_counts = _source_counts(recent_items)
    with SessionLocal() as db:
        hint = event_hint_map(db).get(item.id)
        overrides = decision_map(db)
        from app.models import AppState
        body_record = db.get(AppState, 'news_body:' + str(item.id))
        capture = json.loads(body_record.value) if body_record else {}
    import hashlib
    if capture.get('fingerprint') != hashlib.sha256((item.content or '').encode()).hexdigest():
        capture = {}
    capture_note = (f"已提取网页正文 {capture.get('paragraphs',0)} 段、{len(item.content or '')} 字，本次未主动截断；图片、音视频和动态内容不在文字提取范围内。"
                    if capture.get('status') == 'extracted' else
                    "这条旧收录的正文完整性尚未核实，可能只有节选；请勿把以下文字当作完整报道。")
    from app.editorial import select_document
    decision = select_document(item, overrides)
    related_items = [related for related in recent_items
                     if related.id != item.id and select_document(related, overrides).selected][:6]
    selection_note = (
        "<div class='notice'><strong>未入选时政精选：</strong>"
        f"{escape(decision.reasons[0])}。此页保留原始收录；原站栏目和媒体来源级别不代表本项目认为它是时政重点。"
        f"<a href='{_build_href('/today')}'>返回时政精选 →</a></div>"
        if not decision.selected else ""
    )
    event_note = (
        f"<p class='event-hint'><a href='{_build_href('/events', event_id=hint['event_id'])}'>对照这件事的其他报道与归并依据 →</a></p>"
        if hint else ""
    )
    main_html = (
        "<section class='panel article-shell'>"
        "<div class='panel-head'><div><h2>原文阅读</h2><div class='panel-subtitle'>核对报道内容与原文出处。</div></div></div>"
        f"{selection_note}"
        "<div class='article-meta-grid'>"
        f"<div class='sync-item'><strong>发布日期</strong><span>{escape(item.published or item.published_at.strftime('%Y-%m-%d'))}</span></div>"
        f"<div class='sync-item'><strong>来源</strong><span>{escape(source_label(item.source))}</span></div>"
        f"<div class='sync-item'><strong>来源级别</strong><span>{escape(source_trust_label(item.source))}</span></div>"
        f"<div class='sync-item'><strong>年份</strong><span>{item.year} 年</span></div>"
        f"<div class='sync-item'><strong>来源域名</strong><span>{escape(_source_hostname(item.link))}</span></div>"
        "</div>"
        "<div class='actions'>"
        f"{bookmark(str(item.id), item.title, f'{BASE_PATH}/news/{item.id}', item.published_at.strftime('%Y-%m-%d'))}"
        f"<a class='ghost-link' href='{escape(item.link)}' target='_blank' rel='noreferrer'>原站原文</a>"
        f"<a class='ghost-link' href='{_build_href(f'/year/{item.year}', source=item.source)}'>查看同年同来源</a>"
        "</div>"
        f"<div class='notice'><strong>正文采集状态：</strong>{escape(capture_note)} 完整信息请以原站原文为准。</div>"
        f"<div class='notice'><strong>摘要：</strong>{escape(item.summary or '暂无摘要')}</div>"
        f"<div class='notice'><strong>可信说明：</strong>{escape(source_trust_note(item.source))}</div>"
        f"{event_note}<div class='article-body'>{_render_article_body(item)}</div>"
        f"{render_news_points(item, BASE_PATH, details=True)}"
        "</section>"
    )

    side_html = (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>继续读时政</h2><div class='panel-subtitle'>近期符合时政筛选规则的其他报道。</div></div></div>"
        + _render_recent_updates(related_items, "当前没有可推荐的相关文章。")
        + "</section>"
        + _shared_sidebar(
            year_counts,
            current_year,
            recent_items,
            source_counts,
            f"/year/{item.year}",
            active_source=item.source,
        )
    )

    return _render_layout(
        active_tab="archive",
        hero_title=item.title,
        hero_text="保留收录原文；是否入选时政精选另按内容判断。",
        stats=[
            ("来源", source_label(item.source)),
            ("发布日期", item.published or item.published_at.strftime("%Y-%m-%d")),
            ("年份", f"{item.year} 年"),
            ("数据库总条数", str(count_news_records())),
        ],
        main_html=main_html,
        side_html=side_html,
        year_counts=year_counts,
        source_counts=source_counts,
        current_year=current_year,
        selected_year=item.year,
        selected_source=item.source,
        page_title=item.title,
    )


@router.get("/status", response_class=HTMLResponse)
async def status_page(sync_status: str = Query(default="")) -> HTMLResponse:
    current_year = datetime.now(LOCAL_TZ).year
    year_counts = get_year_counts(min_year=MIN_FILTER_YEAR)
    recent_items, _ = query_news(year=None, search=None, months=24)
    source_counts = _source_counts(recent_items)
    task_status = get_sync_status()
    last_sync_at = get_app_state("last_sync_at", "尚未同步")
    last_sync_result = sync_status or get_app_state("last_sync_result", "")

    main_html = _render_learning_status() + _render_sync_panel(task_status, last_sync_at, last_sync_result)
    side_html = (
        "<section class='panel'>"
        "<div class='panel-head'><div><h2>状态说明</h2><div class='panel-subtitle'>同步页只保留任务状态和告警，不再展示来源覆盖。</div></div></div>"
        "<div class='notice compact'><strong>当前用途：</strong>看任务是否在跑、最近一次结果、以及来源异常告警。</div>"
        "</section>"
    )

    return _render_layout(
        active_tab="status",
        hero_title="同步状态",
        hero_text="同步状态页只负责任务状态与异常告警，不承担来源覆盖和专题展示。",
        stats=[
            ("当前状态", "进行中" if task_status["in_progress"] else "空闲"),
            ("最近同步", last_sync_at),
            ("数据库总条数", str(count_news_records())),
            ("已收录条数", str(len(recent_items))),
        ],
        main_html=main_html,
        side_html=side_html,
        year_counts=year_counts,
        source_counts=source_counts,
        current_year=current_year,
        page_title="同步状态",
    )
