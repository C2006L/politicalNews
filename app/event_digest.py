"""Local extractive comparison. Novel wording is not a verified new fact."""
from difflib import SequenceMatcher
import hashlib
import re
from typing import Any

METHOD = 'extractive-comparison-1'


def normalized(text: str) -> str:
    # Attribution differences alone do not create new event information.
    text = re.sub(r'^(?:新华社|中新社|中新网)[^。\n]{0,35}?\d{1,2}日电\s*(?:[（(]记者[^（）()]{0,50}[）)])?', '', text)
    return re.sub(r'[\s，,。；;：:！？!?、“”"‘’（）()]+', '', text).lower()


def sentences(text: str) -> list[dict[str, Any]]:
    result = []
    for match in re.finditer(r'[^。！？\n]+[。！？]?', text[:12000]):
        raw = match[0]
        quote = raw.strip()
        if re.match(r'^(?:《[^》]{1,20}报\s*》\s*[（(]|责任编辑|编辑[:：]|来源[:：]|本文来源|相关阅读|更多推荐|Copyright|版权)', quote):
            continue
        if 18 <= len(quote) <= 400:
            start = match.start() + len(raw) - len(raw.lstrip())
            result.append({'quote': quote, 'start': start, 'end': start + len(quote)})
    return result[:40]


def focus(quote: str) -> tuple[str, int]:
    if re.search(r'施行|实施日期|生效|截至|截止|自\d|\d{1,2}月\d{1,2}日', quote):
        return '时间与安排', 4
    if re.search(r'适用于|适用范围|面向|覆盖|对象为', quote):
        return '涉及范围', 3
    if re.search(r'印发|发布|规定|明确|要求|建立|完善|措施|实施', quote):
        return '举措与要求', 3
    if re.search(r'\d+(?:\.\d+)?\s*(?:%|亿|万|个|项|条|元)', quote):
        return '关键数字', 2
    return '报道要点', 1


def build_digest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [r for r in entries if r['is_current'] and r['status'] != 'rejected'
                and (r.get('selection') or {}).get('selected')]
    confirmed = [r for r in eligible if r['status'] == 'confirmed']
    mode = 'confirmed' if len(confirmed) >= 2 else 'candidate_preview'
    chosen = sorted(confirmed if mode == 'confirmed' else eligible,
                    key=lambda r: (r['snapshot']['published_at'], r['news_id']))[:24]
    seen: dict[str, dict[str, Any]] = {}
    reports: list[dict[str, Any]] = []
    highlights: list[dict[str, Any]] = []
    baseline_day = chosen[0]['snapshot']['published_at'][:10] if chosen else ''
    for row in chosen:
        snap = row['snapshot']
        extracted = sentences(snap['text'])
        novel = repeated = similar = 0
        additions = []
        # Compare against earlier articles, never count repetition inside one article as a reprint.
        pending = []
        local_keys: set[str] = set()
        for span in extracted:
            key = normalized(span['quote'])
            if key in local_keys:
                continue
            local_keys.add(key)
            ref = {'article_id': row['id'], 'news_id': row['news_id'], 'title': snap['title'],
                   'publisher': row['publisher'], 'published_at': snap['published_at'],
                   'fingerprint': hashlib.sha256(snap['text'].encode()).hexdigest(), 'field': 'text', **span}
            if key in seen:
                repeated += 1
                continue
            neighbors = list(seen.items())[-160:]
            close = next((prior for old, prior in neighbors
                          if abs(len(old)-len(key)) <= max(len(key),len(old)) * .25
                          and SequenceMatcher(None, old, key, autojunk=False).ratio() >= .78), None)
            label, score = focus(span['quote'])
            if close:
                similar += 1
                # Changes in numbers, conditions or negation may matter. Do not silently deduplicate.
                item = {'kind': 'changed_wording', 'label': '近似表述有差异，需对照',
                        'evidence': ref, 'previous': close, 'score': score}
            else:
                novel += 1
                marker = re.search(r'新增|已完成|正式实施|正式施行|获批|通过验收|最新进展', span['quote'])
                progress = bool(marker and not re.search(r'未|没有|不再|计划|拟|将|预计|有望|可能',
                                span['quote'][max(0,marker.start()-8):marker.start()]))
                progress = progress and snap['published_at'][:10] > baseline_day and bool(seen)
                item = {'kind': 'progress_clue' if progress else 'additional_wording',
                        'label': '后续进展线索，待核对' if progress else label,
                        'evidence': ref, 'previous': None, 'score': score}
            additions.append(item)
            pending.append((key, ref))
        for key, ref in pending:
            seen.setdefault(key, ref)
        state = ('insufficient' if not extracted else 'repeated' if not novel and not similar
                 else 'different' if similar else 'baseline' if not reports else 'additional')
        reports.append({'news_id': row['news_id'], 'title': snap['title'], 'publisher': row['publisher'],
                        'published_at': snap['published_at'], 'state': state, 'repeated': repeated,
                        'additional': novel, 'different': similar, 'compared_sentences': len(extracted),
                        'text_limited': len(snap['text']) > 12000 or len(extracted) == 40})
        highlights.extend(additions)
    highlights.sort(key=lambda item: (-item['score'], item['evidence']['published_at'], item['evidence']['news_id'], item['evidence']['start']))
    return {'method': METHOD, 'mode': mode, 'articles': reports, 'points': highlights[:12],
            'total_points': len(highlights), 'excluded': len(entries)-len(chosen),
            'limits': '逐句原文摘录，优先呈现时间安排、措施、范围与数字，不代表重要性或影响评估。仅比较前 24 篇当前入选稿，每篇前 12000 字中的最多 40 句，展示最多 12 条。新增指相对本组已比较文字，不等于首次发生、独立报道或事实核验；日期仅到日，同日先后不确定。'}
