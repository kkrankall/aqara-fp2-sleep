# Aqara FP2 Sleep

A Home Assistant integration for the sleep monitoring mode of the Aqara
Presence Sensor FP2: heart rate, breathing, body movement, sleep stages,
each night's totals and a sleep score for the sleeper's age, with a
dashboard card.

The FP2 only shares sleep data with Aqara's cloud, not over HomeKit or
Matter. This integration reads it the way the Aqara Home app does, through
Aqara's app API, with your Aqara Home account. There is no developer
account, API key, MQTT broker or add-on to set up.

![The sleep card in light and dark themes: a sleep score of 86, 9h 31m asleep against the 9–12 h recommended at age 8, a hypnogram of the night's stages, the light, deep, REM and awake breakdown, the four parts of the score, and heart rate, breathing and movement through the night](https://raw.githubusercontent.com/kkrankall/aqara-fp2-sleep/main/docs/images/card.png)

## Setup

1. In the Aqara Home app, set the FP2's work mode to **Sleep monitoring**.
2. Install through HACS (custom repository
   `https://github.com/kkrankall/aqara-fp2-sleep`, category Integration),
   then restart Home Assistant.
3. **Settings › Devices & services › Add integration › Aqara FP2 Sleep.**
   Sign in with your Aqara Home account and pick the region your app uses.
   The integration lists the FP2s on the account; with one FP2 it is set up
   straight away.

Your phone stays signed in. The integration keeps its own session and
stores only an MD5 hash of the password, which is what Aqara's sign-in
needs; when Aqara asks for a new sign-in, Home Assistant asks you for the
password again.

## Entities

| Entity | |
| --- | --- |
| Heart rate, Respiration rate, Body movement | Live readings, about every minute. `measured_at` is when the FP2 took the reading. |
| Sleep stage | Out of bed, In bed, Awake, REM, Light, Deep. |
| Night asleep, Night light / deep / REM sleep | Hours since 18:00, by Aqara's own times (see below). `Night asleep` carries the night's stage changes for the card. |
| Fell asleep, Woke up | Times from the same stage changes. |
| Sleep score | Last night out of 100, for the sleeper's age (see below). |
| Sleep debt | Hours short of the recommended minimum over the last 7 nights. |
| Illuminance | The FP2's light sensor. |
| FP2 mode, FP2 online, Connection problem | Diagnostics. A repair appears if the FP2 leaves Sleep monitoring mode. |
| Sleep state code | Aqara's raw stage code (0-5), disabled by default. |

Entity ids start with `aqara_fp2_sleep_`, the same as the SleepRadar app's,
so history and dashboards carry over when switching from it.

### Why the night totals are exact

The FP2's sleep stage reaches Aqara's cloud 7-19 minutes after it changes,
so counting time-in-state as values arrive in Home Assistant is off by
however much that delay varies. The integration reads the night's stage
changes from Aqara's history log, which carries the time each change
happened, and adds up the time between them. The latest change counts only
once the next one arrives, so a total never runs ahead of Aqara and is exact
by morning.

### Importing past nights

The action **Aqara FP2 Sleep: Import past nights** (`fp2_sleep.import_history`)
reads past nights from Aqara's history log, adds their night totals to
Home Assistant's long-term statistics and scores them, so charts of past
nights and the usual bedtime fill in right after setup. Aqara keeps about two weeks (15 days on a US account in
November 2026); nights it no longer has are skipped, and a night is imported only when the log also covers the day
before it, so the oldest night can't come in cut short. Hours that already
have statistics are never changed. With `nights` (1-30, default 30) it tries
that many nights back; the response lists what happened to each.

## Sleep score

Each night gets a score out of 100 once it is over: an hour after the last
time asleep, or at 18:00 at the latest. It stays on that night until the
next one is over, so in the evening it shows last night's.

Set the sleeper's birth date under **Settings › Devices & services › Aqara
FP2 Sleep › Configure**. The score compares the night with the hours of
sleep recommended for that age; without a birth date it uses the adult
range. The date stays in Home Assistant, and only the age is used, so the
range moves on birthdays and past nights are re-scored with it.

| Age | Hours per day | Source |
| --- | --- | --- |
| 0-3 months | 14-17 | National Sleep Foundation |
| 4-11 months | 12-16, naps included | American Academy of Sleep Medicine |
| 1-2 years | 11-14, naps included | AASM |
| 3-5 years | 10-13, naps included | AASM |
| 6-12 years | 9-12 | AASM |
| 13-17 years | 8-10 | AASM |
| 18-64 years | 7-9 | National Sleep Foundation |
| 65 and over | 7-8 | National Sleep Foundation |

The AASM's ranges for children are endorsed by the American Academy of
Pediatrics.

| Part | Points | Full points | No points |
| --- | --- | --- | --- |
| Enough sleep | 50 | Inside the recommended range | 3 h short, or 4 h over |
| Regular bedtime | 20 | Fell asleep within 30 minutes of the usual time (the median of the past week) | 2 h off |
| Settling | 10 | Asleep within 20 minutes of getting into bed | 60 minutes |
| Unbroken | 20 | Asleep 95% of the time from falling asleep to waking | 75% |

Points scale evenly between the two. A part that can't be worked out is
left out and the others scaled to 100: the usual bedtime needs three scored
nights in the week before, and settling needs the FP2 to have seen the
sleeper get into bed after 18:00. A night with less than 2 hours asleep
(slept elsewhere, or no reading) isn't scored.

Naps count toward the hours, as the guidelines for young children include
them, but not toward Unbroken: that looks only at the night's main sleep.
Deep and REM sleep aren't scored. The FP2 estimates them from radar, and
REM needs a heart-rate reading it doesn't always get, so a score built on
them would grade the sensor more than the sleep.

The `nights` attribute lists the last 30 nights' scores, hours and
recommended range, for charts. The score is a guide to habits, not a
medical measure.

## Card

The integration serves `custom:fp2-sleep-card` to every dashboard; no
resource to add.

```yaml
type: custom:fp2-sleep-card
name: Bedroom          # optional
device_id: …           # optional, when there is more than one FP2
show_score: true       # optional
show_vitals: true      # optional
```

It shows the sleep score, time asleep against the hours recommended for the
age, when sleep started and ended, a hypnogram of the night by Aqara's
times, the stage breakdown, what each part of the score came to, and heart
rate, breathing and movement through the night. It follows the dashboard's
light or dark theme and stacks the vitals on narrow screens. Until tonight
is over the score shows last night's, marked "Last night". It also works
with the SleepRadar app's entities, drawing stages by arrival time.

<img src="https://raw.githubusercontent.com/kkrankall/aqara-fp2-sleep/main/docs/images/card-phone.png" alt="The sleep card at phone width: a score of 86, the recommended 9–12 h and 1h 15m short this week, the score's four parts in two rows, and heart rate, breathing and movement stacked" width="360">

The screenshots use made-up data.

## Charts

Two charts for a dashboard view, with
[apexcharts-card](https://github.com/RomRider/apexcharts-card) from the
Marketplace. Both put each night at noon of the morning it ended.

**Score per night**, from the sleep score's `nights` attribute, coloured by
band:

```yaml
type: custom:apexcharts-card
graph_span: 30d
span:
  end: day
header:
  show: true
  title: Sleep score
  show_states: true
  colorize_states: true
series:
  - entity: sensor.aqara_fp2_sleep_sleep_score
    name: Score
    type: column
    show:
      in_header: false
    data_generator: |
      return (entity.attributes.nights || [])
        .filter((n) => typeof n.score === "number")
        .map((n) => {
          const [y, m, d] = n.date.split("-").map(Number);
          return [new Date(y, m - 1, d, 12).getTime(), n.score];
        });
  - entity: sensor.aqara_fp2_sleep_sleep_score
    name: Last night
    show:
      in_chart: false
      in_header: raw
apex_config:
  chart:
    height: 200
  plotOptions:
    bar:
      colors:
        ranges:
          - { from: 0, to: 69.99, color: "#ff7043" }
          - { from: 70, to: 84.99, color: "#f9a825" }
          - { from: 85, to: 100, color: "#43a047" }
  yaxis:
    min: 0
    max: 100
```

**Hours per night** against the recommended minimum. A night runs from 18:00
to 18:00 and crosses midnight, so daily statistics split it in two; this
takes each night's largest hourly maximum instead. The 18:00 hour is left
out: until the first poll after 18:00 the totals still hold the night
before, and its maximum would show that night's total.

```yaml
type: custom:apexcharts-card
graph_span: 30d
span:
  end: day
series:
  - entity: sensor.aqara_fp2_sleep_night_asleep
    name: Asleep
    type: column
    data_generator: |
      const res = await hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: new Date(new Date(start).getTime() - 30 * 3600e3).toISOString(),
        end_time: new Date(end).toISOString(),
        statistic_ids: [entity.entity_id],
        period: "hour",
        types: ["max"],
      });
      const nights = new Map();
      for (const row of res[entity.entity_id] || []) {
        const t = new Date(row.start);
        if (t.getHours() === 18) continue;
        const shown = new Date(t);
        if (shown.getHours() > 18) shown.setDate(shown.getDate() + 1);
        shown.setHours(12, 0, 0, 0);
        if (shown < new Date(start)) continue;
        nights.set(shown.getTime(), Math.max(nights.get(shown.getTime()) || 0, row.max || 0));
      }
      return [...nights].sort((a, b) => a[0] - b[0]);
  - entity: sensor.aqara_fp2_sleep_sleep_score
    name: Recommended minimum
    type: line
    stroke_dash: 5
    data_generator: |
      return (entity.attributes.nights || []).map((n) => {
        const [y, m, d] = n.date.split("-").map(Number);
        return [new Date(y, m - 1, d, 12).getTime(), n.min_h];
      });
```

For light, deep and REM stacked, give each its own series with the same
generator and set `stacked: true`. ApexCharts stacks columns by position,
so every series needs the same nights: keep nights with no REM as 0
rather than leaving them out.

## Polling

By default Aqara is polled every minute from 18:00 to 12:00 and while
someone is in bed, and every five minutes otherwise.

To poll only when someone is around, pick a local presence sensor for the
same room under **Settings › Devices & services › Aqara FP2 Sleep ›
Configure**, for example the FP2's own presence sensor in HomeKit. Then:

- while the sensor sees someone, Aqara is polled every minute, and the
  moment it turns on there is an immediate read;
- after it clears, polling stays at every minute for 30 minutes, because
  Aqara's last stage change (out of bed) arrives up to ~20 minutes late;
- while the room is empty, Aqara is polled every 30 minutes, which keeps
  FP2 online, the sleep-mode repair and the 18:00 night rollover current;
- if the sensor becomes unavailable, polling carries on as if someone were
  there.

Nothing is lost while polling is idle: the night totals, fell-asleep and
woke-up times come from Aqara's history log, and each read picks up from
the last one.

The night's stage changes are re-read at most every five minutes, the device
list (name, firmware, room) every six hours.

## Notes

- The heart rate depends on the FP2's view of the chest. If it holds one
  value all night, the FP2 is not getting a reading; REM sleep also needs
  it.
- Regions: USA (Americas and others), Europe, China, Russia, South Korea.
