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
    body=f'<div class="eyebrow">COLLECTION LEDGER</div><h1>公众号收录覆盖</h1><p>近90天时政优先：{data["window"]["start"]} — {data["window"]["end"]}。当前已收录 {data["total"]} 篇正文，学习材料另列。</p><p class="report-scope">{escape(data["scope"])}</p><p><a href="{base}/learning">返回资料目录 →</a></p>'
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
        body+='</section>'
    states={'verification_required':'遇到平台验证','partial':'仍有缺口','directory_checked':'本批目录已检查','unknown_directory':'未掌握完整目录'}
    ends={'declared_directory_covered':'目录链接已发现完，正文另行核对','directory_end':'已到公开目录末尾','repeated_or_empty_page':'返回重复或空页，仍需核查','repeated_cursor':'分页未前进','page_limit':'达到本批页数上限','ValueError':'页面需验证或目录无法解析'}
    for row in data['items']:
        body+=f'<article class="learning-entry"><h2>{escape(row["name"])}</h2><p>本站正文 {row["imported"]} 篇 · {states[row["state"]]} · 全号历史总量未知</p>'
        body+=f'<p>页面元数据日期 {row["dated"]} 篇 · 近90天收录 {row["recent_90"]} 篇 · 其中时政初筛候选 {row["recent_affairs"]} 篇 · 日期待核实 {row["date_unknown"]} 篇</p>'
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
