"""A night's sleep stages from Aqara's history log, by Aqara's own times.

The FP2's live sleep state reaches Aqara's cloud 7-19 minutes after it
changed, so counting time-in-state on arrival is off by the change in that
delay. The history log carries the time each change happened. The time from
one change to the next counts toward the earlier change's stage; the latest
change stays open until the next one arrives, so a total never runs ahead of
what Aqara has confirmed and is exact by morning.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .const import ASLEEP_CODES, NIGHT_START_HOUR, STAGES
from .api import HISTORY_PAGE_SIZE

REBUILD_CHUNK_S = 2 * 3600   # window per request when reading a whole night
MIN_CHUNK_S = 10 * 60        # a full page is split down to this window
REFRESH_WINDOW_S = 3600      # re-read on each refresh; covers the upload delay

TOTALS = {
    "asleep": ASLEEP_CODES,
    "light": frozenset({"4"}),
    "deep": frozenset({"5"}),
    "rem": frozenset({"3"}),
}

HistoryReader = Callable[[int, int], Awaitable[list[dict[str, Any]]]]


def aqara_seconds(raw: Any) -> float | None:
    """Aqara's timeStamp (epoch ms, or s) as epoch seconds; None if it is not
    a plain number in 2001-2096."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not (raw.isascii() and raw.isdigit()):
            return None
        raw = int(raw)
    if not isinstance(raw, (int, float)):
        return None
    seconds = raw / 1000 if raw >= 1e11 else raw
    return float(seconds) if 1e9 <= seconds <= 4e9 else None


def aqara_time(raw: Any) -> datetime | None:
    seconds = aqara_seconds(raw)
    return None if seconds is None else datetime.fromtimestamp(seconds, timezone.utc)


def code_string(raw: Any) -> str | None:
    """A small code sent as a number or a string, as its decimal string."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.isascii() and raw.isdigit():
            return str(int(raw))
    return None


def night_start(now: datetime) -> datetime:
    """The start of the night `now` (aware, local) belongs to."""
    start = now.replace(hour=NIGHT_START_HOUR, minute=0, second=0, microsecond=0)
    if start > now:
        start = (start - timedelta(days=1)).replace(hour=NIGHT_START_HOUR)
    return start


@dataclass
class NightSummary:
    """What a night's confirmed stage changes add up to."""

    start: datetime
    hours: dict[str, float]
    stages: list[tuple[datetime, str]]  # (time, stage code), oldest first
    sleep_start: datetime | None
    sleep_end: datetime | None
    awakenings: int


@dataclass
class NightStages:
    start: datetime | None = None
    events: dict[float, str] = field(default_factory=dict)  # epoch s -> code
    complete: bool = False

    async def update(self, read: HistoryReader, now: datetime) -> None:
        """Read new stage changes: the whole night the first time (or after
        a failed read), then the last hour, which catches late uploads."""
        start = night_start(now)
        if start != self.start:
            self.start, self.events, self.complete = start, {}, False
        begin = start.timestamp()
        end = now.timestamp()
        if not self.complete:
            self.complete = await self._read(read, begin, end, REBUILD_CHUNK_S)
        else:
            await self._read(read, max(begin, end - REFRESH_WINDOW_S), end, REFRESH_WINDOW_S)

    async def _read(self, read: HistoryReader, begin: float, end: float, chunk: float) -> bool:
        """Merge the changes between begin and end, `chunk` seconds per
        request; a full page is split in half until it fits. False when any
        part could not be read in full."""
        complete = True
        t = begin
        while t < end:
            stop = min(end, t + chunk)
            entries = await read(int(t * 1000), int(stop * 1000))
            if len(entries) >= HISTORY_PAGE_SIZE and stop - t > MIN_CHUNK_S:
                complete = await self._read(read, t, stop, (stop - t) / 2) and complete
            else:
                if len(entries) >= HISTORY_PAGE_SIZE:
                    complete = False
                for entry in entries:
                    seconds = aqara_seconds(entry.get("timeStamp"))
                    code = code_string(entry.get("value"))
                    if seconds is not None and code is not None and t - 1 <= seconds <= stop + 1:
                        self.events[seconds] = code
            t = stop
        return complete

    def summary(self) -> NightSummary | None:
        """None until the night has been read in full."""
        if not self.complete or self.start is None:
            return None
        ordered = sorted(self.events.items())
        hours = dict.fromkeys(TOTALS, 0.0)
        for (begin, code), (end, _next) in zip(ordered, ordered[1:]):
            for key, codes in TOTALS.items():
                if code in codes:
                    hours[key] += (end - begin) / 3600

        sleep_start = sleep_end = None
        awakenings = 0
        asleep = False
        for seconds, code in ordered:
            if code in ASLEEP_CODES:
                if sleep_start is None:
                    sleep_start = seconds
                elif not asleep:
                    awakenings += 1  # back to sleep after waking
                asleep = True
                sleep_end = None
            elif code in STAGES and asleep:
                asleep = False
                sleep_end = seconds

        def utc(seconds: float | None) -> datetime | None:
            return None if seconds is None else datetime.fromtimestamp(seconds, timezone.utc)

        return NightSummary(
            start=self.start,
            hours={key: round(value, 3) for key, value in hours.items()},
            stages=[(utc(seconds), code) for seconds, code in ordered],
            sleep_start=utc(sleep_start),
            sleep_end=utc(sleep_end),
            awakenings=awakenings,
        )
