# Calendar inbox

Drop an Outlook calendar PDF print in this folder and the scheduled job files
it: parsed, recorded in the audit store, and the original moved to
`data/uploads/` so the same file is never imported twice. A running board picks
up the new rows on its next read — nobody has to open the dashboard.

Sweep it by hand with:

    make inbox

**Print Work Week or Day view.** A month grid has no end times, so it can only
report day-level load and can never book a time.

Files here are gitignored: a calendar print carries event titles for everyone
overlaid on the page.
