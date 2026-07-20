# IPSA CSBS output status

The current Jessi-facing deliverable is
`IPSA_CSBS_scoring_assignments_master.csv`. It was generated from the live IPSA
REDCap API after Full Data Set access became available and contains exactly four
columns: `ID`, `Visit Month`, `Examiner`, and `Assigned Scoring Clinician`.

The successful run contains 1,174 completed CSBS visits across 371 exact REDCap
IDs: 247 at 9 months, 222 at 12 months, 209 at 15 months, 232 at 18 months, and
264 at 24 months. Every `demo_id` is preserved verbatim and used as its own case
key. This includes 486 unsuffixed rows, 344 `--1` rows, and 344 rows ending in
`--2`; suffixes are never stripped or merged. The 486-row unsuffixed ID/event
multiset exactly matches REDCap report 4692.

REDCap provided 540 examiner values; the remaining 634 genuinely blank examiner
cells used Jessi's workload-balanced, seed-42 fallback. Final assignments are
Emma 390, Tessa 390, and Axie 394.

Examiner text is preserved as returned by REDCap. All current nonblank values
were recognized through an exact allowlist. For co-scored values, every named
Emma/Tessa/Axie clinician was excluded from that visit's candidate pool and
credited in contact history. Contact history remains isolated by exact REDCap
ID, uses the actual Date of Evaluation, and removes duplicates only within that
same exact ID before applying the Never Seen, Least Visits, and Furthest in Time
rules.

All 44 notebook quality checks passed. Supporting assignment, candidate-trace,
exclusion, workload, field-mapping, and run-manifest files are retained for
audit. The older permission-filtered run remains isolated under
`invalid_permission_filtered_run_2026-07-20/` and must not be used.
