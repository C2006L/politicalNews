"""Publisher commentary is a supplement to news, not a forced daily quota."""
from html import escape

from sqlalchemy.orm import Session

from app.editorial import CATEGORIES, select_document
from app.models import LearningMaterial
from app.material_quality import latest_quality


def render_material_notes(db: Session, base: str, overrides: dict, track: str = 'all') -> str:
    safe = escape(base, quote=True)
    rows = db.query(LearningMaterial).order_by(LearningMaterial.imported_at.desc(), LearningMaterial.id.desc()).all()
    selected = []
    counts = dict.fromkeys(CATEGORIES, 0)
    for row in rows:
        result = select_document(row, overrides)
        counts[result.category] += 1
        if result.selected and latest_quality(db,row)['featured_eligible']:
            if track in {'postgrad','civil','essay'}:
                from app.exam_profiles import assess
                relevance=assess(row,'civil' if track=='essay' else track,overrides)
                if not relevance['eligible']:
                    continue
            selected.append((row, result))
    body = '<section class="account-curation"><h2>公众号里的时政补充</h2>'
    if selected:
        body += '<p>按最近整理时间展示，不把导入时间当成发布时间；评论与事实分别阅读。</p><ul>'
        for row, result in selected[:3]:
            body += f'<li><small>{escape(row.account)} · {CATEGORIES[result.category]}</small><a href="{safe}/learning/{row.id}">{escape(row.title)}</a><p>{escape(result.reasons[0])}</p></li>'
        body += '</ul>'
    else:
        body += f'<p>当前 {len(rows)} 篇公众号资料中，尚无符合规则的时政补充；背诵、备考建议和推广不用于凑满精选。</p>'
    body += '<p class="curation-counts">' + ' · '.join(f'{label} {counts[key]}' for key, label in CATEGORIES.items() if counts[key]) + '</p>'
    return body + f'<p><a href="{safe}/curation?kind=material">查看分类与调整 →</a> · <a href="{safe}/learning">全部公众号原文 →</a></p></section>'
