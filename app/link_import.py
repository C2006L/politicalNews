"""Import reviewed, cached public articles with identity evidence, never execute HTML."""
import base64
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs
from bs4 import BeautifulSoup
from app.database import SessionLocal
from app.material_sources import sources
from app.materials import extract_material, EXTRACTOR_VERSION, LOCK, ImportBusy
from app.models import LearningMaterial, MaterialRevision
from app.revisions import append_revision


def literal_number(html: str, names: str) -> str | None:
    assignment=re.search(r'var\s+(?:'+names+r')\s*=\s*([^;\n]+)',html)
    if not assignment:return None
    value=assignment[1].strip()
    if not re.fullmatch(r'''["']\d*["'](?:\s*\|\|\s*["']\d*["'])*''',value):return None
    numbers=re.findall(r'''["'](\d+)["']''',value)
    return numbers[0] if numbers else None


def literal_biz(html: str) -> str | None:
    """Read only literal base64 alternatives, including WeChat's empty fallback."""
    assignment=re.search(r'var\s+biz\s*=\s*([^;\n]+)',html)
    if not assignment:return None
    expression=assignment[1].strip()
    if not re.fullmatch(r'''["'][A-Za-z0-9+/=]*["'](?:\s*\|\|\s*["'][A-Za-z0-9+/=]*["'])*''',expression):return None
    values=re.findall(r'''["']([A-Za-z0-9+/=]+)["']''',expression)
    return values[0] if values else None


def import_cached_link(key: str, url: str, html: str) -> dict[str, int | str]:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'mp.weixin.qq.com' or not (re.fullmatch(r'/s/[A-Za-z0-9_~\-]+', parsed.path) or parsed.path == '/s'):
        raise ValueError('Invalid public article URL')
    source = next((s for s in sources() if s['key'] == key and s['enabled'] and s['mp_id']), None)
    if source is None:
        raise ValueError('Source is not enabled')
    soup = BeautifulSoup(html, 'html.parser')
    name = soup.select_one('#js_name')
    title = soup.select_one('meta[property="og:title"]')
    biz = literal_biz(html)
    if not name or name.get_text(' ', strip=True) != source['name'] or not biz or not title:
        raise ValueError('Article identity metadata is missing or mismatched')
    identity = 'MP_WXS_' + base64.b64decode(biz, validate=True).decode()
    if identity != source['mp_id']:
        raise ValueError('Article belongs to a different account')
    if parsed.path == '/s':
        query = parse_qs(parsed.query)
        mid = literal_number(html,'mid|appmsgid')
        idx = literal_number(html,'idx')
        if query.get('__biz') != [biz] or not mid or not idx or query.get('mid') != [mid] or query.get('idx') != [idx]:
            raise ValueError('Long article URL does not match page identity')
    payload = extract_material(html)
    payload['import_origin'] = 'user_shared_link'
    # Keep publication-date status conservative; this importer only verifies account identity.
    title_text = str(title.get('content', '')).strip()
    if not title_text:
        raise ValueError('Article title is missing')
    fingerprint = hashlib.sha256((EXTRACTOR_VERSION + html + title_text + url).encode()).hexdigest()
    if not LOCK.acquire(blocking=False):
        raise ImportBusy('Another material import is running')
    try:
        with SessionLocal() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            material = db.query(LearningMaterial).filter_by(url=url, account=source['name']).first()
            if material and material.fingerprint == fingerprint:
                return {'id': material.id, 'state': 'unchanged'}
            if material is None:
                material = LearningMaterial(source_id='shared-link:' + hashlib.sha256(url.encode()).hexdigest())
                db.add(material)
            elif not db.query(MaterialRevision.id).filter_by(material_id=material.id).first():
                append_revision(db, material)
            material.account, material.title, material.url = source['name'], title_text, url
            material.payload, material.fingerprint = payload, fingerprint
            material.imported_at = datetime.now(timezone.utc).replace(tzinfo=None)
            revision = append_revision(db, material, html)
            material_id = material.id
            db.commit()
            return {'id': material_id, 'state': 'imported', 'revision': revision}
    finally:
        LOCK.release()
