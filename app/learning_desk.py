"""A small, evidence-bound reading shortlist; no inferred personal progress."""
from html import escape

from sqlalchemy import func

from app.database import SessionLocal
from app.models import KnowledgeLink, KnowledgePoint, LearningMaterial, MaterialRevision


def render_learning_desk(base: str) -> str:
    safe = escape(base, quote=True)
    with SessionLocal() as db:
        latest = db.query(MaterialRevision.material_id.label('material_id'),
                          func.max(MaterialRevision.revision).label('revision')).group_by(
                              MaterialRevision.material_id).subquery()
        versions = db.query(MaterialRevision).join(latest, (
            (MaterialRevision.material_id == latest.c.material_id) &
            (MaterialRevision.revision == latest.c.revision))).join(
                LearningMaterial, LearningMaterial.id == MaterialRevision.material_id)
        selected = []
        accounts: set[str] = set()
        # Latest organized revision, not an unverified publication date.
        for version in versions.order_by(LearningMaterial.imported_at.desc(), LearningMaterial.id.desc()):
            snap = version.snapshot
            account = snap['account']
            if account in accounts:
                continue
            payload = snap.get('payload', {})
            refs = {line['id'] for line in payload.get('evidence', [])}
            destination = None
            for kind, anchor, label in [('questions', 'question-0', '回想一道题'), ('points', 'points', '读重点提法')]:
                entries = payload.get(kind, [])
                if entries and entries[0].get('evidence') and set(entries[0]['evidence']) <= refs:
                    destination = (anchor, label)
                    break
            if destination is None:
                continue
            accounts.add(account)
            anchor, label = destination
            selected.append(f'<li><small>{escape(account)} · {label}</small>'
                            f'<a href="{safe}/learning/{version.material_id}?revision={version.revision}#{anchor}">'
                            f'{escape(snap["title"])}</a></li>')
            if len(selected) == 2:
                break
        # Group only non-rejected relationships for each material's CURRENT revision.
        matches = db.query(KnowledgePoint.id, KnowledgePoint.title,
                           func.count(func.distinct(LearningMaterial.account)).label('accounts')).join(
            KnowledgeLink, KnowledgeLink.point_id == KnowledgePoint.id).join(
            MaterialRevision, MaterialRevision.id == KnowledgeLink.revision_id).join(
            latest, (MaterialRevision.material_id == latest.c.material_id) &
            (MaterialRevision.revision == latest.c.revision)).join(
            LearningMaterial, LearningMaterial.id == MaterialRevision.material_id).filter(
            KnowledgeLink.status != 'rejected').group_by(KnowledgePoint.id, KnowledgePoint.title).having(
            func.count(func.distinct(LearningMaterial.account)) >= 2).order_by(
            func.count(func.distinct(LearningMaterial.account)).desc(), KnowledgePoint.id).first()
    readings = '<ul>' + ''.join(selected) + '</ul>' if selected else (
        f'<p>还没有可自测的文字材料。</p><a href="{safe}/learning">查看已有原文 →</a> · '
        f'<a href="{safe}/getting-started">准备资料 →</a>')
    comparison = (f'<a class="desk-topic" href="{safe}/knowledge/{escape(matches.id, quote=True)}">'
                  f'{escape(matches.title[:-2])}<span>{escape(matches.title[-2:])} →</span></a>'
                  f'<p>{matches.accounts} 个账号有对应原文；分别核对，候选关联仍待确认。</p>'
                  if matches else f'<p>当前还没有两位老师共同覆盖的考点。</p><a href="{safe}/knowledge">浏览已有考点 →</a>')
    return f'''<section class="learning-desk" aria-labelledby="learning-desk-title">
<div class="desk-heading"><h2 id="learning-desk-title">今天，从这里接着学</h2><span>读原文 · 对照 · 再回想</span></div>
<div class="desk-columns"><section><h3><span>01</span> 选一份材料</h3>{readings}
<small class="desk-note">按最近整理选取，每位老师一份；不是今日发布或已学进度。</small></section>
<section><h3><span>02</span> 对照同一考点</h3>{comparison}</section>
<section data-desk-review><h3><span>03</span> 接着复习</h3><p data-desk-review-summary role="status">正在读取本浏览器复习安排…</p>
<a href="{safe}/practice" data-desk-review-link>我的复习 →</a><small class="desk-note">只读取本浏览器记录，不把已读、收藏算作学会。</small></section></div></section>'''
