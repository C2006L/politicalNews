from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.routers.learning import layout

router = APIRouter(tags=['资料导入'])


@router.get('/learning-import', response_class=HTMLResponse)
def import_page() -> HTMLResponse:
    base = escape(get_settings().base_path, quote=True)
    return layout('公众号资料导入', f'''<a href="{base}/learning">← 公众号研读</a>
<div class="eyebrow">SOURCE & IMPORT</div><h1>检查资料，再继续学习。</h1>
<p class="learning-notice">这里检查并导入 WeRSS 已保存的正文，不会替代上游采集、重新扫码或补齐公众号漏采文章。</p>
<section data-material-import data-base="{base}">
<form data-import-form><label>管理口令 <input data-import-token type="password" autocomplete="off" maxlength="128" required></label>
<button type="submit" data-import-button disabled>检查并导入一批</button></form>
<p><button type="button" data-upstream-button disabled>主动检查上游通道</button> <a href="http://127.0.0.1:8003" target="_blank" rel="noopener noreferrer">打开本机 WeRSS →</a></p>
<p>使用关联审核页设置的管理口令，或已配置的管理员令牌。口令不保存在浏览器。</p>
<p data-import-message role="status" aria-live="polite"></p><p data-upstream-message role="status" aria-live="polite"></p>
<p><a href="{base}/review">设置管理口令 →</a> · <a href="{base}/status">完整同步状态 →</a></p>
<h2>主动检查最新文章通道</h2><p data-upstream-state role="status">正在读取检查记录…</p>
<p data-upstream-time></p><div data-upstream-accounts></div>
<p>检查针对两个已订阅公众号的最新文章入口，不抓取正文。通过只表示检查时目标入口可访问，不等于登录永久有效，也不证明内容已采全。</p>
<p>超过两小时的结果会标为过时；主动检查至少间隔一分钟。</p>
<h2>上游文章库</h2>
<p data-source-state role="status"></p><div data-source-accounts></div>
<p>库文件可读不能证明授权有效；目前最新一篇通道仍可能漏掉多篇更新和历史文章，实时授权状态保持未确认。</p>
<section data-body-recovery data-base="{base}"><h2>恢复已知缺正文</h2>
<p>只处理两个已订阅账号中已经发现、但尚无正文的文章。每次最多 3 篇排队，逐篇重取；未知历史文章不会因此自动补齐。</p>
<p>沿用上方管理口令。任务已提交不代表正文已恢复，只有有效正文实际出现并导入后才显示完成。</p>
<p data-recovery-summary role="status" aria-live="polite">正在检查恢复队列…</p>
<p><button type="button" data-recovery-start disabled>将缺正文文章加入恢复队列</button> <button type="button" data-recovery-check disabled>检查恢复结果</button></p>
<p data-recovery-message role="status" aria-live="polite"></p><div data-recovery-list></div>
<p><button type="button" data-recovery-prev disabled>上一页</button> <span data-recovery-page></span> <button type="button" data-recovery-next disabled>下一页</button></p>
</section>
<h2>主项目整理进度</h2><p data-import-state role="status" aria-live="polite">正在读取状态…</p>
<p data-import-result></p><p data-import-scan></p><p data-import-time></p>
<p>每批最多检查 200 篇，会保存进度并继续检查剩余正文；一轮结束后重新检查已有内容，更正也会重新整理。断开页面不会清除进度。</p>
</section><script src="{base}/static/material_import.js" defer></script><script src="{base}/static/body_recovery.js" defer></script>''')
