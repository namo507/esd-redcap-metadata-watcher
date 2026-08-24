# Frontend

One HTML file, one stylesheet, and one small script per section. No framework,
no bundler, no `node_modules`. The backend serves it from the same origin when
run locally, and the published copy talks to the API cross-origin or falls back
to a frozen snapshot.

```
index.html          structure, and the markup for all four sections
styles.css          ESD design tokens and components
config.js           API_BASE. Empty means same origin
static-board.js     the frozen snapshot, used when no API answers
js/core.js          state, fetch, routing, page chrome
js/team.js          the team table and the protocol clock
js/assign.js        the queue, the pair, entering a visit
js/calendars.js     uploads, what came back, exports
js/logic.js         the decision map
js/boot.js          start-up and the once-a-minute refresh
assets/             lab and UofSC logos, section icons
```

Loaded in that order. `core.js` first because everything else uses its
helpers, `boot.js` last because it starts everything. One file per section, so
a change to the team view means opening `team.js` and nothing else.

Open it by running the backend: `python3 -m backend.server`.

## Brand

Tokens come from the `esd-lab` design system: discovery blue `#3366FF`,
science blue `#91BAF4`, cool blue `#E6EEFC`, cool white `#F4F4F6`, with
confident orange, optimal yellow and firetruck red as accents. Libre Franklin
throughout, 20px card radius, pill buttons. The drifted blues `#005CBE` and
`#2A61E6` must never appear.

Lab logo sits left of the UofSC logo at equal height, in the header and the
footer.

Every colour on the page is a token declared at the top of `styles.css`,
including the state colours for free, busy, warning and blocked. The brand
palette has no green or amber, so those are declared together with a note
saying they are borrowed for state only and stay out of the chrome. The one
deliberate exception is the seven Outlook category swatches: they are the
hues a coordinator is looking at in Outlook while matching a colour to a
person, so they are hardcoded and must not become brand tokens.

## What a row tells you about a person

The team table shows **roles**, not a guess from their certifications. Someone
signed off on one assessment is not thereby able to run a visit, and the page
used to say "clinician" on the strength of the first. A row now carries the
roles from `config/roster.json` and, where the manual gives one, the visit ages
that person can run alone: *Lauren Puttock — coordinator, clinician, tech,
1m-12m, van*. Maggie and Ramiro read *coordinator, tech* because the manual
prints no solo range beside their names.

A pairing shows which **vehicle** it should take and why, and flags a visit
that runs **out of hours**, because that puts the pair into the rotation.

## Design rules this page follows

**No bare codes.** `V002 · NANO` is an eyebrow; the heading is
"Family 5031 · 3mo NANO visit".

**Explanations are inline, never behind a link.** Each coordinator shows a
stacked bar of what earned their score, a plain-language "leads on" line, and
the facts behind it.

**The bar is on a true 0-to-1 scale.** It is not normalised to the widest
option, because a pool where nobody scores well has to look like one.

**Every criterion is listed, including the ones that scored nothing.** A missing
row reads as "not considered"; a zero reads as "considered, earned nothing".

**Absence is explained.** "Not available for this visit (3)" lists each excluded
person and the one reason that excluded them, in words: *Not certified for ADOS*,
*Family exclusion on file*.

**Confidence is words, never a probability.** Clear choice, Slight edge, Too
close to call.

**Overriding takes one click and captures a reason** from a fixed list.

**Blocked people are shown, not hidden**, greyed with the veto stated:
*Already at their capacity for the week*.

## Accessibility

Keyboard reachable with visible focus rings, status never carried by colour
alone (every state has a text label), `prefers-reduced-motion` respected, and
the layout collapses to a single column under 940px.
