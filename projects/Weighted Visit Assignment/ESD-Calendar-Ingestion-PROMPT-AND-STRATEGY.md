# Calendar Ingestion: Prompt, Changes and Strategy

**For the ESD weighted visit-assignment engine (v3.0.0).**
Written against a real artefact: `Calendar - Shrivastava, Namit - Outlook.pdf`,
a month view of August 2026 with seven overlaid calendars.

Companion to [`ESD-Visit-Scheduling-v3-SPEC.md`](ESD-Visit-Scheduling-v3-SPEC.md).
Extractor: [`esd_scheduler/ingest_outlook_pdf.py`](esd_scheduler/ingest_outlook_pdf.py).
Worked output: [`data/ingest/outlook-2026-08.json`](data/ingest/outlook-2026-08.json).

---

## 0. The headline

The engine's Layer 0/1 need **intervals**: a start, an end, and a person. An
Outlook **month-view PDF cannot supply any of the three.** It prints a start
time, a status word, and a colour chip, and it silently drops rows past a
seven-per-day ceiling.

So the strategy is not "parse the PDF into availability". It is:

> **Tier the sources. Let the PDF do the one job it is actually good at —
> characterising demand patterns and cross-checking the live feed — and let
> Microsoft Graph remain the only thing that can veto a coordinator in Layer 1.**

Everything below follows from that. Building it the other way round produces a
scheduler that confidently books over meetings it never saw.

---

## 1. What the real export actually proved

Run on the attached file, 195 entries across 2026-07-27 → 2026-09-04:

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| 1 | **No end times anywhere** | 189 of 195 entries have a start; 0 have an end | No entry can become an interval. Time-level scheduling is impossible from this source. |
| 2 | **Silent row-cap truncation** | 17 of 33 day cells hold exactly 7 rows; no `+N more` marker printed | On half the days, data is missing with **no in-band signal that it is missing**. |
| 3 | **Truncation cuts the afternoon** | Capped cells' last printed start: median 11:40. Uncapped: 09:45 | An empty afternoon on a capped day is missing data, **not** free time. This inverts the naive read of the calendar. |
| 4 | **No colour legend** | Header is plain comma-joined text at 11 pt; zero swatches in the header band | Six of seven calendars cannot be attributed to a person. |
| 5 | **One colour *is* resolvable** | 14 of 14 titled events are `navy`; 0 of 175 non-navy entries carry a title | `navy` = the export owner's own calendar. Only the owner's calendar renders titles; shared calendars render free/busy only. Confidence ≈ 0.85. |
| 6 | **Extreme morning loading** | 117 of 189 starts at 09:00; 154 of 189 before noon; only 24 at or after 12:00 | Real signal about lab rhythm — but see finding 3 before treating afternoons as open. |
| 7 | **Two hard recurring blocks** | `Check in- Namit/Je` weekly Thu 09:00 (×5); `Lab Meeting` weekly Fri 09:30 (×5) | Genuinely usable. These belong in the roster as standing commitments, not as calendar reads. |
| 8 | **No timezone printed** | No tz string anywhere in the page text | Cannot normalise. Must be supplied out of band. |

Findings 2 and 3 together are the important ones. They mean the failure mode of
this source is not "incomplete" but **"incomplete in a direction that looks like
availability"**. That is the worst possible bias for a scheduler.

---

## 2. Source tiering doctrine

Add an explicit tier to every availability fact. The tier, not the recency,
decides what a fact is allowed to do.

| Tier | Source | Grants | Can veto in Layer 1? |
|---|---|---|---|
| **1** | Graph `getSchedule` / Google `freeBusy` | Real intervals with status | **Yes.** The only tier that can. |
| **2** | Work-week or day-view export, ICS file, manual entry | Intervals, possibly stale | Yes, subject to the Layer 0 freshness gate |
| **3** | **Month-view PDF export** | Day-level pressure, recurring patterns, roster discovery | **No. Never.** |

Hard rule to encode: **a Tier-3 fact may lower a score, seed a prior, or raise a
flag. It may never mark a coordinator available, and it may never mark one
unavailable.** The existing engine already refuses to let a score rescue a Layer 1
failure; this is the mirror of that rule, and it is what stops a lossy PDF from
quietly becoming ground truth.

The extractor enforces this by *type*: it returns `PdfEntry`/`DaySignal`
objects, never a `CalendarSnapshot`. `feasibility.py` only accepts
`CalendarSnapshot`. A Tier-3 fact cannot reach Layer 1 because it is the wrong
shape, not because someone remembered a rule.

---

## 3. The full agent prompt

Copy-pasteable. This is the original brief, hardened by what the real file
taught. Changes from the draft are marked **[HARDENED]**.

````markdown
ROLE
You are an availability-ingestion agent for the ESD Lab visit-scheduling engine
(v3). You convert a visible Outlook calendar surface into structured scheduling
intelligence. You are an *evidence extractor*, not a scheduler of record.

PRIME DIRECTIVE  [HARDENED]
Classify the source before extracting anything, and never let a source do more
than its class permits.

  TIER 1  Graph getSchedule / Google freeBusy -> real intervals. May veto.
  TIER 2  work-week / day view, ICS, manual  -> intervals, maybe stale. May veto.
  TIER 3  MONTH VIEW (any form)              -> day-level only. MAY NEVER VETO
                                                AND MAY NEVER CONFIRM.

If the surface is a month view, you MUST set
`extraction_grade = "TIER-3 / DEGRADED"`, you MUST leave
`free_intervals_per_person`, `blocked_intervals_per_person` and
`shared_feasible_intervals` as empty arrays, and you MUST state in the
coordinator summary that time-level scheduling is not supported by this source.
Emitting a time slot from a month view is a failure, not a best effort.

DETECTION TASKS
1. Identify the view type (day / work week / week / month / split / schedule).
2. Read the visible date range, and expand it to true ISO dates. Month grids
   label only the first cell of each month; every bare number inherits the month
   most recently labelled. [HARDENED: assigning bare numbers to the view's own
   month misdates the entire leading spill week.]
3. Identify every overlaid calendar named in the header.
4. Read the timezone. If none is printed, set `timezone: null` and add an
   uncertainty note. Do not infer it from the viewer's locale. [HARDENED]

EXTRACTION TASKS
For every printed entry, capture:
  date, start_time (if printed), end_time (if printed), status_label,
  event_title (if printed), calendar_color_id, confidence_score,
  evidence_text (minimal verbatim copy).

STATUS RULES
- Rank uncertainty as Busy > Tentative > Free.
- The printed status word is the evidence. Where the word and the colour tint
  disagree, the word wins. [HARDENED]
- Colour tint is a secondary signal only: a solid chip implies busy, a pale
  tint of the same hue implies tentative or free.
- An all-day "Free" row is NOT an empty day. It means a free-marked all-day
  item exists. Record it as `free` with a note, never as absence. [HARDENED]

ATTRIBUTION RULES  [HARDENED — this whole block is new]
- A colour identifies WHICH overlaid calendar an entry belongs to. It does NOT
  identify a person unless a legend is visible.
- If no legend is visible, emit `calendar_color_id` and set `participant: null`.
  Do not map colours to the header name order. Outlook assigns colours by the
  order calendars were added, which is not the order they are listed.
- ONE inference is permitted, because it rests on a permission asymmetry rather
  than a guess: only the export owner's own calendar renders event TITLES;
  shared calendars render free/busy only. If exactly one colour carries every
  titled event and no other colour carries any, that colour is the owner's own
  calendar. Assert it at confidence <= 0.85 and record the evidence counts.
- Any other colour-to-person claim is fabrication. Refuse it.

COMPLETENESS RULES  [HARDENED — this whole block is new]
- Blank is never free. Absence of a printed entry is absence of evidence.
- Detect row-cap truncation: if the maximum entries-per-cell is reached by
  multiple cells, the view is truncating. Month-view prints commonly cap at 6-8
  rows and may emit NO "+N more" marker.
- Report `truncated_cells` explicitly, and set `uncertainty_penalty` high for
  every one of them.
- Test the truncation direction: compare the last printed start time on capped
  cells against uncapped cells. If capped cells run later, the entries being
  dropped are the later ones, and late-day emptiness on those cells is missing
  data. State this in the summary.

NORMALIZATION
Emit per entry:
  person_id_or_name (null if unresolved), calendar_color_id, date,
  interval_start, interval_end (null if not printed), availability_state,
  certainty_flag, confidence_score, source = "visible_calendar", tier.

DERIVATION
Derive only what the tier supports.
  TIER 1-2: free/blocked intervals per person, shared feasible intervals,
            earliest and best feasible slot, ranked fallbacks.
  TIER 3:   schedule_density_by_day, start_hour_histogram,
            recurring_blocks_detected, joint day pressure, ranked DAYS.
            Every interval array stays empty. [HARDENED]

Density at Tier 3 is a LOAD PROXY, not utilisation: with no end times you can
count commitments, you cannot measure consumed hours. Say so. [HARDENED]

SCORING
Score(slot) = w1*participant_overlap + w2*predicted_visit_success
            + w3*temporal_proximity - w4*conflict_penalty
            - w5*fragmentation_penalty - w6*uncertainty_penalty

At Tier 3, w5 is unavailable (fragmentation needs intervals): set it to 0 and
note the omission rather than approximating it. [HARDENED]

final_slot_score = alpha * calendar_feasibility + beta * predictive_visit_score
If the predictive model supplies nothing, set predictive_visit_score = null,
beta = 0, and report calendar feasibility alone. Do not substitute a default.
[HARDENED]

OUTPUT
Return exactly two sections: SECTION 1 JSON, SECTION 2 COORDINATOR SUMMARY.
The JSON MUST carry `extraction_grade` and a
`blocking_gaps_before_time_level_scheduling` array. [HARDENED]
The summary MUST lead with what the source cannot support before it offers a
recommendation. [HARDENED]

FAILSAFE
- Partial visibility -> partial output with explicit uncertainty. Never fill.
- No exact times -> summarise by day and say time-level scheduling needs an
  expanded export.
- No common slot -> identify the least-conflicted near-feasible options and say
  they are least-conflicted, not feasible.
- Any claim you cannot point at a pixel for does not go in the output.
````

---

## 4. Changes to the engine

### 4.1 New module (built)

`esd_scheduler/ingest_outlook_pdf.py`

- Reconstructs the grid from weekday headers and day-number cells, so every
  entry maps to a real ISO date, with the month-label inheritance rule.
- Reads the per-event colour chip (~2.7 × 10.6 pt vertical bar) and maps it to a
  hue family and tone via `HUE_FAMILIES`.
- Classifies status from the printed word, falling back to tone.
- Emits `PdfEntry` (explicitly *not* an interval) and `DaySignal` rollups.
- Populates `unresolved[]` with the structural gaps rather than papering over
  them.

```bash
python -m esd_scheduler.ingest_outlook_pdf "Calendar - Shrivastava, Namit - Outlook.pdf"
```

### 4.2 New module (to build)

`esd_scheduler/evidence.py` — the tiering layer.

```python
@dataclass(frozen=True)
class AvailabilityEvidence:
    person_key: Optional[str]       # None until attribution resolves
    calendar_color_id: Optional[str]
    day: date
    start: Optional[time]
    end: Optional[time]             # None => not an interval, cannot veto
    state: str                      # busy | tentative | free | named_event
    tier: int                       # 1, 2 or 3
    confidence: float
    truncated_cell: bool
    source_ref: str

    def can_veto(self) -> bool:
        return self.tier <= 2 and self.start is not None and self.end is not None
```

`can_veto()` is the single choke point. `feasibility.evaluate` consults only
evidence where it returns True.

### 4.3 Changed: `feasibility.py`

Add one predicate to the Layer 1 chain, between `Cal` and `W`:

| New | Predicate | Fails when |
|---|---|---|
| `Ev` | evidence sufficiency | the only availability evidence for this coordinator is Tier 3 |

Failing `Ev` produces `fail_reason = "tier3_evidence_only"`, which routes to
manual confirmation exactly like `calendar_unavailable`. It does **not** mean
"busy"; it means "we do not know, and this source is not allowed to guess".

### 4.4 Changed: `scoring.py`

Tier-3 signal enters at Layer 2 only, and only through the burden term, where
being wrong costs a ranking position rather than a double-booking:

```
committed_hours_effective = committed_hours + mu * day_pressure_prior
```

`day_pressure_prior` comes from `DaySignal.density` for that (person, day),
scaled to hours by an elicited `mu` (default 0.4 h per counted commitment).
Gate it on `cfg.use_tier3_priors`, default **off** for the pilot: turn it on only
once attribution is resolved, otherwise it applies one person's load to a hue.

### 4.5 Changed: `config.py`

```python
use_tier3_priors: bool = False
tier3_hours_per_commitment: float = 0.4     # mu
tier3_max_score_effect: float = 0.10        # hard cap on how far a prior can move S
tier3_max_age_days: int = 30                # a month-view export goes stale fast
calendar_color_map: Dict[str, str] = {}     # hue -> person, operator-supplied
display_timezone: str = "America/New_York"  # exports print none; state it once
```

`tier3_max_score_effect` matters: it bounds the blast radius of the weakest
source so a bad attribution cannot reorder a shortlist on its own.

### 4.6 Changed: `store.py`

Three columns on `candidate_score`:

| Column | Type | Purpose |
|---|---|---|
| `evidence_tier` | INTEGER | Highest-quality tier that informed this row |
| `tier3_prior_hours` | REAL | How much Tier-3 prior entered the burden term |
| `truncated_source_cell` | INTEGER | The backing export cell was at its row cap |

New table `ingest_run`: `ingest_id`, `source_file`, `source_sha256`, `tier`,
`view_type`, `date_range`, `entries`, `truncated_cells`, `unresolved_json`,
`ingested_at`. The SHA means a re-parse of the same export is detectable, and a
changed export is never confused with a changed calendar.

### 4.7 Changed: `drift.py` and `report.py`

Four metrics, all of which would have caught the problems in §1:

- `tier3_only_rate` — share of scored candidates with no Tier-1/2 evidence.
  Rising means the Graph integration is degrading and nobody noticed.
- `truncated_source_rate` — share of Tier-3 days at the row cap.
- `attribution_resolved_rate` — share of Tier-3 entries with a `person_key`.
  Currently **1/7 hues = 14%**.
- `pdf_vs_graph_disagreement` — for days covered by both, how often the PDF
  shows a commitment where Graph showed free. This is the cross-check that makes
  the weak source earn its keep: it audits the strong one.

New surprise codes: `TIER3_ONLY_ASSIGNMENT`, `TRUNCATED_SOURCE_DAY`,
`PDF_GRAPH_DISAGREEMENT`.

### 4.8 Changed: `cli.py`

```bash
python -m esd_scheduler ingest-pdf FILE.pdf [--map colors.json] [--write]
python -m esd_scheduler evidence-report --week-start 2026-08-17
```

---

## 5. Scoring integration

The brief's contract maps onto the engine already in place:

```
final_slot_score = alpha * calendar_feasibility + beta * predictive_visit_score
```

- `predictive_visit_score` **is** the existing `S(c,v) = wΦΦ + wΩΩ + wΨΨ + wP·P`.
  It is already built, tested and audited. Do not build a second one.
- `calendar_feasibility` is the Layer 0/1 output, which in the current engine is
  boolean by design: a candidate is feasible or is not.
- So for Tier 1–2, `alpha` is degenerate and the correct composition is the one
  already implemented — hard filter, then score. **Do not soften Layer 1 into a
  weight.** That is the single change most likely to reintroduce double-booking.
- `alpha`/`beta` become meaningful only at Tier 3, where feasibility is genuinely
  graded rather than boolean. Suggested pilot values: `alpha = 0.35`,
  `beta = 0.65`, with `alpha` forced to 0 whenever Tier-1 evidence exists for
  the coordinator.

Day-level ranking (the only thing Tier 3 supports):

$$\text{feas}(d) = w_1 \cdot \text{overlap}(d) + w_3 \cdot \text{prox}(d) - w_4 \cdot \text{conflict}(d) - w_6 \cdot \text{uncert}(d)$$

with `overlap = 1 − density/9`, `prox = 1 − days_out/21`,
`conflict = busy/7`, `uncert = 0.75` on truncated cells and `0.30` otherwise
(never 0 — no export of this kind is ever certain). `w5` (fragmentation) is
omitted, not approximated: it needs intervals.

---

## 6. Fixing the source — the highest-value change

Everything above is mitigation. The actual fix is a five-minute change to how
the export is produced, and it should happen before any of §4.2 onwards.

**In order of preference:**

1. **Connect Microsoft Graph.** An app registration with `Calendars.Read`
   (application permission) makes all of this moot. `calendarsync.GraphProvider`
   is already written and waiting for a token. This is the only option that
   yields real intervals continuously.
2. **If a manual export is unavoidable, export Work Week view, not Month.**
   Work Week prints start *and* end times and does not row-cap. That single
   change moves the source from Tier 3 to Tier 2 and unlocks time-level
   scheduling.
3. **Export one calendar per file.** Solves attribution completely and costs six
   extra prints. Alternatively, supply `config/calendar-colors.json` mapping hue
   to person once — the colours are stable per Outlook profile.
4. **State the timezone** in the filename or a covering note.

Without at least (2), the answer to "when can we schedule this visit?" from this
source is, correctly, "this file cannot tell you."

---

## 7. Roadmap

**P0 — this week, no engineering**
- Confirm the display timezone.
- Re-export as Work Week, or supply the colour map.
- Add the two recurring blocks (Thu 09:00 check-in, Fri 09:30 lab meeting) to
  the roster as standing commitments. They are stable, evidenced, and do not
  need a calendar read at all.

**P1 — one to two days**
- `evidence.py` with `AvailabilityEvidence.can_veto()`.
- Layer 1 `Ev` predicate + `tier3_evidence_only` fail reason.
- `ingest_run` table and the three `candidate_score` columns.
- `ingest-pdf` CLI command.

**P2 — after Graph is connected**
- `pdf_vs_graph_disagreement` metric and the three surprise codes.
- Turn on `use_tier3_priors` **only** once `attribution_resolved_rate` is 1.0.
- Elicit `mu` the same way `gamma` is elicited: one question to the team.

**P3**
- Learn the lab's start-hour distribution from Graph and replace the hand-set
  `tier3_hours_per_commitment` with a fitted value.
- Feed observed no-show rates by time-of-day into `predictive_visit_score`.

---

## 8. Test plan

Add to `tests/test_engine.py`:

| Test | Asserts |
|---|---|
| `test_month_view_never_produces_intervals` | every `PdfEntry.end_time is None`; the three interval arrays are empty |
| `test_tier3_evidence_cannot_veto` | a coordinator with only Tier-3 evidence fails Layer 1 with `tier3_evidence_only`, and is **not** recorded as busy |
| `test_tier3_prior_is_capped` | with `use_tier3_priors` on, no prior moves `S(c,v)` by more than `tier3_max_score_effect` |
| `test_leading_spill_week_dates` | the first grid row parses to July, not the view's own month |
| `test_row_cap_detection` | a fixture with N cells at the cap sets `truncated_cells` and raises the uncertainty penalty |
| `test_owner_calendar_inference` | the titled-events asymmetry resolves exactly one hue, at confidence ≤ 0.85 |
| `test_no_legend_no_attribution` | with no legend, all other hues keep `participant is None` |
| `test_all_day_free_is_not_absence` | an all-day `Free` row is recorded as state `free`, never dropped |

---

## 9. Failure modes this design defends against

| Failure | How it happens | Defence |
|---|---|---|
| Booking over an invisible meeting | Month view dropped it at the row cap | Tier 3 can never confirm availability |
| One person's load applied to another | Hue guessed against header order | `participant` stays null without a legend; priors gated off |
| Afternoon looks free, is not | Truncation cuts later entries | Truncation direction tested and reported; uncertainty penalty raised |
| Stale export treated as current | PDF has no fetch timestamp | `tier3_max_age_days`, and `ingest_run.source_sha256` |
| Weak source silently becomes ground truth | Graph outage, PDF still present | `tier3_only_rate` metric plus the `TIER3_ONLY_ASSIGNMENT` surprise code |
| Layer 1 softened into a weight | Reading `alpha * feasibility` too literally | `alpha` forced to 0 whenever Tier-1 evidence exists |

---

## 10. Standing rule

> The engine already refuses to let a high score rescue a Layer 1 failure.
> This document adds its mirror: **a weak source may not manufacture a Layer 1
> pass.** Absence of evidence is routed to a human, never resolved into
> availability.
