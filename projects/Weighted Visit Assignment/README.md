# ESD Weighted Visit Assignment

Fair and efficient assignment of coordinators to home visits, for the NICO and
NANO protocols. Three-layer policy, four-criterion score, and a full audit trail
so the weights can be validated rather than asserted.

**Live board:** <https://esd-visitboard.netlify.app> — public, no login, no
backend. Demonstration data.

**Start here:** [`ESD-Visit-Scheduling-v3-SPEC.md`](ESD-Visit-Scheduling-v3-SPEC.md)
is the specification and operating manual.
[`ESD-Visit-Scheduling-v3.pptx`](ESD-Visit-Scheduling-v3.pptx) is the deck for
the lab meeting.

```bash
make serve     # the Visitboard at http://127.0.0.1:8765
make static    # build the public, backend-free copy into dist-static/
make publish   # build it and deploy to Netlify
make demo      # synthetic lab (real roster names, synthetic attributes)
make test      # 66 anchors across engine, privacy, API and the static build
make debrief   # reports/debrief-<week>.md and .html
```

## The board

`make serve` starts the whole stack on one port. There is nothing to install:
the backend is stdlib Python over the same engine, and the frontend is one HTML
file with no bundler. Pick a visit, see who the board would send and why, see
who was ruled out and why, assign, and record a reason if you disagree.

## What is here

```
esd_scheduler/          the engine (pure standard library)
  config.py             versioned weights and parameters
  models.py             coordinators, families, visits, calendars
  calendarsync.py       Layer 0: Graph / Google adapters, staleness policy
  feasibility.py        Layer 1: seven hard predicates
  scoring.py            Layer 2: Phi, Omega, Psi, P
  ranking.py            Layer 3: rank, calibrated review band, surprises
  optimize.py           interval-scheduling DP, min-cost flow, regret
  engine.py             orchestration and commit
  store.py              append-only SQLite audit log
  drift.py              weekly fairness and drift metrics
  sensitivity.py        AHP, DEMATEL, conditional logit, OAT, criticality
  report.py             the weekly debrief
  demo.py               deterministic synthetic lab (roster from the shared calendar)
  ingest_outlook_pdf.py month-view PDF ingester (Tier 3, day-level only)
  graphcheck.py         the privacy break-it probes
  cli.py                command line

backend/                stdlib HTTP API over the engine (no dependencies)
  server.py             routes, static serving, path-traversal guard
  session.py            one lab state; turns engine output into screen language
  tests/test_api.py     boots a real server and talks HTTP to it
frontend/               one page, no framework, no build step
  index.html            structure
  styles.css            ESD design tokens
  app.js                fetch, render, assign (talks to the API or to StaticBoard)
  static-board.js       answers the same routes offline, for the public build
  assets/               lab and UofSC logos

tests/test_engine.py    correctness anchors, incl. the hand-computed reference case
tests/test_graph_privacy.py  free/busy-only guards on the Graph path
automation/             launchd agents: 5-min sync, nightly reconcile, weekly debrief
deck/                   math renders, deck build, rasteriser, brand QA
config/engine.json      the live parameter set (versioned, fingerprinted)
data/                   audit database (gitignored)
reports/                generated debriefs (gitignored)
```

## Two ways to run it

`make serve` runs the full stack: the Python engine answers every request live,
and assignments are written to the append-only audit log.

`make static` freezes the engine's answers into `dist-static/board.json` and
ships a copy that needs no backend at all — that is what is deployed publicly.
The frontend is the same files in both modes; it probes `/api/health` on boot
and falls back to `static-board.js`, which serves the identical routes from the
snapshot. The only thing the browser recomputes is the burden term, because
assigning a visit changes a coordinator's committed hours;
`tests/test_static_board.py` pins that against the engine.

## The model in one screen

**Layer 0** — calendar freshness. `fresh` auto-commits, `stale` makes the
assignment provisional and blocks family notification, `expired` fails Layer 1.
Staleness is a status, never a score penalty.

**Layer 1** — `W ∧ A ∧ ¬X ∧ ¬E ∧ K ∧ Cal ∧ Ramp`. Date window, open slot (with
travel buffer), no calendar clash, no family exclusion, credentials, calendar
fresh, onboarding cap. No score rescues a failure.

**Layer 2** — `S = 0.45·Φ + 0.15·Ω + 0.30·Ψ + 0.10·P`

- **Φ** continuity: `(1 − e^(−k/κ)) · e^(−Δ/τ)`, direction flipped by the
  family's preference flag. `k = 0 ⇒ Φ = 0`, which is where the cold-start bug
  dies by construction.
- **Ω** family preference: named 1.0, other-named 0.35, soft-avoid 0.0,
  **nothing on record 0.5**. Absent data is neutral, never extreme.
- **Ψ** burden relief: `1 − clip(B/Cap)` where `B = committed + duration +
  γ·travel/60`. Capacity-referenced, not pool-referenced.
- **P** protocol continuity: did this person run the previous checkpoint.

**Layer 3** — top 3, review band calibrated by Monte Carlo over the weight
simplex, per-decision selection stability, tie-breaks logged with their seed.

## Commands

| | |
|---|---|
| `python -m esd_scheduler init` | write config, create the audit database |
| `python -m esd_scheduler demo` | synthetic lab, full assignment cycle |
| `python -m esd_scheduler score V001` | explain one visit's ranking, line by line |
| `python -m esd_scheduler plan-week` | greedy vs optimiser, with measured regret |
| `python -m esd_scheduler sync` | pull calendars, record the SLO |
| `python -m esd_scheduler import-calendar cal.pdf` | read an Outlook PDF print (`--record` to log it) |
| `python -m esd_scheduler drift` | weekly drift metrics |
| `python -m esd_scheduler debrief` | write the weekly debrief |
| `python -m esd_scheduler calibrate --write` | recalibrate the review band from the log |
| `python -m esd_scheduler sensitivity` | OAT, criticality, redundancy, revealed preference |
| `python -m esd_scheduler ahp judgments.json` | derive weights from pairwise comparisons |

## Uploading an Outlook calendar

Print the shared Outlook calendar to PDF and drop it on **Sync calendars** in the
board, or run `make import-calendar FILE=cal.pdf`. There is nothing to configure
first: the file identifies its own people, and the board syncs on upload.

**How it knows who is who.** Outlook stacks every shared calendar onto one page
and distinguishes them only by colour. The print looks like it has no legend —
but it does, hidden in plain sight: each calendar's name in the header is drawn
in that calendar's own colour. The importer reads those colours, matches the
names to the roster, and attributes every entry with no human in the loop. A
calendar that is not on the roster (the export owner's own, typically) is left
unattributed rather than matched to someone.

If a particular export loses its header colours, the board falls back to a
stored colour map and asks you to match them by hand once. A printed legend
always beats a stored map — the file is evidence, the map is someone's memory of
it.

**What the upload is worth** depends entirely on which view was printed:

| Printed view | Tier | What it yields | Can it decide? |
|---|---|---|---|
| Work Week / Day | 2 | real start **and end** times | yes, applied on upload |
| Month | 3 | day-level availability per person | shows load; never books a time |

A month grid answers "who has room this month" — the board draws a per-person
month grid of clear / some commitments / spoken for. It cannot answer "free at
2pm", because no end time is printed anywhere on the page. For that, print
**Work Week**.

**The lab's own calendars.** An Outlook overlay mixes two kinds of calendar,
and they must never be treated the same way. A person's calendar lists times
they are *not* free. A policy calendar often lists the opposite:

| Calendar | Role | It marks time that is |
|---|---|---|
| Offered Times ESD | `offered_window` | **allowed** &mdash; a visit must sit inside one |
| Clinician Shifts | `clinician_shift` | **covered** &mdash; needed for clinical visits |
| PSYCHOLOGY, ESDI LAB | `lab_space` | **taken** &mdash; the room is already booked |
| A coordinator's name | `coordinator` | **taken** &mdash; that person is busy |

Roles are guessed from the printed calendar name and can be corrected in
`config/calendar-roles.json`. Getting the polarity backwards is the failure that
matters: reading "Offered Times ESD" as busy time would rule out exactly the
slots the lab set aside for visits. Anything unrecognised stays `unknown` and
affects nothing.

Each filter reports `pass`, `fail` or **not applicable**, and the last is not a
quiet pass &mdash; it means the calendar was absent or empty for that range, so
nothing was checked. A filter with no windows shows as off rather than being
hidden, because an empty calendar and a missing one mean different things.

**Cut-off days.** A month cell fits a fixed number of rows and prints no "+N
more" marker — it simply stops drawing, and afternoons go first. The importer
detects this by noticing many cells stopping at exactly the same count, and
marks those days **not visible** rather than clear. An empty-looking afternoon on
one of them is not evidence of free time.

**Corrections.** Blocks read from a work-week print take effect immediately,
because a PDF's event boxes are vector rectangles and its time gutter is vector
text — the times are read exactly, not guessed at by OCR, so there is nothing to
proofread. Anything wrong can still be rejected under *What was read from the
PDF*, and the board updates at once.

Uploaded PDFs are written to `data/uploads/`, which is gitignored: a month export
carries event titles for everyone on the page.

## Status

The machinery is pilot-ready. **The weights are not yet validated** — they are
analyst-assigned, and everything in `sensitivity.py` exists to fix that before
the pilot concludes. Until then, treat a ranking as a shortlist rather than a
decision.

Six things are needed from the team, listed on the last slide of the deck and in
§7 of the spec. The two that block the least and matter the most: elicit γ (one
question) and set real per-coordinator capacity.

## Dependencies

Engine and tests: Python 3.9+, standard library only.
Deck build: `matplotlib`, `python-pptx`, `Pillow`, `fontTools`.
