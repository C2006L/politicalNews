from datetime import datetime

import pytest

from app import curated_materials, exam_profiles
from app.database import SessionLocal
from app.editorial import Selection, CATEGORIES
from app.models import LearningMaterial


@pytest.mark.parametrize('track', ['all', 'civil', 'postgrad', 'essay'])
def test_home_notes_keep_three_readable_matches_and_full_archive_counts(monkeypatch, track):
    checked = []

    def quality(db, row):
        checked.append(row.id)
        return {'featured_eligible': row.id != 7}

    monkeypatch.setattr(curated_materials, 'latest_quality', quality)
    monkeypatch.setattr(curated_materials, 'select_document', lambda row, overrides:
                        Selection('policy', True, ['原文政策'], []))
    monkeypatch.setattr(exam_profiles, 'assess', lambda row, profile, overrides:
                        {'eligible': row.id != 8})
    with SessionLocal() as db:
        for identity in range(1, 9):
            db.add(LearningMaterial(id=identity, source_id=str(identity), account='测试来源',
                                    title=f'政策{identity}', url=f'https://example.org/{identity}',
                                    fingerprint=str(identity), payload={}, imported_at=datetime(2026, 11, 18)))
        db.commit()
        html = curated_materials.render_material_notes(db, '', {}, track)

    expected = [8, 6, 5] if track == 'all' else [6, 5, 4]
    for identity in range(1, 9):
        assert (f'href="/learning/{identity}"' in html) == (identity in expected)
    assert f'{CATEGORIES["policy"]} 8' in html
    # Reject incomplete pages before filling the three slots, then avoid
    # parsing saved HTML for the remaining archive on every home request.
    assert checked == ([8, 7, 6, 5] if track == 'all' else [7, 6, 5, 4])
