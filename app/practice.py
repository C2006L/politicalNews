"""Version-bound recall cards; personal progress stays in the browser."""
from html import escape


def add_button(material_id: int, revision: int | None, kind: str, index: int) -> str:
    if revision is None:
        return ''
    return (f'<button type="button" data-practice-add data-material="{material_id}" '
            f'data-revision="{revision}" data-kind="{escape(kind, quote=True)}" data-index="{index}">加入复习</button>')
