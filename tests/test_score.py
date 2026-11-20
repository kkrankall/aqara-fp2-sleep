"""The sleep score: a night's facts, age bands and the parts of the score."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.fp2_sleep.score import (
    LOG_NIGHTS,
    night_facts,
    prune,
    recommended_hours,
    score_log,
    score_night,
    sleep_debt,
)

TZ = ZoneInfo("America/New_York")
START = datetime(2026, 11, 19, 18, 0, tzinfo=TZ)


def at(hour: int, minute: int = 0, second: int = 0, day: int | None = None) -> float:
    day = day or (19 if hour >= 18 else 20)
    return datetime(2026, 11, day, hour, minute, second, tzinfo=TZ).timestamp()


# Shaped like a real night: in and out of bed at bedtime, a wake-up to go
# out of the room, up at 5:47, and an afternoon nap.
NIGHT = [
    (at(19, 13), "1"), (at(19, 13, 6), "2"), (at(19, 58), "0"),
    (at(20, 0), "1"), (at(20, 16), "1"), (at(20, 16, 6), "2"),
    (at(20, 33), "4"), (at(20, 48), "5"), (at(23, 0), "2"), (at(23, 0, 1), "0"),
    (at(23, 21), "1"), (at(23, 28), "4"), (at(2, 0), "3"), (at(2, 30), "4"),
    (at(5, 47), "0"),
    (at(13, 0), "4"), (at(14, 0), "2"),
]


def test_recommended_hours_by_age() -> None:
    on = date(2026, 11, 20)
    assert recommended_hours(None, on) == (7, 9)
    assert recommended_hours(date(2026, 9, 1), on) == (14, 17)    # 2 months
    assert recommended_hours(date(2026, 7, 20), on) == (12, 16)   # 4 months that day
    assert recommended_hours(date(2025, 11, 21), on) == (12, 16)  # a day short of 1
    assert recommended_hours(date(2025, 11, 20), on) == (11, 14)
    assert recommended_hours(date(2022, 1, 5), on) == (10, 13)
    assert recommended_hours(date(2018, 3, 10), on) == (9, 12)
    assert recommended_hours(date(2013, 11, 20), on) == (8, 10)   # 13 today
    assert recommended_hours(date(1990, 1, 1), on) == (7, 9)
    assert recommended_hours(date(1950, 1, 1), on) == (7, 8)


def test_facts_split_the_main_sleep_from_a_nap() -> None:
    facts = night_facts(START, NIGHT, at(15, 30))
    main = (at(5, 47) - at(20, 33)) / 3600
    awake = (at(23, 28) - at(23, 0)) / 3600
    assert facts["sleep_start"] == datetime(2026, 11, 19, 20, 33, tzinfo=TZ).isoformat()
    assert facts["sleep_end"] == datetime(2026, 11, 20, 5, 47, tzinfo=TZ).isoformat()
    assert facts["main_h"] == round(main - awake, 3)
    assert facts["asleep_h"] == round(main - awake + 1, 3)  # the nap counts toward hours
    assert facts["rem_h"] == 0.5
    assert facts["wake_ups"] == 1
    assert facts["efficiency"] == round((main - awake) / main, 3)  # but not here
    assert facts["settle_min"] == 33.0  # back in bed at 20:00, asleep 20:33
    assert facts["bedtime_min"] == 153.0


def test_a_night_is_scored_only_once_it_is_over() -> None:
    morning = [e for e in NIGHT if e[0] <= at(5, 47)]
    assert night_facts(START, morning[:-1], at(5, 50)) is None  # asleep now
    assert night_facts(START, morning, at(6, 30)) is None       # up 43 minutes
    assert night_facts(START, morning, at(6, 47)) is not None   # up an hour
    assert night_facts(START, morning[:-1], at(5, 50), final=True) is not None
    assert night_facts(START, [(at(19, 0), "1"), (at(23, 0), "0")], at(17)) is None  # no sleep


def test_straight_from_out_of_bed_to_asleep_settles_at_once() -> None:
    events = [(at(20, 0), "0"), (at(20, 5), "4"), (at(6, 0), "0")]
    assert night_facts(START, events, at(8))["settle_min"] == 0.0
    events = [(at(20, 5), "4"), (at(6, 0), "0")]
    assert night_facts(START, events, at(8))["settle_min"] is None  # in bed before 18:00


def facts(asleep: float, efficiency: float = 0.95, settle: float | None = 10, bedtime: float = 150) -> dict:
    return {
        "start": START.isoformat(),
        "asleep_h": asleep,
        "efficiency": efficiency,
        "settle_min": settle,
        "bedtime_min": bedtime,
    }


def test_score_parts_for_a_child() -> None:
    eight = date(2018, 3, 10)
    night = score_night(facts(7.926, 0.857, 32.5, 153), eight, [140, 150, 160])
    assert (night.low, night.high, night.age_months) == (9, 12, 104)
    assert night.date == date(2026, 11, 20)
    assert night.points == {
        "duration": round(50 * (7.926 - 6) / 3, 1),
        "regularity": 20.0,
        "settling": round(10 * (60 - 32.5) / 40, 1),
        "continuity": round(20 * (0.857 - 0.75) / 0.2, 1),
    }
    assert night.score == round(50 * 1.926 / 3 + 20 + 10 * 27.5 / 40 + 20 * 0.107 / 0.2)
    assert night.bedtime_shift_min == 3.0
    assert night.shortfall_h == round(9 - 7.926, 3)


@pytest.mark.parametrize(
    ("asleep", "points"),
    [(9.0, 50.0), (12.0, 50.0), (7.5, 25.0), (6.0, 0.0), (5.0, 0.0), (14.0, 25.0), (16.5, 0.0)],
)
def test_duration_points(asleep: float, points: float) -> None:
    night = score_night(facts(asleep), date(2018, 3, 10), [])
    assert night.points["duration"] == points


def test_regularity_and_settling_points() -> None:
    eight = date(2018, 3, 10)
    late = score_night(facts(10, bedtime=225), eight, [150, 150, 150])  # 75 min late
    assert late.points["regularity"] == 10.0
    assert score_night(facts(10, bedtime=30), eight, [150] * 5).points["regularity"] == 0.0
    assert score_night(facts(10, settle=40), eight, []).points["settling"] == 5.0
    assert score_night(facts(10, settle=90), eight, []).points["settling"] == 0.0


def test_missing_parts_are_left_out_and_the_rest_scaled() -> None:
    night = score_night(facts(9.5, 0.85, None), date(2018, 3, 10), [150, 160])  # 2 nights: too few
    assert set(night.points) == {"duration", "continuity"}
    assert night.bedtime_shift_min is None
    assert night.score == round(100 * (50 + 10) / 70)


def test_too_little_sleep_is_not_scored() -> None:
    night = score_night(facts(1.5), date(2018, 3, 10), [])
    assert night.score is None and night.points == {}
    assert night.shortfall_h == 7.5


def test_the_log_scores_against_the_week_before() -> None:
    log = {}
    for i, (asleep, bedtime) in enumerate([(9.5, 150), (8.0, 160), (1.0, 400), (9.0, 140), (8.5, 230)]):
        start = START + timedelta(days=i)
        log[start.isoformat()] = {**facts(asleep, bedtime=bedtime), "start": start.isoformat()}
    nights = score_log(log, date(2018, 3, 10))
    assert [n.date.day for n in nights] == [20, 21, 22, 23, 24]
    assert nights[2].score is None                   # 1 h: not scored, not a bedtime
    assert nights[3].bedtime_shift_min is None       # only 2 scored nights before
    assert nights[4].bedtime_shift_min == 80.0       # 230 - median(150, 160, 140)
    assert sleep_debt(nights) == round(1.0 + 0.5, 2)  # 8.0 and 8.5 h; the 1 h night isn't counted
    assert sleep_debt([]) is None


def test_prune_keeps_the_latest_nights() -> None:
    log = {(START + timedelta(days=i)).isoformat(): {} for i in range(LOG_NIGHTS + 5)}
    prune(log)
    assert len(log) == LOG_NIGHTS
    assert (START + timedelta(days=4)).isoformat() not in log
    assert (START + timedelta(days=5)).isoformat() in log
