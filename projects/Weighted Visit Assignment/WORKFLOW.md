# One visit, end to end

Every file on the main path carries a banner saying where it sits — `STEP n OF
9` in the engine and the backend, `SCREEN x` in the frontend. This is the same
map in one place, following a single visit the whole way so the order is
visible without opening twelve files.

The visit followed here is **family 5901's 9m NANO visit**, which is a demo
family: real participants are in a different block of ids, and none of them
appear in this repository. Everything else — the coordinators, the rules, the
arithmetic — is the lab's own.

```
  a printed calendar                a study record
        │                                 │
   1  read the file              4  which families, when due
   2  what is this calendar              │
   3  whose time is this                 │
        └──────────────┬─────────────────┘
                       │
                 5  who exists
                 6  who can go at all
                 7  how good a fit
                 8  two people and a slot
                 9  one board, one answer
                       │
              A..F  the screens
```

---

## 1 · Read the file

`esd_scheduler/ingest_outlook_pdf.py` — a PDF is vector extraction. The
rectangles and the header legend come out of the file, so the times are exact
rather than measured.

`esd_scheduler/ingest_image.py` — a screenshot is measured against the ruled
grid instead, and refuses below 75 pixels per hour. At 63 px/hour it was
measured putting a block **135 minutes** from where it was drawn.
`ingest_image_neural.py` is an optional second pass, off by default.

```
345 vector drawings, 209 text lines
legend: "Bell, Margaret", "Puttock, Lauren", "Offered Times ESD", …
one rectangle -> Wed 26 Aug 10:00–12:00, exact to the minute
```

## 2 · What is this calendar

`esd_scheduler/calendar_roles.py` — each printed name is classified. Polarity
is the thing to get right: reading a positive calendar as busy would rule out
exactly the slots the lab set aside for visits.

```
"Puttock, Lauren"      -> coordinator      time TAKEN
"Offered Times ESD"    -> offered window   time a visit MAY use
"PSYCHOLOGY, ESDI LAB" -> lab room         not a person
```

## 3 · Whose time is this

`esd_scheduler/calendar_import.py` — the legend maps colour to person.
Anything it cannot name is reported, never guessed.

```
10 overlaid calendars, 0 needing a human to identify
Bell 13, Oak 9, Puttock 10, Soto 20, Tous 6   = 58 blocks
Lucas-Mariano: recognised, not attributed — he is active: false
```

## 4 · Which families, and when are they due

`esd_scheduler/redcap.py` → `backend/nano.py` for the screen, with
`esd_scheduler/schedule.py` holding the manual's checkpoint table.

REDCap has no window fields and should not. It holds the anchor dates; the
manual decides what they mean:

```
PT, 1m–24m               -> count from the due date
everyone else, and 36m   -> count from the birthday
born 1 Jun, due 1 Jul    -> the 1m visit's ideal date is 1 Aug
```

## 5 · Who exists

`esd_scheduler/roster.py` — roles, the manual's solo range, van training, and
the name each document uses for the same person.

```
the export prints "Soto, Morgan"; the manual says "Makenzie"
one person, one row, both names resolve to C05
```

## 6 · Who can go at all

`esd_scheduler/constraints.py` runs eight hard gates in order, first failure
wins. `eligibility.py` answers the same question per seat, so a screen can say
which rule stopped somebody. `resources.py` holds the physical limits.

```
1 NDD override   2 assessments   3 in-training buddy   4 solo range
5 closed day     6 availability  7 tech kits           8 capacity

Margaret Bell, 9m: assessments FAIL (no Bayley 9–12m)
  -> cannot be the clinician, CAN be the tech
```

Gates 2 and 3 are about the clinician's seat and are skipped for a tech. The
manual asks the clinician to run the assessments and asks nothing of the tech.

## 7 · How good a fit

`esd_scheduler/scoring.py` — four criteria, each 0 to 1, weighted and added.
The weights are in `config/engine.json` and are dropdowns on the board.

```
S = 0.45·φ + 0.15·Ω + 0.30·Ψ + 0.10·P

knows the family  0.000 × 0.45 = 0.000
family's choice   0.500 × 0.15 = 0.075
has room          0.665 × 0.30 = 0.200
did the last one  0.000 × 0.10 = 0.000
                                 ─────
                                 0.275
```

## 8 · Two people and a slot

`esd_scheduler/pairing.py` — the manual staffs a visit with one clinician and
one tech, so the pair is the thing being chosen.

```
Lauren Puttock + Sanjana Oak    0.263   Mon 9:00 AM   best match
Lauren Puttock + Margaret Bell  0.201   Wed 9:00 AM
```

The slot chooser honours Fridays, in-lab days and university holidays itself,
because it picks its own slot rather than reusing a gate's.

## 9 · One board, one answer

`backend/session.py` holds the state and is the only place engine output turns
into what a screen needs. `server.py` is routes and nothing else; a second
implementation of the logic would drift from the first.

```
/api/visit and /api/assign share one eligible pool
```

---

## The screens

| | file | what it is for |
|---|---|---|
| A | `core.js` | state, fetch, routing. Loaded first |
| B | `boot.js` | first fetch, nav wiring, refresh loop |
| C | `nano.js` · `calendars.js` · `team.js` | the three ways in: a participant, a print, the week |
| D | `assign.js` | the queue and the visit's own facts |
| E | `mindmap.js` | the decision, opened one level at a time |
| F | `logic.js` · `settings.js` | the workings, and the knobs |

```
Family 5901  ->  Pairs that work  4
                 Who can go       4
                 Ruled out        0
   -> Lauren Puttock + Sanjana Oak  0.263  Mon 9:00 AM  best match
```

---

## Running each step on its own

```bash
make doctor          # can this machine run any of it
make redcap-sync     # step 4, into a gitignored cache
make ocr-accuracy    # steps 1–3, scored against known times
make simulate        # steps 4–9, narrated
make smoke           # all of it over real HTTP, against real prints
make serve-scratch   # the board, against a throwaway config
```

`tests/test_docs.py` checks this file still names files that exist, so the map
cannot quietly rot away from the tree it describes.
