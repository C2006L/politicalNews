import json
from pathlib import Path

import pytest

from app.knowledge import match_points
from scripts.evaluate_knowledge import evaluate

CASES = Path(__file__).parent / 'fixtures/knowledge_cases.json'


@pytest.mark.parametrize('case', json.loads(CASES.read_text(encoding='utf-8'))['cases'])
def test_relationship_regression_cases(case):
    assert match_points(case['text']) == set(case['expected'])


def test_report_distinguishes_false_and_missed_links(tmp_path):
    data = {'annotation_status': 'not_verified', 'cases': [
        {'text': '中国梦的本质是什么', 'expected': []},
        {'text': '其他内容', 'expected': ['modernization-dream-essence']}]}
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    report = evaluate(path)
    assert report['false_links'] == report['missed_links'] == 1
    assert len(report['failures']) == 2 and not report['textbook_verified']
