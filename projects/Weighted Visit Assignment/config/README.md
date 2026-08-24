# Configuration

Everything in here is data the lab owns. Changing any of it changes what the
board does on the next start, and none of it is a code change.

| File | What it decides | Safe to edit by hand |
|---|---|---|
| `engine.json` | The four criterion weights and every threshold | Yes, see below |
| `roster.json` | Who exists, what they are signed off on, capacity | Yes |
| `protocol-schedule.json` | Checkpoint offsets, windows and visit lengths | Only against the manual |
| `reliability-matrix.json` | Who may run which assessment | Only against the manual |
| `lab-resources.json` | Tech kits, closed days, working hours, vehicles | Yes, see below |
| `calendar-roles.json` | Which overlaid calendars are people and which are policy | Yes |
| `calendar-colors.json` | Which Outlook colour belongs to whom | Through the board, not by hand |
| `calendar-colors.example.json`, `calendar-map.example.json` | Templates to copy. Not read by anything | n/a |
| `PowerBI-Theme-ESD.json` | A Power BI theme. Untracked, and nothing here reads it | n/a, see below |

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

## Roles, and adding a person

`roster.json` is the single source for who the board schedules, and `roles`
is what the board asks about them. Never their name.

| Role | What it allows |
|---|---|
| `coordinator` | Schedules visits, has an in-lab day |
| `clinician` | Can be *the* clinician on a visit, within their solo range |
| `tech` | Can be the tech on a visit |
| `grad_student` | Goes on visits on their own calendar's time. The manual says to check with them before offering one, so the board flags it |

`solo_from` and `solo_to` are the manual's **"Visits Can Do Solo"** column,
transcribed: Lauren and Sanjana 1m–12m, Makenzie 6m–12m. Maggie, Ramiro, Sofia
and Morgan have no range printed beside their name, so they cannot be the
clinician however many assessments they are signed off on. Ramiro is reliable
in Bayley 9-12m and still techs a 9m visit rather than running it. Being
signed off on an assessment is not the same as being able to run the visit,
and the board asks both questions separately.

`only_checkpoints` overrides the range, for a row like Emma's which reads
*"only schedule for 36m visits"*.

**To add a graduate student**, copy a row and set:

```json
{
  "id": "G01",
  "name": "...",
  "roles": ["grad_student", "tech"],
  "confirm_before_offering": true
}
```

Leave `solo_from`/`solo_to` unset unless they can run a visit alone. No code
knows anyone's name, so nothing else changes.

## The roster

`roster.json` is the single source for who the board schedules. Both the demo
and a live board build their coordinators from it.

Adding somebody is a new row. Removing somebody is `"active": false`, not a
deletion, so past decisions still name a person who exists. What is real in
that file and what is synthetic is documented at the top of it.

## What the protocol schedule decides

`protocol-schedule.json` carries three things per checkpoint, all from the
manual:

- **when it is due** — `offset_months` from the family's anchor, with the
  window either side. Months rather than days because the lab schedules on a
  calendar: thirty days after 1 July is 31 July, but the manual's own example
  puts that visit on 1 August, and 1080 days is sixteen days short of a third
  birthday. `offset_days` remains as a fallback for a protocol that has not
  been given months
- **how long it runs** — the Visit Lengths table. A visit entered without a
  length takes this one rather than a flat two hours
- **whether anyone attends** — `remote: true`. NANO 24m is the only one:
  the manual says "we do not see participants for an in-person visit", so the
  board offers no staff for it, uses no vehicle and consumes no tech kit

### Which date a checkpoint counts from

Not one date per family. The manual gives three cases:

- **PT, 1m–24m** — counts from the **expected due date**. Its example: born
  1 June, one month premature, due 1 July, so the 1m ideal date is 1 August.
- **TD and ASIB** — counts from the **birthday**.
- **36m, everyone** — counts from the birthday, because the manual puts every
  participant on their third birthday *"regardless of status"*.

So a family records `participant_status`, `birth_date` and `due_date`, and the
checkpoint decides which applies. Storing a single pre-adjusted anchor would
put a preterm baby's early visits right and their 36m visit early by exactly
how premature they were.

`family_id_format` states what a participant ID looks like per protocol. NANO
is the manual's own wording, "four digits starting with 5", so a mistyped ID
is refused when the visit is entered instead of turning up on a calendar
invite. A protocol with no entry accepts anything, which is the right default
for a study still settling its conventions.

## The lab's physical limits

`lab-resources.json` holds the rules that stop a visit happening whatever the
scores say. All transcribed from the manual except one:

- **`tech_kits`** — `{"NANO": 2}`. *"No more than 2 NANO visits can happen at
  one time - we only have 2 NANO tech kits."* Buying a third kit is a number
  here, not a release.
- **`closed_weekdays`** — `[4]`, Friday. *"Fridays are designated lab meeting
  days."* A Friday can be taken with a logged override.
- **`working_hours`** — 9 to 5, Monday to Friday, with 30 minutes' grace.
  *"anything that is scheduled to go beyond 30 minutes outside of 9am-5pm"* is
  an out-of-hours visit. The grace is what stops a visit running ten minutes
  late from counting as an evening shift.
- **`vehicles`** — the Assigning a Vehicle rules. Two van-trained staff take
  the van outright; one is enough to take it; nobody trained, or a home marked
  van inaccessible, means the rental.

**`holidays` ships empty and that is not the same as "there are none."** The
manual gives the rule — *"On designated USC staff holidays, no visits should be
scheduled (no exceptions to this)"* — but not the dates. Add them as
`"2026-11-26"` strings. Until you do, the board cannot check the rule and says
so rather than quietly scheduling through a holiday. Unlike a Friday, a holiday
cannot be overridden, because the manual says it cannot.

## Two files that are transcriptions, not opinions

`protocol-schedule.json` and `reliability-matrix.json` are copied from the ESD
Lab Scheduling Manual and carry `confirmed: true` with the date they were
transcribed. They are not defaults anyone invented, and changing a number in
them means the board no longer matches the manual. Change the manual first.

## The Power BI theme

`PowerBI-Theme-ESD.json` turned up in this folder and is **not tracked by
git**, so it is on one machine only. **Nothing in this project reads it** —
the board's colours are tokens at the top of `frontend/styles.css`. It is
described here because a file sitting in `config/` should say what it is
rather than leave the next person guessing.

One thing to know before it is used on anything carrying the lab's name: its
palette is not the ESD one. It opens `#2C3E50`, `#27AE60`, `#E74C3C`, none of
which are canon. The lab's signature is discovery blue `#3366FF` with science
blue `#91BAF4` and cool blue `#E6EEFC`, and orange, yellow, red and pink as
accents that never dominate.

## Never commit

`calendar.env` and `calendar-map.json` are credentials and are ignored by git.
`calendar-colors.json` is written by the board when somebody matches colours to
people; it carries who confirmed it and when, and an unconfirmed map is treated
as a guess rather than as evidence.
