"""A nightly sleep score against the hours recommended for the sleeper's age.

Each night is reduced to a few facts from Aqara's stage changes (time
asleep, the main sleep period, how long it took to fall asleep), kept in a
small log. The score is worked out from those facts when it is shown, so a
birthday or a new birth date re-scores every night in the log.

Out of 100:
  Enough sleep     50  asleep for the hours recommended for the age
  Regular bedtime  20  fell asleep near the usual time of the last week
  Settling         10  fell asleep soon after getting into bed
  Unbroken         20  little time awake once asleep (sleep efficiency)
A part that can't be worked out for a night is left out and the rest
scaled to 100. Deep and REM sleep are not scored: the FP2 estimates them
from radar, and REM needs a heart-rate reading it doesn't always get.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from .const import ASLEEP_CODES

# Recommended hours of sleep per 24 hours (naps included), by age in months:
# the American Academy of Sleep Medicine's consensus for children (2016,
# endorsed by the American Academy of Pediatrics), and the National Sleep
# Foundation's for newborns and adults (2015). (age below, min, max)
AGE_BANDS: tuple[tuple[int | None, float, float], ...] = (
    (4, 14, 17),      # 0-3 months
    (12, 12, 16),     # 4-11 months
    (36, 11, 14),     # 1-2 years
    (72, 10, 13),     # 3-5 years
    (156, 9, 12),     # 6-12 years
    (216, 8, 10),     # 13-17 years
    (780, 7, 9),      # 18-64 years
    (None, 7, 8),     # 65 and over
)
ADULT_HOURS = (7.0, 9.0)  # without a birth date

WEIGHTS = {"duration": 50, "regularity": 20, "settling": 10, "continuity": 20}

# Asleep stretches less than this apart are one sleep period; the night's
# main period is the one with the most sleep (naps are the others).
PERIOD_GAP_S = 2 * 3600
# A night is scored once nobody has been asleep for this long: Aqara's stage
# changes arrive up to ~20 minutes late, and a short wake isn't the morning.
SETTLE_S = 60 * 60
MIN_SCORED_HOURS = 2.0  # less than this: slept elsewhere, or no reading
LOG_NIGHTS = 120
REGULARITY_NIGHTS = 7
REGULARITY_MIN_NIGHTS = 3
DEBT_NIGHTS = 7

# Full points up to the first value, none from the second, linear between.
DURATION_SHORT_H = 3.0   # below the minimum, all points gone 3 h short
DURATION_LONG_H = 4.0    # above the maximum, all points gone 4 h over
REGULARITY_MIN = (30, 120)
SETTLING_MIN = (20, 60)
EFFICIENCY = (0.95, 0.75)


def age_months(birth: date, on: date) -> int:
    months = (on.year - birth.year) * 12 + on.month - birth.month
    if on.day < birth.day:
        months -= 1
    return max(0, months)


def recommended_hours(birth: date | None, on: date) -> tuple[float, float]:
    if birth is None:
        return ADULT_HOURS
    months = age_months(birth, on)
    for below, low, high in AGE_BANDS:
        if below is None or months < below:
            return (float(low), float(high))
    return ADULT_HOURS  # not reached


def night_facts(
    start: datetime, events: list[tuple[float, str]], now: float, final: bool = False
) -> dict[str, Any] | None:
    """The night's facts from its stage changes ((epoch s, code), any
    order), or None while it isn't over: someone is asleep, or was less
    than SETTLE_S ago. With `final` (the night has ended) it is over
    regardless. None too when nobody slept."""
    ordered = sorted(events)
    if not ordered:
        return None
    if not final and (ordered[-1][1] in ASLEEP_CODES):
        return None  # asleep right now
    segments = [
        (begin, end, code)
        for (begin, code), (end, _next) in zip(ordered, ordered[1:])
        if end > begin
    ]
    asleep = [(begin, end) for begin, end, code in segments if code in ASLEEP_CODES]
    if not asleep:
        return None
    if not final and now - asleep[-1][1] < SETTLE_S:
        return None

    periods: list[list[float]] = []  # [begin, end, seconds asleep, wake-ups]
    for begin, end in asleep:
        if periods and begin - periods[-1][1] < PERIOD_GAP_S:
            last = periods[-1]
            if begin > last[1]:
                last[3] += 1  # awake in between
            last[1] = end
            last[2] += end - begin
        else:
            periods.append([begin, end, end - begin, 0])
    main_begin, main_end, main_asleep, wake_ups = max(periods, key=lambda p: p[2])

    # Into bed: the change after the last time out of bed before falling
    # asleep, else the night's first change if it came before sleep.
    bed = None
    before = [(t, code) for t, code in ordered if t < main_begin]
    for i in range(len(before) - 1, -1, -1):
        if before[i][1] == "0":
            # Straight from out of bed to asleep: no time settling.
            bed = before[i + 1][0] if i + 1 < len(before) else main_begin
            break
    else:
        if before:
            bed = before[0][0]

    def hours(codes: frozenset[str]) -> float:
        return round(sum(end - begin for begin, end, code in segments if code in codes) / 3600, 3)

    return {
        "start": start.isoformat(),
        "sleep_start": datetime.fromtimestamp(main_begin, start.tzinfo).isoformat(),
        "sleep_end": datetime.fromtimestamp(main_end, start.tzinfo).isoformat(),
        "asleep_h": hours(ASLEEP_CODES),
        "light_h": hours(frozenset({"4"})),
        "deep_h": hours(frozenset({"5"})),
        "rem_h": hours(frozenset({"3"})),
        "main_h": round(main_asleep / 3600, 3),
        "wake_ups": int(wake_ups),
        "efficiency": round(main_asleep / (main_end - main_begin), 3),
        "settle_min": round((main_begin - bed) / 60, 1) if bed is not None else None,
        "bedtime_min": round((main_begin - start.timestamp()) / 60, 1),
    }


def _fraction(value: float, full: float, none: float) -> float:
    """1 at `full` or better, 0 at `none` or worse, linear between."""
    if full == none:
        return 1.0 if value == full else 0.0
    return min(1.0, max(0.0, (value - none) / (full - none)))


@dataclass
class ScoredNight:
    facts: dict[str, Any]
    date: date             # the morning the night ended
    low: float
    high: float
    age_months: int | None
    score: int | None      # None: too little sleep to score
    points: dict[str, float] = field(default_factory=dict)
    bedtime_shift_min: float | None = None
    shortfall_h: float = 0.0


def score_night(
    facts: dict[str, Any], birth: date | None, previous_bedtimes: list[float]
) -> ScoredNight:
    morning = datetime.fromisoformat(facts["start"]).date() + timedelta(days=1)
    low, high = recommended_hours(birth, morning)
    asleep = float(facts["asleep_h"])
    night = ScoredNight(
        facts=facts,
        date=morning,
        low=low,
        high=high,
        age_months=age_months(birth, morning) if birth else None,
        score=None,
        shortfall_h=round(max(0.0, low - asleep), 3),
    )
    if asleep < MIN_SCORED_HOURS:
        return night

    if asleep < low:
        duration = _fraction(asleep, low, low - DURATION_SHORT_H)
    elif asleep > high:
        duration = _fraction(-asleep, -high, -(high + DURATION_LONG_H))
    else:
        duration = 1.0
    parts = {"duration": duration}
    if len(previous_bedtimes) >= REGULARITY_MIN_NIGHTS:
        shift = float(facts["bedtime_min"]) - median(previous_bedtimes)
        night.bedtime_shift_min = round(shift, 1)
        parts["regularity"] = _fraction(-abs(shift), -REGULARITY_MIN[0], -REGULARITY_MIN[1])
    if facts.get("settle_min") is not None:
        parts["settling"] = _fraction(-float(facts["settle_min"]), -SETTLING_MIN[0], -SETTLING_MIN[1])
    parts["continuity"] = _fraction(float(facts["efficiency"]), *EFFICIENCY)

    night.points = {key: round(WEIGHTS[key] * value, 1) for key, value in parts.items()}
    possible = sum(WEIGHTS[key] for key in parts)
    night.score = round(100 * sum(WEIGHTS[key] * value for key, value in parts.items()) / possible)
    return night


def score_log(log: dict[str, dict[str, Any]], birth: date | None) -> list[ScoredNight]:
    """Every night in the log (night start -> facts), oldest first, each
    scored against the scored nights of the week before it."""
    nights = sorted((datetime.fromisoformat(facts["start"]), facts) for facts in log.values())
    scored: list[ScoredNight] = []
    starts: list[datetime] = []
    for start, facts in nights:
        week = [
            night.facts["bedtime_min"]
            for night, earlier in zip(scored, starts)
            if night.score is not None
            and timedelta(0) < start - earlier <= timedelta(days=REGULARITY_NIGHTS)
        ]
        scored.append(score_night(facts, birth, week))
        starts.append(start)
    return scored


def sleep_debt(scored: list[ScoredNight]) -> float | None:
    """Hours short of the recommended minimum over the latest night and the
    DEBT_NIGHTS - 1 before it (nights not recorded don't count)."""
    counted = [n for n in scored if n.score is not None]
    if not counted:
        return None
    latest = counted[-1].date
    return round(
        sum(n.shortfall_h for n in counted if (latest - n.date).days < DEBT_NIGHTS), 2
    )


def prune(log: dict[str, dict[str, Any]]) -> None:
    for key in sorted(log, key=lambda k: datetime.fromisoformat(k))[:-LOG_NIGHTS]:
        del log[key]
