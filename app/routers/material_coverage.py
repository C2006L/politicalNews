from html import escape
from typing import Any
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.material_coverage import coverage
from app.config import get_settings
from app.routers.learning import layout

router=APIRouter(tags=['公众号覆盖'])


@router.get('/api/material-coverage')
def coverage_api() -> dict[str, Any]:
    return coverage()


@router.get('/material-coverage')
def coverage_page() -> HTMLResponse:
    data=coverage();base=escape(get_settings().base_path,quote=True)
    body=f'<div class="eyebrow">COLLECTION LEDGER</div><h1>公众号收录覆盖</h1><p>近90天时政优先：{data["window"]["start"]} — {data["window"]["end"]}。本站保存 {data["total"]} 条文章记录，原页质量与去重后的链接清单另计，学习材料另列。</p><p class="report-scope">{escape(data["scope"])}</p><p><a href="{base}/learning">返回资料目录 →</a></p>'
    body+=f'<p><a href="{base}/material-quality">检查公众号正文质量 →</a> · <a href="{base}/news-quality">检查网站正文质量 →</a></p>'
    from app.database import SessionLocal
    from app.models import AppState
    import json
    with SessionLocal() as db:
        record=db.get(AppState,'official_websites_latest')
        try:website=json.loads(record.value) if record else {}
        except ValueError:website={}
    if website:
        body+='<section class="learning-entry"><h2>官方网站补充</h2><p>独立收录在时政资料库，不计入公众号篇数或历史覆盖率。每批有限额，并非近90天全量。</p>'
        for item in website.get('sources',[]):
            body+=f'<p><a href="{base}/source/{escape(item["source"])}">{escape(item["label"])} →</a> · 本批新增 {item["saved"]} 篇 · 已有 {item["existing"]} 篇 · 日期未知 {item["date_unknown"]} 篇 · 失败 {len(item["failed"])} 条</p>'
            if 'not_attempted' in item:
                body+=f'<p>已发现链接 {item["discovered"]} 条 · 本批请求 {item["attempted"]} 条 · 尚未尝试 {item["not_attempted"]} 条。这里只说明当前栏目清单的进度，不是全号90天覆盖率。</p>'
        body+='</section>'
    states={'verification_required':'遇到平台验证','partial':'仍有缺口','directory_checked':'本批目录已检查','unknown_directory':'未掌握完整目录'}
    ends={'declared_directory_covered':'目录链接已发现完，正文另行核对','directory_end':'已到公开目录末尾','repeated_or_empty_page':'返回重复或空页，仍需核查','repeated_cursor':'分页未前进','page_limit':'达到本批页数上限','ValueError':'页面需验证或目录无法解析'}
    for row in data['items']:
        body+=f'<article class="learning-entry"><h2>{escape(row["name"])}</h2><p>本站保存 {row["imported"]} 条文章记录 · {states[row["state"]]} · 全号历史总量未知</p>'
        body+=f'<p>页面元数据日期 {row["dated"]} 篇 · 近90天收录 {row["recent_90"]} 篇 · 其中时政初筛候选 {row["recent_affairs"]} 篇 · 日期待核实 {row["date_unknown"]} 篇</p>'
        if row['original_updates_pending']:
            body+=f'<p>上游片段有变化、原页待复查 {row["original_updates_pending"]} 篇；当前保留已存原页，不把片段变化当成原文已更新。</p>'
        collector=row.get('collector')
        if collector:
            collector_states={'authorization_required':'历史列表授权待恢复','authorization_or_limit':'历史通道受限，授权或配额原因待核实','access_restricted':'历史列表访问受限',
                              'history_partial':'历史列表补采进行中','window_scanned':'已扫描接口返回的本期列表',
                              'incremental_checked':'已检查最新列表','history_error':'历史补采未完成',
                              'cursor_reset':'分页顺序有变化，已回到起点复核','list_ready':'历史列表可访问，范围与正文另计'}
            label=collector_states.get(collector['status'],'等待历史列表验证')
            body+=f'<p>WeRSS主通道：{label} · 已发现 {collector["discovered"]} 条列表文章 · 正文待补 {collector["pending_bodies"]} 篇</p>'
            if collector.get('latest_fallback'):
                body+='<p>本批仅最新稿兜底可用，历史列表未完成；最新文章抓取成功不代表近90天补齐。</p>'
            if collector.get('retry_after'):
                body+='<p>采集已退避暂停并保存断点；不会仅凭同一个错误码要求反复扫码。</p>'
            if collector.get('complete'):
                body+='<p>这里只核对接口实际返回的列表范围，仍不等于已确认全号90天发布总量。</p>'
        history=row.get('history',{})
        if history.get('initialized'):
            body+=f'<p>持续补采清单：发现 {history["discovered"]} 条独立链接 · 其中正文可读 {history["body_available"]} 篇 · 近90天时政待补 {history["pending"]} 条 · 待重试 {history["retry"]} 条 · 需人工检查 {history["failed"]} 条</p>'
            body+=f'<p>学习材料另列 {history["learning"]} 条 · 超出90天 {history["outside_window"]} 条 · 发布日期待核实 {history["date_unknown"]} 条。以上维度可能交叉，不相加作为文章总量。</p>'
            if history.get('body_repair_pending'):
                body+=f'<p>已有资料待补原页 {history["body_repair_pending"]} 篇，独立于时政初筛；修复后仍按实际文章用途分流。</p>'
            if history['blocked']:
                body+='<p>该号遇到微信验证，已暂停请求并保存进度；需在本机完成验证后显式继续。其他公众号可独立补采。</p>'
            if history.get('body_needs_review'):
                body+=f'<p>已保存但正文范围需检查 {history["body_needs_review"]} 篇，不计为正文可读。</p>'
            if history['history_entry']['state']=='wechat_client_required':
                body+='<p>最近检查：全号历史入口要求在微信客户端打开，没有取得可核查的完整列表；公开合集和文章链接单独补采。</p>'
            directory_states={'complete':'已到该合集目录末尾，正文另计','pending':'等待继续翻页','retry':'等待重试','stalled':'分页未前进，需检查','count_mismatch':'已到末页但链接数少于声明，存在缺口','failed':'请求失败，需检查','verification_required':'等待平台验证'}
            for directory in history['directories']:
                total=directory['declared'] if directory['declared'] is not None else '未知'
                body+=f'<p class="report-scope">公开合集 {escape(directory["album"])}：声明 {total} 篇 · 已发现 {directory["discovered"]} 条 · 已读取 {directory["pages"]} 页 · {directory_states.get(directory["status"],"待检查")}</p>'
            body+='<p>清单会从新取得的正文继续发现历史链接，并保存分页进度。合集只覆盖其中一部分文章，尚未取得完整的全号历史分母，不能据此计算90天全号覆盖率。</p></article>'
            continue
        recent=row.get('recent_batch')
        if recent:
            body+=f'<p>近90天优先批次：发现目录链接 {recent["discovered"]} 条 · 初步候选 {recent["scope_candidates"]} 条 · 学习材料另列 {recent["deferred_learning"]} 条 · 明确非时政另列 {recent["deferred_non_affairs"]} 条 · 待补 {recent["remaining"]} 条。候选仍需正文内容评审。</p>'
        linked=row.get('linked_batch')
        if linked:
            body+=f'<p>正文内相关阅读：新发现 {linked["linked_discovered"]} 条同账号链接 · 新增正文 {linked["imported"]} 篇 · 尚未尝试 {linked["remaining"]} 条。链接中的刊载日期和内容范围仍需核实。</p>'
            if linked['blocked']:
                body+='<p>本批正文请求遇到微信验证页，已停止该号补采并保留失败链接；待采链接不计为已收录正文。</p>'
        if row['discovered'] is not None:
            body+=f'<p>本批发现 {row["discovered"]} 条独立文章链接 · 已有或本批成功 {row["covered_in_batch"]} 条 · 待补 {row["remaining"]} 条 · 失败 {row["failures"]} 条</p>'
        if row['history_access']=='wechat_client_required':
            body+='<p>历史入口要求在微信客户端打开，当前未取得可供自动核查的完整列表；近90天覆盖率尚不能计算。</p>'
        for directory in row['directories']:
            total=directory['declared'] if directory['declared'] is not None else '未知'
            body+=f'<p class="report-scope">公开合集 {escape(directory["album"])}：目录声明 {total} 篇，已发现 {directory["discovered"]} 条。{ends.get(directory["end"],"请求未完成，需核查")}</p>'
        body+='</article>'
    return layout('公众号收录覆盖',body)
