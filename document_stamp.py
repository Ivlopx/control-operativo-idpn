"""Publication-date file stamps, shared by planning, transfer and storage."""
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

DATE_STAMP = r'\d{4}-\d{2}-\d{2}(?:-[2-9]\d*|-[1-9]\d+)?'
# Both schemes remain readable for manifests and files created before this change.
STAMP_SUFFIX = r'(?:-v\d{3,}|-' + DATE_STAMP + r')?'


def publication_day():
    return datetime.now(ZoneInfo('America/Mexico_City')).date().isoformat()


def valid_stamp(value):
    if not isinstance(value, str):
        return False
    if re.fullmatch(r'v\d{3,}', value):
        return True
    if not re.fullmatch(DATE_STAMP, value):
        return False
    try:
        date.fromisoformat(value[:10])
        return True
    except ValueError:
        return False


def stamp_from_filename(filename):
    match = re.search(r'-(' + DATE_STAMP + r'|v\d{3,})\.[^.]+$', filename, re.I)
    return match[1] if match and valid_stamp(match[1]) else None


def next_stamp(files):
    today = publication_day()
    used = []
    for item in files:
        stamp = stamp_from_filename(item['key'].rsplit('/', 1)[-1])
        if stamp and stamp[:10] == today:
            used.append(int(stamp[11:]) if len(stamp) > 10 else 1)
    number = max(used, default=0) + 1
    return today + (f'-{number}' if number > 1 else '')


def stamp_sort_key(filename):
    stamp = stamp_from_filename(filename)
    if stamp and not stamp.startswith('v'):
        return (1, stamp[:10], int(stamp[11:]) if len(stamp) > 10 else 1)
    return (0, '', int(stamp[1:]) if stamp else 0)
