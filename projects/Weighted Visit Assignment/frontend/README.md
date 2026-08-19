# Frontend

One HTML file, one stylesheet, one script. No framework, no bundler, no
`node_modules`. The backend serves it from the same origin, so there is no CORS
config and no second process.

```
index.html    structure
styles.css    ESD design tokens and components
app.js        fetch, render, assign
assets/       lab and UofSC logos
```

Open it by running the backend: `python3 -m backend.server`.

## Brand

Tokens come from the `esd-lab` design system: discovery blue `#3366FF`,
science blue `#91BAF4`, cool blue `#E6EEFC`, cool white `#F4F4F6`, with
confident orange, optimal yellow and firetruck red as accents. Libre Franklin
throughout, 20px card radius, pill buttons. The drifted blues `#005CBE` and
`#2A61E6` must never appear.

Lab logo sits left of the UofSC logo at equal height, in the header and the
footer.

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
