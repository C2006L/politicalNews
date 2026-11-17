from datetime import date,datetime,timezone
from app.material_dates import page_date,recent


def test_timestamp_timezone_window_and_no_import_time_fallback():
    stamp=int(datetime(2026,11,16,20,tzinfo=timezone.utc).timestamp())
    assert page_date(f'var ct = "" || "{stamp}";',date(2026,11,17))=='2026-11-17'
    assert page_date('<p>2026-11-17</p>',date(2026,11,17)) is None
    assert page_date(f'var ct = run("{stamp}");',date(2026,11,17)) is None
    assert page_date(f'var ct = "{stamp}";',date(2026,11,16)) is None
    assert recent('2026-08-20',date(2026,11,17))
    assert not recent('2026-08-19',date(2026,11,17))
    assert not recent(None,date(2026,11,17))
