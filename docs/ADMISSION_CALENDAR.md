# Admission calendar

`/calendar` lists every date a Sofia parent acts on, from nursery to eighth grade, for the
child's birth year, and offers it as a calendar feed to subscribe to (Google Calendar,
Apple Calendar, Outlook) or download. Code: `frontend/src/admission/calendar.js` (which
dates), `ics.js` (the feed), `src/prerender/calendars.js` (the build writes the feeds),
`src/components/CalendarPage/`.

## No parent data anywhere

The feeds are static files, one per birth year and language:
`/calendar/sofia-born-2024.ics`, `/en/calendar/sofia-born-2024.ics`. The build writes
one for every birth year with something still ahead (eighth-graders to be, up to next
year's babies). A feed's URL says only the birth year, so subscribing stores nothing
about the parent, and the site keeps no list of subscribers.

Calendar apps re-fetch a subscribed feed on their own (the feed asks for once a day;
Google takes up to a day or so). Each event's UID is fixed
(`born2024-kindergarten-2027-ranking-1@schooldecider.com`), so when a deploy changes a
date the app moves the event rather than adding a second one. That is how an estimated
date becomes the official one: the rule file changes, the next deploy rebuilds the
feeds, and every subscribed calendar follows.

The birth year comes from the one saved profile on the device (`child.birthYear`, read
with `childBirthYear` in `src/admission/saved.js`; the kindergarten calculator reads and
writes the same field), so a parent who saved calculator answers never types it again.
A year picked on the page goes in the URL (`?born=2024`, the link the feed's events point
back to) and is saved only if the parent taps "Remember this year on this device".

## Steps and dates

| Step | September start | Dates from |
|---|---|---|
| Nursery (optional) | birth year + 1 | kindergarten rule file (one ordinance, one ИСОДЗ calendar) |
| Kindergarten, first group | + 3 | kindergarten rule file |
| Preparatory group at a school (optional) | + 5 | kindergarten rule file |
| First grade | + 7 | first-grade rule file |
| НВО in 7th grade and eighth grade | + 14 | eighth-grade rule file (national) |

Each campaign's dates are `schedule.milestones` in its rule file:

```json
"milestones": [
  { "key": "free_places", "date": "2026-03-30" },
  { "key": "applications_lock", "date": "2026-05-21", "time": "12:00" },
  { "key": "ranking", "round": 1, "date": "2026-05-22" },
  { "key": "enrol", "round": 1, "date": "2026-05-26", "end": "2026-06-05", "time": "16:00" },
  { "key": "current_rankings", "date": "2026-09-04", "end": "2026-11-27" }
],
"milestones_source": "isodz-calendar-2026",
"estimate_by": "weekday",
"published_by": "01-05"
```

Keys: `free_places`, `applications_lock` (the system locks; the last moment to apply or
change an application), `applications` (eighth grade), `ranking`, `enrol`,
`current_rankings`, `nvo_bg`, `nvo_math`. Every key needs a label per stage in
`calendar.milestones` in both i18n files (a test checks). `milestones_source` names an
entry of the file's `sources`. Sofia's are copied from the ИСОДЗ calendar
(`https://kg.sofia.bg/api/public/calendarEvents`, which the rule watch follows; only the
current year is there), eighth grade's from the Minister's yearly schedule.

**Campaigns without official dates.** When a campaign's file has no milestones (or no
file exists yet), the latest official milestones are moved to its year and the step is
marked estimated: `estimate_by: "weekday"` keeps the weekday (a Friday ranking stays a
Friday; Sofia kindergartens), `"date"` keeps the day of the month (Sofia first grade, whose
dates follow the ordinance's calendar dates). An estimated campaign shows only its first
round, plus, when `published_by` is set, a "check the official dates" event on that day
(Sofia's schedule is due by 5 January, чл. 13 ал. 1). A test checks that the estimate
matches the dates the campaign's own estimated file already gives. Separately, a step
whose rules are not verified for that campaign says the rules may still change.

**When the official schedule comes out**, add its `milestones` to the campaign's file and
drop `schedule.estimated` (the rule watch's drafter is told to). Nothing else changes: the
next deploy updates the page and every feed.

Past dates are left out: a campaign still running shows only what is left of it.
