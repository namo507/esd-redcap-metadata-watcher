# ESD Visitboard: improvement prompt

**Target:** https://esd-visitboard.namit507.chatgpt.site
**Companion docs:** [`ESD-Visit-Scheduling-v3-SPEC.md`](ESD-Visit-Scheduling-v3-SPEC.md) ·
[`ESD-Graph-Privacy-RESEARCH-REPORT.md`](ESD-Graph-Privacy-RESEARCH-REPORT.md) ·
[`ESD-Calendar-Ingestion-PROMPT-AND-STRATEGY.md`](ESD-Calendar-Ingestion-PROMPT-AND-STRATEGY.md)

---

## 0. Read this before building anything

The meeting feedback was specific and it was not "make it prettier":

> *"It's very pretty. It looks like it's got the foundations of a lot of pieces
> that you would absolutely want, but I would definitely need to be trained on
> how to do this."*
>
> *"Well, it might be — before you dedicate any time to making it easier, it'd be
> good to collaborate with the group to understand what we mean by that."*

Two things follow, and the second is the one that is easy to ignore.

1. **The information architecture is right.** Three numbered steps, a visit
   queue, a filter row, an updates log. Do not restructure it.
2. **The PI explicitly asked us not to start redesigning yet.** Phase 0 below is
   therefore not optional preamble — it *is* the next deliverable. Building the
   changes in §3 before running §1 would be doing the thing we were just asked
   not to do.

"I'd need to be trained on how to use this" is a design finding, not a training
requirement. A tool used a few times a week by five people cannot depend on
remembered instructions. Every change below removes a thing someone would
otherwise have to be told.

---

## 1. Phase 0 — discovery protocol (do this first)

**Participants — the six coordinators on the shared Outlook calendar, plus the
PI:**

| | Runs the assignment? | Why they are in the session |
|---|---|---|
| Margaret Bell | yes | Weekly user of the queue and the checks |
| Lauren Puttock | yes | Weekly user |
| Sanjana Oak | yes | Weekly user |
| Sofia Tous | yes | Weekly user; has a family exclusion on file, so exercises task T2 |
| Morgan Soto | yes | Part-time; the capacity and working-hours edges show up here first |
| Ramiro Lucas-Mariano | yes | Weekly user |
| The PI | reviews | Owns the weights, the review band and the override taxonomy |
| Namit Shrivastava | builds | Observes and records; does not answer questions during the session |

**Time:** 30 minutes each, individually, not as a group — a group session
converges on the loudest opinion and the individual confusions are lost, which
are exactly what this is for.

**Method: silent observation.** Send each person a real visit to assign. Say
nothing. Screen-record. Every question they ask out loud is a defect; write the
sentence down verbatim.

**The five tasks:**

| # | Task | What it tests |
|---|---|---|
| T1 | "Assign a coordinator to the Aug 26 visit for Family 1088." | Can they complete the core loop unaided? |
| T2 | "Why was Sofia Tous not offered for this visit?" | Is exclusion legible? Currently: no. |
| T3 | "You think Lauren Puttock is the better choice. Do that." | Is override possible, and does it capture why? |
| T4 | "Which coordinator is carrying the most work this week?" | Is fairness visible at all? |
| T5 | "A family calls and says Tuesday no longer works. What now?" | Reschedule path — does one exist? |

**Instrument three numbers**, so "more intuitive" becomes measurable rather
than a matter of taste:

- **Time to first assignment**, unaided. Target under 3 minutes.
- **Questions asked per task.** Target 0 by the second visit.
- **Vocabulary miss rate** — terms read aloud with a rising, questioning
  intonation (`V-022`, `NANO`, `3 month`, `2 OF 5`, `Needs calendar`).

**Deliverable from Phase 0:** a one-page findings note listing every verbatim
question, mapped to the section of §3 that answers it. Circulate to the group
before writing code. Anything in §3 that no participant tripped over gets
deprioritised, whatever this document says.

---

## 2. The build prompt

Copy-paste from here down once Phase 0 is done.

````markdown
ROLE
You are improving the ESD Visitboard, an internal tool used by a small research
lab to assign coordinators to family home visits. Users are research
coordinators and a PI, not software people. They will use it a few times a
week, so nothing may rely on remembered training.

WHAT EXISTS AND MUST BE KEPT
- Header with lab identity and a live Outlook connection indicator
- Filter row: search, date, status, coordinator, clear
- Visit queue on the left; selected visit detail on the right
- A three-step assignment flow: calendar checks -> required checks -> choose
- Updates log at the bottom
- The visual design. It was described as "very pretty" and is not the problem.

THE PROBLEM TO SOLVE
A capable user said they would need to be trained to use it. Every change must
remove something a user would otherwise have to be told. If a change adds a
thing to learn, it is the wrong change.

DESIGN CONSTRAINTS
1. Plain language over lab shorthand. Never show a bare code where a phrase
   fits. "V-022 - NANO - 3 month" reads as "3-month NANO visit - Family 1088",
   with the code available on hover or in small secondary text.
2. Explain in place, never behind a link. "How the suggestion works" must be
   visible next to the suggestion, not one click away.
3. Every state must say what to do next. A disabled button explains which
   specific item is blocking it and links to that item.
4. Never show a number without its meaning. "2 OF 5" is currently unreadable.
5. Absence must be explained. If a coordinator is missing from the ranking, the
   interface says who and why, in one line each.
6. The tool recommends; the human decides. Overriding must be one click, and
   must capture a reason from a fixed list.
7. Progressive disclosure: the common path is visible, the detail is one
   expand away, nothing important is hidden.
8. Accessible: keyboard reachable, visible focus, 4.5:1 contrast minimum,
   status never carried by colour alone.

PRIVACY REQUIREMENTS (non-negotiable)
The tool reads free/busy only. It must never display, store, or request an
event title, location, or body. The UI must make this legible, because the
lab's central worry is that the tool sees more than it should:
- A persistent, plain-language badge: "Reads free/busy only. Never reads event
  titles."
- The connection panel names the exact permission in use.
- If an event title is ever received from the API, discard it at ingestion; it
  must not reach the browser.

OUT OF SCOPE
Do not restructure the three-step flow. Do not add a new colour palette. Do not
add features not traceable to a Phase 0 finding.
````

---

## 3. Specific changes, keyed to observed problems

Each row is a defect that is visible in the current build. Priority is by how
directly it causes the "I'd need training" reaction.

### P0 — the things that cause the training reaction

| # | Observed now | Change |
|---|---|---|
| 3.1 | `2 OF 5` badge, unexplained | Either label it (`Visit 2 of 5 this week for Family 1088`) or remove it. An unexplained number costs more attention than it returns. |
| 3.2 | `V-022 · NANO · 3 month` as the primary identity | Lead with `3-month NANO visit`; `V-022` becomes small secondary text. Add a one-line glossary tooltip on `NICO`/`NANO` giving the study name. |
| 3.3 | `How the suggestion works` is a link | Inline it. Under each ranked coordinator show the four criteria as a compact bar with the actual contributions: continuity, family preference, burden relief, protocol continuity. The engine already computes and stores these — see `contrib_*` in the audit schema. |
| 3.4 | Only three coordinators appear; the other three vanish | Add a collapsed **"Not available for this visit (3)"** section listing each excluded person with the single reason that excluded them: `Not ADOS-certified`, `Calendar shows busy 1:00-4:00`, `Family exclusion on file`. This is Phase 0 task T2, and it is currently unanswerable. |
| 3.5 | `Complete the open calendar and required checks to continue.` | Name the blocker and link to it: `Sanjana Oak still needs a calendar check` as a button that scrolls to and focuses that card. |
| 3.6 | Step 2 checklist appears pre-ticked, and reads as assertions | Make each item an explicit unchecked confirmation with the evidence beside it: `Time works — 1:00-4:00 PM fits Margaret's 8:00-5:00 working hours`. A checklist that arrives pre-satisfied trains people to click through it. |
| 3.7 | No override path | Add `Choose someone else` on every ranked option. On click, require a reason from the fixed list already defined in `store.py`: family request, coordinator request, clinical judgment, training opportunity, calendar data wrong, credential data wrong, travel data wrong, history data wrong. **Split "the data was wrong" from "I disagreed"** — the engine's weight validation depends on that distinction, and it is the highest-value data the UI can collect. |

### P1 — once live Outlook is connected

| # | Observed now | Change |
|---|---|---|
| 3.8 | 6 coordinators × 3 buttons = 18 manual clicks per visit | Replace with the live result: a per-coordinator strip showing the requested window with busy blocks shaded, sourced from `getSchedule`. Manual entry survives only as an offline fallback. |
| 3.9 | `Outlook check: manual` badge is unexplained | Three explicit states: **Live** (with "checked 2 minutes ago"), **Stale** (with "last checked 40 minutes ago — recheck"), **Not connected** (with what that means for trust). Mirrors the Layer 0 freshness classes in the spec. |
| 3.10 | Nothing distinguishes "free" from "nothing scheduled" | Show three visually distinct states: **outside working hours** (hatched), **free within working hours** (light), **explicitly marked free** (light with a dot). This was raised directly in the meeting ("it'd have to discern that between 8:30 and 5 I'm free") and the API supports it via `workingHours`. |
| 3.11 | Recheck is implicit | Keep the "Calendar rechecked now" affordance and make it mandatory at commit: re-query the single chosen person immediately before assigning. One API call; closes the double-booking race. |

### P2 — surface what the engine already knows

The v3 engine computes all of this and writes it to the audit log. None of it
reaches the screen, which is why the tool looks simpler than it is and why the
ranking feels like a black box.

| # | Change |
|---|---|
| 3.12 | **Review band.** When the top two are within the calibrated band (currently 0.020), show `Close call — two good options` rather than a confident first place. Present them side by side. |
| 3.13 | **Selection stability.** Show the leader's `P(top-1)` as plain words: `Clear choice` (>0.85), `Slight edge` (0.6–0.85), `Too close to call` (<0.6). Never print the raw probability. |
| 3.14 | **Fairness panel.** A small always-visible strip: visits and hours per coordinator this week, with the busiest flagged. Phase 0 task T4 currently has no answer anywhere in the UI. |
| 3.15 | **Constraint vetoes are not rankings.** When a coordinator is skipped for the travel-share cap or being over capacity, say so in those words. A system veto is not a human override and must not read as one. |
| 3.16 | **Weekly debrief link.** The engine already generates a branded HTML debrief. Link the latest one from the header. |

### P3 — flow completeness

| # | Change |
|---|---|
| 3.17 | Reschedule path (Phase 0 task T5). Currently there is none. |
| 3.18 | Structured updates log: reason codes as chips plus free text, not free text alone. |
| 3.19 | First-run guidance: a dismissible three-step overlay pointing at queue, checks, and choose. |
| 3.20 | Empty states with a next action: no visits match the filter; no coordinator passes the checks; Outlook not connected. |

---

## 4. Privacy surface

The lab's dominant concern is that the tool sees more than it should. The
interface should answer that without anyone having to ask.

**Header badge, always visible:**

> 🔒 **Reads free/busy only** — never event titles

**Connection panel, expanded:**

> Signed in as *you*. The board sees exactly what you already see in Outlook
> when you open a colleague's calendar: busy, free, tentative, out of office.
> Event titles, locations and descriptions are not requested and are not
> received.
> Permission in use: `Calendars.Read.Shared` (delegated, read-only)
> This tool cannot create, edit or delete anyone's calendar events.

Rules for the client:
- Never render `subject`, `location` or `isPrivate`, even if present in a payload.
- Strip them server-side at ingestion; they must not reach the browser at all.
- If a busy block has no title — which is every block — label it **`Busy`**, not
  `Untitled event`. The second phrasing implies a title was withheld and invites
  the question of who can see it.

---

## 5. Acceptance criteria

Ship when all of these hold, tested with someone who did **not** build it:

1. A coordinator who has never seen the tool assigns a visit unaided in under
   three minutes.
2. They can answer "why wasn't Sanjana offered?" without asking anyone.
3. They can override the top suggestion, and the reason is captured in the
   audit log with the correct reason class.
4. They can say who is carrying the most work this week.
5. No screen shows a number, code or badge whose meaning is not on screen.
6. Every disabled control names its specific blocker.
7. No event title appears anywhere, and the privacy statement is visible without
   scrolling.
8. Keyboard-only completion of the full assignment flow.
9. All three "close call" states render correctly against seeded fixtures.

---

## 6. Non-goals

- Redesigning the visual language. It works.
- Restructuring the three-step flow. It is the right shape.
- Adding features with no Phase 0 finding behind them.
- Exposing raw scores. Show contributions and plain-language confidence; a
  coordinator should never have to reason about a number between 0 and 1.
- Building anything that depends on live Outlook before the IT ticket in the
  research report is answered.
