# The engine

Pure Python, standard library only. It decides which two people to send to a
family visit, and records why. Nothing here imports the web server, so the same
engine answers the board, the command line and the scheduled jobs.

Read it in layer order. A visit falls through the layers and stops at the first
one that rules everybody out, which is also the order the board explains a
decision in.

## The four layers

| Layer | Question | Module |
|---|---|---|
| 0 | Is the calendar evidence current enough to trust? | `calendarsync.py` |
| 1 | Who is outright ineligible? | `feasibility.py`, `constraints.py` |
| 2 | Of those left, who scores best? | `scoring.py` |
| 3 | Is the winner clear, or is it too close to call? | `ranking.py` |

Layer 1 is boolean and short-circuits: the first failed gate is the reason.
Layer 2 is the weighted sum of four criteria and never overrides Layer 1.
Layer 3 decides whether the top score is far enough clear of the second to
recommend on its own.

## Reading a calendar

    ingest_outlook_pdf.py   vector extraction from a printed Outlook PDF
    ingest_image.py         the same from a screenshot, measured in pixels
    calendar_import.py      tiering, attribution, and what counts as evidence
    calendar_roles.py       what an overlaid calendar means: person, or policy

A PDF is read exactly. An image is measured and is always approximate, so it
is filed at a lower evidence tier and needs confirming before it counts. The
rule underneath all of it: an absent calendar means *unknown*, never free.

## Deciding

    engine.py       orchestration: score one visit, plan a week, commit
    feasibility.py  Layer 1 hard eligibility
    constraints.py  the individual gates, in the order they fire
    scoring.py      Layer 2, the weighted sum
    ranking.py      Layer 3, the review band, ties, surprise detection
    pairing.py      two people per visit: one clinician, one tech
    optimize.py     week-at-a-time assignment, and when to escalate
    scenarios.py    every gate combination, enumerated rather than sampled

## Knowing what is owed

    schedule.py     when each family's next checkpoint is due
    availability.py who is free, slot by slot, once calendars are in

## The lab itself

    models.py       the domain objects everything else passes around
    resources.py    the lab's physical limits: kits, closed days, vehicles
    roster.py       the people, read from config/roster.json
    lab.py          building a lab state from the roster, inventing nothing
    demo.py         the synthetic lab: invented families, visits and busy time

`lab.py` and `demo.py` share the coordinator half deliberately, so adding
somebody to the roster shows up in both and they cannot drift apart.

## Configuration, audit and reporting

    config.py       the weights and thresholds, versioned and fingerprinted
    store.py        append-only SQLite audit log
    report.py       the weekly debrief
    drift.py        weekly drift detection
    sensitivity.py  weight validation and one-at-a-time perturbation
    calendarsync.py Layer 0 freshness, and the Graph client the CLI can use
    graphcheck.py   the automated "try to break it" probes
    cli.py          the command line entry point

## Two things worth knowing before changing anything

**The weights live in `config/engine.json`, not in code.** Editing the four
numbers changes the ranking on the next start. They must sum to 1.0 and the
error says so in readable numbers if they do not. The identity reported and
stored is the label plus the config fingerprint, so an edit files a new audit
row rather than overwriting the numbers behind an earlier decision.

**Nothing here reaches the network on the board's path.** `calendarsync.py`
and `graphcheck.py` contain a Microsoft Graph client, and only `cli.py` can
reach it. The server builds a `MockProvider` and reads uploaded calendars.
