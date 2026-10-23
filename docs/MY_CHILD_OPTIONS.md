# My child's options

A page where a parent sees, for one child and one start year, every realistic option in one
list: where the child can get in, how long the trip is, what it costs, and whether it
teaches the languages they want. Mike, 2026-10-23: parents never enter anything twice, and
whatever they enter is shared with the calculator, the list and the school pages.

Status: design, for review before building.

## What exists today, and where parents already enter things twice

| Fact | Where it is entered | Where it is kept |
|---|---|---|
| Birth year | list age picker; kindergarten calculator | URL `birth_year`; `admissionAnswers.kindergarten.answers.birthYear` |
| Home | list location box (GPS, typed, map); first-grade calculator address check | `userLocation` {lat, lng, address}; `admissionAnswers.home` {streetId, number} |
| Start year | list age picker; calculator year tabs | URL `target_year`; `admissionAnswers.<stage>.startYear` |
| Languages | list filter | URL `language_focus` |
| Budget | nowhere (price sort only) | none |

The list already shows, per school: the saved answers against its cut-off (`schoolVerdict.js`),
straight-line distance, rush-hour drive time (`docs/DRIVE_TIMES.md`), the headline fee, and can
hide schools "clearly below". So most of the page is packaging; the real work is the shared
profile and public transport times.

## Decisions

### 1. One profile, the existing store

`admissionAnswers` (src/admission/saved.js) becomes version 2 and holds the profile. No new
storage key. Shape:

```
{ version: 2,
  child: { birthYear } | null,
  home: { streetId?, number?, point?: { lat, lng, label } } | null,
  preferences: { languages: [...], yearlyBudget: number | null, travel: 'car' | 'transit' } | null,
  kindergarten: {...as v1, without answers.birthYear},
  grade_1: {...as v1} }
```

- **Migration.** A v1 value is read as v2: `child.birthYear` from the kindergarten answers,
  `home` unchanged, and the old `userLocation` (if any) becomes `home.point` when the
  parent has saved anything (opt-in already given). After that `userLocation` is no longer
  written. Unreadable values still count as nothing saved.
- **Birth year** is read from `child`; the kindergarten form shows it prefilled and writes
  it back there. The list age picker prefills from it when the URL has no `birth_year`
  (the URL still wins, so shared links keep working).
- **Home** keeps both parts because they answer different questions: the street id and
  number are what the official catchment table needs; the point is what distances and travel
  times need. Whichever is entered first, the other is derived once, on the parent's tap
  (decision 7: an address goes to OpenStreetMap only on a tap). A home set on the list's
  location box or the map becomes the profile home when the parent has a profile.
- **Current location** (GPS "near me", for a quick look) stays a separate, unsaved,
  in-memory location on the list. It never overwrites the home.
- **Opt-in stays.** Nothing is stored until the parent presses "Save on this device" (here,
  in the calculator, or on the list). "Forget" on any page forgets the whole profile.

### 2. The page

Route `/my-child` (BG `Моето дете`), linked from the header, the calculator's saved box and
the list.

- **Top: the profile, as a summary with "change".** Anything already saved shows as a filled
  line. Only what is missing is asked, in this order: birth year, start year (defaults to
  `planningYear()`; the stage follows from the age group), home, languages (optional), yearly
  budget (optional, "no limit" allowed), car or public transport.
- **Points questions are not re-asked here.** If the stage has a calculator and no saved
  answers, the page says "To see your chances, answer the points questions (about 2 minutes)"
  and links to the calculator, which returns here after saving. Without them the list still
  works, with chance shown as "not checked".
- **The list: grouped, not scored.** No weighted score: a single number mixing chance, time
  and money hides the trade-off a parent wants to make themselves and is hard to explain.
  Instead:
  1. Hard filters from the profile: the age group and start year; languages; budget (on the
     yearly estimate, see 4). Each says how many it hid, with "show them".
  2. Groups by chance, in this order: "You're above last year's cut-off", "Close to the
     cut-off", "No cut-off published" (incl. all private schools), "Below the cut-off"
     (collapsed). The same verdicts as the list cards, never a percentage.
  3. Inside a group, shortest trip first (car or public transport, as chosen).
  Each row: name, type, trip time, yearly cost estimate, languages, verdict line, link to
  the school page and "add to compare".
- **Map and full list** are one link away: "see these on the map" opens the search page
  with the same filters in the URL.

### 3. Travel times

Car: live already. Public transport: new, built the same way so the home never leaves the
browser.

- **Data.** Sofia's static GTFS (CC BY 4.0; credit "Център за градска мобилност ЕАД /
  Столична община"). A GitHub Actions job (like `road-graph.yml`) cuts a typical weekday's
  departures between 06:30 and 09:30, packs them for RAPTOR (stops, routes as stop
  patterns, trips sorted by time, footpath transfers between stops within ~400 m) and
  publishes one gzipped file next to the road graph.
- **Search.** In the existing routing worker: walk from home to stops within ~1 km
  (straight line × 1.3 at 4.5 km/h, to start), RAPTOR with up to 3 transfers for a 07:30
  departure (and arrival-by-08:00 as a later option), walk from stops to each school. One
  search reaches every school.
- **Shown as** "~N min by public transport", with the departure time it assumes and the
  operator credit.
- **Spike first.** Before UI: file size (target under 2 MB gzipped), phone search time
  (target under 1 s on a 6x slowed CPU) and a check of 30 home-school pairs against a
  journey planner. Metro is timetable-only in the feed, which is fine for this.

### 4. Cost

The yearly cost estimate is designed in the "Rastem.bg competitor analysis" thread (one
total per year for the selected stage, transport separate). This page uses that function
and does not build its own. Until it lands, the budget filter and the column use the
headline fee × months of the school year, labelled as fees only. State schools show "free"
plus any known fees.

### 5. Privacy

Unchanged promise: the profile stays in this browser, saving is opt-in, routing runs in the
browser on whole-city files, and the only thing that ever leaves is the tap-to-find address
lookup that exists today.

## Out of scope for the first version

- More than one child. The profile has one `child`; a second child is a later step
  (an array plus a switcher) if parents ask.
- Eighth grade. Its score depends on exam results, not on the home; the page sends those
  parents to the 8th-grade calculator.
- Accounts or syncing between devices.

## Build order

1. Profile v2 + migration, calculator and list reading/writing it (no visible new page).
2. `/my-child` page with car times and the fees-only cost, live flow and coverage test.
3. Public transport spike, then the GTFS job and the transit option.
4. Swap in the yearly cost estimate when it is merged.
