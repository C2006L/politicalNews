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
<nav aria-label="主导航">{links}</nav><button class="library-toggle collection" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="library">我的收藏 <span data-save-count>0</span></button></header>'''


def collection() -> str:
    return '''<dialog id="library" class="library-dialog" aria-labelledby="library-title">
<div class="library-heading"><div><span class="eyebrow">SAVED FOR LATER</span><h2 id="library-title">我的收藏</h2></div><button type="button" data-close-library aria-label="关闭收藏">关闭 ×</button></div>
<p class="library-note">保存在当前浏览器，方便下次回看。</p><ul data-saved-list></ul>
</dialog><div class="toast" role="status" aria-live="polite" hidden></div>'''


def bookmark(key: str, title: str, url: str, date: str = "") -> str:
    return (f'<button class="bookmark-button" type="button" data-save-id="{escape(key, quote=True)}" '
            f'data-title="{escape(title, quote=True)}" data-url="{escape(url, quote=True)}" '
            f'data-date="{escape(date, quote=True)}" aria-pressed="false">收藏</button>')
