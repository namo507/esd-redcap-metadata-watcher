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
| GET | `/api/calendar/imports` | Every upload on file, newest first |
| POST | `/api/calendar/upload` | `{filename, data}` with the file base64-encoded |
| POST | `/api/calendar/review` | Confirm or reject one block read from an image |
| POST | `/api/calendar/review-all` | Settle every pending block at once |
| GET | `/api/calendar/colors` | The stored colour-to-person map |
| POST | `/api/calendar/colors` | Save it, with who confirmed it |

`tests/test_docs.py` checks this list against the routes the server actually
registers, so an endpoint added without a line here fails the suite.

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
