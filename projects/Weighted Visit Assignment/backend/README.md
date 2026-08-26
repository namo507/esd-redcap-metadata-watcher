# Backend

Stdlib HTTP server over the `esd_scheduler` engine. No dependencies, no build
step, no virtualenv. If Python runs, this runs.

```bash
python3 -m backend.server            # http://127.0.0.1:8765
python3 -m backend.server --port 9000 --no-browser
python3 backend/tests/test_api.py    # 12 end-to-end tests
```

## Files

| | |
|---|---|
| `server.py` | Routing, JSON responses, static file serving, path-traversal guard |
| `session.py` | One `LabState` plus its audit store; translates engine output into what a screen needs |
| `nano.py` | The NANO study's participants and windows, as the dropdowns need them |
| `settings.py` | The tunables, their allowed values, and what applying one does |
| `build_static.py` | Freezes the board into `dist-static/` so the page works with no API |
| `export_snapshot.py` | Writes the board out for the exports the queue offers |
| `tests/test_api.py` | Boots a real server on an ephemeral port and talks HTTP to it |

## Endpoints

Reading the board:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Mode, weight vector, calendar source, privacy posture |
| GET | `/api/board` | Everything the page needs in one round trip |
| GET | `/api/roster` | Coordinators with current load |
| GET | `/api/coordinators` | The team table: free hours, sign-offs, day by day |
| GET | `/api/visits` | The visit queue |
| GET | `/api/visit?id=V001` | One visit: ranked pairs, exclusions with reasons, notices |
| GET | `/api/availability` | Who is free, slot by slot, across the week |
| GET | `/api/schedule` | Which family is owed a visit next, and when |
| GET | `/api/fairness` | Weekly spread, CV, shuffle-test p-value |
| GET | `/api/week` | Greedy vs optimiser regret |
| GET | `/api/logic` | The decision map the "How it decides" section draws |

Changing it:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/assign` | `{visit_id, coordinator_id, reason_code?, reason_text?}` |
| POST | `/api/unassign` | `{visit_id}` |
| POST | `/api/visits` | Enter a real visit. See `config/README.md` on live mode |
| POST | `/api/visits/remove` | `{visit_id}`, for a visit that was entered |
| POST | `/api/reset` | Rebuild the board from scratch |

Calendars:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/nano/families` | Every NANO participant and the one window that matters |
| GET | `/api/nano/family` | `?id=5901` — all eight checkpoints for one family |
| POST | `/api/nano/plan` | `{family_id, checkpoint}` — put it on the board and rank who takes it |
| GET | `/api/settings` | Every knob the lab may turn, with its value and its allowed choices |
| POST | `/api/settings` | `{key, value}` — set one, applied at once. Returns what moved plus the whole catalogue |
| GET | `/api/calendar/read-table` | What the last upload read, one row per calendar, ready to correct |
| GET | `/api/calendar/imports` | Every upload on file, newest first |
| POST | `/api/calendar/upload` | `{filename, data}` with the file base64-encoded |
| POST | `/api/calendar/review` | Confirm or reject one block read from an image |
| POST | `/api/calendar/review-all` | Settle every pending block at once |
| GET | `/api/calendar/colors` | The stored colour-to-person map |
| POST | `/api/calendar/colors` | Save it, with who confirmed it |

`tests/test_docs.py` checks this list against the routes the server actually
registers, so an endpoint added without a line here fails the suite.

### Turning a knob

`settings.py` holds the catalogue: every tunable, its current value and the
list of values it accepts. Three things follow from that shape.

**A knob is data.** Adding one is a row in `KNOBS`, or a row the roster
generates — per-person capacity works that way, so somebody joining the lab
gets a control with no code change. No route and no frontend edit.

**Only offered values are accepted.** The dropdown sends a value, the server
checks it against the same option list it published, and refuses anything
else. A typo cannot reach the engine, and a number cannot arrive as a string.

**Applying does not reset the board.** `LabSession.reload_settings()` re-reads
the config files and pushes the per-person values onto the coordinators
already in play; the uploaded calendar, the assignments and the activity log
survive. A scheduler nudging a weight is not asking to lose the week's work,
and if tweaking cost them the upload they would stop tweaking.

A change is written to the file in `config/`, not to a database. That is
deliberate: the config files stay the single source of what the board is
doing, a tweak shows up in `git diff` like any other change to them, and a
setting turned on a whim can be read back or reverted by whoever comes next.
It also means the comments in those files are preserved through an edit,
which a test checks.

The weights are the one knob with a consequence worth stating: they must sum
to 1, so setting one rescales the other three in proportion and the response
reports where all four landed. `weight_vector_id` carries a fingerprint of the
config, so a decision recorded earlier still points at the numbers that
produced it.

### What a read-table row says

One row per overlaid calendar in the print, because that is what an Outlook
export is. Three fields carry the state a scheduler has to act on:

- `needs_mapping` — the board could not tell whose calendar this is. These
  rows lead the table and carry a dropdown. Nothing is guessed from a colour.
- `scheduled` — whether the board would ever offer this person. `false` for
  somebody the roster holds as `active: false`: the calendar is recognised as
  theirs, so it is not an open question, but its blocks are not read and the
  row is greyed. A count of `0` would have read as "free all week", so the
  count is left blank instead.
- `coordinator_id` — who it resolved to. `options` is built from the roster on
  every request, so adding a coordinator makes them selectable with no code
  change, and anyone already attributed on the print is included even when
  they are not being scheduled, flagged rather than hidden.

## Rules this layer enforces

**No scheduling logic lives here.** Every ranking, veto and eligibility decision
comes from `esd_scheduler`. This module decides only what to show.

**Layer 1 failures are not assignable over HTTP.** Posting an ineligible
coordinator returns 400, so a crafted request cannot do what the UI prevents.

**An override without a reason is refused.** Choosing past rank 1 requires a
code from the closed vocabulary in `store.OVERRIDE_REASON_CODES`. The
`data_defect` / `preference` split is what the weight re-elicitation depends on,
and an unexplained override is a lost data point.

**Event titles never reach the browser.** The engine strips `subject`,
`location` and `isPrivate` at ingestion; a test asserts the API payload is clean.

## Threading

`ThreadingHTTPServer` serves each request on its own thread while sharing one
`AuditStore`. SQLite is opened with `check_same_thread=False` and every
statement goes through a lock inside the store. This was a real bug the API
tests caught, not a precaution.
