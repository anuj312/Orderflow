"""IST / NSE session helpers. IST has no DST, so a fixed +05:30 offset is exact
and avoids depending on an OS timezone database (Windows lacks one)."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional

IST = timezone(timedelta(hours=5, minutes=30), "IST")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
SLOT_MINUTES = 5
SESSION_MINUTES = 375  # 09:15 -> 15:30
SESSION_SLOTS = SESSION_MINUTES // SLOT_MINUTES  # 75


def now_ist() -> datetime:
    return datetime.now(IST)


IST_OFFSET_SECONDS = 19800
OPEN_MINUTE_OF_DAY = 9 * 60 + 15
_EPOCH_DATE = date(1970, 1, 1)


def to_ist(dt: datetime) -> datetime:
    """Convert to IST. Naive datetimes are interpreted as IST wall-clock time."""
    if dt.tzinfo is IST:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def ist_day_minute(t: int | float) -> tuple[int, float]:
    """Fast epoch -> (IST day number since 1970-01-01, minutes since 09:15 that day)."""
    s = t + IST_OFFSET_SECONDS
    return int(s // 86400), (s % 86400) / 60.0 - OPEN_MINUTE_OF_DAY


def day_number(d: date) -> int:
    return (d - _EPOCH_DATE).days


def day_from_number(n: int) -> date:
    return _EPOCH_DATE + timedelta(days=n)


def open_epoch_of_day(n: int) -> int:
    return n * 86400 - IST_OFFSET_SECONDS + OPEN_MINUTE_OF_DAY * 60


def parse_holidays(raw: Optional[str] = None) -> set[date]:
    """Parse NSE_HOLIDAYS ("YYYY-MM-DD,YYYY-MM-DD"). Invalid entries are ignored."""
    raw = os.getenv("NSE_HOLIDAYS", "") if raw is None else raw
    out: set[date] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(date.fromisoformat(part))
        except ValueError:
            continue
    return out


def is_trading_day(d: date, holidays: Iterable[date] = ()) -> bool:
    return d.weekday() < 5 and d not in set(holidays)


def session_open_dt(d: date) -> datetime:
    return datetime.combine(d, SESSION_OPEN, tzinfo=IST)


def session_close_dt(d: date) -> datetime:
    return datetime.combine(d, SESSION_CLOSE, tzinfo=IST)


def minutes_since_open(dt: datetime) -> float:
    dt = to_ist(dt)
    return (dt.hour * 60 + dt.minute - OPEN_MINUTE_OF_DAY) + (dt.second + dt.microsecond / 1e6) / 60.0


def in_session_hours(dt: datetime) -> bool:
    return 0 <= minutes_since_open(dt) <= SESSION_MINUTES


def slot_index(dt: datetime, slot_minutes: int = SLOT_MINUTES) -> Optional[int]:
    """Index of the session bucket containing dt (09:15-anchored). None outside session.
    A tick stamped exactly 15:30:00 belongs to the final bucket."""
    m = minutes_since_open(dt)
    if m < 0 or m > SESSION_MINUTES:
        return None
    idx = int(m // slot_minutes)
    last = (SESSION_MINUTES - 1) // slot_minutes
    return min(idx, last)


def slot_start(d: date, idx: int, slot_minutes: int = SLOT_MINUTES) -> datetime:
    return session_open_dt(d) + timedelta(minutes=idx * slot_minutes)


def slot_end(d: date, idx: int, slot_minutes: int = SLOT_MINUTES) -> datetime:
    return min(slot_start(d, idx + 1, slot_minutes), session_close_dt(d))


def minute_floor(dt: datetime) -> datetime:
    dt = to_ist(dt)
    return dt.replace(second=0, microsecond=0)


def previous_trading_day(d: date, holidays: Iterable[date] = ()) -> date:
    hs = set(holidays)
    cur = d - timedelta(days=1)
    while not is_trading_day(cur, hs):
        cur -= timedelta(days=1)
    return cur


def next_trading_day(d: date, holidays: Iterable[date] = ()) -> date:
    hs = set(holidays)
    cur = d + timedelta(days=1)
    while not is_trading_day(cur, hs):
        cur += timedelta(days=1)
    return cur


def epoch(dt: datetime) -> int:
    return int(to_ist(dt).timestamp())
