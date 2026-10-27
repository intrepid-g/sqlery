### `t/unit/app/test_schedules.py`

Celery's schedule contract (interval, crontab, solar) translated to sqlery scheduled tasks: "is this task due now, and when should the scheduler look again?"

**Interval schedules: due / not due**
- Two schedules with the same interval are equal; different intervals are not equal.
- A schedule survives serialisation (pickle/JSON) and comes back equal.
- Interval 10s, last run 10s ago; expect: due, next check in 10s.
- Interval 10s, last run 3s ago; expect: not due, 7s remaining.
- Interval is sub-second (0.1s); expect: due, next check in 0.1s.
- Interval of 0 or negative; expect: due, next check in 0 (never a negative wait).
- Interval of 1s, relative mode; expect: next check capped at 0.25s (aligned to the clock), non-relative: 1s.
- Hourly task last run 09:00, now 10:59:30 (it ran late); expect: due. Relative mode: next check in 30s (realigns to the hour boundary). Non-relative: next check in 3600s (drift is kept).

**Interval schedules: clock changes (DST) and timezones**
- Elapsed real time decides due-ness for plain intervals: a 10s task is due exactly 10 real seconds later even when the wall clock jumps forward an hour (spring forward) or repeats an hour (fall back).
- Same task 3 real seconds later; expect: not due, 7s remaining, on both sides of a DST change.
- Relative hourly task across spring-forward and fall-back; expect: due exactly at the next wall-clock hour boundary, and 2s remaining when checked 2s before it.
- Relative schedules across many zone quirks (NY, Lord Howe 30-minute DST, Havana midnight gap, Cairo, Singapore 90-minute shift, Nauru, Kathmandu 15-minute gap, Samoa skipped calendar day, Kwajalein 23-hour day, Goose Bay repeated midnight, Chatham, Casey), using either zoneinfo or dateutil zones; expect, for each: not due with the exact remaining seconds to the next wall-clock boundary, not due 1s before, due at the boundary, and still due (with shortened next check) 10s late.
- Daily non-relative task across DST change; expect: due after 24 real hours, not at the same wall-clock time.
- Repeated hour (fall back) is not skipped and not run twice for a 1h interval.

**Cron expressions: parsing**
- `*`, ranges (`1-10`), lists (`1,2,3`), steps (`*/2`, `2-9/5`), and combinations (`2-4/3,*/5,0-21/4`) expand to the right sets for minute/hour/weekday/day/month, honouring each field's minimum (days and months start at 1).
- Wrapping ranges (`11-1`, `55-5/3`) wrap around the field.
- Names work: weekdays (`fri`, `tuesday,sunday`, `mon-fri`) and months (`feb`, `Mar`, `april`, `aug-oct`); numbers as ints, strings, or tuples/iterators.
- Bad input is rejected at creation, never at run time: empty string, empty list item (`1,,2`), empty step (`*/`), negative number, below-minimum (`0` for months/days), above-maximum (`60` minute, `24` hour, `7` weekday, `32` day, `13` month), unknown names (`jaan`, `fooday`), non-iterable types (TypeError).
- A step range with too few parts is a parse error.
- Impossible dates are rejected: Feb 30, Apr 31, empty day or month lists ("never occurs"). Possible oddities are accepted: Feb 29, day 31 in some months, `30,31` with Feb and Mar.
- Cron from a string: `* * * * *` equals default; `0 8 5 * *` equals minute 0 hour 8 day 5; `* * * * SUN` works; a string without 5 fields raises ValueError.
- Equal specs written differently (`1, 2` vs `1-2`, `*/15` vs `1,16,31`) compare equal; different specs, other types, and interval schedules never equal.
- A cron schedule survives serialisation repeatedly (5 round trips), keeping its custom clock function.
- Default spec is every minute; repr contains the spec.

**Cron: next run calculation**
- Every-minute at 14:30:15; expect: next 14:31:00; at 14:59:15; next 15:00.
- Minutes [5,42] at 14:30; expect: 14:42 same hour; minutes [5,10,15] at 14:30; expect: 15:05.
- Hours [12,17] expect later today; hour [12] after noon; expect tomorrow 12:05.
- Weekday only (Saturday 14:30 after it fired); expect: next Saturday. Mon-Fri on a Saturday; expect: Monday 00:05.
- Specific month and day (Oct 18, Nov 13); expect: jumps to that month.
- Day-of-month 29 in January; expect: Jan 29, or skips to March in non-leap years, Feb 29 in leap years; Feb 29 itself repeats only after four years.
- Day-of-month AND weekday must both match (Monday the 18th; Saturday the 29th; Friday the 29th in months 1-10); expect: scheduler searches ahead to the next date satisfying all fields, even months or years away.
- Defensive: if the date can never match (bypassing validation), the search gives up with RuntimeError rather than looping forever.
- Daily 09:00 in Paris across DST end and start; expect: stays 09:00 local, with the right UTC offset.
- Negative UTC offset (Los Angeles) with day-of-month range 27-31 near midnight; expect: correct next run, no off-by-one day.
- Last run given in a different timezone than the schedule (e.g. Vilnius vs UTC, also with UTC disabled); expect: converted to the schedule's timezone first, next run same day 08:40 UTC, not a day later.
- Missed slots long ago (last run yesterday, now past the next day): next run is computed from last run and returned as already past (due), not skipped.

**Cron: is_due**
- Every minute, last run 61s ago; expect: due, next check at next minute boundary (within 1s).
- Last run this minute; expect: not due, same next check.
- Due-ness does not depend on the day of the week (Sat, Sun, Mon).
- Hourly :30 at 10:30, last run 06:30; expect: due, next check 3600s. At 10:29 with last run 09:30; expect: not due, 60s.
- Quarter-hourly (`*/15`) at :15 and :30 due; at :14 and :29 not due, 60s remaining.
- Daily 07:30, weekly Thursday, monthly (Thursday between 8th-14th), yearly (March Thursday 8-14), Feb 26-28 at 22:00: each is due at its slot with the full period as next check, and not due between slots with exact remaining seconds.
- Missed run catch-up: daily task whose slot passed since last run; with no starting deadline configured; expect: due immediately.
- With a starting deadline of 1h: missed slot more than 1h ago; expect: NOT due (skip, wait for next slot), remaining counts to the next slot. Missed slot within the deadline (even if several slots missed, or last run days ago); expect: due. Exactly on the deadline; expect: due.
- Deadline works with irregular schedules (`:00,:45`): the most recent missed slot counts, not an average gap; and across a DST jump.
- Last run in the future; expect: not due, next run computed beyond it (no duplicate fire).
- Last run exactly at the latest feasible time, or after it; expect: not due (no double run of the same slot).
- Negative UTC zone with day range at midnight; expect: due, next check 3600.

**Solar schedules (sunrise/sunset events; closest idea: event-driven scheduled task)**
- Missing optional dependency; expect: clear configuration error naming the extra to install.
- Invalid latitude (outside -90..90), longitude (outside -180..180), or unknown event name; expect: ValueError at creation.
- Equality by event and location; unequal to another event or interval schedule; serialisable; has repr.
- Due when remaining time is 0; not due when hours away.
- Across DST changes in zoneinfo or dateutil zones; expect: remaining time measured in real elapsed time (e.g. 37 min / 55 min).
- Dusk events use below-horizon angles; centre-of-sun events use only valid parameters.
- Days where the event never happens (polar night/midnight sun, summer astronomical dawn): skip to the next day it does happen, not due every night; the search is bounded (max days), then returns that bound instead of looping forever.
