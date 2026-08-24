# Configuration

Everything in here is data the lab owns. Changing any of it changes what the
board does on the next start, and none of it is a code change.

| File | What it decides | Safe to edit by hand |
|---|---|---|
| `engine.json` | The four criterion weights and every threshold | Yes, see below |
| `roster.json` | Who exists, what they are signed off on, capacity | Yes |
| `protocol-schedule.json` | Checkpoint offsets, windows and visit lengths | Only against the manual |
| `reliability-matrix.json` | Who may run which assessment | Only against the manual |
| `calendar-roles.json` | Which overlaid calendars are people and which are policy | Yes |
| `calendar-colors.json` | Which Outlook colour belongs to whom | Through the board, not by hand |
| `calendar-colors.example.json`, `calendar-map.example.json` | Templates to copy. Not read by anything | n/a |

## The weights

`engine.json` holds the part most likely to need tuning:

```json
"weights": { "phi": 0.45, "omega": 0.15, "psi": 0.30, "p": 0.10 }
```

    phi     continuity            the family has seen this person before
    omega   family preference     they asked for, or asked to avoid, somebody
    psi     burden relief         spreading load across the team
    p       protocol continuity   the same rater as the previous checkpoint

They must sum to 1.0. A set that does not is refused at startup with the total
and the four values printed, so a typo reads as a typo. Changing them takes
effect on the next start, and the board reports which set it is running under
at `/api/health` as `weight_vector_id`, with the config fingerprint appended so
the label always moves with the numbers.

Raising one weight means lowering another. `esd_scheduler/sensitivity.py` will
perturb one at a time and report how much the ranking moves, which is the
honest way to see whether a change matters before adopting it.

## The roster

`roster.json` is the single source for who the board schedules. Both the demo
and a live board build their coordinators from it.

Adding somebody is a new row. Removing somebody is `"active": false`, not a
deletion, so past decisions still name a person who exists. What is real in
that file and what is synthetic is documented at the top of it.

## Two files that are transcriptions, not opinions

`protocol-schedule.json` and `reliability-matrix.json` are copied from the ESD
Lab Scheduling Manual and carry `confirmed: true` with the date they were
transcribed. They are not defaults anyone invented, and changing a number in
them means the board no longer matches the manual. Change the manual first.

## Never commit

`calendar.env` and `calendar-map.json` are credentials and are ignored by git.
`calendar-colors.json` is written by the board when somebody matches colours to
people; it carries who confirmed it and when, and an unconfirmed map is treated
as a guess rather than as evidence.
