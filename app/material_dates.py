"""Publication dates parsed from saved page metadata; never from import time."""
from datetime import date, datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo


def page_date(raw: str | None, today: date | None=None) -> str | None:
    if not raw:return None
    assignment=re.search(r'var\s+ct\s*=\s*([^;\n]+)',raw)
    if not assignment:return None
    expression=assignment[1].strip()
    if not re.fullmatch(r'''["']\d*["'](?:\s*\|\|\s*["']\d*["'])*''',expression):return None
    values=re.findall(r'''["'](\d+)["']''',expression)
    if not values:return None
    try:day=datetime.fromtimestamp(int(values[0]),timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date()
    except (ValueError,OverflowError,OSError):return None
    end=today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if day<date(2010,1,1) or day>end:return None
    return day.isoformat()


def recent(day: str | None,today: date | None=None) -> bool:
    if not day:return False
    end=today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    return end-timedelta(days=89)<=date.fromisoformat(day)<=end
