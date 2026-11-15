"""Shared navigation and browser-local collection UI for every page."""
from html import escape


def header(base: str, active: str) -> str:
    base = escape(base, quote=True)
    tabs = [("today", "/today", "每日时政"), ("archive", "/archive", "往期回顾"),
            ("categories", "/categories", "专题资料"), ("learning", "/learning", "公众号研读")]
    links = ''.join(
        f'<a class="{"active" if key == active else ""}" '
        f'{"aria-current=\"page\"" if key == active else ""} href="{base}{path}">{label}</a>'
        for key, path, label in tabs)
    return f'''<header class="mast site-mast"><a class="brand" href="{base}/"><b aria-hidden="true">时</b>时政自习室</a>
<nav aria-label="主导航">{links}</nav><div class="mast-tools"><a href="{base}/search" aria-label="统一检索">检索</a><button class="library-toggle collection" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="library">我的收藏 <span data-save-count>0</span></button></div></header><script src="{base}/static/practice.js" data-practice-script data-base="{base}" defer></script>'''


def collection(base: str = '') -> str:
    return f'''<dialog id="library" class="library-dialog" aria-labelledby="library-title">
<div class="library-heading"><div><span class="eyebrow">SAVED FOR LATER</span><h2 id="library-title">我的收藏</h2></div><button type="button" data-close-library aria-label="关闭收藏">关闭 ×</button></div>
<p class="library-note">保存在当前浏览器，方便下次回看。</p><p><a href="{escape(base, quote=True)}/practice">我的复习 →</a></p><ul data-saved-list></ul>
<details class="collection-backup"><summary>备份收藏与已读</summary>
<p>只在本机处理，不上传。复习卡和回想安排请到“我的复习”单独备份。恢复只补缺项，保留本机同编号收藏。</p>
<button type="button" data-collection-export>导出收藏与已读</button>
<p><label>选择备份 <input type="file" accept=".json,application/json" data-collection-file></label></p>
<p data-collection-preview role="status" aria-live="polite"></p><button type="button" data-collection-restore disabled>确认合并恢复</button>
<p data-collection-message role="status" aria-live="polite"></p>
<p>仅用于同一资料库迁移浏览器。换资料库后编号可能对应不同文章，恢复前请核对；文件不包含新闻正文、管理口令或公众号授权。</p>
</details></dialog><div class="toast" role="status" aria-live="polite" hidden></div>
<script src="{escape(base, quote=True)}/static/collection_backup.js"></script>'''


def bookmark(key: str, title: str, url: str, date: str = "") -> str:
    return (f'<button class="bookmark-button" type="button" data-save-id="{escape(key, quote=True)}" '
            f'data-title="{escape(title, quote=True)}" data-url="{escape(url, quote=True)}" '
            f'data-date="{escape(date, quote=True)}" aria-pressed="false">收藏</button>')
