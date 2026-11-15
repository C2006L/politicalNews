"""Offline relationship regression report; not a textbook correctness score."""
import argparse
import json
from pathlib import Path
from typing import Any

from app.knowledge import match_points


def evaluate(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding='utf-8'))
    false_links = missed_links = 0
    failures = []
    for index, case in enumerate(data['cases']):
        expected = set(case['expected'])
        actual = match_points(case['text'])
        extra, missing = actual - expected, expected - actual
        false_links += len(extra)
        missed_links += len(missing)
        if extra or missing:
            failures.append({'case': index + 1, 'false_links': sorted(extra), 'missed_links': sorted(missing)})
    return {'cases': len(data['cases']), 'false_links': false_links, 'missed_links': missed_links,
            'failures': failures, 'annotation_status': data['annotation_status'],
            'textbook_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1] / 'tests/fixtures/knowledge_cases.json')
    args = parser.parse_args()
    result = evaluate(args.cases)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['failures']))
