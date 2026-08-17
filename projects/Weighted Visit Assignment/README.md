# ESD Weighted Visit Assignment

Fair and efficient assignment of coordinators to home visits, for the NICO and
NANO protocols. Three-layer policy, four-criterion score, and a full audit trail
so the weights can be validated rather than asserted.

**Start here:** [`ESD-Visit-Scheduling-v3-SPEC.md`](ESD-Visit-Scheduling-v3-SPEC.md)
is the specification and operating manual.
[`ESD-Visit-Scheduling-v3.pptx`](ESD-Visit-Scheduling-v3.pptx) is the deck for
the lab meeting.

```bash
make demo      # synthetic lab, score + assign a week, log every decision
make test      # 25 correctness anchors, under ten seconds
make debrief   # reports/debrief-<week>.md and .html
```

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
  demo.py               deterministic synthetic lab
  cli.py                command line

tests/test_engine.py    correctness anchors, incl. the hand-computed reference case
automation/             launchd agents: 5-min sync, nightly reconcile, weekly debrief
deck/                   math renders, deck build, rasteriser, brand QA
config/engine.json      the live parameter set (versioned, fingerprinted)
data/                   audit database (gitignored)
reports/                generated debriefs (gitignored)
```

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
| `python -m esd_scheduler drift` | weekly drift metrics |
| `python -m esd_scheduler debrief` | write the weekly debrief |
| `python -m esd_scheduler calibrate --write` | recalibrate the review band from the log |
| `python -m esd_scheduler sensitivity` | OAT, criticality, redundancy, revealed preference |
| `python -m esd_scheduler ahp judgments.json` | derive weights from pairwise comparisons |

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
