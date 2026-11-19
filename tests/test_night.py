"""Night totals from Aqara's stage-change times."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from custom_components.fp2_sleep.api import HISTORY_PAGE_SIZE
from custom_components.fp2_sleep.night import NightStages, aqara_seconds, code_string, night_start

TZ = ZoneInfo("America/New_York")
START = datetime(2026, 11, 18, 18, 0, tzinfo=TZ)


def at(hour: int, minute: int = 0) -> datetime:
    day = 18 if hour >= 18 else 19
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


NIGHT = [(at(20, 30), 4), (at(23, 40), 5), (at(0, 32), 4), (at(2, 0), 2), (at(2, 10), 4)]


def reader(log, calls=None):
    async def read(start_ms: int, end_ms: int):
        if calls is not None:
            calls.append((start_ms, end_ms))
        hits = [
            {"timeStamp": int(when.timestamp() * 1000), "value": str(code)}
            for when, code in sorted(log, reverse=True)
            if start_ms <= when.timestamp() * 1000 <= end_ms
        ]
        return hits[:HISTORY_PAGE_SIZE]

    return read


def test_night_start_rolls_at_six_pm_and_across_dst() -> None:
    assert night_start(at(3)) == START
    assert night_start(at(18)) == START
    assert night_start(at(17, 59)) == START
    # The night of the November DST change still starts at 18:00 local.
    assert night_start(datetime(2026, 11, 1, 12, 0, tzinfo=TZ)) == datetime(2026, 10, 31, 18, 0, tzinfo=TZ)


async def test_totals_count_each_change_until_the_next() -> None:
    night = NightStages()
    await night.update(reader(NIGHT), at(3))
    summary = night.summary()
    assert summary is not None
    assert summary.hours == {"asleep": 5.5, "light": 4.633, "deep": 0.867, "rem": 0.0}
    assert summary.sleep_start == at(20, 30).astimezone(timezone.utc)
    assert summary.sleep_end is None  # asleep again since 02:10
    assert summary.awakenings == 1
    assert [code for _, code in summary.stages] == ["4", "5", "4", "2", "4"]


async def test_wake_time_and_refresh_window() -> None:
    log = [*NIGHT, (at(5, 52), 0)]
    calls: list[tuple[int, int]] = []
    night = NightStages()
    await night.update(reader(log[:-1], calls), at(5, 50))
    first = len(calls)
    assert first == 6  # 18:00 → 05:50 in 2-hour windows
    # Later updates re-read from an hour before the last good read, which
    # brings the late change.
    await night.update(reader(log, calls), at(6, 10))
    assert len(calls) == first + 1
    assert calls[-1] == (int(at(4, 50).timestamp() * 1000), int(at(6, 10).timestamp() * 1000))
    summary = night.summary()
    assert summary.sleep_end == at(5, 52).astimezone(timezone.utc)
    assert summary.hours["asleep"] == round(5.5 + (3 * 60 + 42) / 60, 3)


async def test_a_new_night_starts_over() -> None:
    night = NightStages()
    await night.update(reader(NIGHT), at(3))
    await night.update(reader(NIGHT), at(18, 5) + timedelta(days=1))
    summary = night.summary()
    assert summary.start == START + timedelta(days=1)
    assert summary.stages == [] and summary.hours["asleep"] == 0


async def test_a_full_page_is_split() -> None:
    busy = [(START + timedelta(seconds=20 * i), 4 if i % 2 else 5) for i in range(400)]
    calls: list[tuple[int, int]] = []
    night = NightStages()
    await night.update(reader(busy, calls), START + timedelta(hours=3))
    assert night.complete
    assert len(night.events) == 400
    assert any(end - start < 2 * 3600 * 1000 for start, end in calls)


async def test_a_failed_read_leaves_the_night_incomplete() -> None:
    async def boom(start_ms: int, end_ms: int):
        raise TimeoutError

    night = NightStages()
    try:
        await night.update(boom, at(3))
    except TimeoutError:
        pass
    assert night.summary() is None
    await night.update(reader(NIGHT), at(3, 1))
    assert night.summary() is not None


def test_parsers() -> None:
    assert aqara_seconds(1_763_600_000_000) == 1_763_600_000.0
    assert aqara_seconds("1763600000") == 1_763_600_000.0
    assert aqara_seconds(True) is None and aqara_seconds("x") is None and aqara_seconds(5) is None
    assert code_string(4) == "4" and code_string(" 05 ") == "5" and code_string(4.0) == "4"
    assert code_string(True) is None and code_string("4.5") is None and code_string(None) is None


async def test_a_gap_between_reads_is_filled() -> None:
    """Idle polling, an outage or a restart leaves hours unread; the next
    read starts from the last good one, so no change is missed."""
    log = [*NIGHT, (at(4, 0), 5), (at(4, 20), 4)]
    calls: list[tuple[int, int]] = []
    night = NightStages()
    await night.update(reader(NIGHT, calls), at(3))
    first = len(calls)

    async def down(start_ms: int, end_ms: int):
        raise TimeoutError

    try:
        await night.update(down, at(4, 30))
    except TimeoutError:
        pass
    await night.update(reader(log, calls), at(6))
    assert calls[first][0] == int(at(2).timestamp() * 1000)  # an hour before 03:00
    assert night.summary().hours["deep"] == round(52 / 60 + 20 / 60, 3)
